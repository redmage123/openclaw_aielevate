import { create } from "zustand";
import { createControllerProxy } from "./controller-proxy";
import { loadLogs as loadLogsCtrl } from "../../ui/controllers/logs";

type LogsState = {
  logsLoading: boolean;
  logsError: string | null;
  logsCursor: number | null;
  logsFile: string | null;
  logsEntries: unknown[];
  logsTruncated: boolean;
  logsLastFetchAt: number | null;
  logsLimit: number;
  logsMaxBytes: number;
  logsFilterText: string;
  logsLevelFilters: Record<string, boolean>;
  logsAutoFollow: boolean;
};

type LogsActions = {
  load: (opts?: { reset?: boolean; quiet?: boolean }) => Promise<void>;
  setFilterText: (t: string) => void;
  toggleLevel: (level: string) => void;
  setAutoFollow: (v: boolean) => void;
};

export const useLogsStore = create<LogsState & LogsActions>((set, get) => ({
  logsLoading: false,
  logsError: null,
  logsCursor: null,
  logsFile: null,
  logsEntries: [],
  logsTruncated: false,
  logsLastFetchAt: null,
  logsLimit: 500,
  logsMaxBytes: 512_000,
  logsFilterText: "",
  logsLevelFilters: { debug: true, info: true, warn: true, error: true },
  logsAutoFollow: true,

  load: async (opts) => {
    const proxy = createControllerProxy();
    await loadLogsCtrl(proxy as never, opts);
  },

  setFilterText: (t) => set({ logsFilterText: t }),

  toggleLevel: (level) => {
    const current = get().logsLevelFilters;
    set({ logsLevelFilters: { ...current, [level]: !current[level] } });
  },

  setAutoFollow: (v) => set({ logsAutoFollow: v }),
}));

export default useLogsStore;
