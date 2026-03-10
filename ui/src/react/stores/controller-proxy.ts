import { useGatewayStore } from "./gateway";
import { useChatStore } from "./chat";
import { useUiStore } from "./ui";
import { useAuthStore } from "./auth";
import { useSessionsStore } from "./sessions";
import { useChannelsStore } from "./channels";
import { useConfigStore } from "./config";
import { useAgentsStore } from "./agents";
import { useSkillsStore } from "./skills";
import { useCronStore } from "./cron";
import { useDebugStore } from "./debug";
import { useLogsStore } from "./logs";
import { useNodesStore } from "./nodes";
import { usePresenceStore } from "./presence";

type StoreRef = { getState: () => Record<string, unknown>; setState: (partial: Record<string, unknown>) => void };

// All registered stores for fallback lookup
const ALL_STORES: Array<() => StoreRef> = [
  () => useGatewayStore as unknown as StoreRef,
  () => useChatStore as unknown as StoreRef,
  () => useUiStore as unknown as StoreRef,
  () => useAuthStore as unknown as StoreRef,
  () => useSessionsStore as unknown as StoreRef,
  () => useChannelsStore as unknown as StoreRef,
  () => useConfigStore as unknown as StoreRef,
  () => useAgentsStore as unknown as StoreRef,
  () => useSkillsStore as unknown as StoreRef,
  () => useCronStore as unknown as StoreRef,
  () => useDebugStore as unknown as StoreRef,
  () => useLogsStore as unknown as StoreRef,
  () => useNodesStore as unknown as StoreRef,
  () => usePresenceStore as unknown as StoreRef,
];

