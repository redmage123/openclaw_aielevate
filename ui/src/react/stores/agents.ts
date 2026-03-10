import { create } from "zustand";
import { createControllerProxy } from "./controller-proxy";
import {
  loadAgents as loadAgentsCtrl,
  loadToolsCatalog as loadToolsCatalogCtrl,
} from "../../ui/controllers/agents";

type AgentsState = {
  agentsLoading: boolean;
  agentsList: unknown;
  agentsError: string | null;
  agentsSelectedId: string | null;
  agentsPanel: string;
  toolsCatalogLoading: boolean;
  toolsCatalogResult: unknown;
  toolsCatalogError: string | null;
  agentFilesLoading: boolean;
  agentFilesList: unknown;
  agentFilesError: string | null;
};

type AgentsActions = {
  load: () => Promise<void>;
  loadToolsCatalog: (agentId?: string | null) => Promise<void>;
  selectAgent: (id: string | null) => void;
  setPanel: (panel: string) => void;
};

export const useAgentsStore = create<AgentsState & AgentsActions>((set) => ({
  agentsLoading: false,
  agentsList: null,
  agentsError: null,
  agentsSelectedId: null,
  agentsPanel: "overview",
  toolsCatalogLoading: false,
  toolsCatalogResult: null,
  toolsCatalogError: null,
  agentFilesLoading: false,
  agentFilesList: null,
  agentFilesError: null,

  load: async () => {
    const proxy = createControllerProxy();
    await loadAgentsCtrl(proxy as never);
  },

  loadToolsCatalog: async (agentId) => {
    const proxy = createControllerProxy();
    await loadToolsCatalogCtrl(proxy as never, agentId);
  },

  selectAgent: (id) => set({ agentsSelectedId: id }),
  setPanel: (panel) => set({ agentsPanel: panel }),
}));

export default useAgentsStore;
