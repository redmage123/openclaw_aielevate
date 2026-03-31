// ---------------------------------------------------------------------------
// Agent Evolve — Plugin entry point
// Automated agent workspace mutation with observation, LLM-driven evolution,
// fitness gating, and git-tracked rollback.
// ---------------------------------------------------------------------------

import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { createEvolveCli } from "./src/cli.js";
import { startAutoEvolveCron } from "./src/cron.js";
import { registerGatewayMethods, resolveWorkspaceDir } from "./src/gateway.js";
import { registerObserverHooks } from "./src/observer.js";
import { parseConfig } from "./src/types.js";

export default function register(api: OpenClawPluginApi): void {
  const config = parseConfig(api.pluginConfig as Record<string, unknown> | undefined);

  if (!config.enabled) {
    api.logger.info?.("[agent-evolve] plugin disabled via config");
    return;
  }

  // Observer — captures structured observations from every agent session
  registerObserverHooks(api, config);

  // Gateway methods — evolve.status, evolve.run, evolve.rollback, evolve.observations, evolve.sweep
  registerGatewayMethods(api, config);

  // CLI — openclaw evolve {status, run, sweep, rollback, observations}
  api.registerCli(createEvolveCli(api), { commands: ["evolve"] });

  // Auto-evolve cron — nightly sweep of all agents with failure observations
  const schedule = config.autoEvolveSchedule;
  if (schedule) {
    const cron = startAutoEvolveCron({
      config,
      resolveWorkspaceDir: (agentId: string) => resolveWorkspaceDir(api, agentId),
      logger: {
        info: (msg: string) => api.logger.info?.(msg),
        warn: (msg: string) => api.logger.warn?.(msg),
        error: (msg: string) => api.logger.error?.(msg),
      },
    });

    // Clean up on process exit
    process.on("exit", () => cron.stop());
  }

  // Post-evolution heartbeat verification uses runHeartbeatOnce (if available)
  // to verify the mutated agent responds correctly after evolution
  const hasHeartbeat = typeof api.runtime?.system?.runHeartbeatOnce === "function";
  if (hasHeartbeat) {
    api.logger.debug?.("[agent-evolve] runHeartbeatOnce available for post-evolution verification");
  }

  api.logger.info?.(
    `[agent-evolve] plugin loaded — observer, gateway, CLI${schedule ? `, auto-evolve cron (${schedule})` : ""} registered`,
  );
}
