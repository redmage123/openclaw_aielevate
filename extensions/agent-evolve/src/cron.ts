// ---------------------------------------------------------------------------
// Agent Evolve — Nightly auto-evolution cron
// Scans all agents for sufficient failure observations and triggers evolution.
// ---------------------------------------------------------------------------

import { readdir } from "node:fs/promises";
// OpenClawPluginApi type not needed here — cron uses Logger and config directly
import { readJsonl, countJsonlLines } from "./jsonl.js";
import { runEvolutionCycle } from "./loop.js";
import { analyzePatterns, hasSignificantPatterns } from "./mutator.js";
import { resolveEvolveBaseDir, resolveObservationsPath } from "./paths.js";
import type { AgentEvolveConfig, Observation, EvolutionRecord } from "./types.js";

type Logger = {
  info?: (message: string) => void;
  warn?: (message: string) => void;
  error?: (message: string) => void;
};

/** Discover agent IDs that have observation data. */
async function discoverAgentsWithObservations(): Promise<string[]> {
  const baseDir = resolveEvolveBaseDir();
  try {
    const entries = await readdir(baseDir, { withFileTypes: true });
    const agentIds: string[] = [];
    for (const entry of entries) {
      if (entry.isDirectory() && !entry.name.startsWith(".")) {
        const count = await countJsonlLines(resolveObservationsPath(entry.name));
        if (count > 0) agentIds.push(entry.name);
      }
    }
    return agentIds;
  } catch {
    return [];
  }
}

/** Format a summary line for ntfy/logging. */
function formatResult(record: EvolutionRecord): string {
  const mutations = record.plan?.mutations?.length ?? 0;
  const gate = record.gate;
  if (record.status === "applied") {
    return `${record.agentId}: EVOLVED (${mutations} mutations, gate ${gate.preScore.toFixed(2)}→${gate.postScore.toFixed(2)})${record.gitTag ? ` [${record.gitTag}]` : ""}`;
  }
  if (record.status === "rejected") {
    const reasons = gate.regressions.length > 0 ? gate.regressions.join(", ") : "below threshold";
    return `${record.agentId}: REJECTED (${reasons})`;
  }
  return `${record.agentId}: ${record.status}`;
}

/** Send a notification via ntfy.sh (fire-and-forget). */
async function notifyNtfy(title: string, body: string, logger: Logger): Promise<void> {
  const topic = process.env.NTFY_TOPIC ?? "aielevate-alerts";
  try {
    await fetch(`https://ntfy.sh/${topic}`, {
      method: "POST",
      headers: {
        Title: title,
        Priority: "default",
        Tags: "robot,dna",
      },
      body,
    });
  } catch (err) {
    logger.warn?.(`[agent-evolve] ntfy send failed: ${err}`);
  }
}

type ResolveWorkspaceFn = (agentId: string) => string;

/** Run the nightly auto-evolution sweep.
 *  Scans all agents with observations, triggers evolution for eligible ones. */
export async function runAutoEvolveSweep(params: {
  config: AgentEvolveConfig;
  resolveWorkspaceDir: ResolveWorkspaceFn;
  logger: Logger;
}): Promise<{ evolved: string[]; rejected: string[]; skipped: string[]; errors: string[] }> {
  const { config, resolveWorkspaceDir, logger } = params;
  const results = {
    evolved: [] as string[],
    rejected: [] as string[],
    skipped: [] as string[],
    errors: [] as string[],
  };

  const agentIds = await discoverAgentsWithObservations();
  logger.info?.(`[agent-evolve] auto-evolve sweep: ${agentIds.length} agents with observations`);

  const summaryLines: string[] = [];

  for (const agentId of agentIds) {
    try {
      // Check threshold
      const obsCount = await countJsonlLines(resolveObservationsPath(agentId));
      if (obsCount < config.minObservationsBeforeEvolve) {
        results.skipped.push(agentId);
        continue;
      }

      // Check for significant patterns
      const observations = await readJsonl<Observation>(resolveObservationsPath(agentId), {
        limit: 50,
      });
      const patterns = analyzePatterns(observations);
      if (!hasSignificantPatterns(patterns)) {
        results.skipped.push(agentId);
        continue;
      }

      // Run evolution
      logger.info?.(`[agent-evolve] auto-evolving ${agentId}...`);
      const workspaceDir = resolveWorkspaceDir(agentId);
      const record = await runEvolutionCycle({
        agentId,
        config,
        workspaceDir,
        logger,
      });

      if (record.status === "applied") {
        results.evolved.push(agentId);
      } else {
        results.rejected.push(agentId);
      }
      summaryLines.push(formatResult(record));
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      // "no significant failure patterns" and "only N observations" are normal skips
      if (msg.includes("no significant") || msg.includes("only ")) {
        results.skipped.push(agentId);
      } else {
        results.errors.push(`${agentId}: ${msg}`);
        logger.warn?.(`[agent-evolve] auto-evolve error for ${agentId}: ${msg}`);
      }
    }
  }

  // Send ntfy summary if anything happened
  if (summaryLines.length > 0 || results.errors.length > 0) {
    const title = `Agent Evolve: ${results.evolved.length} evolved, ${results.rejected.length} rejected, ${results.errors.length} errors`;
    const body = [
      ...summaryLines,
      ...results.errors.map((e) => `ERROR: ${e}`),
      `Skipped: ${results.skipped.length} agents`,
    ].join("\n");
    await notifyNtfy(title, body, logger);
  }

  logger.info?.(
    `[agent-evolve] sweep complete: ${results.evolved.length} evolved, ${results.rejected.length} rejected, ${results.skipped.length} skipped, ${results.errors.length} errors`,
  );

  return results;
}

