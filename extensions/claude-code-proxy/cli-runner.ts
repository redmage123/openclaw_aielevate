import { spawn, type ChildProcess } from "node:child_process";
import type { Readable } from "node:stream";
import type { ClaudeCliJsonResult, ClaudeCodeProxyConfig } from "./types.js";
import { MODEL_MAP, MAX_CONCURRENT, DEFAULT_CLAUDE_BINARY, DEFAULT_TIMEOUT_MS } from "./types.js";

// Build a clean env that strips CLAUDECODE to avoid nesting detection
function cleanEnv(): NodeJS.ProcessEnv {
  const env = { ...process.env };
  delete env.CLAUDECODE;
  return env;
}

// Track active processes for graceful shutdown
const activeProcesses = new Set<ChildProcess>();

export function killAllActive(): void {
  for (const proc of activeProcesses) {
    proc.kill("SIGTERM");
  }
  activeProcesses.clear();
}

function resolveModel(requestedModel: string): string {
  const mapped = MODEL_MAP[requestedModel];
  if (!mapped) {
    throw new Error(`Unknown model: ${requestedModel}`);
  }
  return mapped;
}

function checkConcurrencyLimit(): void {
  if (activeProcesses.size >= MAX_CONCURRENT) {
    throw new Error("Too many concurrent requests");
  }
}

function buildArgs(opts: {
  model: string;
  systemPrompt: string | undefined;
  outputFormat: string;
  replaceSystemPrompt?: boolean;
  extraFlags: string[];
}): string[] {
  const args = ["-p", "--output-format", opts.outputFormat, "--model", resolveModel(opts.model)];
  if (opts.systemPrompt) {
    // --system-prompt replaces the default (hides built-in tool definitions);
    // --append-system-prompt adds to it (preserves built-in context).
    const flag = opts.replaceSystemPrompt ? "--system-prompt" : "--append-system-prompt";
    args.push(flag, opts.systemPrompt);
  }
  args.push(...opts.extraFlags);
  return args;
}

/**
 * Run Claude CLI in non-streaming mode. Returns parsed JSON result.
 */
export async function runCli(opts: {
  prompt: string;
  model: string;
  systemPrompt?: string;
  maxTokens?: number;
  disableBuiltinTools?: boolean;
  config: ClaudeCodeProxyConfig;
  signal?: AbortSignal;
}): Promise<ClaudeCliJsonResult> {
  const { prompt, model, systemPrompt, maxTokens, config, signal } = opts;
  checkConcurrencyLimit();

  const binary = config.claudeBinaryPath || DEFAULT_CLAUDE_BINARY;
  const timeoutMs = config.timeoutMs || DEFAULT_TIMEOUT_MS;
  const args = buildArgs({
    model,
    systemPrompt,
    outputFormat: "json",
    replaceSystemPrompt: opts.disableBuiltinTools,
    extraFlags: [],
  });

  return new Promise<ClaudeCliJsonResult>((resolve, reject) => {
    let settled = false;

    const settle = (fn: typeof resolve | typeof reject, value: ClaudeCliJsonResult | Error) => {
      if (settled) return;
      settled = true;
      (fn as (v: ClaudeCliJsonResult | Error) => void)(value);
    };

    const proc = spawn(binary, args, {
      stdio: ["pipe", "pipe", "pipe"],
      env: cleanEnv(),
    });
    activeProcesses.add(proc);

    const chunks: Buffer[] = [];

    proc.stdout.on("data", (chunk: Buffer) => chunks.push(chunk));
    // Capture stderr for diagnostics
    const stderrChunks: Buffer[] = [];
    proc.stderr.on("data", (chunk: Buffer) => stderrChunks.push(chunk));

    // Write prompt to stdin and close
    proc.stdin.write(prompt);
    proc.stdin.end();

    const timer = setTimeout(() => {
      proc.kill("SIGTERM");
      settle(reject, new Error(`Claude CLI timed out after ${timeoutMs}ms`));
    }, timeoutMs);

    const onAbort = () => {
      proc.kill("SIGTERM");
      settle(reject, new Error("Request aborted"));
    };
    signal?.addEventListener("abort", onAbort, { once: true });

    proc.on("close", (code) => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      activeProcesses.delete(proc);

      const stdout = Buffer.concat(chunks).toString("utf-8").trim();
      const stderr = Buffer.concat(stderrChunks).toString("utf-8").trim();

      // CLI may exit non-zero but still produce valid JSON with error info
      try {
        const result = JSON.parse(stdout) as ClaudeCliJsonResult;
        if (result.is_error) {
          settle(reject, new Error(result.result || "Claude CLI returned an error"));
          return;
        }
        settle(resolve, result);
      } catch {
        if (code !== 0) {
          const detail = stderr ? ` — stderr: ${stderr.slice(0, 500)}` : "";
          settle(reject, new Error(`Claude CLI exited with code ${code}${detail}`));
        } else {
          settle(reject, new Error("Failed to parse Claude CLI output"));
        }
      }
    });

    proc.on("error", (err) => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      activeProcesses.delete(proc);
      settle(reject, new Error(`Failed to spawn Claude CLI: ${err.message}`));
    });
  });
}

/**
 * Run Claude CLI in streaming mode. Returns a readable stream of NDJSON lines.
 */
export function runCliStream(opts: {
  prompt: string;
  model: string;
  systemPrompt?: string;
  maxTokens?: number;
  disableBuiltinTools?: boolean;
  config: ClaudeCodeProxyConfig;
  signal?: AbortSignal;
}): { stream: Readable; process: ChildProcess } {
  const { prompt, model, systemPrompt, maxTokens, config, signal } = opts;
  checkConcurrencyLimit();

  const binary = config.claudeBinaryPath || DEFAULT_CLAUDE_BINARY;
  const timeoutMs = config.timeoutMs || DEFAULT_TIMEOUT_MS;
  const args = buildArgs({
    model,
    systemPrompt,
    outputFormat: "stream-json",
    replaceSystemPrompt: opts.disableBuiltinTools,
    extraFlags: ["--verbose"],
  });

  const proc = spawn(binary, args, {
    stdio: ["pipe", "pipe", "pipe"],
    env: cleanEnv(),
  });
  activeProcesses.add(proc);

  // Write prompt to stdin and close
  proc.stdin.write(prompt);
  proc.stdin.end();

  const timer = setTimeout(() => {
    proc.kill("SIGTERM");
  }, timeoutMs);

  const onAbort = () => {
    proc.kill("SIGTERM");
  };
  signal?.addEventListener("abort", onAbort, { once: true });

  proc.on("close", () => {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onAbort);
    activeProcesses.delete(proc);
  });

  proc.on("error", () => {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onAbort);
    activeProcesses.delete(proc);
  });

  return { stream: proc.stdout, process: proc };
}
