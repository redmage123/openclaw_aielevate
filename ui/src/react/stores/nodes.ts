import { create } from "zustand";
import { createControllerProxy } from "./controller-proxy";
import { loadNodes as loadNodesCtrl } from "../../ui/controllers/nodes";

type NodesState = {
  nodesLoading: boolean;
  nodes: unknown[];
  devicesLoading: boolean;
  devicesList: unknown;
  devicesError: string | null;
};

type NodesActions = {
  load: (opts?: { quiet?: boolean }) => Promise<void>;
  loadDevices: () => Promise<void>;
};

export const useNodesStore = create<NodesState & NodesActions>(() => ({
  nodesLoading: false,
  nodes: [],
  devicesLoading: false,
  devicesList: null,
  devicesError: null,

  load: async (opts) => {
    const proxy = createControllerProxy();
    await loadNodesCtrl(proxy as never, opts);
  },

  loadDevices: async () => {
    // TODO: call loadDevices controller if it exists
    const proxy = createControllerProxy();
    void proxy;
  },
}));

export default useNodesStore;
