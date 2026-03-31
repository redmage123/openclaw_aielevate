// ---------------------------------------------------------------------------
// Agent Evolve — Evolution loop orchestrator
// Solve → Observe → Evolve → Gate → Reload
// ---------------------------------------------------------------------------

import { open, readFile, unlink, mkdir } from "node:fs/promises";
import { backupWorkspace, applyMutations, rollbackMutations, runGate } from "./gate.js";
import { createEvolutionTag, getLatestEvolutionTagNumber } from "./git.js";
import { readJsonl, appendJsonl, rotateJsonl } from "./jsonl.js";
import { analyzePatterns, hasSignificantPatterns, proposeMutations } from "./mutator.js";
import { resolveObservationsPath, resolveEvolutionPath, resolveLockPath } from "./paths.js";
import type { AgentEvolveConfig, Observation, EvolutionRecord } from "./types.js";

type Logger = {
  info?: (...args: unknown[]) => void;
  warn?: (...args: unknown[]) => void;
  error?: (...args: unknown[]) => void;
  debug?: (...args: unknown[]) => void;
};

// Simple file-based lock to prevent concurrent evolution cycles
async function acquireLock(agentId: string): Promise<boolean> {
  const lockPath = resolveLockPath(agentId);
  try {
    await mkdir(lockPath.replace(/[^/]+$/, ""), { recursive: true });
    // Try exclusive creation — fails if file already exists
    const handle = await open(lockPath, "wx");
    await handle.writeFile(JSON.stringify({ pid: process.pid, ts: Date.now() }));
    await handle.close();
    return true;
  } catch (err) {
    // Check if stale lock (older than 30 minutes)
    try {
      const content = await readFile(lockPath, "utf-8");
      const lock = JSON.parse(content) as { ts: number };
      if (Date.now() - lock.ts > 30 * 60_000) {
        await unlink(lockPath);
        return acquireLock(agentId);
      }
    } catch {
      // Can't read lock — treat as locked
    }
    return false;
  }
}

async function releaseLock(agentId: string): Promise<void> {
  try {
    await unlink(resolveLockPath(agentId));
  } catch {
    // Already removed
  }
}

/** Run a full evolution cycle for an agent.
 *
 *  Steps:
 *  1. Read observations since last evolution
 *  2. Analyze failure patterns
 *  3. Propose LLM-driven mutations
 *  4. Run fitness gate (pre/post comparison)
 *  5. Git-tag and persist if passed; rollback if failed
 */
export async function runEvolutionCycle(params: {
  agentId: string;
  config: AgentEvolveConfig;
  workspaceDir: string;
  logger: Logger;
  dryRun?: boolean;
}): Promise<EvolutionRecord> {
  const { agentId, config, workspaceDir, logger, dryRun } = params;

  // Acquire per-agent lock
  const locked = await acquireLock(agentId);
  if (!locked) {
    throw new Error(`[agent-evolve] evolution cycle already in progress for ${agentId}`);
  }

  try {
    return await runCycleInner(agentId, config, workspaceDir, logger, dryRun);
  } finally {
    await releaseLock(agentId);
  }
}

