import { create } from "zustand";
import { createControllerProxy } from "./controller-proxy";
import { loadSkills as loadSkillsCtrl } from "../../ui/controllers/skills";

type SkillsState = {
  skillsLoading: boolean;
  skillsReport: unknown;
  skillsError: string | null;
  skillsFilter: string;
  skillsBusyKey: string | null;
  skillEdits: Record<string, string>;
  skillMessages: Record<string, { kind: "success" | "error"; message: string }>;
};

type SkillsActions = {
  load: (options?: { clearMessages?: boolean }) => Promise<void>;
  setFilter: (f: string) => void;
};

export const useSkillsStore = create<SkillsState & SkillsActions>((set) => ({
  skillsLoading: false,
  skillsReport: null,
  skillsError: null,
  skillsFilter: "",
  skillsBusyKey: null,
  skillEdits: {},
  skillMessages: {},

  load: async (options) => {
    const proxy = createControllerProxy();
    await loadSkillsCtrl(proxy as never, options);
  },

  setFilter: (f) => set({ skillsFilter: f }),
}));

export default useSkillsStore;
