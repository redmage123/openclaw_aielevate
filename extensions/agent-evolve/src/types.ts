// ---------------------------------------------------------------------------
// Agent Evolve — Shared type definitions
// ---------------------------------------------------------------------------

/** Error classes bridged from existing pi-embedded-helpers/errors.ts plus
 *  domain-specific patterns detected by the classifier. */
export type EvolveErrorClass =
  | "rate_limit"
  | "timeout"
  | "context_overflow"
  | "auth"
  | "billing"
  | "model_not_found"
  | "format"
  | "tool_failure"
  | "scaffolding_only"
  | "incomplete_delivery"
  | "unknown";

/** A single tool call captured during an agent session. */
export type ObservedToolCall = {
  toolName: string;
  durationMs: number;
  success: boolean;
  error?: string;
  isMutating: boolean;
  fingerprint?: string;
};

/** One observation record per agent session, flushed on session_end. */
export type Observation = {
  agentId: string;
  sessionId: string;
  timestamp: number;
  outcome: "success" | "partial" | "failure";
  errorClass?: EvolveErrorClass;
  errorMessage?: string;
  toolCalls: ObservedToolCall[];
  durationMs?: number;
  promptTokens?: number;
  completionTokens?: number;
  messageCount?: number;
};

/** In-memory buffer accumulated across hooks within a single session. */
export type SessionBuffer = {
  agentId: string;
  toolCalls: ObservedToolCall[];
  promptTokens: number;
  completionTokens: number;
  errorMessages: string[];
  outcome: "success" | "partial" | "failure";
  durationMs?: number;
};

// ---------------------------------------------------------------------------
// Mutation types
// ---------------------------------------------------------------------------

export type MutationOperation = "edit" | "create" | "delete";

export type MutationEntry = {
  file: string;
  operation: MutationOperation;
  content?: string;
  reason: string;
};

export type MutationPlan = {
  agentId: string;
  timestamp: number;
  analysis: string;
  mutations: MutationEntry[];
  observationWindow: { from: number; to: number };
  patternsSummary: string[];
};

/** A failure pattern detected by the pure-TS analyzer. */
export type FailurePattern = {
  errorClass: EvolveErrorClass;
  count: number;
  rate: number;
  severity: number;
  examples: string[];
};

// ---------------------------------------------------------------------------
// Gate types
// ---------------------------------------------------------------------------

export type TestResult = {
  prompt: string;
  prePassed: boolean;
  postPassed: boolean;
  preScore: number;
  postScore: number;
};

export type GateResult = {
  passed: boolean;
  preScore: number;
  postScore: number;
  regressions: string[];
  improvements: string[];
  testResults: TestResult[];
};

// ---------------------------------------------------------------------------
// Evolution record (persisted to evolution.jsonl)
// ---------------------------------------------------------------------------

export type EvolutionRecord = {
  id: string;
  agentId: string;
  timestamp: number;
  plan: MutationPlan;
  gate: GateResult;
  status: "applied" | "rolled_back" | "rejected";
  gitTag?: string;
};

// ---------------------------------------------------------------------------
// Plugin config (typed mirror of configSchema)
// ---------------------------------------------------------------------------

export type AgentEvolveConfig = {
  enabled: boolean;
  minObservationsBeforeEvolve: number;
  maxMutationsPerCycle: number;
  fitnessThreshold: number;
  mutableFiles: string[];
  allowSkillMutation: boolean;
  testPrompts: Record<string, string[]>;
  mutationModel?: string;
  mutationProvider?: string;
  observationRetentionDays: number;
  maxObservationFileBytes: number;
  gateTimeoutMs: number;
  autoEvolveSchedule?: string;
};

export const DEFAULT_CONFIG: AgentEvolveConfig = {
  enabled: true,
  minObservationsBeforeEvolve: 5,
  maxMutationsPerCycle: 3,
  fitnessThreshold: 0.7,
  mutableFiles: ["AGENTS.md", "SOUL.md", "TOOLS.md"],
  allowSkillMutation: true,
  testPrompts: {},
  observationRetentionDays: 30,
  maxObservationFileBytes: 10_485_760,
  gateTimeoutMs: 120_000,
};

export function parseConfig(raw: Record<string, unknown> | undefined): AgentEvolveConfig {
  const r = raw ?? {};
  return {
    ...DEFAULT_CONFIG,
    ...r,
    testPrompts: (r.testPrompts as Record<string, string[]>) ?? {},
    mutableFiles: (r.mutableFiles as string[]) ?? DEFAULT_CONFIG.mutableFiles,
  };
}
