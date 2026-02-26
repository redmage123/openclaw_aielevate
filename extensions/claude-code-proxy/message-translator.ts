import { randomUUID } from "node:crypto";
import type { OpenAiMessage, OpenAiTool, OpenAiToolCall } from "./types.js";

export type TranslatedPrompt = {
  prompt: string;
  systemPrompt: string | undefined;
};

const TOOL_CALL_OPEN = "<tool_call>";
const TOOL_CALL_CLOSE = "</tool_call>";

/**
 * Extract text from message content which may be a string, null, or
 * an OpenAI content-parts array [{type:"text", text:"..."}, ...].
 */
function getContentText(content: unknown): string {
  if (typeof content === "string") return content;
  if (content == null) return "";
  if (Array.isArray(content)) {
    return (content as Array<{ type?: string; text?: string }>)
      .filter((part) => part?.type === "text" && typeof part?.text === "string")
      .map((part) => part.text)
      .join("");
  }
  return String(content);
}

/**
 * Translate OpenAI-style messages (including tool use) into a prompt string
 * and optional system prompt for the Claude CLI's `-p` (pipe) mode.
 *
 * - System messages are concatenated into a single `--append-system-prompt` value.
 * - User/assistant/tool messages are flattened into a multi-turn prompt string.
 * - Tool definitions are added to the system prompt with formatting instructions.
 */
export function translateMessages(
  messages: OpenAiMessage[],
  tools?: OpenAiTool[],
  toolChoice?: "none" | "auto" | { type: "function"; function: { name: string } },
): TranslatedPrompt {
  const systemParts: string[] = [];
  const conversationParts: string[] = [];

  for (const msg of messages) {
    if (msg.role === "system") {
      systemParts.push(getContentText(msg.content));
    } else if (msg.role === "user") {
      conversationParts.push(`Human: ${getContentText(msg.content)}`);
    } else if (msg.role === "assistant") {
      if (msg.tool_calls && msg.tool_calls.length > 0) {
        // Assistant message with tool calls — reconstruct as text with tool_call blocks
        const parts: string[] = [];
        const text = getContentText(msg.content);
        if (text) parts.push(text);
        for (const tc of msg.tool_calls) {
          let args: Record<string, unknown> = {};
          try {
            args = JSON.parse(tc.function.arguments) as Record<string, unknown>;
          } catch {
            // Keep empty object if arguments aren't valid JSON
          }
          parts.push(
            `${TOOL_CALL_OPEN}\n${JSON.stringify({ name: tc.function.name, arguments: args })}\n${TOOL_CALL_CLOSE}`,
          );
        }
        conversationParts.push(`Assistant: ${parts.join("\n")}`);
      } else {
        conversationParts.push(`Assistant: ${getContentText(msg.content)}`);
      }
    } else if (msg.role === "tool") {
      // Tool result — present as a human message so the model sees the result
      const callId = msg.tool_call_id ?? "unknown";
      const name = msg.name ?? "unknown";
      conversationParts.push(
        `Human: [Tool result for ${name} (call_id: ${callId})]:\n${getContentText(msg.content)}`,
      );
    }
  }

  // Add tool definitions to system prompt when tools are provided and not disabled
  if (tools && tools.length > 0 && toolChoice !== "none") {
    const toolSection = buildToolSystemPrompt(tools, toolChoice);
    systemParts.push(toolSection);
  }

  const systemPrompt = systemParts.length > 0 ? systemParts.join("\n\n") : undefined;
  const prompt = conversationParts.length > 0 ? conversationParts.join("\n\n") : "";

  return { prompt, systemPrompt };
}

