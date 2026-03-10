import { create } from "zustand";
import { createControllerProxy } from "./controller-proxy";

// TODO: wire loadCronJobs, toggleCronJob, runCronJob, removeCronJob from ../../ui/controllers/cron

type CronState = {
  cronLoading: boolean;
  cronJobsLoadingMore: boolean;
  cronJobs: unknown[];
  cronJobsTotal: number;
  cronJobsHasMore: boolean;
  cronJobsNextOffset: number | null;
  cronJobsLimit: number;
  cronJobsQuery: string;
  cronJobsEnabledFilter: string;
  cronJobsSortBy: string;
  cronJobsSortDir: string;
  cronStatus: unknown;
  cronError: string | null;
  cronForm: unknown;
  cronFieldErrors: Record<string, string>;
  cronEditingJobId: string | null;
  cronRunsJobId: string | null;
  cronRunsLoadingMore: boolean;
  cronRuns: unknown[];
  cronRunsTotal: number;
  cronRunsHasMore: boolean;
  cronRunsNextOffset: number | null;
  cronRunsLimit: number;
  cronRunsScope: string;
  cronRunsStatuses: string[];
  cronRunsDeliveryStatuses: string[];
  cronRunsStatusFilter: string;
  cronRunsQuery: string;
  cronRunsSortDir: string;
  cronBusy: boolean;
};

type CronActions = {
  load: () => Promise<void>;
  toggle: (jobId: string, enabled: boolean) => Promise<void>;
  run: (jobId: string) => Promise<void>;
  remove: (jobId: string) => Promise<void>;
};

export const useCronStore = create<CronState & CronActions>(() => ({
  cronLoading: false,
  cronJobsLoadingMore: false,
  cronJobs: [],
  cronJobsTotal: 0,
  cronJobsHasMore: false,
  cronJobsNextOffset: null,
  cronJobsLimit: 50,
  cronJobsQuery: "",
  cronJobsEnabledFilter: "all",
  cronJobsSortBy: "nextRunAt",
  cronJobsSortDir: "asc",
  cronStatus: null,
  cronError: null,
  cronForm: null,
  cronFieldErrors: {},
  cronEditingJobId: null,
  cronRunsJobId: null,
  cronRunsLoadingMore: false,
  cronRuns: [],
  cronRunsTotal: 0,
  cronRunsHasMore: false,
  cronRunsNextOffset: null,
  cronRunsLimit: 50,
  cronRunsScope: "all",
  cronRunsStatuses: [],
  cronRunsDeliveryStatuses: [],
  cronRunsStatusFilter: "all",
  cronRunsQuery: "",
  cronRunsSortDir: "desc",
  cronBusy: false,

  load: async () => {
    // TODO: call loadCronJobs via controller proxy
    const proxy = createControllerProxy();
    void proxy;
  },

  toggle: async (_jobId, _enabled) => {
    // TODO: call toggleCronJob via controller proxy
    const proxy = createControllerProxy();
    void proxy;
  },

  run: async (_jobId) => {
    // TODO: call runCronJob via controller proxy
    const proxy = createControllerProxy();
    void proxy;
  },

  remove: async (_jobId) => {
    // TODO: call removeCronJob via controller proxy
    const proxy = createControllerProxy();
    void proxy;
  },
}));

export default useCronStore;
