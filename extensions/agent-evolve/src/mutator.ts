// ---------------------------------------------------------------------------
// Agent Evolve — Mutator: pattern analysis + LLM-driven mutation proposals
// ---------------------------------------------------------------------------

import { readFile } from "node:fs/promises";
import { join } from "node:path";
import type {
  AgentEvolveConfig,
  Observation,
  FailurePattern,
  MutationPlan,
  MutationEntry,
  EvolveErrorClass,
} from "./types.js";

// ---------------------------------------------------------------------------
// Phase A: Pure-TS pattern analysis (no LLM)
// ---------------------------------------------------------------------------

/** Group observations by error class and compute failure patterns. */
export function analyzePatterns(observations: Observation[]): FailurePattern[] {
  if (observations.length === 0) return [];

  const total = observations.length;
  const byClass = new Map<EvolveErrorClass, Observation[]>();

  for (const obs of observations) {
    if (obs.outcome === "success" && !obs.errorClass) continue;
    const cls = obs.errorClass ?? "unknown";
    const existing = byClass.get(cls) ?? [];
    existing.push(obs);
    byClass.set(cls, existing);
  }

  const patterns: FailurePattern[] = [];

  for (const [errorClass, group] of Array.from(byClass.entries())) {
    const count = group.length;
    const rate = count / total;

    // Severity: weighted by frequency and error class impact
    const classSeverity = ERROR_CLASS_WEIGHTS[errorClass] ?? 1;
    const severity = rate * classSeverity;

    // Collect example error messages (up to 3)
    const examples = group
      .filter((o) => o.errorMessage)
      .slice(0, 3)
      .map((o) => o.errorMessage!);

    patterns.push({ errorClass, count, rate, severity, examples });
  }

  // Sort by severity descending
  patterns.sort((a, b) => b.severity - a.severity);
  return patterns;
}

const ERROR_CLASS_WEIGHTS: Record<string, number> = {
  context_overflow: 3,
  scaffolding_only: 2.5,
  tool_failure: 2,
  incomplete_delivery: 2,
  timeout: 1.5,
  rate_limit: 1,
  format: 1.5,
  auth: 1,
  billing: 0.5,
  model_not_found: 0.5,
  unknown: 0.5,
};

/** Check if patterns are significant enough to warrant an evolution attempt. */
export function hasSignificantPatterns(patterns: FailurePattern[]): boolean {
  if (patterns.length === 0) return false;
  // At least one pattern with severity > 0.3 or rate > 20%
  return patterns.some((p) => p.severity > 0.3 || p.rate > 0.2);
}

// ---------------------------------------------------------------------------
// Phase B: LLM-driven mutation proposal
// ---------------------------------------------------------------------------

type RunEmbeddedPiAgentFn = (params: Record<string, unknown>) => Promise<unknown>;

let _runEmbeddedPiAgent: RunEmbeddedPiAgentFn | undefined;

async function loadRunEmbeddedPiAgent(): Promise<RunEmbeddedPiAgentFn> {
  if (_runEmbeddedPiAgent) return _runEmbeddedPiAgent;
  try {
    const mod = await import("../../../src/agents/pi-embedded-runner.js");
    // oxlint-disable-next-line typescript/no-explicit-any
    const fn = (mod as Record<string, unknown>).runEmbeddedPiAgent;
    if (typeof fn === "function") {
      _runEmbeddedPiAgent = fn as RunEmbeddedPiAgentFn;
      return _runEmbeddedPiAgent;
    }
  } catch {
    // ignore
  }
  throw new Error("[agent-evolve] runEmbeddedPiAgent not available");
}

/** Read workspace files that are eligible for mutation. */
async function readWorkspaceFiles(
  workspaceDir: string,
  mutableFiles: string[],
): Promise<Map<string, string>> {
  const files = new Map<string, string>();
  for (const name of mutableFiles) {
    const filePath = join(workspaceDir, name);
    try {
      const content = await readFile(filePath, "utf-8");
      files.set(name, content);
    } catch {
      // File doesn't exist yet — that's fine
    }
  }
  return files;
}

