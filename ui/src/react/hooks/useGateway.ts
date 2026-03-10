import { useEffect, useCallback, useRef } from "react";
import {
  GatewayBrowserClient,
  type GatewayEventFrame,
  type GatewayHelloOk,
} from "../../ui/gateway.ts";
import { useGatewayStore } from "../stores/gateway.ts";
import { useAuthStore } from "../stores/auth.ts";
import { useChatStore } from "../stores/chat.ts";
import { useUiStore } from "../stores/ui.ts";
import { loadAuthToken, validateAuthToken } from "../../ui/auth-state.ts";
import { loadSettings } from "../../ui/storage.ts";
import { extractText } from "../../ui/chat/message-extract.ts";
import { loadControlUiBootstrapConfig } from "../../ui/controllers/control-ui-bootstrap.ts";

type PresenceEntry = { channel: string; status: string; lastSeen?: number };

type ExecApprovalEntry = {
  id: string;
  command: string;
  expiresAtMs: number;
};

type ChatEventPayload = {
  runId: string;
  sessionKey: string;
  state: "delta" | "final" | "aborted" | "error";
  message?: unknown;
  errorMessage?: string;
};

/**
 * Manages the gateway WebSocket connection lifecycle.
 * Creates a GatewayBrowserClient on mount using settings from localStorage,
 * wires events to the appropriate zustand stores, and tears down on unmount.
 */
export function useGateway() {
  const clientRef = useRef<GatewayBrowserClient | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function init() {
      const auth = useAuthStore.getState();
      const ui = useUiStore.getState();

      // Load bootstrap config to determine auth mode and assistant identity
      const bootstrap = await loadControlUiBootstrapConfig({
        basePath: "",
        assistantName: ui.assistantName,
        assistantAvatar: ui.assistantAvatar,
        assistantAgentId: ui.assistantAgentId,
      });

      if (cancelled) return;

      if (bootstrap.authMode === "multi-user") {
        // Multi-user: check existing session token
        const sessionToken = loadAuthToken();
        if (sessionToken) {
          const user = await validateAuthToken(sessionToken, "");
          if (cancelled) return;
          if (user) {
            auth.setAuthUser(user);
            auth.setAuthState("authenticated");
          } else {
            auth.setAuthState("unauthenticated");
            return; // Don't connect until authenticated
          }
        } else {
          auth.setAuthState("unauthenticated");
          return;
        }
      } else {
        // Single-user mode: no auth required
        auth.setAuthState("not-required");
      }

      // Connect to gateway
      const settings = loadSettings();
      const url = settings.gatewayUrl;
      if (!url) return;

      const token = settings.token || undefined;
      const sessionToken = loadAuthToken() || undefined;

      const client = new GatewayBrowserClient({
        url,
        token,
        sessionToken,

        onHello: (hello: GatewayHelloOk) => {
          const gw = useGatewayStore.getState();
          gw.setConnected(true);
          gw.setHello(hello);
          gw.setError(null);

          // Load initial chat history after connecting
          loadChatHistoryViaClient(client);
        },

        onClose: (info) => {
          const gw = useGatewayStore.getState();
          gw.setConnected(false);
          // Code 1012 = service restart, not a real error
          if (info.code !== 1012 && info.error) {
            gw.setError(info.error.message, info.error.code);
          }
        },

        onEvent: (evt: GatewayEventFrame) => {
          switch (evt.event) {
            case "chat": {
              handleChatPayload(evt.payload as ChatEventPayload | undefined);
              break;
            }

            case "presence": {
              const payload = evt.payload as { presence?: PresenceEntry[] } | undefined;
              if (payload?.presence && Array.isArray(payload.presence)) {
                useGatewayStore.getState().addEvent({
                  ts: Date.now(),
                  type: "presence",
                  detail: JSON.stringify(payload.presence),
                });
              }
              break;
            }

            case "exec.approval.requested": {
              const entry = evt.payload as ExecApprovalEntry | undefined;
              if (entry?.id) {
                useGatewayStore.getState().addEvent({
                  ts: Date.now(),
                  type: "exec.approval.requested",
                  detail: JSON.stringify(entry),
                });
              }
              break;
            }

            case "exec.approval.resolved": {
              const resolved = evt.payload as { id?: string } | undefined;
              if (resolved?.id) {
                useGatewayStore.getState().addEvent({
                  ts: Date.now(),
                  type: "exec.approval.resolved",
                  detail: resolved.id,
                });
              }
              break;
            }

            default: {
              useGatewayStore.getState().addEvent({
                ts: Date.now(),
                type: evt.event,
                detail: typeof evt.payload === "string"
                  ? evt.payload
                  : JSON.stringify(evt.payload ?? ""),
              });
              break;
            }
          }
        },

        onGap: (info) => {
          useGatewayStore.getState().setError(
            `Event gap detected: expected seq ${info.expected}, got ${info.received}. Some events may have been missed.`,
          );
        },
      });

      client.start();
      clientRef.current = client;
      useGatewayStore.getState().setClient(client);
    }

    init();

    return () => {
      cancelled = true;
      if (clientRef.current) {
        clientRef.current.stop();
        clientRef.current = null;
        useGatewayStore.getState().setClient(null);
        useGatewayStore.getState().setConnected(false);
      }
    };
  }, []);

  const connected = useGatewayStore((s) => s.connected);
  return { client: clientRef.current, connected };
}

