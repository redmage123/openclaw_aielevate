import type { OpenAiMessage } from "./types.js";

export type TranslatedPrompt = {
  prompt: string;
  systemPrompt: string | undefined;
};

/**
 * Translate OpenAI-style messages into a prompt string and optional system prompt
 * for the Claude CLI's `-p` (pipe) mode.
 *
 * - System messages are concatenated into a single `--append-system-prompt` value.
 * - User/assistant messages are flattened into a multi-turn prompt string.
 */
export function translateMessages(messages: OpenAiMessage[]): TranslatedPrompt {
  const systemParts: string[] = [];
  const conversationParts: string[] = [];

  for (const msg of messages) {
    if (msg.role === "system") {
      systemParts.push(msg.content);
    } else if (msg.role === "user") {
      conversationParts.push(`Human: ${msg.content}`);
    } else if (msg.role === "assistant") {
      conversationParts.push(`Assistant: ${msg.content}`);
    }
  }

  const systemPrompt = systemParts.length > 0 ? systemParts.join("\n\n") : undefined;
  const prompt = conversationParts.length > 0 ? conversationParts.join("\n\n") : "";

  return { prompt, systemPrompt };
}
