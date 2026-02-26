import type { ServerResponse } from "node:http";
import { createInterface } from "node:readline";
import type { Readable } from "node:stream";

/**
 * Convert Claude CLI stream-json NDJSON output to OpenAI SSE format.
 *
 * Claude stream-json emits one JSON object per line:
 * - `{ type: "assistant", message: { type: "text", text: "..." } }` — content chunks
 * - `{ type: "result", ... }` — final result
 *
 * We translate these to OpenAI's `data: {...}\n\n` SSE format.
 */
export async function pipeStreamToSSE(
  cliStream: Readable,
  res: ServerResponse,
  model: string,
  requestId: string,
): Promise<void> {
  const created = Math.floor(Date.now() / 1000);
  let sentRole = false;

  const rl = createInterface({ input: cliStream, crlfDelay: Infinity });

  // Send the initial role chunk
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
      // Skip malformed lines
      continue;
    }

    if (event.type === "assistant") {
      // message.content is an array: [{type:"text", text:"..."}, {type:"thinking", ...}]
      const message = event.message as
        | {
            content?: Array<{ type?: string; text?: string; thinking?: string }>;
          }
        | undefined;
      const contentParts = message?.content ?? [];
      for (const part of contentParts) {
        if (part.type === "text" && part.text) {
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
        // Skip thinking blocks — not part of the visible response
      }
    } else if (event.type === "result") {
      // Final chunk with finish_reason
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
        usage: extractUsage(event),
      };
      res.write(`data: ${JSON.stringify(stopChunk)}\n\n`);
    }
  }

  // Send [DONE] sentinel
  res.write("data: [DONE]\n\n");
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
