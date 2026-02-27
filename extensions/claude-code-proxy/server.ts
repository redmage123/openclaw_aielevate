import { createHash, randomUUID, timingSafeEqual } from "node:crypto";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { createServer, type Server, type IncomingMessage, type ServerResponse } from "node:http";
import { homedir } from "node:os";
import { join } from "node:path";
import { runCli, runCliStream, killAllActive } from "./cli-runner.js";
import { translateMessages, extractResumePrompt } from "./message-translator.js";
import { pipeStreamToSSE } from "./stream-adapter.js";
import type { ClaudeCodeProxyConfig, OpenAiChatRequest, OpenAiMessage } from "./types.js";
import {
  MODEL_IDS,
  MAX_BODY_BYTES,
  DEFAULT_CONTEXT_WINDOW,
  DEFAULT_MAX_TOKENS,
  PROVIDER_ID,
  HEARTBEAT_INTERVAL_MS,
} from "./types.js";

// ---------------------------------------------------------------------------
// Session store — track CLI session IDs for conversation resumption
// ---------------------------------------------------------------------------
// Keyed by a fingerprint derived from the conversation messages.
// When a follow-up request arrives with more messages than before, we
// resume the CLI session (--resume <id>) and send only the new content.
// This enables prompt caching and memory persistence across turns.

const SESSION_TTL_MS = 30 * 60 * 1000; // 30 minutes
const SESSION_PRUNE_INTERVAL_MS = 5 * 60 * 1000; // prune every 5 min
const SESSION_FILE = join(homedir(), ".openclaw", "claude-code-sessions.json");

type ConversationSession = {
  cliSessionId: string;
  messageCount: number;
  lastUsed: number;
};

const sessionStore = new Map<string, ConversationSession>();

/** Load sessions from disk, discarding any that have expired. */
function loadSessions(): void {
  try {
    const raw = readFileSync(SESSION_FILE, "utf-8");
    const entries = JSON.parse(raw) as Record<string, ConversationSession>;
    const now = Date.now();
    for (const [key, session] of Object.entries(entries)) {
      if (now - session.lastUsed <= SESSION_TTL_MS) {
        sessionStore.set(key, session);
      }
    }
  } catch {
    // File doesn't exist or is corrupt — start fresh
  }
}

/** Persist the current session store to disk. */
function saveSessions(): void {
  try {
    const obj: Record<string, ConversationSession> = {};
    for (const [key, session] of sessionStore) {
      obj[key] = session;
    }
    mkdirSync(join(homedir(), ".openclaw"), { recursive: true });
    writeFileSync(SESSION_FILE, JSON.stringify(obj, null, 2), "utf-8");
  } catch {
    // Non-fatal — sessions will just be lost on restart
  }
}

// Load persisted sessions on module init
loadSessions();

// Prune expired sessions periodically and persist after pruning
const sessionPruneTimer = setInterval(() => {
  const now = Date.now();
  let pruned = false;
  for (const [key, session] of sessionStore) {
    if (now - session.lastUsed > SESSION_TTL_MS) {
      sessionStore.delete(key);
      pruned = true;
    }
  }
  if (pruned) saveSessions();
}, SESSION_PRUNE_INTERVAL_MS);
sessionPruneTimer.unref();

/**
 * Derive a stable conversation fingerprint from the first user message
 * and any system messages. This identifies "the same conversation" across
 * requests so we can resume the CLI session.
 */
function conversationFingerprint(messages: OpenAiMessage[]): string {
  const parts: string[] = [];
  for (const msg of messages) {
    if (msg.role === "system") {
      parts.push(`s:${typeof msg.content === "string" ? msg.content : ""}`);
    } else if (msg.role === "user") {
      // Use only the first user message to anchor the conversation identity.
      // Additional user messages grow the conversation but don't change its ID.
      parts.push(`u:${typeof msg.content === "string" ? msg.content : ""}`);
      break;
    }
  }
  return createHash("sha256").update(parts.join("\n")).digest("hex").slice(0, 16);
}

type Logger = {
  info: (msg: string) => void;
  warn: (msg: string) => void;
  error: (msg: string) => void;
};

// ---------------------------------------------------------------------------
// Auth helpers (inline — plugins can't import core internals)
// ---------------------------------------------------------------------------

