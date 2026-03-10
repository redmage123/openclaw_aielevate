import { create } from "zustand";
import { createControllerProxy } from "./controller-proxy";
import {
  loadDebug as loadDebugCtrl,
  callDebugMethod as callDebugMethodCtrl,
} from "../../ui/controllers/debug";

type DebugState = {
  debugLoading: boolean;
  debugStatus: unknown;
  debugHealth: unknown;
  debugModels: unknown[];
  debugHeartbeat: unknown;
  debugCallMethod: string;
  debugCallParams: string;
  debugCallResult: string | null;
  debugCallError: string | null;
};

type DebugActions = {
  load: () => Promise<void>;
  call: () => Promise<void>;
  setCallMethod: (m: string) => void;
  setCallParams: (p: string) => void;
};

export const useDebugStore = create<DebugState & DebugActions>((set) => ({
  debugLoading: false,
  debugStatus: null,
  debugHealth: null,
  debugModels: [],
  debugHeartbeat: null,
  debugCallMethod: "",
  debugCallParams: "{}",
  debugCallResult: null,
  debugCallError: null,

  load: async () => {
    const proxy = createControllerProxy();
    await loadDebugCtrl(proxy as never);
  },

  call: async () => {
    const proxy = createControllerProxy();
    await callDebugMethodCtrl(proxy as never);
  },

  setCallMethod: (m) => set({ debugCallMethod: m }),
  setCallParams: (p) => set({ debugCallParams: p }),
}));

export default useDebugStore;
