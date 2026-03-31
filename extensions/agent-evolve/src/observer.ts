// ---------------------------------------------------------------------------
// Agent Evolve — Observer: captures structured observations from agent runs
// ---------------------------------------------------------------------------

import type { OpenClawPluginApi } from "../../../src/plugins/types.js";
import type {
  PluginHookAfterToolCallEvent,
  PluginHookToolContext,
  PluginHookLlmOutputEvent,
  PluginHookAgentContext,
  PluginHookAgentEndEvent,
  PluginHookSessionEndEvent,
  PluginHookSessionContext,
} from "../../../src/plugins/types.js";
import { classifyError, detectScaffoldingOnly, detectIncompleteDelivery } from "./classifier.js";
import { appendJsonl, rotateJsonl } from "./jsonl.js";
import { resolveObservationsPath } from "./paths.js";
import type { AgentEvolveConfig, Observation, SessionBuffer, ObservedToolCall } from "./types.js";

// In-memory buffers keyed by sessionId
const sessionBuffers = new Map<string, SessionBuffer>();

function getOrCreateBuffer(sessionId: string, agentId: string): SessionBuffer {
  let buf = sessionBuffers.get(sessionId);
  if (!buf) {
    buf = {
      agentId,
      toolCalls: [],
      promptTokens: 0,
      completionTokens: 0,
      errorMessages: [],
      outcome: "success",
    };
    sessionBuffers.set(sessionId, buf);
  }
  return buf;
}

/** Register all observer hooks on the plugin API. */
export function registerObserverHooks(api: OpenClawPluginApi, config: AgentEvolveConfig): void {
  const log = api.logger;

  // --- after_tool_call: capture each tool invocation ---
  api.on("after_tool_call", (event: PluginHookAfterToolCallEvent, ctx: PluginHookToolContext) => {
    const sessionId = ctx.sessionKey ?? "unknown";
    const agentId = ctx.agentId ?? "unknown";
    const buf = getOrCreateBuffer(sessionId, agentId);

    // Simple name-based heuristic for mutation detection
    const isMutating = /write|edit|exec|bash|send|message|delete|apply_patch|process/i.test(
      event.toolName,
    );
    const fingerprint = `${event.toolName}|${JSON.stringify(event.params).slice(0, 100)}`;

    const observed: ObservedToolCall = {
      toolName: event.toolName,
      durationMs: event.durationMs ?? 0,
      success: !event.error,
      error: event.error,
      isMutating,
      fingerprint,
    };

    buf.toolCalls.push(observed);

    if (event.error) {
      buf.errorMessages.push(event.error);
      if (buf.outcome === "success") buf.outcome = "partial";
    }
  });

  // --- llm_output: capture token usage and detect error patterns ---
  api.on("llm_output", (event: PluginHookLlmOutputEvent, ctx: PluginHookAgentContext) => {
    const sessionId = ctx.sessionKey ?? event.sessionId ?? "unknown";
    const agentId = ctx.agentId ?? "unknown";
    const buf = getOrCreateBuffer(sessionId, agentId);

    if (event.usage) {
      buf.promptTokens += event.usage.input ?? 0;
      buf.completionTokens += event.usage.output ?? 0;
    }

    // Scan assistant texts for error patterns
    for (const text of event.assistantTexts ?? []) {
      if (/error|failed|exception|crash/i.test(text) && text.length < 500) {
        buf.errorMessages.push(text.slice(0, 200));
      }
    }
  });

  // --- agent_end: capture overall run outcome ---
  api.on("agent_end", (event: PluginHookAgentEndEvent, ctx: PluginHookAgentContext) => {
    const sessionId = ctx.sessionKey ?? "unknown";
    const agentId = ctx.agentId ?? "unknown";
    const buf = getOrCreateBuffer(sessionId, agentId);

    buf.durationMs = event.durationMs;
    if (!event.success) {
      buf.outcome = "failure";
      if (event.error) buf.errorMessages.push(event.error);
    }
  });

  // --- session_end: flush buffered data to JSONL ---
  api.on("session_end", async (event: PluginHookSessionEndEvent, ctx: PluginHookSessionContext) => {
    const sessionId = ctx.sessionId ?? event.sessionId;
    const buf = sessionBuffers.get(sessionId);
    if (!buf) return;

    // Determine error class from collected errors
    let errorClass = undefined;
    if (buf.errorMessages.length > 0) {
      errorClass = await classifyError(buf.errorMessages[0]);
    }

    // Detect domain-specific failure patterns
    if (!errorClass || errorClass === "unknown") {
      if (detectScaffoldingOnly(buf.toolCalls)) {
        errorClass = "scaffolding_only";
        if (buf.outcome === "success") buf.outcome = "partial";
      } else if (detectIncompleteDelivery(buf.toolCalls)) {
        errorClass = "incomplete_delivery";
        if (buf.outcome === "success") buf.outcome = "partial";
      }
    }

    const observation: Observation = {
      agentId: buf.agentId,
      sessionId,
      timestamp: Date.now(),
      outcome: buf.outcome,
      errorClass,
      errorMessage: buf.errorMessages[0],
      toolCalls: buf.toolCalls,
      durationMs: buf.durationMs ?? event.durationMs,
      promptTokens: buf.promptTokens || undefined,
      completionTokens: buf.completionTokens || undefined,
      messageCount: event.messageCount,
    };

    const obsPath = resolveObservationsPath(buf.agentId);

    try {
      await appendJsonl(obsPath, observation);
      await rotateJsonl(obsPath, config.maxObservationFileBytes);
      log.debug?.(`[agent-evolve] observation recorded for ${buf.agentId}: ${observation.outcome}`);
    } catch (err) {
      log.warn?.(`[agent-evolve] failed to write observation: ${err}`);
    }

    // Clear buffer
    sessionBuffers.delete(sessionId);
  });

  log.info?.("[agent-evolve] observer hooks registered");
}

/** Expose buffer map for testing. */
export function getSessionBuffers(): Map<string, SessionBuffer> {
  return sessionBuffers;
}
