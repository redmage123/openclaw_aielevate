// ---------------------------------------------------------------------------
// Agent Evolve — Gate: fitness validation before accepting mutations
// ---------------------------------------------------------------------------

import { readFile, writeFile, unlink, mkdir } from "node:fs/promises";
import { join, dirname } from "node:path";
import { resolveBackupDir } from "./paths.js";
import type {
  AgentEvolveConfig,
  MutationPlan,
  GateResult,
  TestResult,
  MutationEntry,
} from "./types.js";

type RunEmbeddedPiAgentFn = (params: Record<string, unknown>) => Promise<unknown>;

let _runEmbeddedPiAgent: RunEmbeddedPiAgentFn | undefined;

async function loadRunEmbeddedPiAgent(): Promise<RunEmbeddedPiAgentFn> {
  if (_runEmbeddedPiAgent) return _runEmbeddedPiAgent;
  try {
    const mod = await import("../../../src/agents/pi-embedded-runner.js");
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

// ---------------------------------------------------------------------------
// Backup / restore workspace files
// ---------------------------------------------------------------------------

/** Backup mutable files before applying mutations. Returns map of file -> content. */
export async function backupWorkspace(
  workspaceDir: string,
  plan: MutationPlan,
): Promise<Map<string, string>> {
  const backups = new Map<string, string>();
  const backupDir = resolveBackupDir(plan.agentId);
  await mkdir(backupDir, { recursive: true });

  for (const mutation of plan.mutations) {
    const filePath = join(workspaceDir, mutation.file);
    try {
      const content = await readFile(filePath, "utf-8");
      backups.set(mutation.file, content);
      const backupPath = join(backupDir, mutation.file.replace(/\//g, "__"));
      await writeFile(backupPath, content, "utf-8");
    } catch {
      backups.set(mutation.file, "");
    }
  }

  return backups;
}

/** Apply mutations to workspace files. */
export async function applyMutations(
  workspaceDir: string,
  mutations: MutationEntry[],
): Promise<void> {
  for (const mutation of mutations) {
    const filePath = join(workspaceDir, mutation.file);

    if (mutation.operation === "delete") {
      try {
        await unlink(filePath);
      } catch {
        // Already gone
      }
      continue;
    }

    // edit or create
    await mkdir(dirname(filePath), { recursive: true });
    await writeFile(filePath, mutation.content ?? "", "utf-8");
  }
}

/** Rollback workspace files from backup. */
export async function rollbackMutations(
  workspaceDir: string,
  backups: Map<string, string>,
): Promise<void> {
  for (const entry of Array.from(backups.entries())) {
    const [file, content] = entry;
    const filePath = join(workspaceDir, file);
    if (content === "") {
      try {
        await unlink(filePath);
      } catch {
        // Already gone
      }
    } else {
      await writeFile(filePath, content, "utf-8");
    }
  }
}

// ---------------------------------------------------------------------------
// Scoring — LLM-judged response quality
// ---------------------------------------------------------------------------

async function scoreResponse(
  testPrompt: string,
  response: string,
  config: AgentEvolveConfig,
): Promise<number> {
  const runAgent = await loadRunEmbeddedPiAgent();

  const judgePrompt = `You are a quality judge. Score the following agent response on a scale of 0.0 to 1.0.

Criteria:
- Completeness: Does it fully address the prompt?
- Correctness: Is the response accurate and error-free?
- Actionability: Does it provide concrete, useful output?
- No errors: Are there any error messages, failures, or apologetic language?

## Test Prompt
${testPrompt}

## Agent Response
${response.slice(0, 4000)}

Respond with ONLY a JSON object: {"score": 0.85, "reasoning": "brief explanation"}`;

  const result = await runAgent({
    message: judgePrompt,
    disableTools: true,
    maxTurns: 1,
    model: config.mutationModel,
    provider: config.mutationProvider,
  });

  const resultObj = result as Record<string, unknown>;
  const payloads = resultObj.payloads as Array<{ text?: string }> | undefined;
  const rawText =
    payloads
      ?.map((p) => p.text ?? "")
      .join("\n")
      .trim() ?? "";

  try {
    const cleaned = rawText.replace(/^```(?:json)?\s*|\s*```$/gi, "").trim();
    const parsed = JSON.parse(cleaned) as { score: number };
    return Math.max(0, Math.min(1, parsed.score));
  } catch {
    return 0.5;
  }
}

async function runTestPrompt(testPrompt: string, config: AgentEvolveConfig): Promise<string> {
  const runAgent = await loadRunEmbeddedPiAgent();

  const result = await runAgent({
    message: testPrompt,
    disableTools: true,
    maxTurns: 1,
    model: config.mutationModel,
    provider: config.mutationProvider,
  });

  const resultObj = result as Record<string, unknown>;
  const payloads = resultObj.payloads as Array<{ text?: string }> | undefined;
  return (
    payloads
      ?.map((p) => p.text ?? "")
      .join("\n")
      .trim() ?? ""
  );
}

// ---------------------------------------------------------------------------
// Gate execution
// ---------------------------------------------------------------------------

/** Run the fitness gate: compare pre and post mutation quality. */
export async function runGate(params: {
  agentId: string;
  plan: MutationPlan;
  workspaceDir: string;
  config: AgentEvolveConfig;
}): Promise<GateResult> {
  const { agentId, plan, workspaceDir, config } = params;
  const testPrompts = config.testPrompts[agentId] ?? [];

  // No test prompts configured — analysis-only mode
  if (testPrompts.length === 0) {
    return {
      passed: true,
      preScore: 0,
      postScore: 0,
      regressions: [],
      improvements: ["analysis-only mode: no test prompts configured"],
      testResults: [],
    };
  }

  // Backup and run pre-mutation tests
  const backups = await backupWorkspace(workspaceDir, plan);
  const preResults: Array<{ prompt: string; response: string; score: number }> = [];

  for (const prompt of testPrompts) {
    const response = await runTestPrompt(prompt, config);
    const score = await scoreResponse(prompt, response, config);
    preResults.push({ prompt, response, score });
  }

  const preScore = preResults.reduce((sum, r) => sum + r.score, 0) / preResults.length;

  // Apply mutations
  await applyMutations(workspaceDir, plan.mutations);

  // Run post-mutation tests
  const postResults: Array<{ prompt: string; response: string; score: number }> = [];

  for (const prompt of testPrompts) {
    const response = await runTestPrompt(prompt, config);
    const score = await scoreResponse(prompt, response, config);
    postResults.push({ prompt, response, score });
  }

  const postScore = postResults.reduce((sum, r) => sum + r.score, 0) / postResults.length;

  // Compare results
  const testResults: TestResult[] = testPrompts.map((prompt, i) => ({
    prompt,
    prePassed: preResults[i].score >= 0.5,
    postPassed: postResults[i].score >= 0.5,
    preScore: preResults[i].score,
    postScore: postResults[i].score,
  }));

  const regressions = testResults
    .filter((r) => r.prePassed && !r.postPassed)
    .map((r) => `Regression on: "${r.prompt.slice(0, 80)}"`);

  const improvements = testResults
    .filter((r) => !r.prePassed && r.postPassed)
    .map((r) => `Improvement on: "${r.prompt.slice(0, 80)}"`);

  const passed = regressions.length === 0 && postScore >= preScore * config.fitnessThreshold;

  // Rollback if gate failed
  if (!passed) {
    await rollbackMutations(workspaceDir, backups);
  }

  return { passed, preScore, postScore, regressions, improvements, testResults };
}