/** Timing-safe string comparison via SHA-256 digests (prevents length-leak). */
function safeEqual(a: string | undefined | null, b: string | undefined | null): boolean {
  if (typeof a !== "string" || typeof b !== "string") return false;
  const hash = (s: string) => createHash("sha256").update(s).digest();
  return timingSafeEqual(hash(a), hash(b));
}

/** Extract bearer token from `Authorization: Bearer <token>` header. */
function getBearerToken(req: IncomingMessage): string | undefined {
  const raw = (
    typeof req.headers.authorization === "string" ? req.headers.authorization : ""
  ).trim();
  if (!raw.toLowerCase().startsWith("bearer ")) return undefined;
  const token = raw.slice(7).trim();
  return token || undefined;
}

// ---------------------------------------------------------------------------
// Auth rate limiter (simplified version of src/gateway/auth-rate-limit.ts)
// ---------------------------------------------------------------------------

const RATE_MAX_ATTEMPTS = 10;
const RATE_WINDOW_MS = 60_000; // 1 min
const RATE_LOCKOUT_MS = 300_000; // 5 min
const RATE_PRUNE_MS = 60_000; // prune every 1 min

type RateLimitEntry = { attempts: number[]; lockedUntil?: number };

function isLoopback(ip: string | undefined): boolean {
  if (!ip) return false;
  return ip === "127.0.0.1" || ip === "::1" || ip === "::ffff:127.0.0.1";
}

function createAuthRateLimiter() {
  const entries = new Map<string, RateLimitEntry>();

  const pruneTimer = setInterval(() => {
    const now = Date.now();
    for (const [key, entry] of entries) {
      if (entry.lockedUntil && now < entry.lockedUntil) continue;
      entry.attempts = entry.attempts.filter((ts) => ts > now - RATE_WINDOW_MS);
      if (entry.attempts.length === 0) entries.delete(key);
    }
  }, RATE_PRUNE_MS);
  pruneTimer.unref();

  return {
    /** Check if IP is allowed. Returns { allowed, retryAfterMs }. */
    check(ip: string | undefined): { allowed: boolean; retryAfterMs: number } {
      if (isLoopback(ip)) return { allowed: true, retryAfterMs: 0 };
      const entry = entries.get(ip ?? "unknown");
      if (!entry) return { allowed: true, retryAfterMs: 0 };
      const now = Date.now();
      if (entry.lockedUntil) {
        if (now < entry.lockedUntil) {
          return { allowed: false, retryAfterMs: entry.lockedUntil - now };
        }
        entry.lockedUntil = undefined;
        entry.attempts = [];
      }
      entry.attempts = entry.attempts.filter((ts) => ts > now - RATE_WINDOW_MS);
      return { allowed: entry.attempts.length < RATE_MAX_ATTEMPTS, retryAfterMs: 0 };
    },

    /** Record a failed auth attempt. */
    recordFailure(ip: string | undefined): void {
      if (isLoopback(ip)) return;
      const key = ip ?? "unknown";
      let entry = entries.get(key);
      if (!entry) {
        entry = { attempts: [] };
        entries.set(key, entry);
      }
      if (entry.lockedUntil && Date.now() < entry.lockedUntil) return;
      const now = Date.now();
      entry.attempts = entry.attempts.filter((ts) => ts > now - RATE_WINDOW_MS);
      entry.attempts.push(now);
      if (entry.attempts.length >= RATE_MAX_ATTEMPTS) {
        entry.lockedUntil = now + RATE_LOCKOUT_MS;
      }
    },

    dispose(): void {
      clearInterval(pruneTimer);
      entries.clear();
    },
  };
}

function sendJson(res: ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
}

function sendError(
  res: ServerResponse,
  status: number,
  message: string,
  type = "invalid_request_error",
): void {
  sendJson(res, status, {
    error: { message, type, param: null, code: null },
  });
}

async function readBody(req: IncomingMessage): Promise<string> {
  // Early rejection via Content-Length header
  const contentLength = req.headers["content-length"];
  if (contentLength && Number.parseInt(contentLength, 10) > MAX_BODY_BYTES) {
    req.destroy();
    throw new BodyTooLargeError();
  }

  const chunks: Buffer[] = [];
  let totalBytes = 0;
  for await (const chunk of req) {
    totalBytes += (chunk as Buffer).length;
    if (totalBytes > MAX_BODY_BYTES) {
      req.destroy();
      throw new BodyTooLargeError();
    }
    chunks.push(chunk as Buffer);
  }
  return Buffer.concat(chunks).toString("utf-8");
}

