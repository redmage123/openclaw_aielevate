// ---------------------------------------------------------------------------
// Agent Evolve — CLI commands: openclaw evolve {status,run,sweep,rollback,observations}
// status/observations read JSONL directly; run/sweep/rollback call gateway via WS.
// ---------------------------------------------------------------------------

import { homedir } from "node:os";
import { join } from "node:path";
import type { OpenClawPluginApi, OpenClawPluginCliContext } from "../../../src/plugins/types.js";
import { readJsonl, countJsonlLines } from "./jsonl.js";
import { analyzePatterns } from "./mutator.js";
import { resolveObservationsPath, resolveEvolutionPath } from "./paths.js";
import { parseConfig } from "./types.js";
import type { Observation, EvolutionRecord } from "./types.js";

// ---------------------------------------------------------------------------
// Gateway WS call — dynamically loads core callGateway
// ---------------------------------------------------------------------------

async function callGatewayWs(method: string, params: Record<string, unknown>): Promise<unknown> {
  // Try core callGateway (WebSocket)
  try {
    const mod = await import("../../../src/gateway/call.js");
    const fn = (mod as Record<string, unknown>).callGateway;
    if (typeof fn === "function") {
      return await (fn as Function)({
        method,
        params,
        url: process.env.OPENCLAW_GATEWAY_URL ?? "ws://127.0.0.1:18789",
        token: process.env.OPENCLAW_GATEWAY_TOKEN,
        timeoutMs: 120_000,
        mode: "cli",
        clientName: "agent-evolve-cli",
      });
    }
  } catch {
    // Not available in this install
  }

  // Fallback: try openclaw plugin-sdk path
  try {
    const mod = await import("openclaw/plugin-sdk");
    const fn = (mod as Record<string, unknown>).callGateway;
    if (typeof fn === "function") {
      return await (fn as Function)({
        method,
        params,
        url: process.env.OPENCLAW_GATEWAY_URL ?? "ws://127.0.0.1:18789",
        token: process.env.OPENCLAW_GATEWAY_TOKEN,
        timeoutMs: 120_000,
        mode: "cli",
        clientName: "agent-evolve-cli",
      });
    }
  } catch {
    // Not available
  }

  throw new Error("Cannot reach gateway — ensure the gateway is running");
}

