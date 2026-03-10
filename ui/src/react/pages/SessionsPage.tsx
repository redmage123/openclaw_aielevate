import { useEffect } from "react";
import { useGatewayStore } from "../stores/gateway.ts";
import { useSessionsStore } from "../stores/sessions.ts";
import PageHeader from "../components/PageHeader.tsx";
import EmptyState from "../components/EmptyState.tsx";
import { Icons } from "../icons.tsx";

type SessionRow = {
  key: string;
  agentId?: string;
  created?: number;
  lastActivity?: number;
  messageCount?: number;
  label?: string;
};

function formatRelativeTime(ts?: number): string {
  if (!ts) return "";
  const diff = Math.floor((Date.now() - ts) / 1000);
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export default function SessionsPage() {
  const connected = useGatewayStore((s) => s.connected);
  const sessionsResult = useSessionsStore((s) => s.sessionsResult);
  const sessionsLoading = useSessionsStore((s) => s.sessionsLoading);
  const sessionsError = useSessionsStore((s) => s.sessionsError);
  const load = useSessionsStore((s) => s.load);
  const deleteSession = useSessionsStore((s) => s.deleteSession);
  const activeMinutes = useSessionsStore((s) => s.sessionsFilterActive);
  const setFilterActive = useSessionsStore((s) => s.setFilterActive);
  const limit = useSessionsStore((s) => s.sessionsFilterLimit);
  const setFilterLimit = useSessionsStore((s) => s.setFilterLimit);

  useEffect(() => {
    if (connected) load();
  }, [connected, load]);

  const sessions: SessionRow[] = Array.isArray(sessionsResult)
    ? (sessionsResult as SessionRow[])
    : [];

  const handleDelete = (key: string) => {
    if (!window.confirm(`Delete session "${key}"?`)) return;
    deleteSession(key);
  };

  return (
    <section>
      <PageHeader
        title="Sessions"
        description={sessions.length > 0 ? `${sessions.length} active sessions` : "Active and recent gateway sessions."}
        actions={
          <button className="btn" onClick={() => load()} disabled={sessionsLoading}>
            {Icons.refreshCw({ width: "14px", height: "14px" })}
            <span style={{ marginLeft: 6 }}>{sessionsLoading ? "Loading..." : "Refresh"}</span>
          </button>
        }
      />

      <div className="card">
        {/* Filters */}
        <div className="filters">
          <label className="field">
            <span>Active within (minutes)</span>
            <input
              value={activeMinutes}
              onChange={(e) => setFilterActive(e.target.value)}
              type="number"
              min={0}
              style={{ width: 100 }}
              placeholder="Any"
            />
          </label>
          <label className="field">
            <span>Max results</span>
            <input
              value={limit}
              onChange={(e) => setFilterLimit(e.target.value)}
              type="number"
              min={1}
              max={500}
              style={{ width: 100 }}
              placeholder="50"
            />
          </label>
        </div>

        {sessionsError && (
          <div className="callout danger" style={{ marginTop: 12 }}>{sessionsError}</div>
        )}

        {/* Sessions list */}
        <div className="list" style={{ marginTop: 16 }}>
          {sessions.length === 0 && !sessionsLoading && (
            <EmptyState
              icon={Icons.fileText}
              title="No sessions"
              description="Sessions will appear here as users start conversations."
            />
          )}
          {sessionsLoading && sessions.length === 0 && (
            <div className="list-loading">
              <div className="spinner" />
              <span className="muted">Loading sessions...</span>
            </div>
          )}
          {sessions.map((s) => (
            <div className="list-item" key={s.key}>
              <div className="list-main">
                <div className="list-title">{s.label ?? s.key}</div>
                <div className="list-sub">
                  {s.agentId && <span>Agent: {s.agentId}</span>}
                  {s.messageCount != null && (
                    <span>{s.agentId ? " | " : ""}{s.messageCount} messages</span>
                  )}
                </div>
              </div>
              <div className="list-meta">
                {s.lastActivity && (
                  <div className="muted">{formatRelativeTime(s.lastActivity)}</div>
                )}
                <button
                  className="btn btn--sm btn--danger"
                  onClick={() => handleDelete(s.key)}
                  title={`Delete session ${s.key}`}
                >
                  {Icons.trash({ width: "12px", height: "12px" })}
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
