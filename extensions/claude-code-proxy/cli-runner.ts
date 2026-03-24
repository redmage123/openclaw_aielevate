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

// Build a minimal env for the Claude CLI subprocess.
// Start from scratch (like `env -i`) to guarantee no nesting-detection
// env vars leak through, then add only what the CLI needs to run.
function cleanEnv(): NodeJS.ProcessEnv {
  return {
    HOME: process.env.HOME,
    PATH: process.env.PATH,
    TERM: "dumb",
    // Pass through proxy/TLS vars if set
    ...(process.env.HTTPS_PROXY ? { HTTPS_PROXY: process.env.HTTPS_PROXY } : {}),
    ...(process.env.HTTP_PROXY ? { HTTP_PROXY: process.env.HTTP_PROXY } : {}),
    ...(process.env.NO_PROXY ? { NO_PROXY: process.env.NO_PROXY } : {}),
    ...(process.env.NODE_EXTRA_CA_CERTS
      ? { NODE_EXTRA_CA_CERTS: process.env.NODE_EXTRA_CA_CERTS }
      : {}),
  };
}

// Resolve the full path to the `claude` binary once at module load time.
// The gateway process has the correct PATH (e.g. nvm paths), but the
// isolated subprocess env may not resolve the same binary (env -i +
// setsid can lose PATH ordering). By caching the absolute path we
// guarantee the correct version is always invoked.
import { execFileSync } from "node:child_process";
let _resolvedClaudeBinary: string | null = null;
function resolveClaudeBinary(configPath: string): string {
  if (configPath && configPath !== DEFAULT_CLAUDE_BINARY) return configPath;
  if (_resolvedClaudeBinary) return _resolvedClaudeBinary;
  try {
    _resolvedClaudeBinary = execFileSync("which", ["claude"], {
      encoding: "utf-8",
      timeout: 5000,
    }).trim();
  } catch {
    _resolvedClaudeBinary = DEFAULT_CLAUDE_BINARY;
  }
  return _resolvedClaudeBinary;
}