class BodyTooLargeError extends Error {
  constructor() {
    super("Request body too large");
  }
}

async function handleCompletions(
  req: IncomingMessage,
  res: ServerResponse,
  config: ClaudeCodeProxyConfig,
  logger: Logger,
): Promise<void> {
  // Item 8: Content-Type validation
  const contentType = req.headers["content-type"] ?? "";
  if (!contentType.includes("application/json")) {
    sendError(res, 415, "Content-Type must be application/json");
    return;
  }

  let body: OpenAiChatRequest;
  try {
    const raw = await readBody(req);
    body = JSON.parse(raw) as OpenAiChatRequest;
  } catch (err) {
    if (err instanceof BodyTooLargeError) {
      sendError(res, 413, "Request body too large");
      return;
    }
    sendError(res, 400, "Invalid JSON body");
    return;
  }

  if (!body.messages || !Array.isArray(body.messages) || body.messages.length === 0) {
    sendError(res, 400, "messages array is required and must not be empty");
    return;
  }

  // Message validation: role is required; content can be string, null, or array
  for (const msg of body.messages) {
    if (typeof msg.role !== "string") {
      sendError(res, 400, "Each message must have a string 'role'");
      return;
    }
  }

  // Item 2: Model whitelist enforcement
  const model = body.model || "claude-sonnet-4-6";
  if (!MODEL_IDS.includes(model)) {
    sendError(res, 400, `Unknown model: ${model}. Available: ${MODEL_IDS.join(", ")}`);
    return;
  }

  // Don't pass gateway tools to the CLI — the CLI handles tool execution
  // natively via its own agentic loop (Bash, Read, Write, etc.).
  // Gateway tool definitions would conflict with the CLI's native tools
  // and create a broken text-based <tool_call> protocol that the model ignores.
  const requestId = `chatcmpl-${randomUUID()}`;

  // Session resumption: if we have a CLI session for this conversation,
  // resume it and send only the new user message. This enables prompt
  // caching (faster follow-ups) and memory persistence across turns.
  const convKey = conversationFingerprint(body.messages);
  const existingSession = sessionStore.get(convKey);
  let prompt: string;
  let systemPrompt: string | undefined;
  let resumeSessionId: string | undefined;

  if (existingSession && body.messages.length > existingSession.messageCount) {
    // Follow-up: resume existing CLI session with only new messages
    resumeSessionId = existingSession.cliSessionId;
    prompt = extractResumePrompt(body.messages, existingSession.messageCount);
    systemPrompt = undefined; // system prompt already in the resumed session
    logger.info(
      `[SESSION] resuming session ${resumeSessionId} (prev=${existingSession.messageCount} msgs, now=${body.messages.length})`,
    );
  } else {
    // Fresh conversation or retry: translate all messages
    const translated = translateMessages(
      body.messages,
      /* tools */ undefined,
      /* toolChoice */ undefined,
    );
    prompt = translated.prompt;
    systemPrompt = translated.systemPrompt;
    resumeSessionId = undefined;
    if (existingSession) {
      logger.info(`[SESSION] resetting stale session for conv=${convKey}`);
      sessionStore.delete(convKey);
      saveSessions();
    }
  }

  const t0 = Date.now();
  logger.info(`[TIMING] request arrived, stream=${!!body.stream} model=${model}`);

  if (body.stream) {
    // Streaming response
    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "X-Request-Id": requestId,
    });

    // SSE heartbeat keeps the connection alive and lets clients detect stalls
    const heartbeat = setInterval(() => {
      if (!res.destroyed) res.write(":heartbeat\n\n");
    }, HEARTBEAT_INTERVAL_MS);
    heartbeat.unref();

    try {
      logger.info(
        `[TIMING] +${Date.now() - t0}ms spawning CLI` +
          (resumeSessionId ? ` (resume=${resumeSessionId})` : " (fresh)"),
      );
      const { stream } = runCliStream({
        prompt,
        model,
        systemPrompt,
        maxTokens: body.max_tokens,
        config,
        resumeSessionId,
        signal: req.destroyed ? AbortSignal.abort() : undefined,
      });
      logger.info(`[TIMING] +${Date.now() - t0}ms CLI spawned`);

      req.on("close", () => {
        stream.destroy();
      });

      let firstChunkLogged = false;
      stream.on("data", () => {
        if (!firstChunkLogged) {
          firstChunkLogged = true;
          logger.info(`[TIMING] +${Date.now() - t0}ms first CLI stdout chunk`);
        }
      });

      const { sessionId: cliSessionId } = await pipeStreamToSSE(
        stream,
        res,
        model,
        requestId,
        /* hasTools */ false,
      );

      // Store session for future resumption
      if (cliSessionId) {
        sessionStore.set(convKey, {
          cliSessionId,
          messageCount: body.messages.length,
          lastUsed: Date.now(),
        });
        saveSessions();
        logger.info(`[SESSION] stored session ${cliSessionId} for conv=${convKey}`);
      }

      logger.info(`[TIMING] +${Date.now() - t0}ms stream complete, sending to browser`);
      clearInterval(heartbeat);
    } catch (err) {
      clearInterval(heartbeat);
      const msg = err instanceof Error ? err.message : "Unknown error";
      logger.error(`Stream error: ${msg}`);

      // Item 7: Guard against writing to destroyed response
      if (!res.destroyed) {
        if (isConcurrencyError(msg)) {
          res.write(
            `data: ${JSON.stringify({ error: { message: "Too many concurrent requests" } })}\n\n`,
          );
        } else {
          res.write(`data: ${JSON.stringify({ error: { message: "Request failed" } })}\n\n`);
        }
        res.write("data: [DONE]\n\n");
      }
    }
    if (!res.destroyed) {
      res.end();
    }
  } else {
    // Non-streaming response
    try {
      logger.info(
        `[TIMING] +${Date.now() - t0}ms calling runCli` +
          (resumeSessionId ? ` (resume=${resumeSessionId})` : " (fresh)"),
      );
      const result = await runCli({
        prompt,
        model,
        systemPrompt,
        maxTokens: body.max_tokens,
        config,
        resumeSessionId,
      });

      // Store session for future resumption
      if (result.session_id) {
        sessionStore.set(convKey, {
          cliSessionId: result.session_id,
          messageCount: body.messages.length,
          lastUsed: Date.now(),
        });
        saveSessions();
      }

      logger.info(`[TIMING] +${Date.now() - t0}ms CLI returned (api=${result.duration_api_ms}ms)`);

      // CLI handles tool execution natively; result.result is the final text.
      const response = {
        id: requestId,
        object: "chat.completion",
        created: Math.floor(Date.now() / 1000),
        model,
        choices: [
          {
            index: 0,
            message: { role: "assistant", content: result.result },
            finish_reason: "stop",
          },
        ],
        usage: {
          prompt_tokens: result.usage?.input_tokens ?? 0,
          completion_tokens: result.usage?.output_tokens ?? 0,
          total_tokens: (result.usage?.input_tokens ?? 0) + (result.usage?.output_tokens ?? 0),
        },
      };
      sendJson(res, 200, response);
    } catch (err) {
      // Item 5: Sanitize error messages — log details server-side, return generic to client
      const msg = err instanceof Error ? err.message : "Unknown error";
      logger.error(`Completion error: ${msg}`);
      if (isConcurrencyError(msg)) {
        sendError(res, 429, "Too many concurrent requests", "rate_limit_error");
      } else {
        sendError(res, 500, "Internal server error", "server_error");
      }
    }
  }
}

