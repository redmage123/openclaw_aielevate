// ---------------------------------------------------------------------------
// Agent Evolve — Path resolution
// ---------------------------------------------------------------------------

import { homedir } from "node:os";
import { join } from "node:path";

const EVOLVE_DIR = "agent-evolve";

/** Base directory for all agent-evolve data: ~/.openclaw/agent-evolve/ */
export function resolveEvolveBaseDir(): string {
  return join(homedir(), ".openclaw", EVOLVE_DIR);
}

/** Per-agent evolve directory: ~/.openclaw/agent-evolve/<agentId>/ */
export function resolveAgentEvolveDir(agentId: string): string {
  return join(resolveEvolveBaseDir(), agentId);
}

/** Observations JSONL file for an agent. */
export function resolveObservationsPath(agentId: string): string {
  return join(resolveAgentEvolveDir(agentId), "observations.jsonl");
}

/** Evolution history JSONL file for an agent. */
export function resolveEvolutionPath(agentId: string): string {
  return join(resolveAgentEvolveDir(agentId), "evolution.jsonl");
}

/** Backup directory for workspace files before mutation. */
export function resolveBackupDir(agentId: string): string {
  return join(resolveAgentEvolveDir(agentId), "backups");
}

/** Lock file path for per-agent evolution concurrency guard. */
export function resolveLockPath(agentId: string): string {
  return join(resolveAgentEvolveDir(agentId), ".evolve.lock");
}
