import { create } from "zustand";
import type { ThemeMode } from "../../ui/theme.ts";
import { resolveTheme } from "../../ui/theme.ts";
import { loadSettings, saveSettings, type UiSettings } from "../../ui/storage.ts";

type UiState = {
  tab: string;
  theme: ThemeMode;
  themeResolved: "light" | "dark";
  navCollapsed: boolean;
  navGroupsCollapsed: Record<string, boolean>;
  chatFocusMode: boolean;
  chatShowThinking: boolean;
  splitRatio: number;
  sessionKey: string;
  assistantName: string;
  assistantAvatar: string | null;
  assistantAgentId: string | null;
  settings: UiSettings;
};

type UiActions = {
  setTab: (tab: string) => void;
  setTheme: (theme: ThemeMode) => void;
  setThemeResolved: (resolved: "light" | "dark") => void;
  toggleNav: () => void;
  toggleNavGroup: (group: string) => void;
  setChatFocusMode: (v: boolean) => void;
  setChatShowThinking: (v: boolean) => void;
  setSplitRatio: (v: number) => void;
  setSessionKey: (key: string) => void;
  setAssistantIdentity: (name: string, avatar: string | null, agentId: string | null) => void;
  cycleTheme: () => void;
};

export const useUiStore = create<UiState & UiActions>((set, get) => {
  const settings = loadSettings();
  return {
    tab: "chat",
    theme: settings.theme,
    themeResolved: resolveTheme(settings.theme),
    navCollapsed: settings.navCollapsed,
    navGroupsCollapsed: settings.navGroupsCollapsed,
    chatFocusMode: settings.chatFocusMode,
    chatShowThinking: settings.chatShowThinking,
    splitRatio: settings.splitRatio,
    sessionKey: settings.sessionKey,
    assistantName: "AI Elevate",
    assistantAvatar: null,
    assistantAgentId: null,
    settings,

    setTab: (tab) => set({ tab }),

    setTheme: (theme) => {
      const resolved = resolveTheme(theme as ThemeMode);
      const next = { ...get().settings, theme: theme as ThemeMode };
      saveSettings(next);
      document.documentElement.dataset.theme = resolved;
      document.documentElement.style.colorScheme = resolved;
      set({ theme: theme as ThemeMode, themeResolved: resolved, settings: next });
    },

    setThemeResolved: (resolved) => {
      document.documentElement.dataset.theme = resolved;
      document.documentElement.style.colorScheme = resolved;
      set({ themeResolved: resolved });
    },

    cycleTheme: () => {
      const current = get().theme;
      const order: ThemeMode[] = ["system", "light", "dark"];
      const idx = order.indexOf(current);
      const next = order[(idx + 1) % order.length];
      get().setTheme(next);
    },

    toggleNav: () => {
      const collapsed = !get().navCollapsed;
      const next = { ...get().settings, navCollapsed: collapsed };
      saveSettings(next);
      set({ navCollapsed: collapsed, settings: next });
    },

    toggleNavGroup: (group) => {
      const groups = { ...get().navGroupsCollapsed };
      groups[group] = !groups[group];
      const next = { ...get().settings, navGroupsCollapsed: groups };
      saveSettings(next);
      set({ navGroupsCollapsed: groups, settings: next });
    },

    setChatFocusMode: (v) => {
      const next = { ...get().settings, chatFocusMode: v };
      saveSettings(next);
      set({ chatFocusMode: v, settings: next });
    },

    setChatShowThinking: (v) => {
      const next = { ...get().settings, chatShowThinking: v };
      saveSettings(next);
      set({ chatShowThinking: v, settings: next });
    },

    setSplitRatio: (v) => {
      const next = { ...get().settings, splitRatio: v };
      saveSettings(next);
      set({ splitRatio: v, settings: next });
    },

    setSessionKey: (key) => {
      const next = { ...get().settings, sessionKey: key, lastActiveSessionKey: key };
      saveSettings(next);
      set({ sessionKey: key, settings: next });
    },

    setAssistantIdentity: (name, avatar, agentId) =>
      set({ assistantName: name, assistantAvatar: avatar, assistantAgentId: agentId }),
  };
});

export default useUiStore;
