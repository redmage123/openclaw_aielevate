import { useEffect } from "react";
import { useGatewayStore } from "../stores/gateway.ts";
import { useNodesStore } from "../stores/nodes.ts";
import PageHeader from "../components/PageHeader.tsx";
import EmptyState from "../components/EmptyState.tsx";
import { Icons } from "../icons.tsx";

type NodeEntry = {
  id: string;
  address?: string;
  status: string;
  platform?: string;
  version?: string;
};

type PendingDevice = {
  requestId: string;
  host?: string;
  requestedAt: number;
};

type PairedDevice = {
  deviceId: string;
  host?: string;
  role: string;
  pairedAt: number;
};

export default function NodesPage() {
  const connected = useGatewayStore((s) => s.connected);
  const nodesRaw = useNodesStore((s) => s.nodes);
  const nodesLoading = useNodesStore((s) => s.nodesLoading);
  const devicesList = useNodesStore((s) => s.devicesList);
  const devicesLoading = useNodesStore((s) => s.devicesLoading);
  const loadNodes = useNodesStore((s) => s.load);
  const loadDevices = useNodesStore((s) => s.loadDevices);

  useEffect(() => {
    if (connected) {
      loadNodes();
      loadDevices();
    }
  }, [connected, loadNodes, loadDevices]);

  const nodes: NodeEntry[] = Array.isArray(nodesRaw)
    ? (nodesRaw as NodeEntry[])
    : [];

  const devicesObj = devicesList as { pending?: PendingDevice[]; paired?: PairedDevice[] } | null;
  const pending: PendingDevice[] = devicesObj?.pending ?? [];
  const paired: PairedDevice[] = devicesObj?.paired ?? [];

  return (
    <section>
      <PageHeader
        title="Nodes & Devices"
        description="Paired devices, pending requests, and live gateway nodes."
        actions={
          <div className="row" style={{ gap: 8 }}>
            <button className="btn" onClick={() => { loadNodes(); loadDevices(); }} disabled={nodesLoading || devicesLoading}>
              {Icons.refreshCw({ width: "14px", height: "14px" })}
              <span style={{ marginLeft: 6 }}>Refresh</span>
            </button>
          </div>
        }
      />

      {/* Pending pairing requests */}
      {pending.length > 0 && (
        <div className="card card--highlight">
          <div className="card-title">
            {Icons.alertTriangle({ width: "16px", height: "16px" })}
            <span style={{ marginLeft: 8 }}>Pending Pairing Requests</span>
            <span className="chip chip--warn" style={{ marginLeft: 8 }}>{pending.length}</span>
          </div>
          <div className="list" style={{ marginTop: 12 }}>
            {pending.map((d) => (
              <div className="list-item" key={d.requestId}>
                <div className="list-main">
                  <div className="list-title">{d.host ?? d.requestId}</div>
                  <div className="list-sub">{new Date(d.requestedAt).toLocaleString()}</div>
                </div>
                <div className="row" style={{ gap: 4 }}>
                  <button className="btn btn--sm primary" title="Approve this device">Approve</button>
                  <button className="btn btn--sm btn--danger" title="Reject this device">Reject</button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Paired devices */}
      <div className="card">
        <div className="card-title">Paired Devices</div>
        <div className="card-sub">Devices authorized to connect to this gateway.</div>
        <div className="list" style={{ marginTop: 12 }}>
          {paired.length === 0 && !devicesLoading && (
            <EmptyState
              icon={Icons.plug}
              title="No paired devices"
              description="Devices will appear here once they complete the pairing flow."
            />
          )}
          {paired.map((d) => (
            <div className="list-item" key={d.deviceId}>
              <div className="list-main">
                <div className="list-title">{d.host ?? d.deviceId}</div>
                <div className="list-sub">
                  <span className="chip">{d.role}</span>
                  <span className="muted" style={{ marginLeft: 8 }}>Paired {new Date(d.pairedAt).toLocaleDateString()}</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Gateway Nodes */}
      <div className="card">
        <div className="card-title">Gateway Nodes</div>
        <div className="card-sub">Live connections and linked nodes.</div>
        <div className="list" style={{ marginTop: 12 }}>
          {nodes.length === 0 && !nodesLoading && (
            <EmptyState
              icon={Icons.globe}
              title="No nodes"
              description="Nodes represent connected gateway instances."
            />
          )}
          {nodesLoading && nodes.length === 0 && (
            <div className="list-loading">
              <div className="spinner" />
              <span className="muted">Loading nodes...</span>
            </div>
          )}
          {nodes.map((n) => (
            <div className="list-item" key={n.id}>
              <div className="list-main">
                <div className="list-title mono">{n.id}</div>
                {n.address && <div className="list-sub">{n.address}</div>}
              </div>
              <div className="list-meta">
                <span className={`chip ${n.status === "online" ? "chip--ok" : ""}`}>{n.status}</span>
                {n.platform && <span className="chip">{n.platform}</span>}
                {n.version && <span className="chip mono">{n.version}</span>}
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
