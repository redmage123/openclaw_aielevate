import { create } from "zustand";
import { createControllerProxy } from "./controller-proxy";
import { loadConfig as loadConfigCtrl } from "../../ui/controllers/config";

// TODO: wire saveConfig and applyConfig from ../../ui/controllers/config

type ConfigState = {
  configLoading: boolean;
  configRaw: string;
  configRawOriginal: string;
  configValid: boolean | null;
  configIssues: unknown[];
  configSaving: boolean;
  configApplying: boolean;
  configSnapshot: unknown;
  configSchema: unknown;
  configSchemaVersion: string | null;
  configSchemaLoading: boolean;
  configUiHints: Record<string, unknown>;
  configForm: Record<string, unknown> | null;
  configFormOriginal: Record<string, unknown> | null;
  configFormMode: "form" | "raw";
  configSearchQuery: string;
  configFormDirty: boolean;
  configActiveSection: string | null;
  configActiveSubsection: string | null;
  applySessionKey: string;
  updateRunning: boolean;
};

type ConfigActions = {
  load: () => Promise<void>;
  save: () => Promise<void>;
  apply: () => Promise<void>;
  setFormMode: (mode: "form" | "raw") => void;
  setRaw: (raw: string) => void;
  setSearchQuery: (q: string) => void;
};

export const useConfigStore = create<ConfigState & ConfigActions>((set) => ({
  configLoading: false,
  configRaw: "",
  configRawOriginal: "",
  configValid: null,
  configIssues: [],
  configSaving: false,
  configApplying: false,
  configSnapshot: null,
  configSchema: null,
  configSchemaVersion: null,
  configSchemaLoading: false,
  configUiHints: {},
  configForm: null,
  configFormOriginal: null,
  configFormMode: "form",
  configSearchQuery: "",
  configFormDirty: false,
  configActiveSection: null,
  configActiveSubsection: null,
  applySessionKey: "",
  updateRunning: false,

  load: async () => {
    const proxy = createControllerProxy();
    await loadConfigCtrl(proxy as never);
  },

  save: async () => {
    // TODO: call saveConfig via controller proxy
    const proxy = createControllerProxy();
    void proxy;
  },

  apply: async () => {
    // TODO: call applyConfig via controller proxy
    const proxy = createControllerProxy();
    void proxy;
  },

  setFormMode: (mode) => set({ configFormMode: mode }),
  setRaw: (raw) => set({ configRaw: raw }),
  setSearchQuery: (q) => set({ configSearchQuery: q }),
}));

export default useConfigStore;
