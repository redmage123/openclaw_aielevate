import { useEffect } from "react";
import { useGatewayStore } from "../stores/gateway.ts";
import { usePresenceStore } from "../stores/presence.ts";
import PageHeader from "../components/PageHeader.tsx";
import EmptyState from "../components/EmptyState.tsx";
import { Icons } from "../icons.tsx";

type PresenceEntry = {
  host?: string;
  mode?: string;
  roles?: string[];
  platform?: string;
  version?: string;
  lastInputSeconds?: number;
  reason?: string;
  connectedAt?: number;
};

function formatAge(connectedAt?: number): string {
  if (!connectedAt) return "--";
  const secs = Math.floor((Date.now() - connectedAt) / 1000);
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m`;
  const h = Math.floor(secs / 3600);
  if (h < 24) return `${h}h ${Math.floor((secs % 3600) / 60)}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

export default function InstancesPage() {
  const connected = useGatewayStore((s) => s.connected);
  const presenceEntries = usePresenceStore((s) => s.presenceEntries) as PresenceEntry[];
  const presenceLoading = usePresenceStore((s) => s.presenceLoading);
  const presenceError = usePresenceStore((s) => s.presenceError);
  const load = usePresenceStore((s) => s.load);

  useEffect(() => {
    if (connected) load();
  }, [connected, load]);

  return (
    <section>
      <PageHeader
        title="Instances"
        description={presenceEntries.length > 0 ? `${presenceEntries.length} connected` : "Connected clients and presence beacons."}
        actions={
          <button className="btn" onClick={() => load()} disabled={presenceLoading}>
            {Icons.refreshCw({ width: "14px", height: "14px" })}
            <span style={{ marginLeft: 6 }}>{presenceLoading ? "Loading..." : "Refresh"}</span>
          </button>
        }
      />

      {presenceError && <div className="callout danger">{presenceError}</div>}

      <div className="card">
        <div className="list">
          {presenceEntries.length === 0 && !presenceLoading && (
            <EmptyState
              icon={Icons.globe}
              title="No instances online"
              description="Connected clients and their presence beacons will appear here."
            />
          )}
          {presenceLoading && presenceEntries.length === 0 && (
            <div className="list-loading">
              <div className="spinner" />
              <span className="muted">Loading instances...</span>
            </div>
          )}
          {presenceEntries.map((entry, i) => {
            const roles = entry.roles?.filter(Boolean) ?? [];
            return (
              <div className="list-item" key={entry.host ?? `entry-${i}`}>
                <div className="list-main">
                  <div className="list-title">{entry.host ?? "Unknown"}</div>
                  <div className="chip-row">
                    <span className="chip">{entry.mode ?? "unknown"}</span>
                    {roles.map((role) => <span className="chip" key={role}>{role}</span>)}
                    {entry.platform && <span className="chip">{entry.platform}</span>}
                    {entry.version && <span className="chip mono">{entry.version}</span>}
                  </div>
                </div>
                <div className="list-meta">
                  <div className="muted">Connected {formatAge(entry.connectedAt)}</div>
                  {entry.lastInputSeconds != null && (
                    <div className="muted">Last input {entry.lastInputSeconds}s ago</div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