// Map property names to the store that owns them.
// Using thunks so stores are resolved at call time (avoids circular-init issues).
const PROPERTY_STORE_MAP: Record<string, () => StoreRef> = {
  // Gateway store
  connected: () => useGatewayStore as unknown as StoreRef,
  client: () => useGatewayStore as unknown as StoreRef,
  hello: () => useGatewayStore as unknown as StoreRef,
  lastError: () => useGatewayStore as unknown as StoreRef,
  lastErrorCode: () => useGatewayStore as unknown as StoreRef,
  updateAvailable: () => useGatewayStore as unknown as StoreRef,

  // Chat store (controller names -> zustand names)
  chatLoading: () => useChatStore as unknown as StoreRef,
  chatSending: () => useChatStore as unknown as StoreRef,
  chatMessage: () => useChatStore as unknown as StoreRef,
  chatMessages: () => useChatStore as unknown as StoreRef,
  chatToolMessages: () => useChatStore as unknown as StoreRef,
  chatStream: () => useChatStore as unknown as StoreRef,
  chatStreamStartedAt: () => useChatStore as unknown as StoreRef,
  chatRunId: () => useChatStore as unknown as StoreRef,
  chatThinkingLevel: () => useChatStore as unknown as StoreRef,
  chatAttachments: () => useChatStore as unknown as StoreRef,
  chatAvatarUrl: () => useChatStore as unknown as StoreRef,

  // UI store
  tab: () => useUiStore as unknown as StoreRef,
  sessionKey: () => useUiStore as unknown as StoreRef,
  assistantName: () => useUiStore as unknown as StoreRef,
  assistantAvatar: () => useUiStore as unknown as StoreRef,
  assistantAgentId: () => useUiStore as unknown as StoreRef,

  // Auth store
  authState: () => useAuthStore as unknown as StoreRef,
  authUser: () => useAuthStore as unknown as StoreRef,
  authError: () => useAuthStore as unknown as StoreRef,
  authLoading: () => useAuthStore as unknown as StoreRef,

  // Sessions store
  sessionsLoading: () => useSessionsStore as unknown as StoreRef,
  sessionsResult: () => useSessionsStore as unknown as StoreRef,
  sessionsError: () => useSessionsStore as unknown as StoreRef,
  sessionsFilterActive: () => useSessionsStore as unknown as StoreRef,
  sessionsFilterLimit: () => useSessionsStore as unknown as StoreRef,
  sessionsIncludeGlobal: () => useSessionsStore as unknown as StoreRef,
  sessionsIncludeUnknown: () => useSessionsStore as unknown as StoreRef,

  // Channels store
  channelsLoading: () => useChannelsStore as unknown as StoreRef,
  channelsSnapshot: () => useChannelsStore as unknown as StoreRef,
  channelsError: () => useChannelsStore as unknown as StoreRef,
  channelsLastSuccess: () => useChannelsStore as unknown as StoreRef,
  whatsappLoginMessage: () => useChannelsStore as unknown as StoreRef,
  whatsappLoginQrDataUrl: () => useChannelsStore as unknown as StoreRef,
  whatsappLoginConnected: () => useChannelsStore as unknown as StoreRef,
  whatsappBusy: () => useChannelsStore as unknown as StoreRef,

  // Config store
  configLoading: () => useConfigStore as unknown as StoreRef,
  configRaw: () => useConfigStore as unknown as StoreRef,
  configRawOriginal: () => useConfigStore as unknown as StoreRef,
  configValid: () => useConfigStore as unknown as StoreRef,
  configIssues: () => useConfigStore as unknown as StoreRef,
  configSaving: () => useConfigStore as unknown as StoreRef,
  configApplying: () => useConfigStore as unknown as StoreRef,
  configSnapshot: () => useConfigStore as unknown as StoreRef,
  configSchema: () => useConfigStore as unknown as StoreRef,
  configSchemaVersion: () => useConfigStore as unknown as StoreRef,
  configSchemaLoading: () => useConfigStore as unknown as StoreRef,
  configUiHints: () => useConfigStore as unknown as StoreRef,
  configForm: () => useConfigStore as unknown as StoreRef,
  configFormOriginal: () => useConfigStore as unknown as StoreRef,
  configFormMode: () => useConfigStore as unknown as StoreRef,
  configSearchQuery: () => useConfigStore as unknown as StoreRef,
  configFormDirty: () => useConfigStore as unknown as StoreRef,
  configActiveSection: () => useConfigStore as unknown as StoreRef,
  configActiveSubsection: () => useConfigStore as unknown as StoreRef,
  applySessionKey: () => useConfigStore as unknown as StoreRef,
  updateRunning: () => useConfigStore as unknown as StoreRef,

  // Agents store
  agentsLoading: () => useAgentsStore as unknown as StoreRef,
  agentsList: () => useAgentsStore as unknown as StoreRef,
  agentsError: () => useAgentsStore as unknown as StoreRef,
  agentsSelectedId: () => useAgentsStore as unknown as StoreRef,
  agentsPanel: () => useAgentsStore as unknown as StoreRef,
  toolsCatalogLoading: () => useAgentsStore as unknown as StoreRef,
  toolsCatalogResult: () => useAgentsStore as unknown as StoreRef,
  toolsCatalogError: () => useAgentsStore as unknown as StoreRef,
  agentFilesLoading: () => useAgentsStore as unknown as StoreRef,
  agentFilesList: () => useAgentsStore as unknown as StoreRef,
  agentFilesError: () => useAgentsStore as unknown as StoreRef,

  // Skills store
  skillsLoading: () => useSkillsStore as unknown as StoreRef,
  skillsReport: () => useSkillsStore as unknown as StoreRef,
  skillsError: () => useSkillsStore as unknown as StoreRef,
  skillsFilter: () => useSkillsStore as unknown as StoreRef,
  skillsBusyKey: () => useSkillsStore as unknown as StoreRef,
  skillEdits: () => useSkillsStore as unknown as StoreRef,
  skillMessages: () => useSkillsStore as unknown as StoreRef,

  // Cron store
  cronLoading: () => useCronStore as unknown as StoreRef,
  cronJobs: () => useCronStore as unknown as StoreRef,
  cronJobsTotal: () => useCronStore as unknown as StoreRef,
  cronJobsHasMore: () => useCronStore as unknown as StoreRef,
  cronJobsNextOffset: () => useCronStore as unknown as StoreRef,
  cronJobsLimit: () => useCronStore as unknown as StoreRef,
  cronJobsQuery: () => useCronStore as unknown as StoreRef,
  cronJobsEnabledFilter: () => useCronStore as unknown as StoreRef,
  cronJobsSortBy: () => useCronStore as unknown as StoreRef,
  cronJobsSortDir: () => useCronStore as unknown as StoreRef,
  cronJobsLoadingMore: () => useCronStore as unknown as StoreRef,
  cronStatus: () => useCronStore as unknown as StoreRef,
  cronError: () => useCronStore as unknown as StoreRef,
  cronForm: () => useCronStore as unknown as StoreRef,
  cronFieldErrors: () => useCronStore as unknown as StoreRef,
  cronEditingJobId: () => useCronStore as unknown as StoreRef,
  cronRunsJobId: () => useCronStore as unknown as StoreRef,
  cronRuns: () => useCronStore as unknown as StoreRef,
  cronRunsTotal: () => useCronStore as unknown as StoreRef,
  cronRunsHasMore: () => useCronStore as unknown as StoreRef,
  cronRunsNextOffset: () => useCronStore as unknown as StoreRef,
  cronRunsLimit: () => useCronStore as unknown as StoreRef,
  cronRunsScope: () => useCronStore as unknown as StoreRef,
  cronRunsStatuses: () => useCronStore as unknown as StoreRef,
  cronRunsDeliveryStatuses: () => useCronStore as unknown as StoreRef,
  cronRunsStatusFilter: () => useCronStore as unknown as StoreRef,
  cronRunsQuery: () => useCronStore as unknown as StoreRef,
  cronRunsSortDir: () => useCronStore as unknown as StoreRef,
  cronRunsLoadingMore: () => useCronStore as unknown as StoreRef,
  cronBusy: () => useCronStore as unknown as StoreRef,

  // Debug store
  debugLoading: () => useDebugStore as unknown as StoreRef,
  debugStatus: () => useDebugStore as unknown as StoreRef,
  debugHealth: () => useDebugStore as unknown as StoreRef,
  debugModels: () => useDebugStore as unknown as StoreRef,
  debugHeartbeat: () => useDebugStore as unknown as StoreRef,
  debugCallMethod: () => useDebugStore as unknown as StoreRef,
  debugCallParams: () => useDebugStore as unknown as StoreRef,
  debugCallResult: () => useDebugStore as unknown as StoreRef,
  debugCallError: () => useDebugStore as unknown as StoreRef,

  // Logs store
  logsLoading: () => useLogsStore as unknown as StoreRef,
  logsError: () => useLogsStore as unknown as StoreRef,
  logsCursor: () => useLogsStore as unknown as StoreRef,
  logsFile: () => useLogsStore as unknown as StoreRef,
  logsEntries: () => useLogsStore as unknown as StoreRef,
  logsTruncated: () => useLogsStore as unknown as StoreRef,
  logsLastFetchAt: () => useLogsStore as unknown as StoreRef,
  logsLimit: () => useLogsStore as unknown as StoreRef,
  logsMaxBytes: () => useLogsStore as unknown as StoreRef,
  logsFilterText: () => useLogsStore as unknown as StoreRef,
  logsLevelFilters: () => useLogsStore as unknown as StoreRef,
  logsAutoFollow: () => useLogsStore as unknown as StoreRef,

  // Nodes store
  nodesLoading: () => useNodesStore as unknown as StoreRef,
  nodes: () => useNodesStore as unknown as StoreRef,
  devicesLoading: () => useNodesStore as unknown as StoreRef,
  devicesList: () => useNodesStore as unknown as StoreRef,
  devicesError: () => useNodesStore as unknown as StoreRef,

  // Presence store
  presenceLoading: () => usePresenceStore as unknown as StoreRef,
  presenceEntries: () => usePresenceStore as unknown as StoreRef,
  presenceError: () => usePresenceStore as unknown as StoreRef,
  presenceStatus: () => usePresenceStore as unknown as StoreRef,
};

