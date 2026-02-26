export type ClaudeCodeProxyConfig = {
  port: number;
  claudeBinaryPath: string;
  timeoutMs: number;
};

export const DEFAULT_PORT = 3456;
export const DEFAULT_CLAUDE_BINARY = "claude";
export const DEFAULT_TIMEOUT_MS = 120_000;
export const DEFAULT_CONTEXT_WINDOW = 200_000;
export const DEFAULT_MAX_TOKENS = 8192;

export const PROVIDER_ID = "claude-code-proxy";

// Security limits
export const MAX_BODY_BYTES = 1_048_576; // 1 MB
export const MAX_CONCURRENT = 10;

// Maps provider model IDs to claude CLI --model flag values
// CLI accepts aliases (sonnet, opus, haiku) or short names (claude-sonnet-4-6)
export const MODEL_MAP: Record<string, string> = {
  "claude-sonnet-4-5": "claude-sonnet-4-5",
  "claude-sonnet-4-6": "claude-sonnet-4-6",
  "claude-opus-4-5": "claude-opus-4-5",
  "claude-opus-4-6": "claude-opus-4-6",
  "claude-haiku-4-5": "claude-haiku-4-5",
};

export const MODEL_IDS = Object.keys(MODEL_MAP);

export type OpenAiMessage = {
  role: "system" | "user" | "assistant";
  content: string;
};

export type OpenAiChatRequest = {
  model: string;
  messages: OpenAiMessage[];
  stream?: boolean;
  max_tokens?: number;
  temperature?: number;
};

// Claude CLI JSON output format (non-streaming)
export type ClaudeCliJsonResult = {
  type: "result";
  subtype: "success" | "error_max_turns";
  is_error: boolean;
  result: string;
  session_id: string;
  cost_usd: number;
  duration_ms: number;
  duration_api_ms: number;
  num_turns: number;
  usage: {
    input_tokens: number;
    output_tokens: number;
    cache_read_input_tokens: number;
    cache_creation_input_tokens: number;
  };
};

// Claude CLI stream-json NDJSON line types
export type ClaudeStreamMessage = {
  type: "assistant";
  message: {
    type: "text";
    text: string;
  };
  session_id: string;
};

export type ClaudeStreamResult = ClaudeCliJsonResult;
