import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { useCopilotStore } from "../stores/copilot.ts";
import { useGatewayStore } from "../stores/gateway.ts";
import { useAuthStore } from "../stores/auth.ts";
import { useUiStore } from "../stores/ui.ts";
import { toSanitizedMarkdownHtml } from "../../ui/markdown.ts";
import { Icons } from "../icons.tsx";
import type { GatewayBrowserClient } from "../../ui/gateway.ts";

const COPILOT_SESSION_KEY = "__copilot__";

/** Map route paths to human-friendly page names. */
function pageNameFromPath(pathname: string): string {
  const seg = pathname.replace(/^\/+/, "").split("/")[0] || "chat";
  const names: Record<string, string> = {
    chat: "Chat",
    sessions: "Sessions",
    channels: "Channels",
    config: "Configuration",
    agents: "Agents",
    skills: "Skills",
    cron: "Scheduled Tasks",
    debug: "Debug",
    logs: "Logs",
    nodes: "Nodes",
    presence: "Presence",
  };
  return names[seg] ?? seg.charAt(0).toUpperCase() + seg.slice(1);
}

/** Build a system context string describing the user's current state. */
function buildContext(opts: {
  page: string;
  connected: boolean;
  userName: string | null;
  version: string | null;
}): string {
  const lines = [
    "You are the AI Elevate assistant embedded in the control panel.",
    "Be concise and helpful. You can help the user navigate the UI, explain features, troubleshoot issues, and answer questions about AI Elevate.",
    `The user is currently on the "${opts.page}" page.`,
    opts.connected
      ? "The gateway is connected and operational."
      : "The gateway is NOT connected — the user may need help reconnecting.",
  ];
  if (opts.userName) lines.push(`The user's name is ${opts.userName}.`);
  if (opts.version) lines.push(`Gateway version: ${opts.version}.`);
  return lines.join(" ");
}

/** Greeting message shown on first open. */
function greetingMessage(userName: string | null, page: string): string {
  const name = userName ? `, ${userName}` : "";
  const lines = [
    `Hi${name}! I'm your AI Elevate assistant.`,
    "",
    `You're on the **${page}** page. I can help you with:`,
    "- Navigating and understanding the control panel",
    "- Configuring channels, agents, and skills",
    "- Troubleshooting connection or gateway issues",
    "- Explaining features and how things work",
    "",
    "Just ask me anything!",
  ];
  return lines.join("\n");
}

