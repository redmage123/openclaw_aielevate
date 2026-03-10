import type { ServerResponse } from "node:http";
import { createInterface } from "node:readline";
import type { Readable } from "node:stream";
import { parseToolCalls } from "./message-translator.js";
import type { OpenAiToolCall } from "./types.js";
import { STREAM_STALE_TIMEOUT_MS } from "./types.js";

// How often to inject a "still working" progress line when the CLI is
// executing tools internally (no NDJSON text events for a while).
const PROGRESS_INTERVAL_MS = 8_000;

type ContentPart = {
  type?: string;
  text?: string;
  thinking?: string;
  name?: string;
  id?: string;
  input?: Record<string, unknown>;
};

/**
 * Format a tool_use content block into a human-readable progress line.
 */
function formatToolProgress(part: ContentPart): string {
  const name = part.name ?? "tool";
  const input = part.input;
  let detail = "";
  if (input) {
    // Show a short preview of the tool input
    if (typeof input.command === "string") {
      detail = `: \`${input.command.slice(0, 120)}\``;
    } else if (typeof input.file_path === "string") {
      detail = `: ${input.file_path}`;
    } else if (typeof input.pattern === "string") {
      detail = `: ${input.pattern}`;
    } else if (typeof input.query === "string") {
      detail = `: ${input.query.slice(0, 80)}`;
    } else if (typeof input.url === "string") {
      detail = `: ${input.url.slice(0, 80)}`;
    }
  }
  return `[${name}${detail}]`;
}

/**
 * Convert Claude CLI stream-json NDJSON output to OpenAI SSE format.
 *
 * Claude stream-json emits one JSON object per line:
 * - `{ type: "system", ... }` — init metadata (tools, model, session)
 * - `{ type: "assistant", message: { content: [...] } }` — content chunks
 * - `{ type: "result", ... }` — final result
 *
 * Content arrays can include `tool_use` blocks when the CLI's agentic loop
 * calls tools. These are surfaced as progress status lines so the user can
 * see what the CLI is doing during long-running tasks.
 *
 * When `hasTools` is true, content is buffered and parsed for <tool_call>
 * blocks at the end so that tool calls are emitted in proper OpenAI format.
 */