/** Register the auto-evolve cron using gateway service lifecycle. */
export function startAutoEvolveCron(params: {
  config: AgentEvolveConfig;
  resolveWorkspaceDir: ResolveWorkspaceFn;
  logger: Logger;
}): { stop: () => void } {
  const { config, resolveWorkspaceDir, logger } = params;

  // Parse schedule — default to 03:00 daily if just "daily" is specified
  const schedule = config.autoEvolveSchedule ?? "0 3 * * *";
  const intervalMs = parseCronToIntervalMs(schedule);

  logger.info?.(
    `[agent-evolve] auto-evolve cron started (interval: ${Math.round(intervalMs / 60000)}min)`,
  );

  // Calculate ms until next run at the scheduled hour
  const msUntilFirst = calculateMsUntilNextRun(schedule);

  let timer: ReturnType<typeof setTimeout> | null = null;
  let interval: ReturnType<typeof setInterval> | null = null;

  // First run at the scheduled time, then repeat at interval
  timer = setTimeout(() => {
    runAutoEvolveSweep({ config, resolveWorkspaceDir, logger }).catch((err) => {
      logger.error?.(`[agent-evolve] auto-evolve sweep failed: ${err}`);
    });

    interval = setInterval(() => {
      runAutoEvolveSweep({ config, resolveWorkspaceDir, logger }).catch((err) => {
        logger.error?.(`[agent-evolve] auto-evolve sweep failed: ${err}`);
      });
    }, intervalMs);
  }, msUntilFirst);

  return {
    stop: () => {
      if (timer) clearTimeout(timer);
      if (interval) clearInterval(interval);
      logger.info?.("[agent-evolve] auto-evolve cron stopped");
    },
  };
}

// Simple cron expression to interval conversion.
// Supports: "0 3 * * *" (daily at 3am), every-N-hours patterns, etc.
function parseCronToIntervalMs(expr: string): number {
  const parts = expr.trim().split(/\s+/);
  if (parts.length < 5) return 24 * 60 * 60_000; // default daily

  const [, hourPart] = parts;

  // "*/N" in hour field = every N hours
  const everyMatch = hourPart.match(/^\*\/(\d+)$/);
  if (everyMatch) {
    return parseInt(everyMatch[1], 10) * 60 * 60_000;
  }

  // Fixed hour = daily
  return 24 * 60 * 60_000;
}

/** Calculate ms until the next occurrence of the cron hour. */
function calculateMsUntilNextRun(expr: string): number {
  const parts = expr.trim().split(/\s+/);
  if (parts.length < 5) return 60_000; // 1 min fallback

  const minute = parseInt(parts[0], 10) || 0;
  const hourPart = parts[1];

  // "*/N" = next occurrence within interval
  const everyMatch = hourPart.match(/^\*\/(\d+)$/);
  if (everyMatch) {
    const intervalH = parseInt(everyMatch[1], 10);
    const now = new Date();
    const currentH = now.getHours();
    const nextH = Math.ceil((currentH + 1) / intervalH) * intervalH;
    const target = new Date(now);
    target.setHours(nextH % 24, minute, 0, 0);
    if (target.getTime() <= now.getTime()) target.setDate(target.getDate() + 1);
    return target.getTime() - now.getTime();
  }

  // Fixed hour
  const targetH = parseInt(hourPart, 10) || 3;
  const now = new Date();
  const target = new Date(now);
  target.setHours(targetH, minute, 0, 0);
  if (target.getTime() <= now.getTime()) target.setDate(target.getDate() + 1);
  return target.getTime() - now.getTime();
}