// Shell-escape a single argument for embedding in a bash -c string
function shellEscape(s: string): string {
  return "'" + s.replace(/'/g, "'\\''") + "'";
}

// Spawn the Claude CLI fully isolated from the current process tree.
// Uses `env -i ... setsid bash -c 'exec claude ...'` which:
// 1. env -i: starts with a completely blank environment (no CLAUDE* vars)
// 2. setsid: creates a new session (severs /proc/<ppid> walk)
// 3. bash -c exec: replaces bash with claude so stdio piping works
function spawnIsolated(binary: string, args: string[], env: NodeJS.ProcessEnv): ChildProcess {
  const cmd = [binary, ...args].map(shellEscape).join(" ");
  // Build env -i arguments: env -i KEY=VAL KEY=VAL ... setsid bash -c 'exec ...'
  const envArgs: string[] = ["-i"];
  for (const [k, v] of Object.entries(env)) {
    if (v !== undefined) envArgs.push(`${k}=${v}`);
  }
  envArgs.push("setsid", "bash", "-c", `exec ${cmd}`);
  return spawn("env", envArgs, {
    stdio: ["pipe", "pipe", "pipe"],
  });
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
  config: ClaudeCodeProxyConfig;
  extraFlags: string[];
}): string[] {
  const args = ["-p", "--output-format", opts.outputFormat, "--model", resolveModel(opts.model)];

  if (opts.systemPrompt) {
    // Always append to preserve the CLI's built-in tool definitions.
    // The CLI handles tool execution natively via its agentic loop.
    args.push("--append-system-prompt", opts.systemPrompt);
  }

  // Config-driven CLI flags
  const cfg = opts.config;
  if (cfg.appendSystemPrompt) {
    args.push("--append-system-prompt", cfg.appendSystemPrompt);
  }
  if (cfg.mcpConfig) {
    for (const path of cfg.mcpConfig) {
      args.push("--mcp-config", path);
    }
  }
  if (cfg.allowedTools && cfg.allowedTools.length > 0) {
    args.push("--allowedTools", cfg.allowedTools.join(","));
  }
  if (cfg.disallowedTools && cfg.disallowedTools.length > 0) {
    args.push("--disallowedTools", cfg.disallowedTools.join(","));
  }
  if (cfg.permissionMode) {
    args.push("--permission-mode", cfg.permissionMode);
  }
  if (cfg.pluginDirs) {
    for (const dir of cfg.pluginDirs) {
      args.push("--plugin-dir", dir);
    }
  }
  if (cfg.addDirs) {
    for (const dir of cfg.addDirs) {
      args.push("--add-dir", dir);
    }
  }
  if (cfg.maxBudgetUsd != null && cfg.maxBudgetUsd > 0) {
    args.push("--max-budget-usd", String(cfg.maxBudgetUsd));
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
  config: ClaudeCodeProxyConfig;
  signal?: AbortSignal;
}): Promise<ClaudeCliJsonResult> {
  const { prompt, model, systemPrompt, maxTokens, config, signal } = opts;
  checkConcurrencyLimit();

  const binary = resolveClaudeBinary(config.claudeBinaryPath || "");
  const timeoutMs = config.timeoutMs || DEFAULT_TIMEOUT_MS;
  const args = buildArgs({
    model,
    systemPrompt,
    outputFormat: "json",
    config,
    extraFlags: [],
  });

  return new Promise<ClaudeCliJsonResult>((resolve, reject) => {
    let settled = false;

    const settle = (fn: typeof resolve | typeof reject, value: ClaudeCliJsonResult | Error) => {
      if (settled) return;
      settled = true;
      (fn as (v: ClaudeCliJsonResult | Error) => void)(value);
    };

    console.log(`[claude-code-proxy] [TIMING] spawning CLI: ${binary} ${args.join(" ")}`);
    const spawnT0 = Date.now();
    const proc = spawnIsolated(binary, args, cleanEnv());
    activeProcesses.add(proc);
    console.log(
      `[claude-code-proxy] [TIMING] spawn returned pid=${proc.pid} +${Date.now() - spawnT0}ms`,
    );

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
  config: ClaudeCodeProxyConfig;
  signal?: AbortSignal;
}): { stream: Readable; process: ChildProcess } {
  const { prompt, model, systemPrompt, maxTokens, config, signal } = opts;
  checkConcurrencyLimit();

  const binary = resolveClaudeBinary(config.claudeBinaryPath || "");
  const timeoutMs = config.timeoutMs || DEFAULT_TIMEOUT_MS;
  const args = buildArgs({
    model,
    systemPrompt,
    outputFormat: "stream-json",
    config,
    extraFlags: ["--verbose"],
  });

  console.log(`[claude-code-proxy] [TIMING] stream spawning CLI: ${binary}`);
  const spawnT0 = Date.now();
  const proc = spawnIsolated(binary, args, cleanEnv());
  activeProcesses.add(proc);
  console.log(
    `[claude-code-proxy] [TIMING] stream spawn returned pid=${proc.pid} +${Date.now() - spawnT0}ms`,
  );

  // Capture stderr for diagnostics
  proc.stderr.on("data", (chunk: Buffer) => {
    const text = chunk.toString("utf-8").trim();
    if (text) console.error(`[claude-code-proxy] CLI stderr: ${text.slice(0, 300)}`);
  });

  // Write prompt to stdin and close
  proc.stdin.write(prompt);
  proc.stdin.end();
  console.log(`[claude-code-proxy] [TIMING] stdin written +${Date.now() - spawnT0}ms`);

  const timer = setTimeout(() => {
    killWithEscalation(proc);
  }, timeoutMs);

  const onAbort = () => {
    killWithEscalation(proc);
  };
  signal?.addEventListener("abort", onAbort, { once: true });

  proc.on("close", (code) => {
    console.log(
      `[claude-code-proxy] [TIMING] stream CLI closed code=${code} +${Date.now() - spawnT0}ms`,
    );
    clearTimeout(timer);
    signal?.removeEventListener("abort", onAbort);
    activeProcesses.delete(proc);
  });

  proc.on("error", (err) => {
    console.error(
      `[claude-code-proxy] [TIMING] stream CLI error: ${err.message} +${Date.now() - spawnT0}ms`,
    );
    clearTimeout(timer);
    signal?.removeEventListener("abort", onAbort);
    activeProcesses.delete(proc);
  });

  return { stream: proc.stdout, process: proc };
}
