import type { Server } from "node:http";
import {
  emptyPluginConfigSchema,
  type OpenClawPluginApi,
  type ProviderAuthContext,
  type ProviderAuthResult,
} from "openclaw/plugin-sdk";
import { startServer, stopServer } from "./server.js";
import {
  PROVIDER_ID,
  MODEL_IDS,
  DEFAULT_PORT,
  DEFAULT_CLAUDE_BINARY,
  DEFAULT_TIMEOUT_MS,
  DEFAULT_CONTEXT_WINDOW,
  DEFAULT_MAX_TOKENS,
  type ClaudeCodeProxyConfig,
} from "./types.js";

function buildModelDefinition(modelId: string) {
  return {
    id: modelId,
    name: modelId,
    api: "openai-completions" as const,
    reasoning: false,
    input: ["text"] as Array<"text">,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: DEFAULT_CONTEXT_WINDOW,
    maxTokens: DEFAULT_MAX_TOKENS,
  };
}

function resolveConfig(pluginConfig?: Record<string, unknown>): ClaudeCodeProxyConfig {
  return {
    port: (pluginConfig?.port as number) || DEFAULT_PORT,
    claudeBinaryPath: (pluginConfig?.claudeBinaryPath as string) || DEFAULT_CLAUDE_BINARY,
    timeoutMs: (pluginConfig?.timeoutMs as number) || DEFAULT_TIMEOUT_MS,
  };
}

const claudeCodeProxyPlugin = {
  id: PROVIDER_ID,
  name: "Claude Code Proxy",
  description: "Local Claude Code CLI proxy provider plugin",
  configSchema: emptyPluginConfigSchema(),

  register(api: OpenClawPluginApi) {
    const config = resolveConfig(api.pluginConfig);
    let server: Server | undefined;

    // Register the LLM provider
    api.registerProvider({
      id: PROVIDER_ID,
      label: "Claude Code Proxy",
      docsPath: "/providers/models",
      auth: [
        {
          id: "local",
          label: "Local CLI proxy",
          hint: "Uses the locally installed claude CLI with your Anthropic Max subscription",
          kind: "custom",
          run: async (ctx: ProviderAuthContext): Promise<ProviderAuthResult> => {
            const confirmed = await ctx.prompter.select({
              message:
                "This provider requires the Claude Code CLI to be installed and authenticated.\n" +
                "Install: npm install -g @anthropic-ai/claude-code\n" +
                "Auth: claude /login\n\n" +
                "Is the claude CLI installed and authenticated?",
              options: [
                { value: "yes", label: "Yes, continue" },
                { value: "no", label: "No, cancel setup" },
              ],
            });

            if (confirmed !== "yes") {
              throw new Error("Setup cancelled — install and authenticate the claude CLI first");
            }

            const baseUrl = `http://127.0.0.1:${config.port}/v1`;
            const defaultModelRef = `${PROVIDER_ID}/${MODEL_IDS[0]}`;

            return {
              profiles: [
                {
                  profileId: `${PROVIDER_ID}:local`,
                  credential: {
                    type: "token",
                    provider: PROVIDER_ID,
                    token: "n/a",
                  },
                },
              ],
              configPatch: {
                models: {
                  providers: {
                    [PROVIDER_ID]: {
                      baseUrl,
                      apiKey: "n/a",
                      api: "openai-completions",
                      authHeader: false,
                      models: MODEL_IDS.map((id) => buildModelDefinition(id)),
                    },
                  },
                },
                agents: {
                  defaults: {
                    models: Object.fromEntries(MODEL_IDS.map((id) => [`${PROVIDER_ID}/${id}`, {}])),
                  },
                },
              },
              defaultModel: defaultModelRef,
              notes: [
                "The claude-code-proxy server starts automatically with the gateway.",
                `Proxy endpoint: http://127.0.0.1:${config.port}/v1/chat/completions`,
                "The claude CLI handles authentication — no API key needed.",
                "Each request spawns a fresh claude -p process (stateless).",
              ],
            };
          },
        },
      ],
    });

    // Register the background service that runs the proxy server
    api.registerService({
      id: PROVIDER_ID,
      async start(ctx) {
        const svcConfig = resolveConfig(api.pluginConfig);
        try {
          server = await startServer(svcConfig, ctx.logger);
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          ctx.logger.error(`claude-code-proxy: failed to start — ${msg}`);
        }
      },
      async stop() {
        if (server) {
          await stopServer(server);
          server = undefined;
        }
      },
    });
  },
};

export default claudeCodeProxyPlugin;