/**
 * Handle an incoming "chat" event payload, dispatching to chat store.
 * Mirrors the logic in src/ui/controllers/chat.ts handleChatEvent.
 */
function handleChatPayload(payload: ChatEventPayload | undefined) {
  if (!payload) return;

  const chat = useChatStore.getState();
  const sessionKey = useUiStore.getState().sessionKey;

  // Ignore events for a different session
  if (payload.sessionKey !== sessionKey) return;

  if (payload.state === "delta") {
    const next = extractText(payload.message);
    if (typeof next === "string") {
      const current = chat.stream ?? "";
      if (!current) {
        // First chunk - mark stream start
        chat.setStreamStartedAt(Date.now());
      }
      if (!current || next.length >= current.length) {
        chat.setStream(next);
      }
    }
  } else if (payload.state === "final") {
    // Append the final message
    if (payload.message) {
      chat.appendMessage(payload.message);
    } else {
      // Fallback: use accumulated stream text
      const streamedText = chat.stream ?? "";
      if (streamedText.trim()) {
        chat.appendMessage({
          role: "assistant",
          content: [{ type: "text", text: streamedText }],
          timestamp: Date.now(),
        });
      }
    }
    chat.setStream(null);
    chat.setRunId(null);
    chat.setStreamStartedAt(null);
  } else if (payload.state === "aborted") {
    // Preserve any streamed text as a message
    const streamedText = chat.stream ?? "";
    if (payload.message) {
      chat.appendMessage(payload.message);
    } else if (streamedText.trim()) {
      chat.appendMessage({
        role: "assistant",
        content: [{ type: "text", text: streamedText }],
        timestamp: Date.now(),
      });
    }
    chat.setStream(null);
    chat.setRunId(null);
    chat.setStreamStartedAt(null);
  } else if (payload.state === "error") {
    chat.addDebugEntry({
      ts: Date.now(),
      level: "error",
      message: payload.errorMessage ?? "chat error",
    });
    chat.setStream(null);
    chat.setRunId(null);
    chat.setStreamStartedAt(null);
  }
}

/**
 * Load chat history by calling the gateway's "chat.history" method.
 */
function loadChatHistoryViaClient(client: GatewayBrowserClient) {
  const sessionKey = useUiStore.getState().sessionKey;
  const chat = useChatStore.getState();
  chat.setLoading(true);

  client
    .request("chat.history", { sessionKey })
    .then((res) => {
      const data = res as { messages?: unknown[] } | undefined;
      if (data?.messages && Array.isArray(data.messages)) {
        useChatStore.getState().setMessages(data.messages);
      }
    })
    .catch((err) => {
      console.warn("[useGateway] Failed to load chat history:", err);
    })
    .finally(() => {
      useChatStore.getState().setLoading(false);
    });
}

/**
 * Actions hook for imperative gateway operations.
 */
export function useGatewayActions() {
  const reconnect = useCallback(() => {
    const gw = useGatewayStore.getState();
    const currentClient = gw.client as GatewayBrowserClient | null;
    if (currentClient) {
      currentClient.stop();
    }
    // Re-mount will be handled by the component lifecycle;
    // for imperative reconnect, create a fresh client.
    const settings = loadSettings();
    const url = settings.gatewayUrl;
    if (!url) return;

    const token = settings.token || undefined;
    const sessionToken = loadAuthToken() || undefined;

    const client = new GatewayBrowserClient({
      url,
      token,
      sessionToken,

      onHello: (hello: GatewayHelloOk) => {
        const store = useGatewayStore.getState();
        store.setConnected(true);
        store.setHello(hello);
        store.setError(null);
        loadChatHistoryViaClient(client);
      },

      onClose: (info) => {
        useGatewayStore.getState().setConnected(false);
        if (info.code !== 1012 && info.error) {
          useGatewayStore.getState().setError(info.error.message, info.error.code);
        }
      },

      onEvent: (evt: GatewayEventFrame) => {
        if (evt.event === "chat") {
          handleChatPayload(evt.payload as ChatEventPayload | undefined);
        } else {
          useGatewayStore.getState().addEvent({
            ts: Date.now(),
            type: evt.event,
            detail: typeof evt.payload === "string"
              ? evt.payload
              : JSON.stringify(evt.payload ?? ""),
          });
        }
      },

      onGap: (info) => {
        useGatewayStore.getState().setError(
          `Event gap: expected ${info.expected}, got ${info.received}`,
        );
      },
    });

    client.start();
    gw.setClient(client);
  }, []);

  const loadChatHistory = useCallback(() => {
    const client = useGatewayStore.getState().client as GatewayBrowserClient | null;
    if (client) {
      loadChatHistoryViaClient(client);
    }
  }, []);

  return { reconnect, loadChatHistory };
}

export default useGateway;
