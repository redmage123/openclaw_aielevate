#!/usr/bin/env node
/**
 * MCP Bridge Server — Exposes OpenClaw multi-agent tools to Claude Code sessions.
 *
 * This stdio-based MCP server provides `sessions_send`, `sessions_spawn`, and
 * `agents_list` tools. It talks to the OpenClaw gateway via HTTP.
 *
 * Environment variables:
 *   OPENCLAW_GATEWAY_URL   — Gateway base URL (default: http://127.0.0.1:18789)
 *   OPENCLAW_GATEWAY_TOKEN — Auth token for the gateway
 *   OPENCLAW_AGENT_ID      — The calling agent's ID (for session key context)
 */

import { createInterface } from "node:readline";
import http from "node:http";

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const GATEWAY_BASE = process.env.OPENCLAW_GATEWAY_URL || "http://127.0.0.1:18789";
const GATEWAY_TOKEN = process.env.OPENCLAW_GATEWAY_TOKEN || "";
const AGENT_ID = process.env.OPENCLAW_AGENT_ID || "unknown";

// ---------------------------------------------------------------------------
// HTTP helper — POST JSON to gateway
// ---------------------------------------------------------------------------

function httpPost(path: string, body: Record<string, unknown>): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    const url = new URL(path, GATEWAY_BASE);
    const data = JSON.stringify(body);

    const req = http.request(
      {
        hostname: url.hostname,
        port: url.port || 18789,
        path: url.pathname,
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(data),
          ...(GATEWAY_TOKEN ? { Authorization: `Bearer ${GATEWAY_TOKEN}` } : {}),
        },
        timeout: 120_000,
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (chunk: Buffer) => chunks.push(chunk));
        res.on("end", () => {
          const text = Buffer.concat(chunks).toString("utf-8");
          try {
            resolve(JSON.parse(text) as Record<string, unknown>);
          } catch {
            reject(new Error(`Invalid JSON from gateway: ${text.slice(0, 500)}`));
          }
        });
      },
    );

    req.on("error", (err) => reject(new Error(`Gateway HTTP error: ${err.message}`)));
    req.on("timeout", () => {
      req.destroy();
      reject(new Error("Gateway request timed out"));
    });
    req.write(data);
    req.end();
  });
}

// ---------------------------------------------------------------------------
// Gateway tool invocation via /tools/invoke HTTP endpoint
// ---------------------------------------------------------------------------

async function invokeGatewayTool(
  tool: string,
  action: string | undefined,
  args: Record<string, unknown>,
  sessionKey?: string,
): Promise<Record<string, unknown>> {
  const body: Record<string, unknown> = { tool, args };
  if (action) body.action = action;
  if (sessionKey) body.sessionKey = sessionKey;

  const result = await httpPost("/tools/invoke", body);
  if (result.error) {
    throw new Error(
      typeof result.error === "string"
        ? result.error
        : (result.error as { message?: string }).message || JSON.stringify(result.error),
    );
  }
  return result;
}

// ---------------------------------------------------------------------------
// MCP Protocol — JSON-RPC 2.0 over stdio
// ---------------------------------------------------------------------------

type JsonRpcRequest = {
  jsonrpc: "2.0";
  id?: string | number;
  method: string;
  params?: Record<string, unknown>;
};

type JsonRpcResponse = {
  jsonrpc: "2.0";
  id: string | number | null;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
};

function sendResponse(response: JsonRpcResponse): void {
  process.stdout.write(JSON.stringify(response) + "\n");
}

// ---------------------------------------------------------------------------
// Tool definitions
// ---------------------------------------------------------------------------

const TOOLS = [
  {
    name: "sessions_send",
    description:
      "Send a message to another agent and get their response. " +
      "Use this for inter-agent communication and collaboration. " +
      "You MUST set asAgentId to your own agent ID (e.g. 'techuni-ceo').",
    inputSchema: {
      type: "object" as const,
      properties: {
        asAgentId: {
          type: "string",
          description: "YOUR agent ID (e.g. 'techuni-ceo'). Required for authorization.",
        },
        agentId: {
          type: "string",
          description: "Target agent ID (e.g. 'techuni-marketing', 'techuni-engineering')",
        },
        message: {
          type: "string",
          description: "The message to send",
        },
        label: {
          type: "string",
          description: "Optional human-readable label for the session",
        },
        timeoutSeconds: {
          type: "number",
          description: "How long to wait for reply (default: 60)",
        },
      },
      required: ["asAgentId", "message", "agentId"],
    },
  },
  {
    name: "sessions_spawn",
    description:
      "Spawn a sub-agent to complete a task independently. " +
      "The result is delivered back automatically when done. " +
      "You MUST set asAgentId to your own agent ID (e.g. 'techuni-ceo').",
    inputSchema: {
      type: "object" as const,
      properties: {
        asAgentId: {
          type: "string",
          description: "YOUR agent ID (e.g. 'techuni-ceo'). Required for authorization.",
        },
        task: {
          type: "string",
          description: "Task description for the sub-agent",
        },
        agentId: {
          type: "string",
          description: "Agent ID to spawn (e.g. 'techuni-engineering')",
        },
        label: {
          type: "string",
          description: "Optional label for the spawned session",
        },
        mode: {
          type: "string",
          enum: ["run", "session"],
          description: '"run" for one-shot (default), "session" for persistent',
        },
      },
      required: ["asAgentId", "task", "agentId"],
    },
  },
  {
    name: "agents_list",
    description:
      "List all agents you can communicate with via sessions_send/sessions_spawn. " +
      "You MUST set asAgentId to your own agent ID (e.g. 'techuni-ceo').",
    inputSchema: {
      type: "object" as const,
      properties: {
        asAgentId: {
          type: "string",
          description: "YOUR agent ID (e.g. 'techuni-ceo'). Required for authorization.",
        },
      },
      required: ["asAgentId"],
    },
  },
];