function buildToolSystemPrompt(
  tools: OpenAiTool[],
  toolChoice?: "auto" | { type: "function"; function: { name: string } },
): string {
  const lines: string[] = [];
  lines.push("# Tool Use Protocol");
  lines.push("");
  lines.push(
    "You are a helpful assistant with access to the following tools. " +
      "When the user's request can be fulfilled by calling a tool, you MUST call it " +
      "using the exact XML format shown below. This is your ONLY mechanism for tool calling.",
  );
  lines.push("");
  lines.push("## Format");
  lines.push("");
  lines.push("To call a tool, output EXACTLY this format (no markdown fences, no extra text):");
  lines.push("");
  lines.push(`${TOOL_CALL_OPEN}`);
  lines.push('{"name": "tool_name", "arguments": {"param1": "value1"}}');
  lines.push(`${TOOL_CALL_CLOSE}`);
  lines.push("");
  lines.push("Rules:");
  lines.push("- Output ONLY the <tool_call> block(s) when calling tools — no surrounding text.");
  lines.push("- You may output multiple <tool_call> blocks to call multiple tools.");
  lines.push(
    '- The JSON inside must have exactly two keys: "name" (string) and "arguments" (object).',
  );
  lines.push(
    "- If the user's request does NOT require a tool call, respond with plain text as normal.",
  );
  lines.push("");

  // Forced function call
  if (toolChoice && typeof toolChoice === "object" && toolChoice.function) {
    lines.push(
      `MANDATORY: You MUST call the "${toolChoice.function.name}" tool. Do not respond with plain text.`,
    );
    lines.push("");
  }

  lines.push("## Available Tools");
  lines.push("");

  for (const tool of tools) {
    lines.push(`### ${tool.function.name}`);
    if (tool.function.description) {
      lines.push(tool.function.description);
    }
    if (tool.function.parameters) {
      lines.push(`Parameters: ${JSON.stringify(tool.function.parameters)}`);
    }
    lines.push("");
  }

  return lines.join("\n");
}

// ---------------------------------------------------------------------------
// Tool call parsing (response → OpenAI format)
// ---------------------------------------------------------------------------

const TOOL_CALL_REGEX = new RegExp(
  `${escapeRegExp(TOOL_CALL_OPEN)}\\s*([\\s\\S]*?)\\s*${escapeRegExp(TOOL_CALL_CLOSE)}`,
  "g",
);

/**
 * Parse Claude's response text for <tool_call> blocks.
 * Returns content (text outside blocks) and any extracted tool calls.
 */
export function parseToolCalls(text: string): {
  content: string;
  toolCalls: OpenAiToolCall[];
} {
  const toolCalls: OpenAiToolCall[] = [];

  // Reset regex state
  TOOL_CALL_REGEX.lastIndex = 0;

  let match: RegExpExecArray | null;
  while ((match = TOOL_CALL_REGEX.exec(text)) !== null) {
    let jsonStr = match[1].trim();
    // Strip optional markdown code fences
    jsonStr = jsonStr.replace(/^```(?:json)?\s*/, "").replace(/\s*```$/, "");
    try {
      const parsed = JSON.parse(jsonStr) as { name: string; arguments?: Record<string, unknown> };
      toolCalls.push({
        id: `call_${randomUUID().replace(/-/g, "").slice(0, 24)}`,
        type: "function",
        function: {
          name: parsed.name,
          arguments: JSON.stringify(parsed.arguments ?? {}),
        },
      });
    } catch {
      // Skip malformed tool call blocks
    }
  }

  // Remove tool_call blocks from content
  let content = text;
  if (toolCalls.length > 0) {
    TOOL_CALL_REGEX.lastIndex = 0;
    content = text.replace(TOOL_CALL_REGEX, "").trim();
  }

  return { content, toolCalls };
}

function escapeRegExp(str: string): string {
  return str.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Extract only new user/tool messages for session resumption.
 * Skips system and assistant messages (already in the CLI session).
 */
export function extractResumePrompt(messages: OpenAiMessage[], afterIndex: number): string {
  const parts: string[] = [];
  for (let i = afterIndex; i < messages.length; i++) {
    const msg = messages[i];
    if (msg.role === "user") {
      parts.push(getContentText(msg.content));
    } else if (msg.role === "tool") {
      const callId = msg.tool_call_id ?? "unknown";
      const name = msg.name ?? "unknown";
      parts.push(`[Tool result for ${name} (call_id: ${callId})]:\n${getContentText(msg.content)}`);
    }
  }
  return parts.join("\n\n");
}
