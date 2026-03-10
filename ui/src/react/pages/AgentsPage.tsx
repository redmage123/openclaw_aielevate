import { useEffect } from "react";
import { useGatewayStore } from "../stores/gateway.ts";
import { useAgentsStore } from "../stores/agents.ts";
import PageHeader from "../components/PageHeader.tsx";
import EmptyState from "../components/EmptyState.tsx";
import { Icons } from "../icons.tsx";

type AgentEntry = {
  id: string;
  name: string;
  workspace?: string;
  model?: string;
  isDefault?: boolean;
};

type AgentPanel = "overview" | "files" | "tools" | "skills";

export default function AgentsPage() {
  const connected = useGatewayStore((s) => s.connected);
  const agentsList = useAgentsStore((s) => s.agentsList);
  const agentsLoading = useAgentsStore((s) => s.agentsLoading);
  const agentsError = useAgentsStore((s) => s.agentsError);
  const agentsSelectedId = useAgentsStore((s) => s.agentsSelectedId);
  const agentsPanel = useAgentsStore((s) => s.agentsPanel) as AgentPanel;
  const load = useAgentsStore((s) => s.load);
  const selectAgent = useAgentsStore((s) => s.selectAgent);
  const setPanel = useAgentsStore((s) => s.setPanel);
  const loadToolsCatalog = useAgentsStore((s) => s.loadToolsCatalog);

  useEffect(() => {
    if (connected) load();
  }, [connected, load]);

  const agents: AgentEntry[] = Array.isArray(agentsList)
    ? (agentsList as AgentEntry[])
    : [];

  const selected = agents.find((a) => a.id === agentsSelectedId) ?? null;

  const handleSelectAgent = (id: string) => {
    selectAgent(id);
    loadToolsCatalog(id);
  };

  return (
    <section>
      <PageHeader
        title="Agents"
        description={agents.length > 0 ? `${agents.length} configured` : "Agent configurations and capabilities."}
        actions={
          <button className="btn" onClick={() => load()} disabled={agentsLoading}>
            {Icons.refreshCw({ width: "14px", height: "14px" })}
            <span style={{ marginLeft: 6 }}>{agentsLoading ? "Loading..." : "Refresh"}</span>
          </button>
        }
      />

      {agentsError && <div className="callout danger">{agentsError}</div>}

      <div className="card">
        {/* Agent list */}
        <div className="list">
          {agents.length === 0 && !agentsLoading && (
            <EmptyState
              icon={Icons.puzzle}
              title="No agents configured"
              description="Agents define how your AI assistant behaves. Configure one in your settings."
            />
          )}
          {agentsLoading && agents.length === 0 && (
            <div className="list-loading">
              <div className="spinner" />
              <span className="muted">Loading agents...</span>
            </div>
          )}
          {agents.map((agent) => (
            <div
              className={`list-item list-item--clickable ${agentsSelectedId === agent.id ? "list-item--active" : ""}`}
              key={agent.id}
              onClick={() => handleSelectAgent(agent.id)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => e.key === "Enter" && handleSelectAgent(agent.id)}
            >
              <div className="list-main">
                <div className="list-title">
                  {agent.name}
                  {agent.isDefault && <span className="chip chip--accent" style={{ marginLeft: 8 }}>default</span>}
                </div>
                <div className="list-sub">
                  <span className="mono">{agent.id}</span>
                  {agent.model && <span> | {agent.model}</span>}
                </div>
              </div>
              <span className="list-arrow">{Icons.chevronRight({ width: "14px", height: "14px" })}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Detail panel */}
      {selected && (
        <div className="card">
          <div className="card-title">{selected.name}</div>

          {/* Panel tabs */}
          <div className="tab-bar">
            {(["overview", "files", "tools", "skills"] as AgentPanel[]).map((p) => (
              <button
                key={p}
                className={`tab-bar__item ${agentsPanel === p ? "tab-bar__item--active" : ""}`}
                onClick={() => setPanel(p)}
              >
                {p.charAt(0).toUpperCase() + p.slice(1)}
              </button>
            ))}
          </div>

          <div style={{ marginTop: 16 }}>
            {agentsPanel === "overview" && (
              <div className="detail-grid">
                <div className="detail-row">
                  <span className="detail-label">ID</span>
                  <span className="mono">{selected.id}</span>
                </div>
                <div className="detail-row">
                  <span className="detail-label">Workspace</span>
                  <span>{selected.workspace ?? "Default"}</span>
                </div>
                <div className="detail-row">
                  <span className="detail-label">Model</span>
                  <span>{selected.model ?? "Default"}</span>
                </div>
              </div>
            )}
            {agentsPanel === "files" && (
              <EmptyState title="Agent Files" description="File management coming soon." />
            )}
            {agentsPanel === "tools" && (
              <EmptyState title="Tools" description="Tool configuration coming soon." />
            )}
            {agentsPanel === "skills" && (
              <EmptyState title="Skills" description="Skill assignments coming soon." />
            )}
          </div>
        </div>
      )}
    </section>
  );
}