export default function AICopilot() {
  const open = useCopilotStore((s) => s.open);
  const minimized = useCopilotStore((s) => s.minimized);
  const messages = useCopilotStore((s) => s.messages);
  const input = useCopilotStore((s) => s.input);
  const sending = useCopilotStore((s) => s.sending);
  const stream = useCopilotStore((s) => s.stream);
  const greeted = useCopilotStore((s) => s.greeted);

  const toggle = useCopilotStore((s) => s.toggle);
  const setOpen = useCopilotStore((s) => s.setOpen);
  const setMinimized = useCopilotStore((s) => s.setMinimized);
  const setInput = useCopilotStore((s) => s.setInput);
  const setSending = useCopilotStore((s) => s.setSending);
  const setStream = useCopilotStore((s) => s.setStream);
  const addMessage = useCopilotStore((s) => s.addMessage);
  const setGreeted = useCopilotStore((s) => s.setGreeted);
  const setContextPage = useCopilotStore((s) => s.setContextPage);

  const connected = useGatewayStore((s) => s.connected);
  const hello = useGatewayStore((s) => s.hello);
  const client = useGatewayStore((s) => s.client) as GatewayBrowserClient | null;
  const authUser = useAuthStore((s) => s.authUser);
  const authState = useAuthStore((s) => s.authState);

  const location = useLocation();
  const page = pageNameFromPath(location.pathname);

  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Track page changes
  useEffect(() => {
    setContextPage(page);
  }, [page, setContextPage]);

  // Auto-greet on first open
  useEffect(() => {
    if (open && !greeted && connected) {
      const userName = authUser?.displayName || authUser?.username || null;
      addMessage({
        id: crypto.randomUUID(),
        role: "assistant",
        content: greetingMessage(userName, page),
        timestamp: Date.now(),
      });
      setGreeted(true);
    }
  }, [open, greeted, connected, authUser, page, addMessage, setGreeted]);

  // Auto-scroll
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, stream]);

  // Focus textarea when opened
  useEffect(() => {
    if (open && !minimized) {
      setTimeout(() => textareaRef.current?.focus(), 100);
    }
  }, [open, minimized]);

  const handleSend = useCallback(async (overrideText?: string) => {
    const text = (overrideText ?? input).trim();
    if (!text || sending || !client) return;

    setInput("");
    setSending(true);

    const userMsg = {
      id: crypto.randomUUID(),
      role: "user" as const,
      content: text,
      timestamp: Date.now(),
    };
    addMessage(userMsg);

    try {
      const userName = authUser?.displayName || authUser?.username || null;
      const version = hello?.server?.version ?? null;
      const systemContext = buildContext({ page, connected, userName, version });

      // Send via gateway chat.send with the copilot session key
      const result = await client.request("chat.send", {
        sessionKey: COPILOT_SESSION_KEY,
        message: text,
        systemPrompt: systemContext,
      });

      const data = result as { runId?: string } | undefined;

      // The response will come via chat events, but for copilot we
      // do a simpler request-response via chat.send's result.
      // If the gateway supports synchronous responses, use that.
      // Otherwise we'll listen for events on the copilot session.
      if (data?.runId) {
        // Wait for the response via polling the session history
        await pollForResponse(client, data.runId, messages.length + 1);
      }
    } catch (err) {
      addMessage({
        id: crypto.randomUUID(),
        role: "assistant",
        content: "Sorry, I couldn't process that request. Please check that the gateway is connected and try again.",
        timestamp: Date.now(),
      });
    } finally {
      setSending(false);
      setStream(null);
    }
  }, [input, sending, client, authUser, hello, page, connected, messages.length, setInput, setSending, addMessage, setStream]);

  /** Poll chat history for the copilot session to get the assistant's response. */
  const pollForResponse = useCallback(
    async (gwClient: GatewayBrowserClient, runId: string, expectedCount: number) => {
      const maxAttempts = 60; // 30 seconds max
      for (let i = 0; i < maxAttempts; i++) {
        await new Promise((r) => setTimeout(r, 500));
        try {
          const res = await gwClient.request("chat.history", {
            sessionKey: COPILOT_SESSION_KEY,
          });
          const data = res as { messages?: Array<{ role: string; content: unknown }> } | undefined;
          if (data?.messages && data.messages.length > expectedCount) {
            // Find the last assistant message
            const last = data.messages[data.messages.length - 1];
            if (last && last.role === "assistant") {
              const text = extractMessageText(last.content);
              addMessage({
                id: crypto.randomUUID(),
                role: "assistant",
                content: text,
                timestamp: Date.now(),
              });
              return;
            }
          }
        } catch {
          // Retry on error
        }
      }
      // Timeout fallback
      addMessage({
        id: crypto.randomUUID(),
        role: "assistant",
        content: "The response took too long. Please try again.",
        timestamp: Date.now(),
      });
    },
    [addMessage],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  // Quick action buttons based on context
  const quickActions = useMemo(() => {
    const actions: Array<{ label: string; prompt: string }> = [];
    if (!connected) {
      actions.push({ label: "Help me connect", prompt: "How do I connect to the gateway?" });
    }
    switch (page) {
      case "Chat":
        actions.push({ label: "How to chat?", prompt: "How does the chat feature work?" });
        break;
      case "Channels":
        actions.push({ label: "Add a channel", prompt: "How do I add a new messaging channel?" });
        break;
      case "Configuration":
        actions.push({ label: "Explain config", prompt: "What are the important configuration options?" });
        break;
      case "Agents":
        actions.push({ label: "What are agents?", prompt: "What are agents and how do they work?" });
        break;
      case "Skills":
        actions.push({ label: "What are skills?", prompt: "What are skills and how do I use them?" });
        break;
    }
    return actions;
  }, [page, connected]);

  const handleQuickAction = useCallback(
    (prompt: string) => {
      handleSend(prompt);
    },
    [handleSend],
  );

  // Don't render if not authenticated
  if (authState !== "authenticated" && authState !== "not-required") return null;

  return (
    <>
      {/* Floating toggle button */}
      {!open && (
        <button
          className="copilot-fab"
          onClick={toggle}
          title="Open AI Assistant"
          aria-label="Open AI Assistant"
        >
          {Icons.messageSquare({ width: "1.4em", height: "1.4em" })}
        </button>
      )}

      {/* Copilot panel */}
      {open && (
        <div className={`copilot-panel ${minimized ? "copilot-panel--minimized" : ""}`}>
          {/* Header */}
          <div className="copilot-header">
            <div className="copilot-header__left">
              <span className="copilot-header__icon">
                {Icons.messageSquare({ width: "1em", height: "1em" })}
              </span>
              <span className="copilot-header__title">AI Assistant</span>
              <span className={`statusDot ${connected ? "ok" : ""}`} />
            </div>
            <div className="copilot-header__actions">
              <button
                className="copilot-header__btn"
                onClick={() => setMinimized(!minimized)}
                title={minimized ? "Expand" : "Minimize"}
              >
                {minimized ? Icons.chevronUp() : Icons.chevronDown()}
              </button>
              <button
                className="copilot-header__btn"
                onClick={() => setOpen(false)}
                title="Close"
              >
                {Icons.x()}
              </button>
            </div>
          </div>

          {!minimized && (
            <>
              {/* Messages */}
              <div className="copilot-messages">
                {messages.map((msg) => (
                  <CopilotBubble key={msg.id} message={msg} />
                ))}

                {/* Streaming indicator */}
                {sending && !stream && (
                  <div className="copilot-bubble copilot-bubble--assistant">
                    <div className="copilot-typing">
                      <span /><span /><span />
                    </div>
                  </div>
                )}

                {stream && (
                  <div className="copilot-bubble copilot-bubble--assistant">
                    <CopilotMarkdown text={stream} />
                  </div>
                )}

                <div ref={bottomRef} />
              </div>

              {/* Quick actions (when no messages or few) */}
              {messages.length <= 1 && quickActions.length > 0 && (
                <div className="copilot-quick-actions">
                  {quickActions.map((action) => (
                    <button
                      key={action.label}
                      className="copilot-quick-btn"
                      onClick={() => handleQuickAction(action.prompt)}
                      disabled={sending}
                    >
                      {action.label}
                    </button>
                  ))}
                </div>
              )}

              {/* Input */}
              <div className="copilot-input-row">
                <textarea
                  ref={textareaRef}
                  className="copilot-input"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder={connected ? "Ask me anything..." : "Gateway not connected..."}
                  rows={1}
                  disabled={!connected || sending}
                />
                <button
                  className="copilot-send-btn"
                  onClick={handleSend}
                  disabled={!connected || sending || !input.trim()}
                  title="Send"
                >
                  {Icons.send({ width: "1em", height: "1em" })}
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </>
  );
}

/** Render a single copilot message bubble. */
function CopilotBubble({ message }: { message: { role: string; content: string } }) {
  const isUser = message.role === "user";
  return (
    <div className={`copilot-bubble copilot-bubble--${isUser ? "user" : "assistant"}`}>
      {isUser ? (
        <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{message.content}</p>
      ) : (
        <CopilotMarkdown text={message.content} />
      )}
    </div>
  );
}

/** Render markdown for assistant messages. */
function CopilotMarkdown({ text }: { text: string }) {
  const html = useMemo(() => toSanitizedMarkdownHtml(text), [text]);
  return (
    <div
      className="markdown-body copilot-md"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

/** Extract plain text from a message content field. */
function extractMessageText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .filter((b: { type?: string }) => b.type === "text")
      .map((b: { text?: string }) => b.text ?? "")
      .join("\n");
  }
  return String(content ?? "");
}
