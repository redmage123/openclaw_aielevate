import { useCallback, useEffect } from "react";
import { useChatStore } from "../stores/chat.ts";
import { useUiStore } from "../stores/ui.ts";
import { useGatewayStore } from "../stores/gateway.ts";
import ChatInput from "../components/chat/ChatInput.tsx";
import MessageList from "../components/chat/MessageList.tsx";
import { Icons } from "../icons.tsx";

export default function ChatPage() {
  const connected = useGatewayStore((s) => s.connected);
  const loading = useChatStore((s) => s.loading);
  const sidebarOpen = useChatStore((s) => s.sidebarOpen);
  const sidebarContent = useChatStore((s) => s.sidebarContent);
  const closeSidebar = useChatStore((s) => s.closeSidebar);
  const loadHistory = useChatStore((s) => s.loadHistory);
  const sessionKey = useUiStore((s) => s.sessionKey);
  const setSessionKey = useUiStore((s) => s.setSessionKey);
  const focusMode = useUiStore((s) => s.chatFocusMode);
  const setChatFocusMode = useUiStore((s) => s.setChatFocusMode);
  const assistantName = useUiStore((s) => s.assistantName);
  const splitRatio = useUiStore((s) => s.splitRatio);

  useEffect(() => {
    if (connected) loadHistory();
  }, [connected, loadHistory]);

  const handleRefresh = useCallback(() => {
    loadHistory();
  }, [loadHistory]);

  const handleNewSession = useCallback(() => {
    setSessionKey("default");
  }, [setSessionKey]);

  const chatCardClass = [
    "card chat",
    focusMode ? "chat--focus" : "",
  ].filter(Boolean).join(" ");

  const mainStyle = sidebarOpen
    ? { flex: `0 0 ${splitRatio * 100}%` }
    : undefined;

  return (
    <div className="chat-view">
      <div className={chatCardClass} style={mainStyle}>
        {/* Header */}
        <div className="chat-header">
          <div className="chat-header__left">
            <span className="chat-header__session-label">Session:</span>
            <span className="chat-header__session-key mono">{sessionKey || "default"}</span>
          </div>
          <div className="chat-header__right">
            <button
              className="btn btn--icon"
              onClick={() => setChatFocusMode(!focusMode)}
              title={focusMode ? "Exit focus mode" : "Focus mode"}
              aria-label={focusMode ? "Exit focus mode" : "Focus mode"}
            >
              {focusMode ? Icons.chevronDown() : Icons.chevronUp()}
            </button>
            <button
              className="btn btn--icon"
              onClick={handleRefresh}
              disabled={loading}
              title="Refresh chat"
              aria-label="Refresh chat"
            >
              {Icons.refreshCw({ width: "14px", height: "14px" })}
            </button>
            <button
              className="btn btn--icon"
              onClick={handleNewSession}
              title="New session"
              aria-label="New session"
            >
              {Icons.plus()}
            </button>
          </div>
        </div>

        {/* Messages area */}
        <MessageList assistantName={assistantName} />

        {/* Input area */}
        <ChatInput />
      </div>

      {/* Optional sidebar for tool output */}
      {sidebarOpen && sidebarContent && (
        <div className="chat-sidebar" style={{ flex: `0 0 ${(1 - splitRatio) * 100}%` }}>
          <div className="chat-sidebar__header">
            <span>Output</span>
            <button className="btn btn--icon" onClick={closeSidebar} title="Close sidebar">
              {Icons.x()}
            </button>
          </div>
          <div className="chat-sidebar__content">
            <pre>{sidebarContent}</pre>
          </div>
        </div>
      )}
    </div>
  );
}
