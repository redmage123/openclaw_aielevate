import type { Server } from "node:http";
import { writeFileSync, mkdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { homedir } from "node:os";
import { fileURLToPath } from "node:url";
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

function resolveAuthToken(
  gatewayConfig?: Record<string, unknown>,
  pluginConfig?: Record<string, unknown>,
): string | undefined {
  // Priority: gateway auth token > env var > explicit plugin config
  const gwAuth = (gatewayConfig as { auth?: { token?: string } } | undefined)?.auth?.token;
  if (gwAuth) return gwAuth;

  const envToken = process.env.OPENCLAW_GATEWAY_TOKEN;
  if (envToken) return envToken;

  const explicit = pluginConfig?.authToken as string | undefined;
  if (explicit) return explicit;

  return undefined;
}

function asStringArray(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const filtered = value.filter((v): v is string => typeof v === "string" && v.length > 0);
  return filtered.length > 0 ? filtered : undefined;
}

function resolveConfig(
  pluginConfig?: Record<string, unknown>,
  gatewayConfig?: Record<string, unknown>,
): ClaudeCodeProxyConfig {
  return {
    port: (pluginConfig?.port as number) || DEFAULT_PORT,
    claudeBinaryPath: (pluginConfig?.claudeBinaryPath as string) || DEFAULT_CLAUDE_BINARY,
    timeoutMs: (pluginConfig?.timeoutMs as number) || DEFAULT_TIMEOUT_MS,
    authToken: resolveAuthToken(gatewayConfig, pluginConfig),
    // CLI passthrough options
    mcpConfig: asStringArray(pluginConfig?.mcpConfig),
    allowedTools: asStringArray(pluginConfig?.allowedTools),
    disallowedTools: asStringArray(pluginConfig?.disallowedTools),
    permissionMode: (pluginConfig?.permissionMode as string) || undefined,
    pluginDirs: asStringArray(pluginConfig?.pluginDirs),
    addDirs: asStringArray(pluginConfig?.addDirs),
    appendSystemPrompt: (pluginConfig?.appendSystemPrompt as string) || undefined,
    maxBudgetUsd: (pluginConfig?.maxBudgetUsd as number) || undefined,
  };
}

/**
 * Generate an MCP config file for the OpenClaw bridge server.
 * This allows Claude Code sessions to access OpenClaw's multi-agent tools
 * (sessions_send, sessions_spawn, agents_list) via MCP.
 */
function generateMcpBridgeConfig(authToken: string | undefined): string | undefined {
  const pluginDir = dirname(fileURLToPath(import.meta.url));
  const bridgeScript = join(pluginDir, "mcp-bridge.ts");
  const configPath = join(homedir(), ".openclaw", "mcp-bridge-config.json");

  try {
    const config = {
      mcpServers: {
        "openclaw-bridge": {
          command: "npx",
          args: ["tsx", bridgeScript],
          env: {
            OPENCLAW_GATEWAY_URL: "http://127.0.0.1:18789",
            ...(authToken ? { OPENCLAW_GATEWAY_TOKEN: authToken } : {}),
            // OPENCLAW_AGENT_ID will be set by the caller if needed
          },
        },
      },
    };

    mkdirSync(dirname(configPath), { recursive: true });
    writeFileSync(configPath, JSON.stringify(config, null, 2));
    return configPath;
  } catch {
    return undefined;
  }
}

const claudeCodeProxyPlugin = {
  id: PROVIDER_ID,
  name: "Claude Code Proxy",
  description: "Local Claude Code CLI proxy provider plugin",
  configSchema: emptyPluginConfigSchema(),

  register(api: OpenClawPluginApi) {
    const config = resolveConfig(api.pluginConfig, api.config.gateway);
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

            const credentialToken = config.authToken || "n/a";

            return {
              profiles: [
                {
                  profileId: `${PROVIDER_ID}:local`,
                  credential: {
                    type: "token",
                    provider: PROVIDER_ID,
                    token: credentialToken,
                  },
                },
              ],
              configPatch: {
                models: {
                  providers: {
                    [PROVIDER_ID]: {
                      baseUrl,
                      apiKey: credentialToken,
                      api: "openai-completions",
                      authHeader: config.authToken ? true : false,
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
                config.authToken
                  ? `Proxy endpoint: http://127.0.0.1:${config.port}/v1/chat/completions?token=${config.authToken}`
                  : `Proxy endpoint: http://127.0.0.1:${config.port}/v1/chat/completions`,
                "The claude CLI handles authentication — no API key needed.",
                "Each request spawns a fresh claude -p process (stateless).",
                config.authToken
                  ? "Auth: pass token via ?token= query param or Authorization: Bearer header."
                  : "No auth token configured — proxy accepts all requests.",
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
        const svcConfig = resolveConfig(api.pluginConfig, api.config.gateway);

        // Generate MCP bridge config for multi-agent tool access
        const mcpBridgePath = generateMcpBridgeConfig(svcConfig.authToken);
        if (mcpBridgePath) {
          // Inject the bridge MCP config so Claude Code sessions get multi-agent tools
          if (!svcConfig.mcpConfig) svcConfig.mcpConfig = [];
          if (!svcConfig.mcpConfig.includes(mcpBridgePath)) {
            svcConfig.mcpConfig.push(mcpBridgePath);
          }
          ctx.logger.info(`claude-code-proxy: MCP bridge config at ${mcpBridgePath}`);
        }

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
