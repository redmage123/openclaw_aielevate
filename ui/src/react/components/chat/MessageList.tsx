import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useChatStore } from "../../stores/chat.ts";
import { toSanitizedMarkdownHtml } from "../../../ui/markdown.ts";
import MessageBubble from "./MessageBubble.tsx";

type Message = {
  id?: string;
  role: string;
  content: string | Array<{ type: string; text?: string }>;
  timestamp?: number;
};

type MessageListProps = {
  assistantName?: string;
  onScroll?: (event: React.UIEvent) => void;
};

export default function MessageList({ assistantName, onScroll }: MessageListProps) {
  const messages = useChatStore((s) => s.messages) as Message[];
  const stream = useChatStore((s) => s.stream);
  const streamStartedAt = useChatStore((s) => s.streamStartedAt);
  const newMessagesBelow = useChatStore((s) => s.newMessagesBelow);
  const setNewMessagesBelow = useChatStore((s) => s.setNewMessagesBelow);
  const containerRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const isAtBottomRef = useRef(true);

  // Auto-scroll to bottom when new messages arrive or stream updates
  useEffect(() => {
    if (isAtBottomRef.current && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages.length, stream]);

  const handleScroll = useCallback(
    (e: React.UIEvent) => {
      const el = e.currentTarget;
      const threshold = 80;
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
      isAtBottomRef.current = atBottom;
      if (atBottom && newMessagesBelow) {
        setNewMessagesBelow(false);
      }
      onScroll?.(e);
    },
    [newMessagesBelow, setNewMessagesBelow, onScroll],
  );

  const scrollToBottom = useCallback(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    setNewMessagesBelow(false);
  }, [setNewMessagesBelow]);

  // Render streaming markdown, memoized on the stream text
  const streamHtml = useMemo(() => {
    if (stream == null || !stream.trim()) return "";
    return toSanitizedMarkdownHtml(stream);
  }, [stream]);

  return (
    <div className="chat-thread" ref={containerRef} onScroll={handleScroll}>
      {messages.length === 0 && !stream && (
        <div className="chat-empty">
          <p className="muted">No messages yet. Start the conversation.</p>
        </div>
      )}

      {messages.map((msg, i) => (
        <MessageBubble
          key={msg.id ?? `msg-${i}`}
          message={msg}
          assistantName={assistantName}
        />
      ))}

      {/* Streaming indicator */}
      {stream != null && (
        <div className="chat-message chat-message--assistant chat-message--streaming">
          <div className="chat-message__header">
            <span className="chat-message__role">{assistantName ?? "Assistant"}</span>
            {streamStartedAt && <StreamTimer start={streamStartedAt} />}
          </div>
          <div className="chat-message__content">
            {streamHtml ? (
              <div
                className="markdown-body"
                dangerouslySetInnerHTML={{ __html: streamHtml }}
              />
            ) : (
              <span className="chat-stream-placeholder" />
            )}
            <span className="chat-cursor" />
          </div>
        </div>
      )}

      <div ref={bottomRef} />

      {/* "New messages" badge when scrolled up */}
      {newMessagesBelow && (
        <button className="chat-new-messages" onClick={scrollToBottom}>
          New messages below
        </button>
      )}
    </div>
  );
}

function StreamTimer({ start }: { start: number }) {
  const [, setTick] = useState(0);

  // Re-render every second to update elapsed time
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, []);

  const elapsed = Math.floor((Date.now() - start) / 1000);
  const label = elapsed < 60 ? `${elapsed}s` : `${Math.floor(elapsed / 60)}m ${elapsed % 60}s`;
  return <span className="chat-message__time">{label}</span>;
}
