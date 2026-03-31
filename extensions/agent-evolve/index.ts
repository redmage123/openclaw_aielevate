// ---------------------------------------------------------------------------
// Agent Evolve — Plugin entry point
// Automated agent workspace mutation with observation, LLM-driven evolution,
// fitness gating, and git-tracked rollback.
// ---------------------------------------------------------------------------

import type { OpenClawPluginApi } from "../../src/plugins/types.js";
import { createEvolveCli } from "./src/cli.js";
import { registerGatewayMethods } from "./src/gateway.js";
import { registerObserverHooks } from "./src/observer.js";
import { parseConfig } from "./src/types.js";

export default function register(api: OpenClawPluginApi): void {
  const config = parseConfig(api.pluginConfig as Record<string, unknown> | undefined);

  if (!config.enabled) {
    api.logger.info?.("[agent-evolve] plugin disabled via config");
    return;
  }

  // Phase 1: Observer — captures structured observations from every agent session
  registerObserverHooks(api, config);

  // Phase 2: Gateway methods — expose evolve.status, evolve.run, evolve.rollback, evolve.observations
  registerGatewayMethods(api, config);

  // Phase 3: CLI — openclaw evolve {status, run, rollback, observations}
  api.registerCli(createEvolveCli(api), { commands: ["evolve"] });

  api.logger.info?.("[agent-evolve] plugin loaded — observer, gateway, and CLI registered");
}