/** Register the `evolve` CLI subcommand. */
export function createEvolveCli(_api: OpenClawPluginApi): (ctx: OpenClawPluginCliContext) => void {
  return ({ program }: OpenClawPluginCliContext) => {
    const evolve = program
      .command("evolve")
      .description("Agent Evolve — automated workspace mutation and self-correction");

    // --- evolve status (local JSONL read) ---
    evolve
      .command("status")
      .argument("[agentId]", "Agent ID to check")
      .description("Show evolution history and observation counts")
      .action(async (agentId?: string) => {
        if (!agentId) {
          console.log("Usage: openclaw evolve status <agentId>");
          return;
        }

        const evolutions = await readJsonl<EvolutionRecord>(resolveEvolutionPath(agentId));
        const obsCount = await countJsonlLines(resolveObservationsPath(agentId));
        const observations = await readJsonl<Observation>(resolveObservationsPath(agentId), {
          limit: 50,
        });
        const patterns = analyzePatterns(observations);

        console.log(`\n  Agent: ${agentId}`);
        console.log(`  Observations: ${obsCount}`);
        console.log(`  Evolutions: ${evolutions.length}`);

        if (patterns.length > 0) {
          console.log("\n  Top Failure Patterns:");
          for (const p of patterns.slice(0, 5)) {
            console.log(
              `    ${p.errorClass}: ${p.count}x (${(p.rate * 100).toFixed(0)}% rate, severity ${p.severity.toFixed(2)})`,
            );
          }
        }

        if (evolutions.length > 0) {
          console.log("\n  Recent Evolutions:");
          for (const evo of evolutions.slice(-5)) {
            const date = new Date(evo.timestamp).toISOString().slice(0, 19);
            const mutations = evo.plan?.mutations?.length ?? 0;
            console.log(`    ${evo.id} [${evo.status}] ${date} — ${mutations} mutations`);
          }
        }

        console.log("");
      });

    // --- evolve observations (local JSONL read) ---
    evolve
      .command("observations")
      .argument("<agentId>", "Agent ID")
      .option("-l, --limit <n>", "Number of observations to show", "20")
      .description("View captured observations and failure patterns")
      .action(async (agentId: string, opts: { limit: string }) => {
        const limit = parseInt(opts.limit, 10);
        const observations = await readJsonl<Observation>(resolveObservationsPath(agentId), {
          limit,
        });
        const patterns = analyzePatterns(observations);
        const total = await countJsonlLines(resolveObservationsPath(agentId));

        console.log(`\n  Agent: ${agentId}`);
        console.log(`  Total observations: ${total}`);
        console.log(`  Showing: ${observations.length}`);

        if (patterns.length > 0) {
          console.log("\n  Failure Patterns:");
          for (const p of patterns.slice(0, 10)) {
            console.log(`    ${p.errorClass}: ${p.count}x (${(p.rate * 100).toFixed(0)}% rate)`);
            for (const ex of p.examples.slice(0, 2)) {
              console.log(`      "${ex.slice(0, 100)}"`);
            }
          }
        }

        if (observations.length > 0) {
          console.log("\n  Recent Observations:");
          for (const obs of observations.slice(-10)) {
            const date = new Date(obs.timestamp).toISOString().slice(0, 19);
            const tools = obs.toolCalls?.length ?? 0;
            console.log(
              `    ${date} [${obs.outcome}] ${obs.errorClass ?? "ok"} — ${tools} tool calls`,
            );
          }
        }

        console.log("");
      });

    // --- evolve run — calls gateway RPC ---
    evolve
      .command("run")
      .argument("<agentId>", "Agent ID to evolve")
      .option("--dry-run", "Propose mutations without applying them")
      .description("Trigger an evolution cycle via gateway")
      .action(async (agentId: string, opts: { dryRun?: boolean }) => {
        console.log(
          `\n  Starting evolution cycle for ${agentId}${opts.dryRun ? " (dry run)" : ""}...`,
        );
        console.log("  (calling gateway — this may take 30-60s for LLM analysis)\n");

        try {
          const result = (await callGatewayWs("evolve.run", {
            agentId,
            dryRun: opts.dryRun,
          })) as Record<string, unknown>;

          const plan = result.plan as Record<string, unknown>;
          const gate = result.gate as Record<string, unknown>;
          const mutations = (plan?.mutations as Array<Record<string, unknown>>) ?? [];

          console.log(`  Result: ${result.status}`);
          console.log(`  Analysis: ${(plan?.analysis as string)?.slice(0, 200)}`);
          console.log(`  Mutations: ${mutations.length}`);
          for (const m of mutations) {
            console.log(`    ${m.operation} ${m.file}: ${(m.reason as string)?.slice(0, 80)}`);
          }
          if (gate) {
            console.log(
              `  Gate: pre=${(gate.preScore as number)?.toFixed(2)} post=${(gate.postScore as number)?.toFixed(2)} passed=${gate.passed}`,
            );
          }
          if (result.gitTag) console.log(`  Git tag: ${result.gitTag}`);
        } catch (err) {
          console.error(`  ${err instanceof Error ? err.message : err}`);
        }

        console.log("");
      });

    // --- evolve sweep — calls gateway RPC ---
    evolve
      .command("sweep")
      .description("Run evolution sweep across all agents via gateway")
      .action(async () => {
        console.log("\n  Starting evolution sweep via gateway...");
        console.log("  (this may take several minutes)\n");

        try {
          const result = (await callGatewayWs("evolve.sweep", {})) as Record<string, unknown>;

          const evolved = (result.evolved as string[]) ?? [];
          const rejected = (result.rejected as string[]) ?? [];
          const skipped = (result.skipped as string[]) ?? [];
          const errors = (result.errors as string[]) ?? [];

          console.log(`  Evolved: ${evolved.join(", ") || "none"}`);
          console.log(`  Rejected: ${rejected.join(", ") || "none"}`);
          console.log(`  Skipped: ${skipped.length} agents`);
          if (errors.length > 0) console.log(`  Errors: ${errors.join("; ")}`);
        } catch (err) {
          console.error(`  ${err instanceof Error ? err.message : err}`);
        }

        console.log("");
      });

    // --- evolve rollback — calls gateway RPC ---
    evolve
      .command("rollback")
      .argument("<agentId>", "Agent ID to rollback")
      .argument("[version]", "Specific evolution version to rollback to")
      .description("Rollback to a previous workspace version via gateway")
      .action(async (agentId: string, version?: string) => {
        console.log(`\n  Rolling back ${agentId}${version ? ` to ${version}` : ""}...`);

        try {
          const result = (await callGatewayWs("evolve.rollback", {
            agentId,
            version,
          })) as Record<string, unknown>;

          console.log(`  Rolled back to: ${result.rolledBackTo}`);
          console.log(`  Files restored: ${(result.files as string[])?.join(", ")}`);
        } catch (err) {
          console.error(`  ${err instanceof Error ? err.message : err}`);
        }

        console.log("");
      });
  };
}
