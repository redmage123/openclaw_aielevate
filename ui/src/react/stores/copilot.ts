import { create } from "zustand";

type CopilotMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: number;
};

type CopilotState = {
  open: boolean;
  minimized: boolean;
  messages: CopilotMessage[];
  input: string;
  sending: boolean;
  stream: string | null;
  greeted: boolean;
  contextPage: string;
};

type CopilotActions = {
  toggle: () => void;
  setOpen: (v: boolean) => void;
  setMinimized: (v: boolean) => void;
  setInput: (v: string) => void;
  setSending: (v: boolean) => void;
  setStream: (v: string | null) => void;
  addMessage: (msg: CopilotMessage) => void;
  setMessages: (msgs: CopilotMessage[]) => void;
  setGreeted: (v: boolean) => void;
  setContextPage: (page: string) => void;
  clear: () => void;
};

const defaults: CopilotState = {
  open: false,
  minimized: false,
  messages: [],
  input: "",
  sending: false,
  stream: null,
  greeted: false,
  contextPage: "chat",
};

export const useCopilotStore = create<CopilotState & CopilotActions>((set) => ({
  ...defaults,

  toggle: () => set((s) => ({ open: !s.open, minimized: false })),
  setOpen: (v) => set({ open: v, minimized: false }),
  setMinimized: (v) => set({ minimized: v }),
  setInput: (v) => set({ input: v }),
  setSending: (v) => set({ sending: v }),
  setStream: (v) => set({ stream: v }),
  addMessage: (msg) => set((s) => ({ messages: [...s.messages, msg] })),
  setMessages: (msgs) => set({ messages: msgs }),
  setGreeted: (v) => set({ greeted: v }),
  setContextPage: (page) => set({ contextPage: page }),
  clear: () => set({ messages: [], stream: null, greeted: false }),
}));

export default useCopilotStore;
