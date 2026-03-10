import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
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

const AGENT_GRADIENTS = [
  "linear-gradient(135deg, #667eea 0%, #764ba2 100%)",
  "linear-gradient(135deg, #f093fb 0%, #f5576c 100%)",
  "linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)",
  "linear-gradient(135deg, #43e97b 0%, #38f9d7 100%)",
  "linear-gradient(135deg, #fa709a 0%, #fee140 100%)",
  "linear-gradient(135deg, #a18cd1 0%, #fbc2eb 100%)",
  "linear-gradient(135deg, #fccb90 0%, #d57eeb 100%)",
  "linear-gradient(135deg, #e0c3fc 0%, #8ec5fc 100%)",
];

function initials(name: string): string {
  return name
    .split(/[\s-_]+/)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("");
}

function inferRole(agent: AgentEntry): string {
  const id = agent.id.toLowerCase();
  if (id.includes("market")) return "Marketing";
  if (id.includes("sales") || id.includes("intake")) return "Sales";
  if (id.includes("support")) return "Support";
  if (id.includes("engineer") || id.includes("dev") || id.includes("cto")) return "Engineering";
  if (id.includes("finance") || id.includes("cfo")) return "Finance";
  if (id.includes("ops")) return "Operations";
  if (id.includes("ceo") || id.includes("exec")) return "Executive";
  if (id.includes("report")) return "Reporting";
  if (agent.isDefault) return "Primary Assistant";
  return "Team Member";
}

type OrgDef = {
  prefix: string;
  label: string;
  description: string;
  gradient: string;
};

const ORG_DEFS: OrgDef[] = [
  { prefix: "techuni-", label: "TechUni AI", description: "Course-creator SaaS platform team", gradient: "linear-gradient(135deg, #667eea 0%, #764ba2 100%)" },
  { prefix: "gigforge-", label: "GigForge", description: "Freelance fulfillment team", gradient: "linear-gradient(135deg, #f093fb 0%, #f5576c 100%)" },
];

type OrgGroup = OrgDef & { agents: AgentEntry[] };

function groupAgents(agents: AgentEntry[]): { orgs: OrgGroup[]; standalone: AgentEntry[] } {
  const remaining = [...agents];
  const orgs: OrgGroup[] = [];

  for (const def of ORG_DEFS) {
    const matched = remaining.filter((a) => a.id.startsWith(def.prefix));
    if (matched.length > 0) {
      orgs.push({ ...def, agents: matched });
      for (const m of matched) {
        const idx = remaining.indexOf(m);
        if (idx >= 0) remaining.splice(idx, 1);
      }
    }
  }

  return { orgs, standalone: remaining };
}

