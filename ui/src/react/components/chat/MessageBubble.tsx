import { useMemo } from "react";
import { toSanitizedMarkdownHtml } from "../../../ui/markdown.ts";

type ContentBlock =
  | { type: "text"; text: string }
  | { type: "image"; source: { type: "base64"; media_type: string; data: string } };

type Message = {
  id?: string;
  role: string;
  content: string | ContentBlock[];
  timestamp?: number;
};

type MessageBubbleProps = {
  message: Message;
  assistantName?: string;
};

/** Extract plain text from a message's content field. */
function extractTextContent(content: string | ContentBlock[]): string {
  if (typeof content === "string") return content;
  return content
    .filter((block): block is { type: "text"; text: string } => block.type === "text")
    .map((block) => block.text)
    .join("\n");
}

/** Extract image blocks from content. */
function extractImages(content: string | ContentBlock[]): Array<{ mediaType: string; data: string }> {
  if (typeof content === "string") return [];
  return content
    .filter((block): block is ContentBlock & { type: "image" } => block.type === "image")
    .map((block) => ({
      mediaType: block.source.media_type,
      data: block.source.data,
    }));
}

function formatTime(ts?: number): string {
  if (!ts) return "";
  const d = new Date(ts);
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export default function MessageBubble({ message, assistantName }: MessageBubbleProps) {
  const isUser = message.role === "user";
  const roleLabel = isUser ? "You" : (assistantName ?? "Assistant");
  const bubbleClass = isUser
    ? "chat-message chat-message--user"
    : "chat-message chat-message--assistant";

  const textContent = useMemo(() => extractTextContent(message.content), [message.content]);
  const images = useMemo(() => extractImages(message.content), [message.content]);

  // Render markdown for assistant messages, memoized to avoid re-parsing
  const renderedHtml = useMemo(() => {
    if (isUser || !textContent) return null;
    return toSanitizedMarkdownHtml(textContent);
  }, [isUser, textContent]);

  return (
    <div className={bubbleClass}>
      <div className="chat-message__header">
        <span className="chat-message__role">{roleLabel}</span>
        {message.timestamp && (
          <span className="chat-message__time">{formatTime(message.timestamp)}</span>
        )}
      </div>
      <div className="chat-message__content">
        {isUser ? (
          <p style={{ whiteSpace: "pre-wrap", margin: 0 }}>{textContent}</p>
        ) : (
          renderedHtml && (
            <div
              className="markdown-body"
              dangerouslySetInnerHTML={{ __html: renderedHtml }}
            />
          )
        )}
        {images.map((img, i) => (
          <img
            key={`img-${i}`}
            src={`data:${img.mediaType};base64,${img.data}`}
            alt="Attached image"
            className="chat-message__image"
            style={{ maxWidth: "100%", borderRadius: 8, marginTop: 8 }}
          />
        ))}
      </div>
    </div>
  );
}
