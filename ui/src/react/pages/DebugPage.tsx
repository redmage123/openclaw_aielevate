import { useEffect } from "react";
import { useGatewayStore } from "../stores/gateway.ts";
import { useDebugStore } from "../stores/debug.ts";
import PageHeader from "../components/PageHeader.tsx";
import { Icons } from "../icons.tsx";

function prettyJson(value: unknown): string {
  if (value == null) return "null";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export default function DebugPage() {
  const connected = useGatewayStore((s) => s.connected);
  const debugLoading = useDebugStore((s) => s.debugLoading);
  const debugStatus = useDebugStore((s) => s.debugStatus);
  const debugHealth = useDebugStore((s) => s.debugHealth);
  const debugModels = useDebugStore((s) => s.debugModels);
  const debugCallMethod = useDebugStore((s) => s.debugCallMethod);
  const debugCallParams = useDebugStore((s) => s.debugCallParams);
  const debugCallResult = useDebugStore((s) => s.debugCallResult);
  const debugCallError = useDebugStore((s) => s.debugCallError);
  const load = useDebugStore((s) => s.load);
  const call = useDebugStore((s) => s.call);
  const setCallMethod = useDebugStore((s) => s.setCallMethod);
  const setCallParams = useDebugStore((s) => s.setCallParams);

  useEffect(() => {
    if (connected) load();
  }, [connected, load]);

  const modelsArray = Array.isArray(debugModels) ? debugModels : [];

  return (
    <section>
      <PageHeader
        title="Debug"
        description="Gateway internals, health checks, and RPC tester."
        actions={
          <button className="btn" onClick={() => load()} disabled={debugLoading}>
            {Icons.refreshCw({ width: "14px", height: "14px" })}
            <span style={{ marginLeft: 6 }}>{debugLoading ? "Refreshing..." : "Refresh"}</span>
          </button>
        }
      />

      <div className="grid grid-cols-2">
        {/* Snapshots */}
        <div className="card">
          <div className="card-title">Gateway Status</div>

          <div className="debug-section">
            <div className="debug-section__label">Status</div>
            <pre className="code-block">{prettyJson(debugStatus)}</pre>
          </div>

          <div className="debug-section">
            <div className="debug-section__label">Health</div>
            <pre className="code-block">{prettyJson(debugHealth)}</pre>
          </div>

          <div className="debug-section">
            <div className="debug-section__label">Models ({modelsArray.length})</div>
            <pre className="code-block">{prettyJson(debugModels)}</pre>
          </div>
        </div>

        {/* Manual RPC call */}
        <div className="card">
          <div className="card-title">RPC Tester</div>
          <div className="card-sub">Send raw JSON-RPC method calls to the gateway.</div>

          <div className="form-grid" style={{ marginTop: 16 }}>
            <label className="field">
              <span>Method</span>
              <input
                value={debugCallMethod}
                onChange={(e) => setCallMethod(e.target.value)}
                placeholder="e.g. system-presence"
                spellCheck={false}
              />
            </label>
            <label className="field">
              <span>Params (JSON)</span>
              <textarea
                value={debugCallParams}
                onChange={(e) => setCallParams(e.target.value)}
                rows={6}
                spellCheck={false}
                className="mono"
                placeholder="{}"
              />
            </label>
          </div>
          <div className="row" style={{ marginTop: 12 }}>
            <button className="btn primary" onClick={() => call()} disabled={!debugCallMethod.trim()}>
              {Icons.zap({ width: "14px", height: "14px" })}
              <span style={{ marginLeft: 6 }}>Execute</span>
            </button>
          </div>
          {debugCallError && (
            <div className="callout danger" style={{ marginTop: 12 }}>{debugCallError}</div>
          )}
          {debugCallResult && (
            <div className="debug-section" style={{ marginTop: 12 }}>
              <div className="debug-section__label">Result</div>
              <pre className="code-block">{debugCallResult}</pre>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
