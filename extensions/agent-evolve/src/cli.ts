// ---------------------------------------------------------------------------
// Agent Evolve — CLI commands: openclaw evolve {status,run,rollback,observations}
// Reads observation/evolution JSONL files directly (no gateway RPC needed).
// ---------------------------------------------------------------------------

import type { OpenClawPluginApi, OpenClawPluginCliContext } from "../../../src/plugins/types.js";
import { readJsonl, countJsonlLines } from "./jsonl.js";
import { analyzePatterns } from "./mutator.js";
import { resolveObservationsPath, resolveEvolutionPath } from "./paths.js";
import type { Observation, EvolutionRecord } from "./types.js";

/** Register the `evolve` CLI subcommand. */
export function createEvolveCli(_api: OpenClawPluginApi): (ctx: OpenClawPluginCliContext) => void {
  return ({ program }: OpenClawPluginCliContext) => {
    const evolve = program
      .command("evolve")
      .description("Agent Evolve — automated workspace mutation and self-correction");

    // --- evolve status ---
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

    // --- evolve observations ---
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

    // --- evolve run (placeholder — requires gateway) ---
    evolve
      .command("run")
      .argument("<agentId>", "Agent ID to evolve")
      .option("--dry-run", "Propose mutations without applying them")
      .description("Trigger an evolution cycle (requires running gateway)")
      .action(async (agentId: string, opts: { dryRun?: boolean }) => {
        console.log(
          `\n  To trigger evolution, use the gateway RPC method 'evolve.run' with agentId="${agentId}"`,
        );
        console.log(`  The gateway handles the full Observe → Evolve → Gate → Reload cycle.`);
        console.log(`  Dry run: ${opts.dryRun ?? false}\n`);
      });

    // --- evolve rollback (placeholder — requires gateway) ---
    evolve
      .command("rollback")
      .argument("<agentId>", "Agent ID to rollback")
      .argument("[version]", "Specific evolution version to rollback to")
      .description("Rollback to a previous workspace version (requires running gateway)")
      .action(async (agentId: string, version?: string) => {
        console.log(
          `\n  To rollback, use the gateway RPC method 'evolve.rollback' with agentId="${agentId}"${version ? `, version="${version}"` : ""}`,
        );
        console.log(`  The gateway handles workspace file restoration.\n`);
      });
  };
}
