import { readFileSync, writeFileSync, mkdirSync, readdirSync, statSync, unlinkSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";
import { randomBytes } from "node:crypto";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";

const OPENCLAW_DIR = join(homedir(), ".openclaw");
const AGENTS_DIR = join(OPENCLAW_DIR, "agents");
const OPS_DIR = join(OPENCLAW_DIR, "extensions", "ai-elevate-ops", "data");
try { mkdirSync(OPS_DIR, { recursive: true }); } catch {}

function loadJson(file: string, fallback: unknown = []): unknown {
  try { return JSON.parse(readFileSync(join(OPS_DIR, file), "utf-8")); } catch { return fallback; }
}
function saveJson(file: string, data: unknown): void {
  writeFileSync(join(OPS_DIR, file), JSON.stringify(data, null, 2));
}
function appendJsonl(file: string, entry: unknown): void {
  writeFileSync(join(OPS_DIR, file), JSON.stringify(entry) + "\n", { flag: "a" });
}

function cleanupSessions(ttlDays: number): { deleted: number; freedBytes: number } {
  const cutoff = Date.now() - ttlDays * 86400000;
  let deleted = 0, freedBytes = 0;
  try {
    for (const agent of readdirSync(AGENTS_DIR)) {
      const sessDir = join(AGENTS_DIR, agent, "sessions");
      try {
        for (const file of readdirSync(sessDir).filter(f => f.endsWith(".jsonl"))) {
          const fp = join(sessDir, file);
          try {
            const stat = statSync(fp);
            if (stat.mtimeMs < cutoff) { freedBytes += stat.size; unlinkSync(fp); try { unlinkSync(fp + ".lock"); } catch {} deleted++; }
          } catch {}
        }
      } catch {}
    }
  } catch {}
  appendJsonl("cleanup-log.jsonl", { timestamp: new Date().toISOString(), deleted, freedBytes });
  return { deleted, freedBytes };
}

function getSessionStats(): Record<string, { count: number; sizeBytes: number }> {
  const stats: Record<string, { count: number; sizeBytes: number }> = {};
  try {
    for (const agent of readdirSync(AGENTS_DIR)) {
      const sessDir = join(AGENTS_DIR, agent, "sessions");
      try {
        const files = readdirSync(sessDir).filter(f => f.endsWith(".jsonl"));
        let size = 0;
        for (const f of files) { try { size += statSync(join(sessDir, f)).size; } catch {} }
        if (files.length > 0) stats[agent] = { count: files.length, sizeBytes: size };
      } catch {}
    }
  } catch {}
  return stats;
}

const plugin = {
  id: "ai-elevate-ops",
  register(api: OpenClawPluginApi) {
    const config = api.pluginConfig || {};
    const ttlDays = (config.sessionTtlDays as number) || 30;
    const cleanupHours = (config.cleanupIntervalHours as number) || 24;

    // Background service for periodic session cleanup
    api.registerService({
      id: "ai-elevate-ops",
      async start(ctx) {
        ctx.logger.info(`[ai-elevate-ops] Started — session TTL: ${ttlDays}d, cleanup every ${cleanupHours}h`);
        const initial = cleanupSessions(ttlDays);
        if (initial.deleted > 0) ctx.logger.info(`[ai-elevate-ops] Initial cleanup: ${initial.deleted} sessions removed`);
        setInterval(() => {
          const r = cleanupSessions(ttlDays);
          if (r.deleted > 0) ctx.logger.info(`[ai-elevate-ops] Cleanup: ${r.deleted} sessions (${Math.round(r.freedBytes/1048576)}MB)`);
        }, cleanupHours * 3600000);
      },
    });

    // Gateway RPC methods (accessible via tools/invoke or gateway methods)
    api.registerGatewayMethod("ops.health", async () => {
      const stats = getSessionStats();
      const totalSessions = Object.values(stats).reduce((s, v) => s + v.count, 0);
      const totalSize = Object.values(stats).reduce((s, v) => s + v.sizeBytes, 0);
      return {
        status: "healthy", timestamp: new Date().toISOString(),
        agents: Object.keys(stats).length, sessions: totalSessions,
        sessionSizeMb: Math.round(totalSize / 1048576 * 10) / 10,
      };
    });

    api.registerGatewayMethod("ops.sessions.stats", async () => getSessionStats());

    api.registerGatewayMethod("ops.sessions.cleanup", async () => cleanupSessions(ttlDays));

    api.registerGatewayMethod("ops.orgs.list", async () => {
      const orgs: Record<string, string[]> = {};
      try {
        for (const agent of readdirSync(AGENTS_DIR)) {
          let org = "ai-elevate";
          if (agent.startsWith("gigforge")) org = "gigforge";
          else if (agent.startsWith("techuni")) org = "techuni";
          if (!orgs[org]) orgs[org] = [];
          orgs[org].push(agent);
        }
      } catch {}
      return { orgs };
    });

    api.registerGatewayMethod("ops.tokens.create", async (params: Record<string, unknown>) => {
      const org = params.org as string;
      if (!org) return { error: "org required" };
      const token = `oat_${org}_${randomBytes(32).toString("hex")}`;
      const tokens = loadJson("org-tokens.json", []) as Array<Record<string, unknown>>;
      tokens.push({ token, org, created: new Date().toISOString(), revoked: false });
      saveJson("org-tokens.json", tokens);
      return { token, org };
    });

    api.registerGatewayMethod("ops.cron.history", async () => {
      try {
        const lines = readFileSync(join(OPS_DIR, "cron-history.jsonl"), "utf-8").trim().split("\n");
        return { history: lines.slice(-50).map(l => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean) };
      } catch { return { history: [] }; }
    });
  },
};

export default plugin;