/**
 * Creates a mutable proxy that bridges legacy controllers (which mutate a state object)
 * with zustand stores (which use immutable set()). Property reads are resolved from the
 * owning store's getState(); property writes are dispatched to the owning store's setState().
 *
 * A local write cache ensures reads-after-writes within a single controller call see
 * the written value immediately, even before React re-renders.
 */
export function createControllerProxy(): Record<string, unknown> {
  const writes: Record<string, unknown> = {};

  return new Proxy({} as Record<string, unknown>, {
    get(_target, prop: string) {
      // Return locally-written value first (read-after-write consistency)
      if (prop in writes) return writes[prop];

      // Known property -> read from its store
      const storeGetter = PROPERTY_STORE_MAP[prop];
      if (storeGetter) {
        return storeGetter().getState()[prop];
      }

      // Fallback: scan all stores
      for (const getStore of ALL_STORES) {
        const state = getStore().getState();
        if (prop in state) return state[prop];
      }
      return undefined;
    },

    set(_target, prop: string, value) {
      writes[prop] = value;

      // Known property -> write to its store
      const storeGetter = PROPERTY_STORE_MAP[prop];
      if (storeGetter) {
        storeGetter().setState({ [prop]: value });
        return true;
      }

      // Fallback: find the owning store
      for (const getStore of ALL_STORES) {
        if (prop in getStore().getState()) {
          getStore().setState({ [prop]: value });
          return true;
        }
      }

      // Unknown property: store locally only (no store to dispatch to)
      return true;
    },

    has(_target, prop: string) {
      if (prop in writes) return true;
      if (prop in PROPERTY_STORE_MAP) return true;
      for (const getStore of ALL_STORES) {
        if (prop in getStore().getState()) return true;
      }
      return false;
    },
  });
}

/**
 * Creates a controller proxy with a pushDebugLog helper pre-attached,
 * suitable for chat controller functions that call state.pushDebugLog().
 */
export function createChatProxy(): Record<string, unknown> {
  const proxy = createControllerProxy();
  proxy.pushDebugLog = (level: string, message: string) => {
    useChatStore.getState().addDebugEntry({ ts: Date.now(), level, message });
  };
  return proxy;
}
