import { useEffect, useState } from "react";
import { useGatewayStore } from "../stores/gateway.ts";
import { useCronStore } from "../stores/cron.ts";
import PageHeader from "../components/PageHeader.tsx";
import EmptyState from "../components/EmptyState.tsx";
import { Icons } from "../icons.tsx";

type CronJob = {
  id: string;
  name: string;
  schedule: string;
  enabled: boolean;
  lastRun?: number;
  nextRun?: number;
  lastStatus?: string;
};

export default function CronPage() {
  const connected = useGatewayStore((s) => s.connected);
  const cronJobs = useCronStore((s) => s.cronJobs) as CronJob[];
  const cronLoading = useCronStore((s) => s.cronLoading);
  const cronError = useCronStore((s) => s.cronError);
  const cronBusy = useCronStore((s) => s.cronBusy);
  const cronRuns = useCronStore((s) => s.cronRuns);
  const cronRunsJobId = useCronStore((s) => s.cronRunsJobId);
  const load = useCronStore((s) => s.load);
  const toggle = useCronStore((s) => s.toggle);
  const run = useCronStore((s) => s.run);
  const remove = useCronStore((s) => s.remove);

  useEffect(() => {
    if (connected) load();
  }, [connected, load]);

  const handleRemove = (job: CronJob) => {
    if (!window.confirm(`Remove cron job "${job.name}"?`)) return;
    remove(job.id);
  };

  const runs = Array.isArray(cronRuns) ? (cronRuns as Array<{
    id: string; jobId: string; startedAt: number; status: string; durationMs?: number;
  }>) : [];

  const enabledCount = cronJobs.filter((j) => j.enabled).length;

  return (
    <section>
      <PageHeader
        title="Scheduled Tasks"
        description={cronJobs.length > 0 ? `${enabledCount} of ${cronJobs.length} enabled` : "Manage recurring scheduled jobs."}
        actions={
          <button className="btn" onClick={() => load()} disabled={cronLoading}>
            {Icons.refreshCw({ width: "14px", height: "14px" })}
            <span style={{ marginLeft: 6 }}>{cronLoading ? "Loading..." : "Refresh"}</span>
          </button>
        }
      />

      {cronError && <div className="callout danger">{cronError}</div>}

      <div className="card">
        {/* Job list */}
        <div className="list">
          {cronJobs.length === 0 && !cronLoading && (
            <EmptyState
              icon={Icons.barChart}
              title="No scheduled tasks"
              description="Cron jobs let you schedule recurring actions like reports, notifications, or data syncs."
            />
          )}
          {cronLoading && cronJobs.length === 0 && (
            <div className="list-loading">
              <div className="spinner" />
              <span className="muted">Loading jobs...</span>
            </div>
          )}
          {cronJobs.map((job) => (
            <div className="list-item" key={job.id}>
              <div className="list-main">
                <div className="list-title">
                  {job.name}
                  <span className={`chip ${job.enabled ? "chip--ok" : ""}`} style={{ marginLeft: 8 }}>
                    {job.enabled ? "enabled" : "disabled"}
                  </span>
                </div>
                <div className="list-sub">
                  <span className="mono">{job.schedule}</span>
                  {job.lastStatus && <span> | Last run: {job.lastStatus}</span>}
                </div>
              </div>
              <div className="list-meta">
                {job.nextRun && <div className="muted">Next: {new Date(job.nextRun).toLocaleString()}</div>}
                <div className="row" style={{ gap: 4, marginTop: 4 }}>
                  <button
                    className="btn btn--sm"
                    onClick={() => toggle(job.id, !job.enabled)}
                    disabled={cronBusy}
                    title={job.enabled ? "Disable this job" : "Enable this job"}
                  >
                    {job.enabled ? "Disable" : "Enable"}
                  </button>
                  <button
                    className="btn btn--sm primary"
                    onClick={() => run(job.id)}
                    disabled={cronBusy}
                    title="Run this job now"
                  >
                    Run Now
                  </button>
                  <button
                    className="btn btn--sm btn--danger"
                    onClick={() => handleRemove(job)}
                    disabled={cronBusy}
                    title="Remove this job"
                  >
                    {Icons.trash({ width: "12px", height: "12px" })}
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Run history */}
      {cronRunsJobId && (
        <div className="card">
          <div className="card-title">Run History</div>
          <div className="list" style={{ marginTop: 12 }}>
            {runs.length === 0 && (
              <EmptyState title="No runs recorded" description="Run history will appear after a job executes." />
            )}
            {runs.map((r) => (
              <div className="list-item" key={r.id}>
                <div className="list-main">
                  <div className="list-title">{new Date(r.startedAt).toLocaleString()}</div>
                  <div className="list-sub">
                    <span className={`chip ${r.status === "success" ? "chip--ok" : r.status === "error" ? "chip--danger" : ""}`}>
                      {r.status}
                    </span>
                    {r.durationMs != null && <span className="muted" style={{ marginLeft: 8 }}>{r.durationMs}ms</span>}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
