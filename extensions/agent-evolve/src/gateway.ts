// ---------------------------------------------------------------------------
// Agent Evolve — Gateway RPC methods
// ---------------------------------------------------------------------------

import { homedir } from "node:os";
import { join } from "node:path";
import type { OpenClawPluginApi } from "../../../src/plugins/types.js";
import { rollbackToTag, listEvolutionTags } from "./git.js";
import { readJsonl, countJsonlLines } from "./jsonl.js";
import { runEvolutionCycle } from "./loop.js";
import { analyzePatterns } from "./mutator.js";
import { resolveObservationsPath, resolveEvolutionPath } from "./paths.js";
import type { AgentEvolveConfig, Observation, EvolutionRecord } from "./types.js";

type Respond = (success: boolean, result?: unknown, error?: unknown) => void;
type HandlerParams = { params: Record<string, unknown>; respond: Respond };

/** Resolve agent workspace directory from config. */
export function resolveWorkspaceDir(api: OpenClawPluginApi, agentId: string): string {
  // Try agent-specific workspace, fall back to default
  const cfg = api.config as Record<string, unknown>;
  const agents = cfg.agents as Record<string, unknown> | undefined;
  const agentList = (agents?.list ?? []) as Array<Record<string, unknown>>;

  const agentEntry = agentList.find((a) => a.id === agentId || a.name === agentId);
  if (agentEntry?.workspace && typeof agentEntry.workspace === "string") {
    return agentEntry.workspace;
  }

  const defaults = agents?.defaults as Record<string, unknown> | undefined;
  if (defaults?.workspace && typeof defaults.workspace === "string") {
    return defaults.workspace;
  }

  // Default workspace path
  return join(homedir(), ".openclaw", "workspace");
}

/** Register all gateway RPC methods. */
export function registerGatewayMethods(api: OpenClawPluginApi, config: AgentEvolveConfig): void {
  const log = api.logger;

  // --- evolve.status ---
  api.registerGatewayMethod("evolve.status", async ({ params, respond }: HandlerParams) => {
    const agentId = params.agentId as string | undefined;

    try {
      if (agentId) {
        const evolutions = await readJsonl<EvolutionRecord>(resolveEvolutionPath(agentId));
        const obsCount = await countJsonlLines(resolveObservationsPath(agentId));
        const observations = await readJsonl<Observation>(resolveObservationsPath(agentId), {
          limit: 20,
        });
        const patterns = analyzePatterns(observations);

        respond(true, {
          agentId,
          observationCount: obsCount,
          evolutionCount: evolutions.length,
          evolutions: evolutions.slice(-10),
          topPatterns: patterns.slice(0, 5),
        });
      } else {
        respond(true, {
          message: "Provide agentId to see evolution status",
        });
      }
    } catch (err) {
      respond(false, undefined, String(err));
    }
  });

  // --- evolve.run ---
  api.registerGatewayMethod("evolve.run", async ({ params, respond }: HandlerParams) => {
    const agentId = params.agentId as string;
    const dryRun = params.dryRun as boolean | undefined;

    if (!agentId) {
      respond(false, undefined, "agentId is required");
      return;
    }

    const workspaceDir = resolveWorkspaceDir(api, agentId);
    log.info?.(`[agent-evolve] evolution cycle triggered for ${agentId} (dryRun=${!!dryRun})`);

    try {
      const record = await runEvolutionCycle({
        agentId,
        config,
        workspaceDir,
        logger: log,
        dryRun,
      });
      respond(true, record);
    } catch (err) {
      log.warn?.(`[agent-evolve] evolution cycle failed: ${err}`);
      respond(false, undefined, String(err));
    }
  });

  // --- evolve.rollback ---
  api.registerGatewayMethod("evolve.rollback", async ({ params, respond }: HandlerParams) => {
    const agentId = params.agentId as string;
    const version = params.version as string | undefined;

    if (!agentId) {
      respond(false, undefined, "agentId is required");
      return;
    }

    const workspaceDir = resolveWorkspaceDir(api, agentId);

    try {
      const tags = await listEvolutionTags(workspaceDir, agentId);
      if (tags.length === 0) {
        respond(false, undefined, "No evolution tags found");
        return;
      }

      // Default to the previous tag (second-to-last)
      let targetTag: string;
      if (version) {
        targetTag = tags.find((t) => t.includes(version)) ?? version;
      } else if (tags.length >= 2) {
        targetTag = tags[tags.length - 2];
      } else {
        respond(false, undefined, "Only one evolution exists, cannot rollback further");
        return;
      }

      const files = config.mutableFiles;
      const success = await rollbackToTag(workspaceDir, targetTag, files);

      if (success) {
        log.info?.(`[agent-evolve] ${agentId}: rolled back to ${targetTag}`);
        respond(true, { agentId, rolledBackTo: targetTag, files });
      } else {
        respond(false, undefined, `Failed to rollback to ${targetTag}`);
      }
    } catch (err) {
      respond(false, undefined, String(err));
    }
  });

  // --- evolve.observations ---
  api.registerGatewayMethod("evolve.observations", async ({ params, respond }: HandlerParams) => {
    const agentId = params.agentId as string;
    const limit = (params.limit as number) ?? 50;
    const since = params.since as number | undefined;

    if (!agentId) {
      respond(false, undefined, "agentId is required");
      return;
    }

    try {
      const observations = await readJsonl<Observation>(resolveObservationsPath(agentId), {
        limit,
        since,
      });
      const patterns = analyzePatterns(observations);
      const total = await countJsonlLines(resolveObservationsPath(agentId));

      respond(true, {
        agentId,
        total,
        returned: observations.length,
        observations,
        patterns: patterns.slice(0, 10),
      });
    } catch (err) {
      respond(false, undefined, String(err));
    }
  });

  // --- evolve.sweep — trigger auto-evolve across all agents ---
  api.registerGatewayMethod("evolve.sweep", async ({ params: _params, respond }: HandlerParams) => {
    log.info?.("[agent-evolve] manual sweep triggered via gateway");
    try {
      const { runAutoEvolveSweep } = await import("./cron.js");
      const results = await runAutoEvolveSweep({
        config,
        resolveWorkspaceDir: (agentId: string) => resolveWorkspaceDir(api, agentId),
        logger: log,
      });
      respond(true, results);
    } catch (err) {
      respond(false, undefined, String(err));
    }
  });

  log.info?.("[agent-evolve] gateway methods registered");
}
