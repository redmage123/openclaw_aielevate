import { randomUUID } from "node:crypto";
import { createServer, type Server, type IncomingMessage, type ServerResponse } from "node:http";
import { runCli, runCliStream, killAllActive } from "./cli-runner.js";
import { translateMessages } from "./message-translator.js";
import { pipeStreamToSSE } from "./stream-adapter.js";
import type { ClaudeCodeProxyConfig, OpenAiChatRequest } from "./types.js";
import {
  MODEL_IDS,
  MAX_BODY_BYTES,
  DEFAULT_CONTEXT_WINDOW,
  DEFAULT_MAX_TOKENS,
  PROVIDER_ID,
} from "./types.js";

type Logger = {
  info: (msg: string) => void;
  warn: (msg: string) => void;
  error: (msg: string) => void;
};

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

  // Item 9: Message content validation
  for (const msg of body.messages) {
    if (typeof msg.role !== "string" || typeof msg.content !== "string") {
      sendError(res, 400, "Each message must have a string 'role' and 'content'");
      return;
    }
  }

  // Item 2: Model whitelist enforcement
  const model = body.model || "claude-sonnet-4-6";
  if (!MODEL_IDS.includes(model)) {
    sendError(res, 400, `Unknown model: ${model}. Available: ${MODEL_IDS.join(", ")}`);
    return;
  }

  const { prompt, systemPrompt } = translateMessages(body.messages);
  const requestId = `chatcmpl-${randomUUID()}`;

  if (body.stream) {
    // Streaming response
    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "X-Request-Id": requestId,
    });

    try {
      const { stream } = runCliStream({
        prompt,
        model,
        systemPrompt,
        maxTokens: body.max_tokens,
        config,
        signal: req.destroyed ? AbortSignal.abort() : undefined,
      });

      req.on("close", () => {
        stream.destroy();
      });

      await pipeStreamToSSE(stream, res, model, requestId);
    } catch (err) {
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
      const result = await runCli({
        prompt,
        model,
        systemPrompt,
        maxTokens: body.max_tokens,
        config,
      });

      const response = {
        id: requestId,
        object: "chat.completion",
        created: Math.floor(Date.now() / 1000),
        model,
        choices: [
          {
            index: 0,
            message: {
              role: "assistant",
              content: result.result,
            },
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

export function createProxyServer(config: ClaudeCodeProxyConfig, logger: Logger): Server {
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

  return server;
}

export function startServer(config: ClaudeCodeProxyConfig, logger: Logger): Promise<Server> {
  return new Promise((resolve, reject) => {
    const server = createProxyServer(config, logger);

    server.on("error", (err) => {
      reject(err);
    });

    server.listen(config.port, "127.0.0.1", () => {
      logger.info(`claude-code-proxy: listening on http://127.0.0.1:${config.port}`);
      resolve(server);
    });
  });
}

export function stopServer(server: Server): Promise<void> {
  killAllActive();
  return new Promise((resolve) => {
    server.close(() => resolve());
  });
}
