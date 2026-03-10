import { create } from "zustand";
import type { GatewayHelloOk } from "../../ui/gateway.ts";

type EventLogEntry = { ts: number; type: string; detail: string };

type GatewayState = {
  connected: boolean;
  hello: GatewayHelloOk | null;
  lastError: string | null;
  lastErrorCode: string | null;
  client: unknown;
  updateAvailable: unknown;
  eventLog: EventLogEntry[];
};

type GatewayActions = {
  setConnected: (v: boolean) => void;
  setHello: (v: GatewayHelloOk) => void;
  setError: (error: string | null, code?: string | null) => void;
  setClient: (client: unknown) => void;
  setUpdateAvailable: (v: unknown) => void;
  addEvent: (entry: EventLogEntry) => void;
  clearEvents: () => void;
};

export const useGatewayStore = create<GatewayState & GatewayActions>((set) => ({
  connected: false,
  hello: null,
  lastError: null,
  lastErrorCode: null,
  client: null,
  updateAvailable: null,
  eventLog: [],

  setConnected: (v) => set({ connected: v }),
  setHello: (v) => set({ hello: v, connected: true }),
  setError: (error, code) => set({ lastError: error, lastErrorCode: code ?? null }),
  setClient: (client) => set({ client }),
  setUpdateAvailable: (v) => set({ updateAvailable: v }),
  addEvent: (entry) => set((s) => ({ eventLog: [...s.eventLog, entry] })),
  clearEvents: () => set({ eventLog: [] }),
}));

export default useGatewayStore;
