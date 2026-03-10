import { useEffect } from "react";
import { useGatewayStore } from "../stores/gateway.ts";
import { usePresenceStore } from "../stores/presence.ts";
import { useSessionsStore } from "../stores/sessions.ts";
import { useChannelsStore } from "../stores/channels.ts";
import { Icons } from "../icons.tsx";
import PageHeader from "../components/PageHeader.tsx";

function formatUptime(ms?: number): string {
  if (!ms) return "--";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 24) {
    const d = Math.floor(h / 24);
    return `${d}d ${h % 24}h`;
  }
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

export default function OverviewPage() {
  const connected = useGatewayStore((s) => s.connected);
  const hello = useGatewayStore((s) => s.hello);
  const lastError = useGatewayStore((s) => s.lastError);

  const presenceEntries = usePresenceStore((s) => s.presenceEntries);
  const loadPresence = usePresenceStore((s) => s.load);

  const sessionsResult = useSessionsStore((s) => s.sessionsResult);
  const loadSessions = useSessionsStore((s) => s.load);

  const channelsSnapshot = useChannelsStore((s) => s.channelsSnapshot);
  const loadChannels = useChannelsStore((s) => s.load);

  const snapshot = hello?.snapshot as Record<string, unknown> | undefined;
  const uptime = formatUptime(snapshot?.uptimeMs as number | undefined);
  const serverVersion = hello?.server?.version;

  useEffect(() => {
    if (connected) {
      loadPresence();
      loadSessions();
      loadChannels();
    }
  }, [connected, loadPresence, loadSessions, loadChannels]);

  const handleRefresh = () => {
    loadPresence();
    loadSessions();
    loadChannels();
  };

  const sessionsArray = Array.isArray(sessionsResult) ? sessionsResult : [];
  const channels = Array.isArray(channelsSnapshot) ? channelsSnapshot : [];
  const connectedChannels = channels.filter((c: { connected?: boolean }) => c.connected);

  return (
    <section>
      <PageHeader
        title="Dashboard"
        description="Gateway status and quick metrics at a glance."
        actions={
          <button className="btn" onClick={handleRefresh}>
            {Icons.refreshCw({ width: "14px", height: "14px" })}
            <span style={{ marginLeft: 6 }}>Refresh</span>
          </button>
        }
      />

      {/* Status bar */}
      <div className="overview-status-bar">
        <div className={`overview-status-indicator ${connected ? "overview-status-indicator--ok" : "overview-status-indicator--error"}`}>
          <span className={`statusDot ${connected ? "ok" : ""}`} />
          <span>{connected ? "Connected" : "Disconnected"}</span>
        </div>
        {serverVersion && <span className="pill mono">v{serverVersion}</span>}
        <span className="muted">Uptime: {uptime}</span>
      </div>

      {lastError && (
        <div className="callout danger">{lastError}</div>
      )}

      {/* Metric cards */}
      <div className="stat-grid">
        <StatCard
          icon={Icons.radio}
          label="Channels"
          value={channels.length > 0 ? `${connectedChannels.length}/${channels.length}` : "--"}
          sub="connected"
          status={connectedChannels.length > 0 ? "ok" : undefined}
        />
        <StatCard
          icon={Icons.fileText}
          label="Sessions"
          value={sessionsArray.length > 0 ? String(sessionsArray.length) : "--"}
          sub="active"
        />
        <StatCard
          icon={Icons.globe}
          label="Instances"
          value={String(presenceEntries.length)}
          sub="online"
          status={presenceEntries.length > 0 ? "ok" : undefined}
        />
        <StatCard
          icon={Icons.zap}
          label="Gateway"
          value={connected ? "Online" : "Offline"}
          sub={connected ? "operational" : "check connection"}
          status={connected ? "ok" : "error"}
        />
      </div>
    </section>
  );
}

function StatCard({ icon, label, value, sub, status }: {
  icon: (p?: React.SVGProps<SVGSVGElement>) => React.ReactNode;
  label: string;
  value: string;
  sub: string;
  status?: "ok" | "error";
}) {
  return (
    <div className="card stat-card">
      <div className="stat-card__icon">{icon({ width: "20px", height: "20px" })}</div>
      <div className="stat-card__label">{label}</div>
      <div className={`stat-card__value ${status ? `stat-card__value--${status}` : ""}`}>{value}</div>
      <div className="stat-card__sub">{sub}</div>
    </div>
  );
}
