import type { ServerResponse } from "node:http";
import { createInterface } from "node:readline";
import type { Readable } from "node:stream";
import { parseToolCalls } from "./message-translator.js";
import type { OpenAiToolCall } from "./types.js";
import { STREAM_STALE_TIMEOUT_MS } from "./types.js";

/**
 * Convert Claude CLI stream-json NDJSON output to OpenAI SSE format.
 *
 * Claude stream-json emits one JSON object per line:
 * - `{ type: "assistant", message: { content: [...] } }` — content chunks
 * - `{ type: "result", ... }` — final result
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

  for await (const line of rl) {
    if (!line.trim()) continue;

    let event: Record<string, unknown>;
    try {
      event = JSON.parse(line) as Record<string, unknown>;
    } catch {
      continue;
    }

    if (event.type === "assistant") {
      const message = event.message as
        | {
            content?: Array<{ type?: string; text?: string; thinking?: string }>;
          }
        | undefined;
      const contentParts = message?.content ?? [];
      for (const part of contentParts) {
        if (part.type === "text" && part.text) {
          // Always stream text immediately — the consuming SDK handles
          // tool calls via delta.tool_calls, not by parsing text blocks.
          if (hasTools) {
            toolBuffer.push(part.text);
          }
          sendRoleChunk();
          const chunk = {
            id: requestId,
            object: "chat.completion.chunk",
            created,
            model,
            choices: [
              {
                index: 0,
                delta: { content: part.text },
                finish_reason: null,
              },
            ],
          };
          res.write(`data: ${JSON.stringify(chunk)}\n\n`);
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
            const contentChunk = {
              id: requestId,
              object: "chat.completion.chunk",
              created,
              model,
              choices: [
                {
                  index: 0,
                  delta: { content },
                  finish_reason: null,
                },
              ],
            };
            res.write(`data: ${JSON.stringify(contentChunk)}\n\n`);
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
            const contentChunk = {
              id: requestId,
              object: "chat.completion.chunk",
              created,
              model,
              choices: [
                {
                  index: 0,
                  delta: { content: fullContent },
                  finish_reason: null,
                },
              ],
            };
            res.write(`data: ${JSON.stringify(contentChunk)}\n\n`);
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
