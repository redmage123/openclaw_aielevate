import { useEffect, useRef } from "react";
import { useGatewayStore } from "../stores/gateway.ts";
import { useLogsStore } from "../stores/logs.ts";
import { usePolling } from "../hooks/usePolling.ts";
import PageHeader from "../components/PageHeader.tsx";
import EmptyState from "../components/EmptyState.tsx";
import { Icons } from "../icons.tsx";

type LogEntry = {
  timestamp?: string;
  level?: string;
  message: string;
  raw: string;
};

const LEVELS = ["debug", "info", "warn", "error"] as const;

function formatTime(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString();
}

export default function LogsPage() {
  const connected = useGatewayStore((s) => s.connected);
  const logsEntries = useLogsStore((s) => s.logsEntries) as LogEntry[];
  const logsLoading = useLogsStore((s) => s.logsLoading);
  const logsError = useLogsStore((s) => s.logsError);
  const logsFilterText = useLogsStore((s) => s.logsFilterText);
  const logsLevelFilters = useLogsStore((s) => s.logsLevelFilters);
  const logsAutoFollow = useLogsStore((s) => s.logsAutoFollow);
  const load = useLogsStore((s) => s.load);
  const setFilterText = useLogsStore((s) => s.setFilterText);
  const toggleLevel = useLogsStore((s) => s.toggleLevel);
  const setAutoFollow = useLogsStore((s) => s.setAutoFollow);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (connected) load({ reset: true });
  }, [connected, load]);

  usePolling(() => load({ quiet: true }), 5000, connected && logsAutoFollow);

  useEffect(() => {
    if (logsAutoFollow && listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [logsEntries.length, logsAutoFollow]);

  const needle = logsFilterText.trim().toLowerCase();
  const filtered = logsEntries.filter((entry) => {
    if (entry.level && !logsLevelFilters[entry.level]) return false;
    if (!needle) return true;
    return [entry.message, entry.raw].join(" ").toLowerCase().includes(needle);
  });

  return (
    <section>
      <PageHeader
        title="Logs"
        description="Real-time gateway log stream."
        actions={
          <button className="btn" onClick={() => load({ reset: true })} disabled={logsLoading}>
            {Icons.refreshCw({ width: "14px", height: "14px" })}
            <span style={{ marginLeft: 6 }}>{logsLoading ? "Loading..." : "Refresh"}</span>
          </button>
        }
      />

      {/* Filters */}
      <div className="card">
        <div className="log-filters">
          <div className="log-filters__search">
            <input
              className="log-search-input"
              value={logsFilterText}
              onChange={(e) => setFilterText(e.target.value)}
              placeholder="Filter logs..."
            />
          </div>
          <div className="log-filters__levels">
            {LEVELS.map((level) => (
              <button
                key={level}
                className={`log-level-btn log-level-btn--${level} ${logsLevelFilters[level] !== false ? "log-level-btn--active" : ""}`}
                onClick={() => toggleLevel(level)}
              >
                {level.toUpperCase()}
              </button>
            ))}
          </div>
          <label className="log-filters__follow">
            <input
              type="checkbox"
              checked={logsAutoFollow}
              onChange={(e) => setAutoFollow(e.target.checked)}
            />
            <span>Auto-follow</span>
          </label>
          <span className="muted" style={{ fontSize: 12 }}>{filtered.length} entries</span>
        </div>

        {logsError && <div className="callout danger" style={{ marginTop: 12 }}>{logsError}</div>}

        {/* Log entries */}
        <div ref={listRef} className="log-viewer">
          {filtered.length === 0 && !logsLoading && (
            <EmptyState
              icon={Icons.fileText}
              title="No log entries"
              description={needle ? "No entries match your filter." : "Logs will appear here as the gateway generates them."}
            />
          )}
          {logsLoading && logsEntries.length === 0 && (
            <div className="log-viewer__loading">
              <div className="spinner" />
              <span className="muted">Loading logs...</span>
            </div>
          )}
          {filtered.map((entry, i) => (
            <div
              key={`${entry.timestamp ?? ""}-${i}`}
              className={`log-entry log-entry--${entry.level ?? "info"}`}
            >
              <span className="log-entry__time">{formatTime(entry.timestamp)}</span>
              {entry.level && (
                <span className={`log-entry__level log-entry__level--${entry.level}`}>
                  {entry.level.toUpperCase()}
                </span>
              )}
              <span className="log-entry__msg">{entry.message}</span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
