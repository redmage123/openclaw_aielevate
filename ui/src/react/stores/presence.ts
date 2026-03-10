import { create } from "zustand";
import { createControllerProxy } from "./controller-proxy";
import { loadPresence as loadPresenceCtrl } from "../../ui/controllers/presence";

type PresenceState = {
  presenceLoading: boolean;
  presenceEntries: unknown[];
  presenceError: string | null;
  presenceStatus: string | null;
};

type PresenceActions = {
  load: () => Promise<void>;
  setEntries: (entries: unknown[]) => void;
};

export const usePresenceStore = create<PresenceState & PresenceActions>((set) => ({
  presenceLoading: false,
  presenceEntries: [],
  presenceError: null,
  presenceStatus: null,

  load: async () => {
    const proxy = createControllerProxy();
    await loadPresenceCtrl(proxy as never);
  },

  setEntries: (entries) => set({ presenceEntries: entries }),
}));

export default usePresenceStore;
