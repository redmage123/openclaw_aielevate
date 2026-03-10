import { useCallback, useEffect, useState } from "react";
import { useGatewayStore } from "../stores/gateway.ts";

type UsageTotals = {
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  estimatedCost: number;
};

type UsageSession = {
  key: string;
  agentId?: string;
  tokens: number;
  cost: number;
};

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function formatCost(n: number): string {
  return `$${n.toFixed(4)}`;
}

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function weekAgoIso(): string {
  const d = new Date();
  d.setDate(d.getDate() - 7);
  return d.toISOString().slice(0, 10);
}

// Usage page does not have a dedicated store yet -- uses local state
// with gateway client RPC calls. When a useUsageStore is added, migrate.
export default function UsagePage() {
  const connected = useGatewayStore((s) => s.connected);
  const client = useGatewayStore((s) => s.client) as {
    request?: (method: string, params: unknown) => Promise<unknown>;
  } | null;
  const [loading, setLoading] = useState(false);
  const [startDate, setStartDate] = useState(weekAgoIso);
  const [endDate, setEndDate] = useState(todayIso);
  const [totals, setTotals] = useState<UsageTotals | null>(null);
  const [sessions, setSessions] = useState<UsageSession[]>([]);

  const handleRefresh = useCallback(async () => {
    if (!client?.request) return;
    setLoading(true);
    try {
      const res = (await client.request("usage", { startDate, endDate })) as {
        totals?: UsageTotals;
        sessions?: UsageSession[];
      } | null;
      if (res) {
        setTotals(res.totals ?? null);
        setSessions(res.sessions ?? []);
      }
    } catch {
      // usage RPC may not be implemented yet
    } finally {
      setLoading(false);
    }
  }, [client, startDate, endDate]);

  useEffect(() => {
    if (connected) handleRefresh();
  }, [connected]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <section>
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
          <div>
            <div className="card-title">Token Usage</div>
            <div className="card-sub">Token consumption and cost analytics.</div>
          </div>
          <div className="row" style={{ gap: 8, alignItems: "center" }}>
            <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
            <span className="muted">to</span>
            <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
            <button className="btn" onClick={handleRefresh} disabled={loading}>
              {loading ? "Loading..." : "Refresh"}
            </button>
          </div>
        </div>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2" style={{ marginTop: 16 }}>
        <div className="card">
          <div className="card-title">Input Tokens</div>
          <div style={{ fontSize: 24, fontWeight: 600, marginTop: 8 }}>
            {totals ? formatTokens(totals.inputTokens) : "--"}
          </div>
        </div>
        <div className="card">
          <div className="card-title">Output Tokens</div>
          <div style={{ fontSize: 24, fontWeight: 600, marginTop: 8 }}>
            {totals ? formatTokens(totals.outputTokens) : "--"}
          </div>
        </div>
        <div className="card">
          <div className="card-title">Total Tokens</div>
          <div style={{ fontSize: 24, fontWeight: 600, marginTop: 8 }}>
            {totals ? formatTokens(totals.totalTokens) : "--"}
          </div>
        </div>
        <div className="card">
          <div className="card-title">Estimated Cost</div>
          <div style={{ fontSize: 24, fontWeight: 600, marginTop: 8 }}>
            {totals ? formatCost(totals.estimatedCost) : "--"}
          </div>
        </div>
      </div>

      {/* Chart placeholder */}
      <div className="card" style={{ marginTop: 16 }}>
        <div className="card-title">Daily Usage</div>
        <div className="muted" style={{ marginTop: 12, padding: "40px 0", textAlign: "center" }}>
          Chart placeholder - daily token usage over time
        </div>
      </div>

      {/* Session breakdown */}
      <div className="card" style={{ marginTop: 16 }}>
        <div className="card-title">Session Breakdown</div>
        <div className="list" style={{ marginTop: 12 }}>
          {sessions.length === 0 && (
            <div className="muted">No session data for the selected range.</div>
          )}
          {sessions.map((s) => (
            <div className="list-item" key={s.key}>
              <div className="list-main">
                <div className="list-title">{s.key}</div>
                {s.agentId && <div className="list-sub">Agent: {s.agentId}</div>}
              </div>
              <div className="list-meta">
                <div>{formatTokens(s.tokens)} tokens</div>
                <div className="muted">{formatCost(s.cost)}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
