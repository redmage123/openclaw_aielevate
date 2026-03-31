// ---------------------------------------------------------------------------
// Agent Evolve — Plugin entry point
// Automated agent workspace mutation with observation, LLM-driven evolution,
// fitness gating, and git-tracked rollback.
// ---------------------------------------------------------------------------

import type { OpenClawPluginApi } from "../../src/plugins/types.js";
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

  // CLI — openclaw evolve {status, run, rollback, observations}
  api.registerCli(createEvolveCli(api), { commands: ["evolve"] });

  // Auto-evolve cron — nightly sweep of all agents with failure observations
  const schedule = config.autoEvolveSchedule;
  if (schedule) {
    const cron = startAutoEvolveCron({
      config,
      resolveWorkspaceDir: (agentId: string) => resolveWorkspaceDir(api, agentId),
      logger: api.logger,
    });

    // Clean up on gateway stop
    api.on("gateway_stop", () => {
      cron.stop();
    });
  }

  api.logger.info?.(
    `[agent-evolve] plugin loaded — observer, gateway, CLI${schedule ? `, auto-evolve cron (${schedule})` : ""} registered`,
  );
}
