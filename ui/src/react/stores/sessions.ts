import { create } from "zustand";
import { createControllerProxy } from "./controller-proxy";
import {
  loadSessions as loadSessionsCtrl,
  deleteSessionAndRefresh as deleteSessionAndRefreshCtrl,
  patchSession as patchSessionCtrl,
} from "../../ui/controllers/sessions";

type SessionsState = {
  sessionsLoading: boolean;
  sessionsResult: unknown;
  sessionsError: string | null;
  sessionsFilterActive: string;
  sessionsFilterLimit: string;
  sessionsIncludeGlobal: boolean;
  sessionsIncludeUnknown: boolean;
};

type SessionsActions = {
  load: () => Promise<void>;
  deleteSession: (key: string) => Promise<boolean>;
  patchSession: (
    key: string,
    patch: { label?: string | null; thinkingLevel?: string | null; verboseLevel?: string | null; reasoningLevel?: string | null },
  ) => Promise<void>;
  setFilterActive: (v: string) => void;
  setFilterLimit: (v: string) => void;
  setIncludeGlobal: (v: boolean) => void;
  setIncludeUnknown: (v: boolean) => void;
};

export const useSessionsStore = create<SessionsState & SessionsActions>((set) => ({
  sessionsLoading: false,
  sessionsResult: null,
  sessionsError: null,
  sessionsFilterActive: "",
  sessionsFilterLimit: "",
  sessionsIncludeGlobal: true,
  sessionsIncludeUnknown: true,

  load: async () => {
    const proxy = createControllerProxy();
    await loadSessionsCtrl(proxy as never);
  },

  deleteSession: async (key: string) => {
    const proxy = createControllerProxy();
    return deleteSessionAndRefreshCtrl(proxy as never, key);
  },

  patchSession: async (key, patch) => {
    const proxy = createControllerProxy();
    await patchSessionCtrl(proxy as never, key, patch);
  },

  setFilterActive: (v) => set({ sessionsFilterActive: v }),
  setFilterLimit: (v) => set({ sessionsFilterLimit: v }),
  setIncludeGlobal: (v) => set({ sessionsIncludeGlobal: v }),
  setIncludeUnknown: (v) => set({ sessionsIncludeUnknown: v }),
}));

export default useSessionsStore;