// ---------------------------------------------------------------------------
// Tool execution
// ---------------------------------------------------------------------------

async function executeTool(name: string, args: Record<string, unknown>): Promise<string> {
  // Determine the calling agent's identity:
  // 1. From tool args (asAgentId) — allows the LLM to specify who it is
  // 2. From env var OPENCLAW_AGENT_ID
  // 3. Fall back to "main"
  const callerAgentId =
    (typeof args.asAgentId === "string" && args.asAgentId.trim()) ||
    (AGENT_ID !== "unknown" ? AGENT_ID : null) ||
    "main";
  const sessionKey = `agent:${callerAgentId}:mcp-bridge`;

  switch (name) {
    case "agents_list": {
      const result = await invokeGatewayTool("agents_list", undefined, {}, sessionKey);
      const data = result.result ?? result.data ?? result;
      return typeof data === "string" ? data : JSON.stringify(data, null, 2);
    }

    case "sessions_send": {
      // sessions_send requires sessionKey (not just agentId) to route the message.
      // Construct target session key: agent:<targetAgentId>:a2a-from-<callerAgentId>
      const targetAgentId = typeof args.agentId === "string" ? args.agentId.trim() : "";
      const targetSessionKey = targetAgentId
        ? `agent:${targetAgentId}:a2a-from-${callerAgentId}`
        : undefined;
      const result = await invokeGatewayTool(
        "sessions_send",
        undefined,
        {
          ...(targetSessionKey ? { sessionKey: targetSessionKey } : {}),
          message: args.message,
          ...(args.label ? { label: args.label } : {}),
          ...(args.timeoutSeconds ? { timeoutSeconds: args.timeoutSeconds } : {}),
        },
        sessionKey,
      );
      const data = result.result ?? result.data ?? result;
      return typeof data === "string" ? data : JSON.stringify(data, null, 2);
    }

    case "sessions_spawn": {
      const result = await invokeGatewayTool(
        "sessions_spawn",
        undefined,
        {
          task: args.task,
          agentId: args.agentId,
          ...(args.label ? { label: args.label } : {}),
          ...(args.mode ? { mode: args.mode } : {}),
        },
        sessionKey,
      );
      const data = result.result ?? result.data ?? result;
      return typeof data === "string" ? data : JSON.stringify(data, null, 2);
    }

    default:
      throw new Error(`Unknown tool: ${name}`);
  }
}

// ---------------------------------------------------------------------------
// MCP message handler
// ---------------------------------------------------------------------------

async function handleMessage(request: JsonRpcRequest): Promise<void> {
  const { id, method, params } = request;

  switch (method) {
    case "initialize":
      sendResponse({
        jsonrpc: "2.0",
        id: id ?? null,
        result: {
          protocolVersion: "2024-11-05",
          capabilities: { tools: {} },
          serverInfo: { name: "openclaw-mcp-bridge", version: "1.0.0" },
        },
      });
      break;

    case "notifications/initialized":
      // Ready
      break;

    case "tools/list":
      sendResponse({
        jsonrpc: "2.0",
        id: id ?? null,
        result: { tools: TOOLS },
      });
      break;

    case "tools/call": {
      const toolName = (params?.name as string) || "";
      const toolArgs = (params?.arguments as Record<string, unknown>) || {};

      try {
        const text = await executeTool(toolName, toolArgs);
        sendResponse({
          jsonrpc: "2.0",
          id: id ?? null,
          result: { content: [{ type: "text", text }] },
        });
      } catch (err) {
        sendResponse({
          jsonrpc: "2.0",
          id: id ?? null,
          result: {
            content: [{ type: "text", text: `Error: ${err instanceof Error ? err.message : err}` }],
            isError: true,
          },
        });
      }
      break;
    }

    default:
      if (id != null) {
        sendResponse({
          jsonrpc: "2.0",
          id,
          error: { code: -32601, message: `Method not found: ${method}` },
        });
      }
  }
}

// ---------------------------------------------------------------------------
// Main — JSON-RPC over stdio
// ---------------------------------------------------------------------------

const rl = createInterface({ input: process.stdin });

rl.on("line", (line) => {
  const trimmed = line.trim();
  if (!trimmed) return;

  try {
    const request = JSON.parse(trimmed) as JsonRpcRequest;
    handleMessage(request).catch((err) => {
      process.stderr.write(`[mcp-bridge] Error: ${err}\n`);
      if (request.id != null) {
        sendResponse({
          jsonrpc: "2.0",
          id: request.id,
          error: { code: -32603, message: String(err) },
        });
      }
    });
  } catch {
    process.stderr.write(`[mcp-bridge] Parse error: ${line.slice(0, 200)}\n`);
  }
});

rl.on("close", () => process.exit(0));

process.stderr.write(
  `[mcp-bridge] OpenClaw MCP Bridge (gateway: ${GATEWAY_BASE}, agent: ${AGENT_ID})\n`,
);