export default function OrgPage() {
  const connected = useGatewayStore((s) => s.connected);
  const agentsList = useAgentsStore((s) => s.agentsList);
  const agentsLoading = useAgentsStore((s) => s.agentsLoading);
  const agentsError = useAgentsStore((s) => s.agentsError);
  const load = useAgentsStore((s) => s.load);
  const navigate = useNavigate();
  const [expandedOrg, setExpandedOrg] = useState<string | null>(null);

  useEffect(() => {
    if (connected) load();
  }, [connected, load]);

  const agents: AgentEntry[] = (() => {
    if (!agentsList) return [];
    // agentsList is AgentsListResult { agents: [...], defaultId }
    const result = agentsList as { agents?: AgentEntry[]; defaultId?: string };
    if (Array.isArray(result.agents)) {
      return result.agents.map((a) => ({
        ...a,
        isDefault: a.id === result.defaultId,
      }));
    }
    if (Array.isArray(agentsList)) return agentsList as AgentEntry[];
    return [];
  })();

  const { orgs, standalone } = groupAgents(agents);

  return (
    <section>
      <PageHeader
        title="Organization"
        description="Your teams of AI agents. Click a team to see its members, or chat directly with standalone agents."
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
        <div className="org-grid">
          {[1, 2, 3].map((i) => (
            <div className="org-card org-card--skeleton" key={i}>
              <div className="skeleton" style={{ width: 64, height: 64, borderRadius: "50%" }} />
              <div className="skeleton" style={{ width: "60%", height: 18, marginTop: 12 }} />
              <div className="skeleton" style={{ width: "40%", height: 14, marginTop: 8 }} />
            </div>
          ))}
        </div>
      )}

      {/* Organization master cards */}
      {orgs.length > 0 && (
        <div className="org-grid org-grid--master">
          {orgs.map((org) => (
            <button
              key={org.prefix}
              className={`org-master-card ${expandedOrg === org.prefix ? "org-master-card--expanded" : ""}`}
              onClick={() => setExpandedOrg(expandedOrg === org.prefix ? null : org.prefix)}
              title={`View ${org.label} team`}
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
                {expandedOrg === org.prefix ? "&#9650;" : "&#9660;"}
              </div>
            </button>
          ))}
        </div>
      )}

      {/* Expanded sub-agents for selected org */}
      {expandedOrg && (
        <OrgSubAgents
          org={orgs.find((o) => o.prefix === expandedOrg)!}
          onChat={(id) => navigate(`/org/${id}/chat`)}
          onClose={() => setExpandedOrg(null)}
        />
      )}

      {/* Standalone agents shown directly */}
      {standalone.length > 0 && (
        <div className="org-section">
          <div className="org-section__header">
            <div className="org-section__indicator" style={{ background: "linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)" }} />
            <div>
              <h2 className="org-section__title">AI Elevate</h2>
              <p className="org-section__desc">Core platform agents and utilities</p>
            </div>
            <span className="org-section__count">
              {standalone.length} {standalone.length === 1 ? "agent" : "agents"}
            </span>
          </div>
          <div className="org-grid">
            {standalone.map((agent, i) => (
              <AgentCard
                key={agent.id}
                agent={agent}
                gradient={AGENT_GRADIENTS[(i + 4) % AGENT_GRADIENTS.length]}
                onClick={() => navigate(`/org/${agent.id}/chat`)}
              />
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function OrgSubAgents({
  org,
  onChat,
  onClose,
}: {
  org: OrgGroup;
  onChat: (id: string) => void;
  onClose: () => void;
}) {
  return (
    <div className="org-expanded">
      <div className="org-expanded__header">
        <button className="btn btn--icon" onClick={onClose} title="Collapse">
          {Icons.chevronUp({ width: "16px", height: "16px" })}
        </button>
        <h3 className="org-expanded__title">{org.label} Team</h3>
        <span className="org-expanded__count">
          {org.agents.length} {org.agents.length === 1 ? "member" : "members"}
        </span>
      </div>
      <div className="org-grid">
        {org.agents.map((agent, i) => (
          <AgentCard
            key={agent.id}
            agent={agent}
            gradient={AGENT_GRADIENTS[i % AGENT_GRADIENTS.length]}
            onClick={() => onChat(agent.id)}
          />
        ))}
      </div>
    </div>
  );
}

function AgentCard({
  agent,
  gradient,
  onClick,
}: {
  agent: AgentEntry;
  gradient: string;
  onClick: () => void;
}) {
  const role = inferRole(agent);
  return (
    <button className="org-card" onClick={onClick} title={`Chat with ${agent.name}`}>
      <div className="org-card__banner" style={{ background: gradient }} />
      <div className="org-card__avatar" style={{ background: gradient }}>
        {initials(agent.name)}
      </div>
      <div className="org-card__body">
        <div className="org-card__name">{agent.name}</div>
        <div className="org-card__role">{role}</div>
        {agent.model && <div className="org-card__model mono">{agent.model}</div>}
        <div className="org-card__tags">
          {agent.isDefault && <span className="chip chip--accent">default</span>}
          <span className="chip">
            <span className="statusDot ok" style={{ marginRight: 4 }} />
            online
          </span>
        </div>
      </div>
      <div className="org-card__action">
        {Icons.messageSquare({ width: "16px", height: "16px" })}
        <span>Chat</span>
      </div>
    </button>
  );
}