export async function pipeStreamToSSE(
  cliStream: Readable,
  res: ServerResponse,
  model: string,
  requestId: string,
  hasTools = false,
  staleTimeoutMs = STREAM_STALE_TIMEOUT_MS,
): Promise<{ sessionId?: string }> {
  const created = Math.floor(Date.now() / 1000);
  let sentRole = false;
  let capturedSessionId: string | undefined;

  // Buffer for tool call detection (only used when hasTools=true)
  const toolBuffer: string[] = [];

  // Track tool activity for progress reporting
  let lastContentAt = Date.now();
  let toolsActive = false;
  let lastToolProgress = "";
  let toolCount = 0;

  // CLI readiness — only log the first fully-populated system event
  let cliReady = false;

  // Stale stream detection — destroy if no data arrives for staleTimeoutMs
  let staleTimer = setTimeout(() => cliStream.destroy(), staleTimeoutMs);
  staleTimer.unref();
  cliStream.on("data", () => {
    clearTimeout(staleTimer);
    staleTimer = setTimeout(() => cliStream.destroy(), staleTimeoutMs);
    staleTimer.unref();
  });
  res.on("close", () => clearTimeout(staleTimer));

  const rl = createInterface({ input: cliStream, crlfDelay: Infinity });

  const sendRoleChunk = () => {
    if (sentRole) return;
    sentRole = true;
    const chunk = {
      id: requestId,
      object: "chat.completion.chunk",
      created,
      model,
      choices: [
        {
          index: 0,
          delta: { role: "assistant", content: "" },
          finish_reason: null,
        },
      ],
    };
    res.write(`data: ${JSON.stringify(chunk)}\n\n`);
  };

  /** Inject a text delta into the SSE stream. */
  const sendContentDelta = (text: string) => {
    sendRoleChunk();
    const chunk = {
      id: requestId,
      object: "chat.completion.chunk",
      created,
      model,
      choices: [
        {
          index: 0,
          delta: { content: text },
          finish_reason: null,
        },
      ],
    };
    res.write(`data: ${JSON.stringify(chunk)}\n\n`);
  };

  // Periodic progress heartbeat: when tools are running and no text has
  // been emitted for a while, inject a status line so the user knows
  // the CLI is still working.
  const progressTimer = setInterval(() => {
    if (!toolsActive || !lastToolProgress) return;
    const silenceMs = Date.now() - lastContentAt;
    if (silenceMs >= PROGRESS_INTERVAL_MS) {
      const elapsed = Math.floor(silenceMs / 1000);
      const suffix = toolCount > 1 ? ` · ${toolCount} tools` : "";
      sendContentDelta(`\n*${lastToolProgress} (${elapsed}s)${suffix}*\n`);
    }
  }, PROGRESS_INTERVAL_MS);
  progressTimer.unref();

  for await (const line of rl) {
    if (!line.trim()) continue;

    let event: Record<string, unknown>;
    try {
      event = JSON.parse(line) as Record<string, unknown>;
    } catch {
      continue;
    }

    if (event.type === "system") {
      // Capture session_id from every system event
      capturedSessionId =
        capturedSessionId || ((event as Record<string, unknown>).session_id as string | undefined);

      const sysToolCount = Array.isArray(event.tools) ? event.tools.length : 0;
      const mcpCount = Array.isArray(event.mcp_servers) ? event.mcp_servers.length : 0;
      const version = (event.claude_code_version as string) || "unknown";
      const isReady = version !== "unknown" && sysToolCount > 0;

      if (!cliReady && isReady) {
        // First fully-populated system event — log it
        cliReady = true;
        console.error(
          `[claude-code-proxy] CLI init: v${version} tools=${sysToolCount} mcp=${mcpCount} model=${event.model ?? "?"}`,
        );
      } else if (cliReady && !isReady) {
        // Post-ready empty system event — warn
        console.error(
          `[claude-code-proxy] unexpected system event after init: v${version} tools=${sysToolCount}`,
        );
      }
      // Pre-ready empty events are expected during startup — skip silently
    } else if (event.type === "assistant") {
      const message = event.message as
        | {
            content?: ContentPart[];
          }
        | undefined;
      const contentParts = message?.content ?? [];

      for (const part of contentParts) {
        if (part.type === "text" && part.text) {
          // Real text content from the model
          lastContentAt = Date.now();
          toolsActive = false;

          if (hasTools) {
            toolBuffer.push(part.text);
          }
          sendContentDelta(part.text);
        } else if (part.type === "tool_use") {
          // The CLI is about to execute a tool — inject a progress line
          // so the user can see what's happening during long tool loops.
          toolsActive = true;
          toolCount++;
          const progress = formatToolProgress(part);
          lastToolProgress = progress;
          console.error(`[claude-code-proxy] tool: ${progress}`);
          sendContentDelta(`\n*${progress}*\n`);
          lastContentAt = Date.now();
        } else if (part.type === "tool_result") {
          // Tool finished — note the activity
          lastContentAt = Date.now();
        }
      }
    } else if (event.type === "result") {
      capturedSessionId = (event as Record<string, unknown>).session_id as string | undefined;
      const usage = extractUsage(event);

      if (hasTools && toolBuffer.length > 0) {
        // Parse buffered content for tool calls
        const fullContent = toolBuffer.join("");
        const { content, toolCalls } = parseToolCalls(fullContent);

        if (toolCalls.length > 0) {
          sendRoleChunk();
          // Emit remaining content (text outside tool_call blocks)
          if (content) {
            sendContentDelta(content);
          }
          // Emit tool calls
          emitToolCallChunks(res, toolCalls, requestId, created, model);
          // Finish with tool_calls reason
          const stopChunk = {
            id: requestId,
            object: "chat.completion.chunk",
            created,
            model,
            choices: [
              {
                index: 0,
                delta: {},
                finish_reason: "tool_calls",
              },
            ],
            usage,
          };
          res.write(`data: ${JSON.stringify(stopChunk)}\n\n`);
        } else {
          // No tool calls found — emit buffered content as a regular response
          sendRoleChunk();
          if (fullContent) {
            sendContentDelta(fullContent);
          }
          const stopChunk = {
            id: requestId,
            object: "chat.completion.chunk",
            created,
            model,
            choices: [
              {
                index: 0,
                delta: {},
                finish_reason: "stop",
              },
            ],
            usage,
          };
          res.write(`data: ${JSON.stringify(stopChunk)}\n\n`);
        }
      } else {
        // Normal finish (no tools or empty buffer)
        sendRoleChunk();
        const stopChunk = {
          id: requestId,
          object: "chat.completion.chunk",
          created,
          model,
          choices: [
            {
              index: 0,
              delta: {},
              finish_reason: "stop",
            },
          ],
          usage,
        };
        res.write(`data: ${JSON.stringify(stopChunk)}\n\n`);
      }
    }
  }

  clearInterval(progressTimer);
  clearTimeout(staleTimer);
  res.write("data: [DONE]\n\n");
  return { sessionId: capturedSessionId };
}

/**
 * Emit tool_call chunks in OpenAI streaming format.
 * Each tool call is sent as a full chunk (name + complete arguments).
 */
function emitToolCallChunks(
  res: ServerResponse,
  toolCalls: OpenAiToolCall[],
  requestId: string,
  created: number,
  model: string,
): void {
  for (let i = 0; i < toolCalls.length; i++) {
    const tc = toolCalls[i];
    const chunk = {
      id: requestId,
      object: "chat.completion.chunk",
      created,
      model,
      choices: [
        {
          index: 0,
          delta: {
            tool_calls: [
              {
                index: i,
                id: tc.id,
                type: "function" as const,
                function: {
                  name: tc.function.name,
                  arguments: tc.function.arguments,
                },
              },
            ],
          },
          finish_reason: null,
        },
      ],
    };
    res.write(`data: ${JSON.stringify(chunk)}\n\n`);
  }
}

function extractUsage(
  result: Record<string, unknown>,
): { prompt_tokens: number; completion_tokens: number; total_tokens: number } | undefined {
  const usage = result.usage as
    | {
        input_tokens?: number;
        output_tokens?: number;
      }
    | undefined;
  if (!usage) return undefined;
  const promptTokens = usage.input_tokens ?? 0;
  const completionTokens = usage.output_tokens ?? 0;
  return {
    prompt_tokens: promptTokens,
    completion_tokens: completionTokens,
    total_tokens: promptTokens + completionTokens,
  };
}
