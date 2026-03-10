import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useGatewayStore } from "../stores/gateway.ts";
import { useAgentsStore } from "../stores/agents.ts";
import { toSanitizedMarkdownHtml } from "../../ui/markdown.ts";
import { Icons } from "../icons.tsx";
import type { GatewayBrowserClient } from "../../ui/gateway.ts";
import { type AgentEntry, AGENT_GRADIENTS, initials, parseAgentsList, findOrgForAgent } from "./org-shared.ts";

type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: number;
};

function extractTextContent(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .filter((b: { type?: string }) => b.type === "text")
      .map((b: { text?: string }) => b.text ?? "")
      .join("\n");
  }
  return String(content ?? "");
}

function formatTime(ts: number): string {
  return new Date(ts).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export default function OrgChatPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const connected = useGatewayStore((s) => s.connected);
  const client = useGatewayStore((s) => s.client) as GatewayBrowserClient | null;
  const agentsList = useAgentsStore((s) => s.agentsList);
  const loadAgents = useAgentsStore((s) => s.load);

  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(true);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const agents = parseAgentsList(agentsList);
  const agent = agents.find((a) => a.id === agentId);
  const agentIndex = agents.findIndex((a) => a.id === agentId);
  const gradient = AGENT_GRADIENTS[(agentIndex >= 0 ? agentIndex : 0) % AGENT_GRADIENTS.length];
  const sessionKey = `agent:${agentId}:webchat`;

  // Load agents if not loaded yet
  useEffect(() => {
    if (connected && agents.length === 0) loadAgents();
  }, [connected, agents.length, loadAgents]);

  // Load chat history for this agent session
  useEffect(() => {
    if (!connected || !client || !agentId) return;
    setLoading(true);
    client
      .request("chat.history", { sessionKey })
      .then((res) => {
        const data = res as { messages?: Array<{ role: string; content: unknown; timestamp?: number }> } | undefined;
        if (data?.messages) {
          setMessages(
            data.messages.map((m, i) => ({
              id: `hist-${i}`,
              role: m.role as "user" | "assistant",
              content: extractTextContent(m.content),
              timestamp: (m.timestamp as number) ?? Date.now(),
            })),
          );
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [connected, client, agentId, sessionKey]);

  // Auto-scroll
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  // Focus textarea
  useEffect(() => {
    textareaRef.current?.focus();
  }, [loading]);

  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || sending || !client) return;

    setInput("");
    setSending(true);

    const userMsg: Message = {
      id: crypto.randomUUID(),
      role: "user",
      content: text,
      timestamp: Date.now(),
    };
    setMessages((prev) => [...prev, userMsg]);

    try {
      const idempotencyKey = crypto.randomUUID();
      await client.request("chat.send", {
        sessionKey,
        message: text,
        idempotencyKey,
      });

      // Poll for assistant response
      const maxAttempts = 120;
      for (let i = 0; i < maxAttempts; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        try {
          const res = await client.request("chat.history", { sessionKey });
          const data = res as { messages?: Array<{ role: string; content: unknown; timestamp?: number }> } | undefined;
          if (data?.messages) {
            const allMsgs = data.messages.map((m, idx) => ({
              id: `msg-${idx}`,
              role: m.role as "user" | "assistant",
              content: extractTextContent(m.content),
              timestamp: (m.timestamp as number) ?? Date.now(),
            }));
            // Check if we got a new assistant message
            if (allMsgs.length > messages.length + 1) {
              setMessages(allMsgs);
              break;
            }
          }
        } catch {
          // retry
        }
      }
    } catch {
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: "Sorry, I couldn't process that message. Please check the gateway connection.",
          timestamp: Date.now(),
        },
      ]);
    } finally {
      setSending(false);
    }
  }, [input, sending, client, sessionKey, messages.length]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  return (
    <div className="org-chat">
      {/* Header */}
      <div className="org-chat__header">
        <button className="btn btn--icon" onClick={() => {
              const orgDef = findOrgForAgent(agentId ?? "");
              navigate(orgDef ? `/org/${orgDef.id}` : "/org");
            }} title="Back to organization">
          {Icons.chevronLeft({ width: "18px", height: "18px" })}
        </button>
        <div className="org-chat__header-avatar" style={{ background: gradient }}>
          {initials(agent?.name ?? agentId ?? "?")}
        </div>
        <div className="org-chat__header-info">
          <div className="org-chat__header-name">{agent?.name ?? agentId}</div>
          <div className="org-chat__header-status">
            <span className={`statusDot ${connected ? "ok" : ""}`} />
            <span>{connected ? "Online" : "Offline"}</span>
            {agent?.model && <span className="muted"> | {agent.model}</span>}
          </div>
        </div>
      </div>

      {/* Messages */}
      <div className="org-chat__messages">
        {loading && (
          <div className="org-chat__loading">
            <div className="spinner" />
            <span className="muted">Loading conversation...</span>
          </div>
        )}

        {!loading && messages.length === 0 && (
          <div className="org-chat__welcome">
            <div className="org-chat__welcome-avatar" style={{ background: gradient }}>
              {initials(agent?.name ?? agentId ?? "?")}
            </div>
            <h3>Start a conversation with {agent?.name ?? agentId}</h3>
            <p className="muted">
              Send a message below to begin. You can give instructions, ask questions,
              or assign tasks directly.
            </p>
          </div>
        )}

        {messages.map((msg) => (
          <OrgChatBubble
            key={msg.id}
            message={msg}
            agentName={agent?.name ?? agentId ?? "Agent"}
            gradient={gradient}
          />
        ))}

        {sending && (
          <div className="org-chat__bubble org-chat__bubble--assistant">
            <div className="org-chat__bubble-avatar" style={{ background: gradient }}>
              {initials(agent?.name ?? agentId ?? "?")}
            </div>
            <div className="org-chat__bubble-content">
              <div className="copilot-typing">
                <span /><span /><span />
              </div>
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="org-chat__compose">
        <textarea
          ref={textareaRef}
          className="org-chat__input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={connected ? `Message ${agent?.name ?? agentId}...` : "Gateway not connected..."}
          rows={1}
          disabled={!connected || sending}
        />
        <button
          className="org-chat__send-btn"
          onClick={handleSend}
          disabled={!connected || sending || !input.trim()}
          title="Send message"
        >
          {Icons.send({ width: "18px", height: "18px" })}
        </button>
      </div>
    </div>
  );
}

function OrgChatBubble({
  message,
  agentName,
  gradient,
}: {
  message: Message;
  agentName: string;
  gradient: string;
}) {
  const isUser = message.role === "user";
  const html = useMemo(() => {
    if (isUser) return null;
    return toSanitizedMarkdownHtml(message.content);
  }, [isUser, message.content]);

  return (
    <div className={`org-chat__bubble org-chat__bubble--${isUser ? "user" : "assistant"}`}>
      {!isUser && (
        <div className="org-chat__bubble-avatar" style={{ background: gradient }}>
          {initials(agentName)}
        </div>
      )}
      <div className="org-chat__bubble-content">
        <div className="org-chat__bubble-header">
          <span className="org-chat__bubble-sender">{isUser ? "You" : agentName}</span>
          <span className="org-chat__bubble-time">{formatTime(message.timestamp)}</span>
        </div>
        {isUser ? (
          <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{message.content}</p>
        ) : (
          html && (
            <div className="markdown-body" dangerouslySetInnerHTML={{ __html: html }} />
          )
        )}
      </div>
    </div>
  );
}