async function runCycleInner(
  agentId: string,
  config: AgentEvolveConfig,
  workspaceDir: string,
  logger: Logger,
  dryRun?: boolean,
): Promise<EvolutionRecord> {
  // 1. Read observations
  const obsPath = resolveObservationsPath(agentId);
  const lastEvolution = await getLastEvolutionTimestamp(agentId);
  const observations = await readJsonl<Observation>(obsPath, {
    since: lastEvolution,
  });

  logger.info?.(
    `[agent-evolve] ${agentId}: ${observations.length} observations since last evolution`,
  );

  // 2. Check threshold
  if (observations.length < config.minObservationsBeforeEvolve) {
    throw new Error(
      `[agent-evolve] ${agentId}: only ${observations.length} observations, need ${config.minObservationsBeforeEvolve}`,
    );
  }

  // 3. Analyze patterns
  const patterns = analyzePatterns(observations);
  logger.info?.(`[agent-evolve] ${agentId}: ${patterns.length} failure patterns detected`);

  if (!hasSignificantPatterns(patterns)) {
    throw new Error(
      `[agent-evolve] ${agentId}: no significant failure patterns found — skipping evolution`,
    );
  }

  // 4. Propose mutations via LLM
  logger.info?.(`[agent-evolve] ${agentId}: proposing mutations...`);
  const plan = await proposeMutations({
    agentId,
    patterns,
    workspaceDir,
    config,
  });

  logger.info?.(
    `[agent-evolve] ${agentId}: ${plan.mutations.length} mutations proposed — ${plan.analysis.slice(0, 100)}`,
  );

  if (plan.mutations.length === 0) {
    throw new Error(`[agent-evolve] ${agentId}: LLM proposed no mutations`);
  }

  // 5. Dry run exit
  if (dryRun) {
    const record: EvolutionRecord = {
      id: `evo-${agentId}-dry`,
      agentId,
      timestamp: Date.now(),
      plan,
      gate: {
        passed: false,
        preScore: 0,
        postScore: 0,
        regressions: [],
        improvements: ["dry run — mutations not applied"],
        testResults: [],
      },
      status: "rejected",
    };
    logger.info?.(
      `[agent-evolve] ${agentId}: dry run complete, ${plan.mutations.length} mutations proposed`,
    );
    return record;
  }

  // 6. Backup workspace
  const backups = await backupWorkspace(workspaceDir, plan);

  // 7. Run gate
  logger.info?.(`[agent-evolve] ${agentId}: running fitness gate...`);
  const gateResult = await runGate({
    agentId,
    plan,
    workspaceDir,
    config,
  });

  // 8. Determine next sequence number
  const nextNum = (await getLatestEvolutionTagNumber(workspaceDir, agentId)) + 1;
  const evoId = `evo-${agentId}-${nextNum}`;

  if (gateResult.passed) {
    // Gate already applied mutations (in runGate) — create git tag
    logger.info?.(
      `[agent-evolve] ${agentId}: gate PASSED (pre=${gateResult.preScore.toFixed(2)}, post=${gateResult.postScore.toFixed(2)})`,
    );

    // If gate was analysis-only (no test prompts), apply mutations now
    const testPrompts = config.testPrompts[agentId] ?? [];
    if (testPrompts.length === 0) {
      await applyMutations(workspaceDir, plan.mutations);
    }

    const tagged = await createEvolutionTag(
      workspaceDir,
      evoId,
      `Agent Evolve: ${plan.analysis.slice(0, 200)}`,
    );

    const record: EvolutionRecord = {
      id: evoId,
      agentId,
      timestamp: Date.now(),
      plan,
      gate: gateResult,
      status: "applied",
      gitTag: tagged ? evoId : undefined,
    };

    await appendJsonl(resolveEvolutionPath(agentId), record);
    await rotateJsonl(resolveObservationsPath(agentId), config.maxObservationFileBytes);

    logger.info?.(`[agent-evolve] ${agentId}: evolution ${evoId} applied successfully`);
    return record;
  }

  // Gate failed — rollback already happened in runGate
  logger.warn?.(
    `[agent-evolve] ${agentId}: gate FAILED — regressions: ${gateResult.regressions.join(", ")}`,
  );

  const record: EvolutionRecord = {
    id: evoId,
    agentId,
    timestamp: Date.now(),
    plan,
    gate: gateResult,
    status: "rejected",
  };

  await appendJsonl(resolveEvolutionPath(agentId), record);

  return record;
}

/** Get the timestamp of the most recent evolution for an agent. */
async function getLastEvolutionTimestamp(agentId: string): Promise<number> {
  const records = await readJsonl<EvolutionRecord>(resolveEvolutionPath(agentId));
  if (records.length === 0) return 0;
  const last = records[records.length - 1];
  return last.timestamp ?? 0;
}
