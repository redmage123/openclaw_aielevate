import { useEffect } from "react";
import { useGatewayStore } from "../stores/gateway.ts";
import { useConfigStore } from "../stores/config.ts";

export default function ConfigPage() {
  const connected = useGatewayStore((s) => s.connected);
  const configLoading = useConfigStore((s) => s.configLoading);
  const configSaving = useConfigStore((s) => s.configSaving);
  const configApplying = useConfigStore((s) => s.configApplying);
  const configRaw = useConfigStore((s) => s.configRaw);
  const configRawOriginal = useConfigStore((s) => s.configRawOriginal);
  const configValid = useConfigStore((s) => s.configValid);
  const configForm = useConfigStore((s) => s.configForm);
  const configFormMode = useConfigStore((s) => s.configFormMode);
  const load = useConfigStore((s) => s.load);
  const save = useConfigStore((s) => s.save);
  const apply = useConfigStore((s) => s.apply);
  const setFormMode = useConfigStore((s) => s.setFormMode);
  const setRaw = useConfigStore((s) => s.setRaw);

  const isDirty = configRaw !== configRawOriginal;

  useEffect(() => {
    if (connected) load();
  }, [connected, load]);

  const handleRawChange = (value: string) => {
    setRaw(value);
  };

  return (
    <section className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div>
          <div className="card-title">Configuration</div>
          <div className="card-sub">Gateway configuration editor.</div>
        </div>
        <div className="row" style={{ gap: 8 }}>
          {configValid === false && <span className="chip chip--danger">Invalid JSON</span>}
          {configValid === true && isDirty && <span className="chip chip--warn">Unsaved changes</span>}
          <button className="btn" onClick={() => load()} disabled={configLoading}>
            {configLoading ? "Loading..." : "Reload"}
          </button>
        </div>
      </div>

      {/* Mode toggle */}
      <div className="row" style={{ gap: 8, marginTop: 14 }}>
        <button
          className={`btn ${configFormMode === "form" ? "primary" : ""}`}
          onClick={() => setFormMode("form")}
        >
          Form
        </button>
        <button
          className={`btn ${configFormMode === "raw" ? "primary" : ""}`}
          onClick={() => setFormMode("raw")}
        >
          Raw JSON
        </button>
      </div>

      {/* Content */}
      <div style={{ marginTop: 16 }}>
        {configFormMode === "raw" ? (
          <textarea
            className="code-block"
            value={configRaw}
            onChange={(e) => handleRawChange(e.target.value)}
            rows={20}
            style={{ width: "100%", fontFamily: "monospace", resize: "vertical" }}
            spellCheck={false}
          />
        ) : (
          <div className="form-grid">
            {configForm ? (
              Object.entries(configForm).map(([key, value]) => (
                <label className="field" key={key}>
                  <span>{key}</span>
                  <input
                    value={typeof value === "string" ? value : JSON.stringify(value)}
                    readOnly
                  />
                </label>
              ))
            ) : (
              <div className="muted">
                {configLoading ? "Loading configuration..." : "No configuration loaded."}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="row" style={{ gap: 8, marginTop: 16 }}>
        <button className="btn primary" onClick={() => save()} disabled={configSaving || !isDirty}>
          {configSaving ? "Saving..." : "Save"}
        </button>
        <button className="btn" onClick={() => apply()} disabled={configApplying}>
          {configApplying ? "Applying..." : "Apply"}
        </button>
      </div>
    </section>
  );
}
