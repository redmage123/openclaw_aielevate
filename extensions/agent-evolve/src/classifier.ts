// ---------------------------------------------------------------------------
// Agent Evolve — Error classification bridge
// Wraps existing pi-embedded-helpers/errors.ts and adds domain-specific patterns
// ---------------------------------------------------------------------------

import type { EvolveErrorClass, ObservedToolCall } from "./types.js";

// Dynamically imported error classification functions from core
type ClassifyFn = (raw: unknown) => string | undefined;
type BoolCheckFn = (msg: string) => boolean;

let _classifyFailoverReason: ClassifyFn | undefined;
let _isContextOverflowError: BoolCheckFn | undefined;
let _isTimeoutErrorMessage: BoolCheckFn | undefined;
let _isRateLimitErrorMessage: BoolCheckFn | undefined;

async function loadErrorHelpers(): Promise<void> {
  if (_classifyFailoverReason) return;
  try {
    const mod = await import("../../../src/agents/pi-embedded-helpers/errors.js");
    _classifyFailoverReason = mod.classifyFailoverReason;
    _isContextOverflowError = mod.isContextOverflowError ?? mod.isLikelyContextOverflowError;
    _isTimeoutErrorMessage = mod.isTimeoutErrorMessage;
    _isRateLimitErrorMessage = mod.isRateLimitErrorMessage;
  } catch {
    // Core module not available — fall back to pattern matching
  }
}

/** Classify an error message into an EvolveErrorClass using core helpers
 *  when available, with regex fallback. */
export async function classifyError(errorMessage: string): Promise<EvolveErrorClass> {
  await loadErrorHelpers();

  if (!errorMessage) return "unknown";
  const msg = errorMessage.toLowerCase();

  // Try core classification first
  if (_classifyFailoverReason) {
    const reason = _classifyFailoverReason(errorMessage);
    if (reason === "rate_limit") return "rate_limit";
    if (reason === "timeout") return "timeout";
    if (reason === "billing") return "billing";
    if (reason === "auth") return "auth";
    if (reason === "model_not_found") return "model_not_found";
    if (reason === "format") return "format";
  }

  // Context overflow
  if (_isContextOverflowError?.(errorMessage)) return "context_overflow";
  if (/context.*overflow|too many tokens|context.*length.*exceed/i.test(msg))
    return "context_overflow";

  // Rate limit
  if (_isRateLimitErrorMessage?.(errorMessage)) return "rate_limit";
  if (/rate.?limit|429|quota.*exceed|too many requests/i.test(msg)) return "rate_limit";

  // Timeout
  if (_isTimeoutErrorMessage?.(errorMessage)) return "timeout";
  if (/timeout|timed?.?out|deadline.*exceed|abort/i.test(msg)) return "timeout";

  // Auth
  if (/invalid.*key|auth.*fail|unauthorized|403|401/i.test(msg)) return "auth";

  // Billing
  if (/402|insufficient.*credit|billing/i.test(msg)) return "billing";

  // Tool failure
  if (/tool.*fail|command.*fail|exec.*error/i.test(msg)) return "tool_failure";

  return "unknown";
}

// Verification tool names that indicate real testing (not just scaffolding)
const VERIFY_TOOLS = new Set([
  "exec",
  "bash",
  "test",
  "run",
  "verify",
  "check",
  "assert",
  "curl",
  "fetch",
]);

/** Detect the scaffolding-only pattern: agent only writes files but never
 *  runs, tests, or verifies anything. This was the CareHaven failure mode. */
export function detectScaffoldingOnly(toolCalls: ObservedToolCall[]): boolean {
  if (toolCalls.length === 0) return false;

  const hasWrites = toolCalls.some(
    (tc) => tc.isMutating && /write|edit|create|apply_patch/i.test(tc.toolName),
  );
  const hasVerification = toolCalls.some((tc) => {
    const baseName = tc.toolName.split(".").pop()?.toLowerCase() ?? tc.toolName.toLowerCase();
    return VERIFY_TOOLS.has(baseName);
  });

  return hasWrites && !hasVerification;
}

/** Detect incomplete delivery pattern: agent attempted to send messages
 *  but they failed. */
export function detectIncompleteDelivery(toolCalls: ObservedToolCall[]): boolean {
  return toolCalls.some((tc) => /message|send|deliver|email/i.test(tc.toolName) && !tc.success);
}