function buildMutationPrompt(
  agentId: string,
  patterns: FailurePattern[],
  workspaceFiles: Map<string, string>,
  config: AgentEvolveConfig,
): string {
  const entries = Array.from(workspaceFiles.entries());
  const filesSection = entries
    .map(([name, content]) => `### ${name}\n\`\`\`\n${content.slice(0, 8000)}\n\`\`\``)
    .join("\n\n");

  const patternsSection = patterns
    .map(
      (p) =>
        `- **${p.errorClass}**: ${p.count} occurrences (${(p.rate * 100).toFixed(0)}% rate, severity ${p.severity.toFixed(2)})\n  Examples: ${p.examples.join(" | ")}`,
    )
    .join("\n");

  return `You are an agent workspace optimizer. Analyze the failure patterns for agent "${agentId}" and propose targeted mutations to its workspace files.

## Failure Patterns
${patternsSection}

## Current Workspace Files
${filesSection}

## Mutation Constraints
- You may ONLY modify these files: ${config.mutableFiles.join(", ")}
${config.allowSkillMutation ? "- You may also CREATE new skill files under skills/" : "- Do NOT create or modify skill files"}
- Maximum ${config.maxMutationsPerCycle} mutations per cycle
- Each mutation must directly address an identified failure pattern
- Keep edits surgical — change only what's needed to fix the pattern
- For context_overflow: reduce prompt length, remove redundant instructions, compress verbose sections
- For scaffolding_only: add verification/testing instructions to the agent prompt
- For tool_failure: add error handling guidance, retry instructions, or alternative tool suggestions
- For incomplete_delivery: add delivery verification and retry instructions

## Response Format
Respond with ONLY valid JSON (no markdown fences):
{
  "analysis": "Brief summary of what you found and why these mutations will help",
  "mutations": [
    {
      "file": "AGENTS.md",
      "operation": "edit",
      "content": "The complete new content for this file",
      "reason": "Why this change addresses the identified pattern"
    }
  ],
  "patternsSummary": ["One-line summary of each pattern addressed"]
}`;
}

function stripCodeFences(s: string): string {
  const trimmed = s.trim();
  const m = trimmed.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
  if (m) return (m[1] ?? "").trim();
  return trimmed;
}

/** Propose mutations using LLM analysis of failure patterns. */
export async function proposeMutations(params: {
  agentId: string;
  patterns: FailurePattern[];
  workspaceDir: string;
  config: AgentEvolveConfig;
}): Promise<MutationPlan> {
  const { agentId, patterns, workspaceDir, config } = params;

  const workspaceFiles = await readWorkspaceFiles(workspaceDir, config.mutableFiles);

  // If workspace is empty, there's nothing to mutate
  if (workspaceFiles.size === 0) {
    throw new Error(`[agent-evolve] no workspace files found in ${workspaceDir}`);
  }

  const prompt = buildMutationPrompt(agentId, patterns, workspaceFiles, config);
  const runAgent = await loadRunEmbeddedPiAgent();

  const result = await runAgent({
    message: prompt,
    disableTools: true,
    maxTurns: 1,
    model: config.mutationModel,
    provider: config.mutationProvider,
  });

  // Extract text from result
  const resultObj = result as Record<string, unknown>;
  const payloads = resultObj.payloads as Array<{ text?: string }> | undefined;
  const rawText =
    payloads
      ?.map((p) => p.text ?? "")
      .join("\n")
      .trim() ?? (typeof resultObj.text === "string" ? resultObj.text : "");

  const cleaned = stripCodeFences(rawText);

  let parsed: { analysis: string; mutations: MutationEntry[]; patternsSummary: string[] };
  try {
    parsed = JSON.parse(cleaned);
  } catch {
    throw new Error(
      `[agent-evolve] failed to parse LLM mutation response: ${cleaned.slice(0, 200)}`,
    );
  }

  // Validate mutations
  const validMutations = parsed.mutations
    .filter((m) => {
      if (!config.mutableFiles.includes(m.file) && !m.file.startsWith("skills/")) return false;
      if (m.file.startsWith("skills/") && !config.allowSkillMutation) return false;
      if (!["edit", "create", "delete"].includes(m.operation)) return false;
      return true;
    })
    .slice(0, config.maxMutationsPerCycle);

  const now = Date.now();
  const timestamps = patterns.flatMap((p) => p.examples.map(() => now));
  const oldest = Math.min(...timestamps, now - 86_400_000);

  return {
    agentId,
    timestamp: now,
    analysis: parsed.analysis ?? "No analysis provided",
    mutations: validMutations,
    observationWindow: { from: oldest, to: now },
    patternsSummary: parsed.patternsSummary ?? [],
  };
}