function isConcurrencyError(msg: string): boolean {
  return msg === "Too many concurrent requests";
}

function handleModels(res: ServerResponse): void {
  const models = MODEL_IDS.map((id) => ({
    id,
    object: "model",
    created: Math.floor(Date.now() / 1000),
    owned_by: PROVIDER_ID,
  }));
  sendJson(res, 200, { object: "list", data: models });
}

function handleHealth(res: ServerResponse): void {
  sendJson(res, 200, { status: "ok", provider: PROVIDER_ID });
}

export function createProxyServer(
  config: ClaudeCodeProxyConfig,
  logger: Logger,
): { server: Server; rateLimiter: ReturnType<typeof createAuthRateLimiter> | undefined } {
  const rateLimiter = config.authToken ? createAuthRateLimiter() : undefined;

  const server = createServer((req: IncomingMessage, res: ServerResponse) => {
    const url = new URL(req.url ?? "/", `http://${req.headers.host ?? "localhost"}`);
    const path = url.pathname;

    // Security headers
    res.setHeader("X-Content-Type-Options", "nosniff");
    res.setHeader("Referrer-Policy", "no-referrer");

    // CORS restricted to localhost origins
    const origin = req.headers.origin ?? "";
    if (/^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(origin)) {
      res.setHeader("Access-Control-Allow-Origin", origin);
    }
    res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type, Authorization");

    if (req.method === "OPTIONS") {
      res.writeHead(204);
      res.end();
      return;
    }

    // Bearer auth — skip for GET /health (health probes must work unauthenticated)
    if (config.authToken && !(req.method === "GET" && path === "/health")) {
      const clientIp = req.socket.remoteAddress;

      // Rate limit check
      if (rateLimiter) {
        const rl = rateLimiter.check(clientIp);
        if (!rl.allowed) {
          const retryAfter = Math.ceil(rl.retryAfterMs / 1000);
          res.writeHead(429, {
            "Content-Type": "application/json",
            "Retry-After": String(retryAfter),
          });
          res.end(
            JSON.stringify({
              error: { message: "Too many failed auth attempts", type: "rate_limit_error" },
            }),
          );
          return;
        }
      }

      // Accept token from Bearer header or ?token= query parameter
      const provided = getBearerToken(req) || url.searchParams.get("token") || undefined;
      if (!safeEqual(provided, config.authToken)) {
        rateLimiter?.recordFailure(clientIp);
        res.writeHead(401, {
          "Content-Type": "application/json",
          "WWW-Authenticate": "Bearer",
        });
        res.end(
          JSON.stringify({ error: { message: "Unauthorized", type: "authentication_error" } }),
        );
        return;
      }
    }

    if (req.method === "POST" && path === "/v1/chat/completions") {
      handleCompletions(req, res, config, logger).catch((err) => {
        const msg = err instanceof Error ? err.message : String(err);
        logger.error(`Unhandled error: ${msg}`);
        if (!res.headersSent) {
          sendError(res, 500, "Internal server error", "server_error");
        }
        if (!res.destroyed) {
          res.end();
        }
      });
      return;
    }

    if (req.method === "GET" && path === "/v1/models") {
      handleModels(res);
      return;
    }

    if (req.method === "GET" && path === "/health") {
      handleHealth(res);
      return;
    }

    sendError(res, 404, `Not found: ${req.method} ${path}`);
  });

  return { server, rateLimiter };
}

