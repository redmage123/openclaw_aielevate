// ---------------------------------------------------------------------------
// Agent Evolve — Git tag/rollback for workspace mutations
// ---------------------------------------------------------------------------

import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

/** Find the git root for a workspace directory. Returns null if not a repo. */
async function findGitRoot(dir: string): Promise<string | null> {
  try {
    const { stdout } = await execFileAsync("git", ["rev-parse", "--show-toplevel"], {
      cwd: dir,
    });
    return stdout.trim() || null;
  } catch {
    return null;
  }
}

/** Create an annotated git tag for an evolution step. */
export async function createEvolutionTag(
  workspaceDir: string,
  tagName: string,
  message: string,
): Promise<boolean> {
  const gitRoot = await findGitRoot(workspaceDir);
  if (!gitRoot) return false;

  try {
    await execFileAsync("git", ["tag", "-a", tagName, "-m", message], {
      cwd: gitRoot,
    });
    return true;
  } catch (err) {
    // Tag may already exist
    const msg = err instanceof Error ? err.message : String(err);
    if (msg.includes("already exists")) return false;
    throw err;
  }
}

/** Rollback specific files to a git tag version. Does NOT do a full checkout. */
export async function rollbackToTag(
  workspaceDir: string,
  tagName: string,
  files: string[],
): Promise<boolean> {
  const gitRoot = await findGitRoot(workspaceDir);
  if (!gitRoot) return false;

  try {
    await execFileAsync("git", ["checkout", tagName, "--", ...files], {
      cwd: gitRoot,
    });
    return true;
  } catch {
    return false;
  }
}

/** List all evolution tags for an agent, sorted by sequence number. */
export async function listEvolutionTags(workspaceDir: string, agentId: string): Promise<string[]> {
  const gitRoot = await findGitRoot(workspaceDir);
  if (!gitRoot) return [];

  try {
    const { stdout } = await execFileAsync("git", ["tag", "-l", `evo-${agentId}-*`], {
      cwd: gitRoot,
    });
    const tags = stdout
      .split("\n")
      .map((t) => t.trim())
      .filter(Boolean);

    // Sort by sequence number
    tags.sort((a, b) => {
      const numA = extractTagNumber(a);
      const numB = extractTagNumber(b);
      return numA - numB;
    });

    return tags;
  } catch {
    return [];
  }
}

/** Get the latest evolution tag sequence number for an agent. */
export async function getLatestEvolutionTagNumber(
  workspaceDir: string,
  agentId: string,
): Promise<number> {
  const tags = await listEvolutionTags(workspaceDir, agentId);
  if (tags.length === 0) return 0;

  const lastTag = tags[tags.length - 1];
  return extractTagNumber(lastTag);
}

function extractTagNumber(tag: string): number {
  const match = tag.match(/(\d+)$/);
  return match ? parseInt(match[1], 10) : 0;
}
