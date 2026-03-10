import { create } from "zustand";
import { createChatProxy } from "./controller-proxy";
import {
  loadChatHistory,
  sendChatMessage,
  abortChatRun,
  handleChatEvent,
} from "../../ui/controllers/chat";
import type { ChatEventPayload } from "../../ui/controllers/chat";

type QueueItem = { id: string; text: string; sentAt?: number };
type Attachment = { id: string; contentType: string; dataUrl: string; name?: string };
type DebugEntry = { ts: number; level: string; message: string };

type ChatState = {
  loading: boolean;
  sending: boolean;
  message: string;
  messages: unknown[];
  toolMessages: unknown[];
  stream: string | null;
  streamStartedAt: number | null;
  runId: string | null;
  queue: QueueItem[];
  attachments: Attachment[];
  sidebarOpen: boolean;
  sidebarContent: string | null;
  newMessagesBelow: boolean;
  debugLog: DebugEntry[];
  showDebug: boolean;
  // Controller-compatible aliases (controllers use chatXxx naming)
  chatLoading: boolean;
  chatSending: boolean;
  chatMessage: string;
  chatMessages: unknown[];
  chatToolMessages: unknown[];
  chatStream: string | null;
  chatStreamStartedAt: number | null;
  chatRunId: string | null;
  chatThinkingLevel: string | null;
  chatAttachments: Attachment[];
  chatAvatarUrl: string | null;
};

type ChatActions = {
  setLoading: (v: boolean) => void;
  setSending: (v: boolean) => void;
  setMessage: (v: string) => void;
  setMessages: (msgs: unknown[]) => void;
  appendMessage: (msg: unknown) => void;
  setToolMessages: (msgs: unknown[]) => void;
  setStream: (v: string | null) => void;
  setStreamStartedAt: (v: number | null) => void;
  setRunId: (v: string | null) => void;
  addToQueue: (item: { id: string; text: string }) => void;
  removeFromQueue: (id: string) => void;
  setAttachments: (items: Attachment[]) => void;
  openSidebar: (content: string) => void;
  closeSidebar: () => void;
  setNewMessagesBelow: (v: boolean) => void;
  addDebugEntry: (entry: DebugEntry) => void;
  toggleDebug: () => void;
  clearDebugLog: () => void;
  reset: () => void;
  // Controller-proxy actions that delegate to legacy controller functions
  loadHistory: () => Promise<void>;
  send: (message: string) => Promise<string | null>;
  abort: () => Promise<boolean>;
  handleEvent: (payload: ChatEventPayload) => string | null;
};

const chatDefaults: ChatState = {
  loading: false,
  sending: false,
  message: "",
  messages: [],
  toolMessages: [],
  stream: null,
  streamStartedAt: null,
  runId: null,
  queue: [],
  attachments: [],
  sidebarOpen: false,
  sidebarContent: null,
  newMessagesBelow: false,
  debugLog: [],
  showDebug: false,
  // Controller-compatible aliases
  chatLoading: false,
  chatSending: false,
  chatMessage: "",
  chatMessages: [],
  chatToolMessages: [],
  chatStream: null,
  chatStreamStartedAt: null,
  chatRunId: null,
  chatThinkingLevel: null,
  chatAttachments: [],
  chatAvatarUrl: null,
};

export const useChatStore = create<ChatState & ChatActions>((set, get) => ({
  ...chatDefaults,

  setLoading: (v) => set({ loading: v }),
  setSending: (v) => set({ sending: v }),
  setMessage: (v) => set({ message: v }),
  setMessages: (msgs) => set({ messages: msgs }),
  appendMessage: (msg) => set((s) => ({ messages: [...s.messages, msg] })),
  setToolMessages: (msgs) => set({ toolMessages: msgs }),
  setStream: (v) => set({ stream: v }),
  setStreamStartedAt: (v) => set({ streamStartedAt: v }),
  setRunId: (v) => set({ runId: v }),
  addToQueue: (item) => set((s) => ({ queue: [...s.queue, item] })),
  removeFromQueue: (id) => set((s) => ({ queue: s.queue.filter((q) => q.id !== id) })),
  setAttachments: (items) => set({ attachments: items }),
  openSidebar: (content) => set({ sidebarOpen: true, sidebarContent: content }),
  closeSidebar: () => set({ sidebarOpen: false, sidebarContent: null }),
  setNewMessagesBelow: (v) => set({ newMessagesBelow: v }),
  addDebugEntry: (entry) => set((s) => ({ debugLog: [...s.debugLog, entry] })),
  toggleDebug: () => set((s) => ({ showDebug: !s.showDebug })),
  clearDebugLog: () => set({ debugLog: [] }),
  reset: () =>
    set({
      ...chatDefaults,
    }),

  // Controller-proxy actions
  loadHistory: async () => {
    const proxy = createChatProxy();
    await loadChatHistory(proxy as never);
  },
  send: async (message: string) => {
    const proxy = createChatProxy();
    const attachments = get().chatAttachments;
    return sendChatMessage(proxy as never, message, attachments as never);
  },
  abort: async () => {
    const proxy = createChatProxy();
    return abortChatRun(proxy as never);
  },
  handleEvent: (payload: ChatEventPayload) => {
    const proxy = createChatProxy();
    return handleChatEvent(proxy as never, payload);
  },
}));

export default useChatStore;
