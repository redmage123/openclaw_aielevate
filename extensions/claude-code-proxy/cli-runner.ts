import { spawn, type ChildProcess } from "node:child_process";
import type { Readable } from "node:stream";
import type { ClaudeCliJsonResult, ClaudeCodeProxyConfig } from "./types.js";
import {
  MODEL_MAP,
  MAX_CONCURRENT,
  DEFAULT_CLAUDE_BINARY,
  DEFAULT_TIMEOUT_MS,
  SIGKILL_DELAY_MS,
} from "./types.js";

// Build a clean env that strips ALL Claude-related markers to avoid nesting detection.
// The Claude CLI checks env vars and parent process tree to detect nesting.
// Strip everything that starts with CLAUDE to ensure the CLI runs as a fresh instance.
function cleanEnv(): NodeJS.ProcessEnv {
  const env = { ...process.env };
  for (const key of Object.keys(env)) {
    if (key.startsWith("CLAUDE")) {
      delete env[key];
    }
  }
  // Explicitly unset TERM_PROGRAM if it's set to something Claude-related
  if (env.TERM_PROGRAM?.toLowerCase().includes("claude")) {
    delete env.TERM_PROGRAM;
  }
  return env;
}

// Track active processes for graceful shutdown
const activeProcesses = new Set<ChildProcess>();

// Send SIGTERM to a process group, then escalate to SIGKILL if it doesn't exit
function killWithEscalation(proc: ChildProcess): void {
  try {
    if (proc.pid) process.kill(-proc.pid, "SIGTERM");
  } catch {
    proc.kill("SIGTERM");
  }
  const escalation = setTimeout(() => {
    try {
      if (proc.pid) process.kill(-proc.pid, "SIGKILL");
    } catch {
      /* already dead */
    }
  }, SIGKILL_DELAY_MS);
  escalation.unref();
  proc.on("close", () => clearTimeout(escalation));
}

export function killAllActive(): void {
  for (const proc of activeProcesses) {
    killWithEscalation(proc);
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
  resumeSessionId?: string;
}): string[] {
  const args = ["-p", "--output-format", opts.outputFormat, "--model", resolveModel(opts.model)];
  if (opts.resumeSessionId) {
    // Resume an existing session — system prompt is already set in the session
    args.push("--resume", opts.resumeSessionId);
  } else if (opts.systemPrompt) {
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
  resumeSessionId?: string;
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
    resumeSessionId: opts.resumeSessionId,
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
      detached: true,
    });
    activeProcesses.add(proc);
    // Unref so the detached process group doesn't prevent Node from exiting
    proc.unref();

    const chunks: Buffer[] = [];

    proc.stdout.on("data", (chunk: Buffer) => chunks.push(chunk));
    // Capture stderr for diagnostics
    const stderrChunks: Buffer[] = [];
    proc.stderr.on("data", (chunk: Buffer) => stderrChunks.push(chunk));

    // Write prompt to stdin and close
    proc.stdin.write(prompt);
    proc.stdin.end();

    const timer = setTimeout(() => {
      const stderr = Buffer.concat(stderrChunks).toString("utf-8").trim();
      if (stderr) {
        console.error(`[claude-code-proxy] CLI stderr before timeout: ${stderr.slice(0, 500)}`);
      }
      killWithEscalation(proc);
      settle(reject, new Error(`Claude CLI timed out after ${timeoutMs}ms`));
    }, timeoutMs);

    const onAbort = () => {
      killWithEscalation(proc);
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
  resumeSessionId?: string;
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
    resumeSessionId: opts.resumeSessionId,
  });

  const proc = spawn(binary, args, {
    stdio: ["pipe", "pipe", "pipe"],
    env: cleanEnv(),
    detached: true,
  });
  activeProcesses.add(proc);
  proc.unref();

  // Capture stderr for diagnostics
  proc.stderr.on("data", (chunk: Buffer) => {
    const text = chunk.toString("utf-8").trim();
    if (text) console.error(`[claude-code-proxy] CLI stderr: ${text.slice(0, 300)}`);
  });

  // Write prompt to stdin and close
  proc.stdin.write(prompt);
  proc.stdin.end();

  const timer = setTimeout(() => {
    killWithEscalation(proc);
  }, timeoutMs);

  const onAbort = () => {
    killWithEscalation(proc);
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
