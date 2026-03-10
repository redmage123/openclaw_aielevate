import { useEffect } from "react";
import { useGatewayStore } from "../stores/gateway.ts";
import { useChannelsStore } from "../stores/channels.ts";
import PageHeader from "../components/PageHeader.tsx";
import EmptyState from "../components/EmptyState.tsx";
import { Icons } from "../icons.tsx";

type ChannelInfo = {
  id: string;
  label: string;
  connected: boolean;
  account?: string;
};

export default function ChannelsPage() {
  const connected = useGatewayStore((s) => s.connected);
  const channelsSnapshot = useChannelsStore((s) => s.channelsSnapshot);
  const channelsLoading = useChannelsStore((s) => s.channelsLoading);
  const channelsError = useChannelsStore((s) => s.channelsError);
  const channelsLastSuccess = useChannelsStore((s) => s.channelsLastSuccess);
  const load = useChannelsStore((s) => s.load);

  useEffect(() => {
    if (connected) load();
  }, [connected, load]);

  const channels: ChannelInfo[] = Array.isArray(channelsSnapshot)
    ? (channelsSnapshot as ChannelInfo[])
    : [];

  const connectedCount = channels.filter((c) => c.connected).length;

  return (
    <section>
      <PageHeader
        title="Channels"
        description={channels.length > 0 ? `${connectedCount} of ${channels.length} connected` : "Messaging channel connections and status."}
        actions={
          <div className="row" style={{ gap: 8 }}>
            {channelsLastSuccess && (
              <span className="muted" style={{ fontSize: 12 }}>
                Updated {new Date(channelsLastSuccess).toLocaleTimeString()}
              </span>
            )}
            <button className="btn" onClick={() => load()} disabled={channelsLoading}>
              {Icons.refreshCw({ width: "14px", height: "14px" })}
              <span style={{ marginLeft: 6 }}>{channelsLoading ? "Loading..." : "Refresh"}</span>
            </button>
          </div>
        }
      />

      {channelsError && (
        <div className="callout danger">{channelsError}</div>
      )}

      {/* Skeleton placeholders while loading */}
      {channels.length === 0 && channelsLoading && (
        <div className="grid grid-cols-2">
          {[1, 2, 3, 4].map((i) => (
            <div className="card" key={i}>
              <div className="skeleton" style={{ width: "40%", height: 16 }} />
              <div className="skeleton" style={{ width: "60%", height: 12, marginTop: 8 }} />
            </div>
          ))}
        </div>
      )}

      {/* Empty state */}
      {channels.length === 0 && !channelsLoading && connected && (
        <EmptyState
          icon={Icons.radio}
          title="No channels configured"
          description="Connect messaging channels like WhatsApp, Telegram, Discord, or Slack to start receiving messages."
        />
      )}

      {/* Channel cards */}
      {channels.length > 0 && (
        <div className="grid grid-cols-2">
          {channels.map((ch) => (
            <div className={`card channel-card ${ch.connected ? "channel-card--connected" : ""}`} key={ch.id}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <div className="card-title" style={{ textTransform: "capitalize" }}>
                  {ch.label}
                </div>
                <span className={`chip ${ch.connected ? "chip--ok" : ""}`}>
                  {ch.connected ? "connected" : "offline"}
                </span>
              </div>
              {ch.account && (
                <div className="card-sub" style={{ marginTop: 4 }}>{ch.account}</div>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
