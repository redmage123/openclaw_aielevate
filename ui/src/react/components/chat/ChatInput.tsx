import { useCallback, useEffect, useRef } from "react";
import { useChatStore } from "../../stores/chat.ts";

function adjustHeight(el: HTMLTextAreaElement) {
  el.style.height = "auto";
  el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
}

export default function ChatInput() {
  const message = useChatStore((s) => s.message);
  const setMessage = useChatStore((s) => s.setMessage);
  const sending = useChatStore((s) => s.sending);
  const loading = useChatStore((s) => s.loading);
  const send = useChatStore((s) => s.send);
  const abort = useChatStore((s) => s.abort);
  const attachments = useChatStore((s) => s.attachments);
  const setAttachments = useChatStore((s) => s.setAttachments);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (textareaRef.current) {
      adjustHeight(textareaRef.current);
    }
  }, [message]);

  const handleSend = useCallback(() => {
    const text = message.trim();
    if (!text) return;
    setMessage("");
    send(text);
  }, [message, setMessage, send]);

  const handleAbort = useCallback(() => {
    abort();
  }, [abort]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        if (!loading && !sending && message.trim()) {
          handleSend();
        }
      }
    },
    [loading, sending, message, handleSend],
  );

  const handleFileSelect = useCallback(() => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    input.multiple = true;
    input.onchange = () => {
      if (!input.files) return;
      const files = Array.from(input.files);
      for (const file of files) {
        const reader = new FileReader();
        reader.onload = () => {
          const dataUrl = reader.result as string;
          setAttachments([
            ...attachments,
            { id: crypto.randomUUID(), contentType: file.type, dataUrl, name: file.name },
          ]);
        };
        reader.readAsDataURL(file);
      }
    };
    input.click();
  }, [attachments, setAttachments]);

  const removeAttachment = useCallback(
    (id: string) => {
      setAttachments(attachments.filter((a) => a.id !== id));
    },
    [attachments, setAttachments],
  );

  return (
    <div className="chat-compose">
      {attachments.length > 0 && (
        <div className="chat-attachments">
          {attachments.map((a) => (
            <div key={a.id} className="chat-attachment-preview">
              <img src={a.dataUrl} alt={a.name ?? "attachment"} />
              <button className="chat-attachment-remove" onClick={() => removeAttachment(a.id)}>
                x
              </button>
            </div>
          ))}
        </div>
      )}
      <div className="chat-input-row">
        <button
          className="btn chat-attach-btn"
          onClick={handleFileSelect}
          title="Attach image"
          disabled={sending}
        >
          +
        </button>
        <textarea
          ref={textareaRef}
          className="chat-input"
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Type a message..."
          rows={1}
          disabled={loading}
        />
        {sending ? (
          <button className="btn chat-abort-btn" onClick={handleAbort} title="Abort">
            Stop
          </button>
        ) : (
          <button
            className="btn primary chat-send-btn"
            onClick={handleSend}
            disabled={loading || !message.trim()}
            title="Send (Enter)"
          >
            Send
          </button>
        )}
      </div>
    </div>
  );
}
