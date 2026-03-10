import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useGatewayStore } from "../stores/gateway.ts";
import { useAgentsStore } from "../stores/agents.ts";
import PageHeader from "../components/PageHeader.tsx";
import EmptyState from "../components/EmptyState.tsx";
import { Icons } from "../icons.tsx";
import { groupAgents, initials, parseAgentsList } from "./org-shared.ts";

export default function OrgPage() {
  const connected = useGatewayStore((s) => s.connected);
  const agentsList = useAgentsStore((s) => s.agentsList);
  const agentsLoading = useAgentsStore((s) => s.agentsLoading);
  const agentsError = useAgentsStore((s) => s.agentsError);
  const load = useAgentsStore((s) => s.load);
  const navigate = useNavigate();

  useEffect(() => {
    if (connected) load();
  }, [connected, load]);

  const agents = parseAgentsList(agentsList);
  const orgs = groupAgents(agents);

  return (
    <section>
      <PageHeader
        title="Organization"
        description="Your teams of AI agents. Click a team to manage it."
        actions={
          <button className="btn" onClick={() => load()} disabled={agentsLoading}>
            {Icons.refreshCw({ width: "14px", height: "14px" })}
            <span style={{ marginLeft: 6 }}>Refresh</span>
          </button>
        }
      />

      {agentsError && <div className="callout danger">{agentsError}</div>}

      {agents.length === 0 && !agentsLoading && (
        <EmptyState
          icon={Icons.puzzle}
          title="No agents configured"
          description="Configure agents in your AI Elevate settings to see your organization's team."
        />
      )}

      {agentsLoading && agents.length === 0 && (
        <div className="org-grid org-grid--master">
          {[1, 2, 3].map((i) => (
            <div className="org-master-card org-card--skeleton" key={i}>
              <div className="org-master-card__banner" style={{ background: "var(--border)" }} />
              <div className="skeleton" style={{ width: 72, height: 72, borderRadius: "50%", marginTop: -36 }} />
              <div className="skeleton" style={{ width: "60%", height: 18, marginTop: 12 }} />
              <div className="skeleton" style={{ width: "40%", height: 14, marginTop: 8 }} />
            </div>
          ))}
        </div>
      )}

      {orgs.length > 0 && (
        <div className="org-grid org-grid--master">
          {orgs.map((org) => (
            <button
              key={org.id}
              className="org-master-card"
              onClick={() => navigate(`/org/${org.id}`)}
              title={`Manage ${org.label}`}
            >
              <div className="org-master-card__banner" style={{ background: org.gradient }} />
              <div className="org-master-card__avatar" style={{ background: org.gradient }}>
                {initials(org.label)}
              </div>
              <div className="org-master-card__body">
                <div className="org-master-card__name">{org.label}</div>
                <div className="org-master-card__desc">{org.description}</div>
                <div className="org-master-card__meta">
                  <span className="chip">
                    {Icons.users({ width: "12px", height: "12px" })}
                    <span style={{ marginLeft: 4 }}>{org.agents.length} members</span>
                  </span>
                </div>
              </div>
              <div className="org-master-card__chevron">
                {Icons.chevronRight({ width: "16px", height: "16px" })}
              </div>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