/**
 * Fire a lightweight CLI invocation in the background to warm up the
 * process: loads the binary, authenticates, initialises MCP servers, etc.
 * The result is discarded — the goal is to pay the cold-start cost before
 * the first real user request arrives.
 */
function warmUpCli(config: ClaudeCodeProxyConfig, logger: Logger): void {
  const t0 = Date.now();
  logger.info("[WARMUP] spawning warm-up CLI process...");
  runCli({
    prompt: "Say OK",
    model: "claude-haiku-4-5",
    systemPrompt: "Reply with exactly: OK",
    maxTokens: 8,
    config,
  })
    .then(() => {
      logger.info(`[WARMUP] warm-up complete in ${Date.now() - t0}ms — CLI is hot`);
    })
    .catch((err) => {
      const msg = err instanceof Error ? err.message : String(err);
      logger.warn(`[WARMUP] warm-up failed (non-fatal): ${msg}`);
    });
}

export function startServer(config: ClaudeCodeProxyConfig, logger: Logger): Promise<Server> {
  return new Promise((resolve, reject) => {
    const { server, rateLimiter } = createProxyServer(config, logger);

    // Stash rate limiter for cleanup on stop
    (server as ServerWithRateLimiter).__rateLimiter = rateLimiter;

    server.on("error", (err) => {
      rateLimiter?.dispose();
      reject(err);
    });

    // HTTP socket timeout: CLI timeout + 30s buffer to prevent connections from living forever
    server.timeout = (config.timeoutMs || 120_000) + 30_000;

    server.listen(config.port, "127.0.0.1", () => {
      logger.info(
        `claude-code-proxy: listening on http://127.0.0.1:${config.port}` +
          (config.authToken ? " (auth: bearer)" : " (auth: none)"),
      );

      // Pre-warm the CLI so the first real request doesn't pay cold-start cost
      warmUpCli(config, logger);

      resolve(server);
    });
  });
}

type ServerWithRateLimiter = Server & {
  __rateLimiter?: ReturnType<typeof createAuthRateLimiter>;
};

export function stopServer(server: Server): Promise<void> {
  killAllActive();
  saveSessions();
  sessionStore.clear();
  clearInterval(sessionPruneTimer);
  (server as ServerWithRateLimiter).__rateLimiter?.dispose();
  return new Promise((resolve) => {
    server.close(() => resolve());
  });
}
