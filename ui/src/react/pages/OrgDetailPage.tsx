import { useEffect, useState, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useGatewayStore } from "../stores/gateway.ts";
import { useAgentsStore } from "../stores/agents.ts";
import { Icons } from "../icons.tsx";
import {
  type AgentEntry,
  type OrgGroup,
  type OrgDef,
  AGENT_GRADIENTS,
  initials,
  inferRole,
  groupAgents,
  parseAgentsList,
  findOrgById,
} from "./org-shared.ts";

type RpcClient = { request: <T>(method: string, params: unknown) => Promise<T> };

type Tab = "projects" | "team" | "channels" | "settings" | "files";

type ChannelAccount = {
  id?: string;
  accountId?: string;
  connected?: boolean;
  loggedIn?: boolean;
  label?: string;
  phone?: string;
  username?: string;
};

type ChannelSnapshot = {
  channelOrder?: string[];
  channelLabels?: Record<string, string>;
  channels?: Record<string, unknown>;
  channelAccounts?: Record<string, ChannelAccount[]>;
};

type ConfigSnapshot = {
  raw?: string;
  config?: Record<string, unknown>;
  hash?: string;
  valid?: boolean;
};

type Binding = { agentId: string; match: { channel: string; accountId?: string } };

type AgentFileEntry = {
  name: string;
  path: string;
  missing: boolean;
  size?: number;
  content?: string;
};

export default function OrgDetailPage() {
  const { orgId } = useParams<{ orgId: string }>();
  const navigate = useNavigate();
  const connected = useGatewayStore((s) => s.connected);
  const client = useGatewayStore((s) => s.client);
  const agentsList = useAgentsStore((s) => s.agentsList);
  const loadAgents = useAgentsStore((s) => s.load);

  const [tab, setTab] = useState<Tab>("projects");

  useEffect(() => {
    if (connected) loadAgents();
  }, [connected, loadAgents]);

  const agents = parseAgentsList(agentsList);
  const orgs = groupAgents(agents);
  const org = orgs.find((o) => o.id === orgId);
  const orgDef = findOrgById(orgId ?? "");

  if (!orgDef) {
    return (
      <section>
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: 20 }}>
          <button className="btn btn--icon" onClick={() => navigate("/org")} title="Back">
            {Icons.chevronLeft({ width: "16px", height: "16px" })}
          </button>
          <span>Organization not found</span>
        </div>
      </section>
    );
  }

  const orgAgents = org?.agents ?? [];
  const rpc = client as RpcClient | null;

  return (
    <section className="org-detail">
      <div className="org-detail__header" style={{ background: orgDef.gradient }}>
        <button className="org-detail__back-btn" onClick={() => navigate("/org")} title="Back to organizations">
          {Icons.chevronLeft({ width: "18px", height: "18px" })}
        </button>
        <div className="org-detail__avatar">{initials(orgDef.label)}</div>
        <div className="org-detail__info">
          <h1 className="org-detail__title">{orgDef.label}</h1>
          <p className="org-detail__desc">{orgDef.description}</p>
          <span className="org-detail__count">{orgAgents.length} members</span>
        </div>
      </div>

      <div className="org-detail__tabs">
        {(["projects", "team", "channels", "settings", "files"] as Tab[]).map((t) => (
          <button
            key={t}
            className={`org-detail__tab ${tab === t ? "org-detail__tab--active" : ""}`}
            onClick={() => setTab(t)}
          >
            {t === "projects" && Icons.zap({ width: "14px", height: "14px" })}
            {t === "team" && Icons.users({ width: "14px", height: "14px" })}
            {t === "channels" && Icons.radio({ width: "14px", height: "14px" })}
            {t === "settings" && Icons.settings({ width: "14px", height: "14px" })}
            {t === "files" && Icons.fileText({ width: "14px", height: "14px" })}
            <span style={{ marginLeft: 6, textTransform: "capitalize" }}>{t}</span>
          </button>
        ))}
      </div>

      <div className="org-detail__content">
        {tab === "projects" && <ProjectsTab orgDef={orgDef} orgAgents={orgAgents} rpc={rpc} connected={connected} />}
        {tab === "team" && (
          <TeamTab
            orgDef={orgDef}
            agents={orgAgents}
            rpc={rpc}
            connected={connected}
            onChat={(id) => navigate(`/org/${id}/chat`)}
            onAgentsChanged={loadAgents}
          />
        )}
        {tab === "channels" && <ChannelsTab orgAgents={orgAgents} rpc={rpc} connected={connected} />}
        {tab === "settings" && <SettingsTab orgAgents={orgAgents} rpc={rpc} connected={connected} />}
        {tab === "files" && <FilesTab orgAgents={orgAgents} rpc={rpc} connected={connected} />}
      </div>
    </section>
  );
}

/* ============================== Projects Tab ============================== */

type ProjectEntry = {
  name: string;
  status: string;
  description: string;
  source: string;
  detail?: ProjectDetail;
};

type ProjectDetail = {
  stack?: string;
  port?: string;
  link?: string;
  tests?: string;
  docker?: string;
  deps?: string;
  arch?: string;
  about?: string;
  features?: string[];
};

/** Parse `### project-name` detail blocks under ## Projects. */
function parseProjectDetails(content: string): Record<string, ProjectDetail> {
  const details: Record<string, ProjectDetail> = {};
  const lines = content.split("\n");
  let inSection = false;
  let currentProject: string | null = null;
  let currentDetail: ProjectDetail = {};

  for (const line of lines) {
    if (/^##\s+Projects\b/i.test(line)) { inSection = true; continue; }
    if (inSection && /^##\s[^#]/.test(line)) break; // next h2 section
    if (!inSection) continue;

    // h3 = project detail block
    const h3 = line.match(/^###\s+(.+)/);
    if (h3) {
      if (currentProject) details[currentProject] = currentDetail;
      currentProject = h3[1].trim();
      currentDetail = {};
      continue;
    }
    if (!currentProject) continue;

    // Parse "- **Key**: Value" lines
    const kvMatch = line.match(/^-\s+\*\*(.+?)\*\*:\s*(.+)/);
    if (kvMatch) {
      const key = kvMatch[1].toLowerCase();
      const val = kvMatch[2].trim();
      if (key === "stack") currentDetail.stack = val;
      else if (key === "port") currentDetail.port = val;
      else if (key === "link") currentDetail.link = val;
      else if (key === "tests") currentDetail.tests = val;
      else if (key === "docker") currentDetail.docker = val;
      else if (key === "deps") currentDetail.deps = val;
      else if (key === "arch") currentDetail.arch = val;
      else if (key === "about") currentDetail.about = val;
      else if (key === "features") {
        currentDetail.features = val.split(/,\s*/);
      }
    }
  }
  if (currentProject) details[currentProject] = currentDetail;
  return details;
}

/** Parse markdown table + detail blocks from AGENTS.md under "## Projects". */
function parseProjectsTable(content: string): ProjectEntry[] {
  const lines = content.split("\n");
  const projects: ProjectEntry[] = [];
  let inSection = false;
  let headerParsed = false;

  for (const line of lines) {
    if (/^##\s+Projects\b/i.test(line)) {
      inSection = true;
      headerParsed = false;
      continue;
    }
    if (inSection && /^##\s[^#]/.test(line)) break; // next h2 section
    if (!inSection) continue;

    if (!headerParsed) {
      if (line.includes("|") && (line.includes("---") || line.toLowerCase().includes("project"))) {
        if (line.includes("---")) headerParsed = true;
        continue;
      }
    }

    const match = line.match(/^\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]*)\s*\|?$/);
    if (match) {
      headerParsed = true;
      const name = match[1].trim();
      const status = match[2].trim();
      const desc = match[3]?.trim() ?? "";
      if (name && name !== "---" && !name.toLowerCase().includes("project")) {
        projects.push({ name, status, description: desc, source: "AGENTS.md" });
      }
    }
  }

  // Merge detail blocks
  const details = parseProjectDetails(content);
  for (const p of projects) {
    if (details[p.name]) p.detail = details[p.name];
  }

  return projects;
}

function ProjectsTab({
  orgDef,
  orgAgents,
  rpc,
  connected,
}: {
  orgDef: OrgDef;
  orgAgents: AgentEntry[];
  rpc: RpcClient | null;
  connected: boolean;
}) {
  const [projects, setProjects] = useState<ProjectEntry[]>([]);
  const [strategyDocs, setStrategyDocs] = useState<{ title: string; excerpt: string; source: string }[]>([]);
  const [activity, setActivity] = useState<{ date: string; content: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedProject, setExpandedProject] = useState<string | null>(null);
  const [expandedActivity, setExpandedActivity] = useState<string | null>(null);

  const loadProjects = useCallback(async () => {
    if (!connected || !rpc) return;
    setLoading(true);
    setError(null);
    try {
      const allProjects: ProjectEntry[] = [];
      const docs: { title: string; excerpt: string; source: string }[] = [];
      const activityEntries: { date: string; content: string }[] = [];

      for (const agent of orgAgents) {
        try {
          // Read AGENTS.md to find ## Projects table
          const agentsFile = await rpc.request<{ file: AgentFileEntry }>("agents.files.get", { agentId: agent.id, name: "AGENTS.md" });
          if (agentsFile?.file?.content) {
            const tableProjects = parseProjectsTable(agentsFile.file.content);
            for (const p of tableProjects) {
              // Deduplicate by name
              if (!allProjects.some((e) => e.name === p.name)) {
                allProjects.push({ ...p, source: agent.name });
              }
            }
          }
        } catch { /* no AGENTS.md */ }

        // Look for strategy/planning docs (only from first few agents to avoid flooding)
        if (docs.length < 5) {
          for (const fname of ["SOUL.md", "TOOLS.md"]) {
            try {
              const fRes = await rpc.request<{ file: AgentFileEntry }>("agents.files.get", { agentId: agent.id, name: fname });
              if (fRes?.file?.content && fRes.file.content.length > 100) {
                const lines = fRes.file.content.split("\n");
                const title = lines.find((l: string) => l.startsWith("# "))?.replace(/^#\s+/, "") ?? fname;
                docs.push({
                  title,
                  excerpt: lines.slice(0, 8).filter((l: string) => l.trim() && !l.startsWith("#")).join(" ").slice(0, 250),
                  source: `${agent.name} / ${fname}`,
                });
              }
            } catch { /* file doesn't exist */ }
          }
        }

        // Read MEMORY.md for recent activity (first agent only)
        if (activityEntries.length === 0) {
          try {
            const memRes = await rpc.request<{ file: AgentFileEntry }>("agents.files.get", { agentId: agent.id, name: "MEMORY.md" });
            if (memRes?.file?.content) {
              activityEntries.push({ date: "Latest Memory", content: memRes.file.content.slice(0, 2000) });
            }
          } catch { /* no memory */ }
        }
      }

      setProjects(allProjects);
      setStrategyDocs(docs);
      setActivity(activityEntries);
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }, [connected, rpc, orgAgents]);

  useEffect(() => {
    loadProjects();
  }, [connected]);

  const statusColors: Record<string, string> = {
    active: "chip--accent",
    demo: "chip--accent",
    completed: "chip--ok",
    delivered: "chip--ok",
    blocked: "chip--danger",
    planned: "",
    proposal: "chip--warn",
  };

  return (
    <div>
      <div className="org-detail__section-header">
        <h3>Projects & Schedule</h3>
        <button className="btn btn--sm" onClick={loadProjects} disabled={loading}>
          {Icons.refreshCw({ width: "12px", height: "12px" })}
          <span style={{ marginLeft: 4 }}>{loading ? "Loading..." : "Refresh"}</span>
        </button>
      </div>

      {error && <div className="callout danger">{error}</div>}
      {loading && <div className="muted" style={{ padding: 16 }}>Scanning workspaces for projects...</div>}

      {!loading && projects.length === 0 && strategyDocs.length === 0 && (
        <div className="muted" style={{ padding: 20 }}>
          No projects found. Add a "## Projects" table to an agent's AGENTS.md to list them here.
        </div>
      )}

      {/* Project list */}
      {projects.length > 0 && (
        <>
          <h4 style={{ margin: "0 0 12px", fontSize: "0.95rem" }}>
            Projects ({projects.length})
          </h4>
          <div className="org-projects-list">
            {projects.map((p) => {
              const d = p.detail;
              const isOpen = expandedProject === p.name;
              return (
                <div key={p.name} className={`org-project-card ${isOpen ? "org-project-card--open" : ""}`}>
                  <button
                    className="org-project-card__header"
                    onClick={() => setExpandedProject(isOpen ? null : p.name)}
                  >
                    <span className={`chip ${statusColors[p.status.toLowerCase()] ?? ""}`}>{p.status}</span>
                    <span className="org-project-card__name">{p.name}</span>
                    {d?.port && <span className="chip mono">:{d.port}</span>}
                    {d?.tests && <span className="chip chip--ok">{d.tests}</span>}
                    <span className="org-project-card__chevron">{isOpen ? "▾" : "▸"}</span>
                  </button>

                  {!isOpen && <div className="org-project-card__summary">{p.description}</div>}

                  {isOpen && (
                    <div className="org-project-card__detail">
                      {/* Action buttons bar */}
                      <div className="org-project-card__actions">
                        {d?.link && (
                          <a href={d.link} target="_blank" rel="noopener noreferrer" className="btn btn--sm btn--launch">
                            {Icons.zap({ width: "13px", height: "13px" })}
                            <span>Launch Demo</span>
                          </a>
                        )}
                        {d?.arch && (
                          <a href={d.arch} target="_blank" rel="noopener noreferrer" className="btn btn--sm btn--doc"
                             title="Architecture Diagram + Spec + Runbook (PDF)"
                          >
                            {Icons.fileText({ width: "13px", height: "13px" })}
                            <span>Architecture PDF</span>
                          </a>
                        )}
                      </div>

                      {d?.about && <p className="org-project-card__about">{d.about}</p>}
                      {!d?.about && p.description && <p className="org-project-card__about">{p.description}</p>}

                      <div className="org-project-card__meta">
                        {d?.stack && (
                          <div className="org-project-card__meta-row">
                            <span className="org-project-card__meta-label">Stack</span>
                            <span className="org-project-card__meta-value">
                              {d.stack.split(/,\s*/).map((t) => (
                                <span key={t} className="chip chip--tech">{t.trim()}</span>
                              ))}
                            </span>
                          </div>
                        )}
                        {d?.port && (
                          <div className="org-project-card__meta-row">
                            <span className="org-project-card__meta-label">Port</span>
                            <span className="org-project-card__meta-value mono">{d.port}</span>
                          </div>
                        )}
                        {d?.tests && (
                          <div className="org-project-card__meta-row">
                            <span className="org-project-card__meta-label">Tests</span>
                            <span className="org-project-card__meta-value">{d.tests}</span>
                          </div>
                        )}
                        {d?.docker && (
                          <div className="org-project-card__meta-row">
                            <span className="org-project-card__meta-label">Docker</span>
                            <span className="org-project-card__meta-value">{d.docker}</span>
                          </div>
                        )}
                        {d?.deps && (
                          <div className="org-project-card__meta-row">
                            <span className="org-project-card__meta-label">Deps</span>
                            <span className="org-project-card__meta-value">
                              {d.deps.split(/,\s*/).map((dep) => (
                                <span key={dep} className="chip chip--dep mono">{dep.trim()}</span>
                              ))}
                            </span>
                          </div>
                        )}
                      </div>

                      {d?.features && d.features.length > 0 && (
                        <div className="org-project-card__features">
                          <span className="org-project-card__meta-label">Features</span>
                          <ul className="org-project-card__feature-list">
                            {d.features.map((f, i) => (
                              <li key={i}>{f.trim()}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}

      {/* Strategy docs */}
      {strategyDocs.length > 0 && (
        <div style={{ marginTop: 24 }}>
          <h4 style={{ margin: "0 0 12px", fontSize: "0.95rem" }}>Agent Docs</h4>
          <div className="org-projects-list">
            {strategyDocs.map((d) => (
              <div key={d.source} className="org-project-card">
                <div className="org-project-card__header" style={{ cursor: "default" }}>
                  <span className="chip">doc</span>
                  <span className="org-project-card__name">{d.title}</span>
                  <span style={{ fontSize: "0.75rem" }} className="muted">{d.source}</span>
                </div>
                <div className="org-project-card__summary">{d.excerpt}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Recent activity */}
      {activity.length > 0 && (
        <div style={{ marginTop: 24 }}>
          <h4 style={{ margin: "0 0 12px", fontSize: "0.95rem" }}>Recent Activity</h4>
          {activity.map((a) => (
            <div key={a.date} className="org-standup-entry">
              <button
                className="org-standup-entry__header"
                onClick={() => setExpandedActivity(expandedActivity === a.date ? null : a.date)}
              >
                <span className="org-standup-entry__date">{a.date}</span>
                <span className="muted" style={{ fontSize: "0.75rem" }}>
                  {expandedActivity === a.date ? "collapse" : "expand"}
                </span>
              </button>
              {expandedActivity === a.date && (
                <pre className="org-standup-entry__content">{a.content}</pre>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ============================== Team Tab ============================== */

function TeamTab({
  orgDef,
  agents,
  rpc,
  connected,
  onChat,
  onAgentsChanged,
}: {
  orgDef: OrgDef;
  agents: AgentEntry[];
  rpc: RpcClient | null;
  connected: boolean;
  onChat: (id: string) => void;
  onAgentsChanged: () => void;
}) {
  const [showAddForm, setShowAddForm] = useState(false);
  const [newName, setNewName] = useState("");
  const [newWorkspace, setNewWorkspace] = useState("");
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [removeConfirm, setRemoveConfirm] = useState<string | null>(null);

  const handleAdd = async () => {
    if (!rpc || !connected || !newName.trim()) return;
    setAdding(true);
    setAddError(null);
    try {
      // Prefix the name with org prefix so it groups correctly
      const prefix = orgDef.prefix === "*" ? "" : orgDef.prefix;
      const agentName = prefix + newName.trim().toLowerCase().replace(/\s+/g, "-");
      await rpc.request("agents.create", {
        name: agentName,
        workspace: newWorkspace.trim() || undefined,
      });
      setNewName("");
      setNewWorkspace("");
      setShowAddForm(false);
      onAgentsChanged();
    } catch (err) {
      setAddError(String(err));
    } finally {
      setAdding(false);
    }
  };

  const handleRemove = async (agentId: string) => {
    if (!rpc || !connected) return;
    setRemoving(agentId);
    try {
      await rpc.request("agents.delete", { agentId, deleteFiles: false });
      setRemoveConfirm(null);
      onAgentsChanged();
    } catch (err) {
      setAddError(`Failed to remove: ${err}`);
    } finally {
      setRemoving(null);
    }
  };

  return (
    <div>
      <div className="org-detail__section-header">
        <h3>Team Members</h3>
        <button className="btn btn--sm" onClick={() => setShowAddForm(!showAddForm)}>
          {showAddForm ? "Cancel" : "+ Add Agent"}
        </button>
      </div>

      {addError && <div className="callout danger" style={{ marginBottom: 12 }}>{addError}</div>}

      {showAddForm && (
        <div className="org-add-agent-form">
          <div className="org-settings__field">
            <label className="org-settings__label">Agent Name</label>
            <input
              className="org-settings__input"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder={orgDef.prefix === "*" ? "e.g. assistant" : `e.g. ${orgDef.prefix}analyst`}
            />
            {orgDef.prefix !== "*" && (
              <span className="muted" style={{ fontSize: "0.75rem" }}>
                Will be created as: {orgDef.prefix}{newName.trim().toLowerCase().replace(/\s+/g, "-") || "..."}
              </span>
            )}
          </div>
          <div className="org-settings__field">
            <label className="org-settings__label">Workspace Path (optional)</label>
            <input
              className="org-settings__input mono"
              value={newWorkspace}
              onChange={(e) => setNewWorkspace(e.target.value)}
              placeholder="/home/bbrelin/ai-elevate/..."
            />
          </div>
          <button className="btn" onClick={handleAdd} disabled={adding || !newName.trim()}>
            {adding ? "Creating..." : "Create Agent"}
          </button>
        </div>
      )}

      {agents.length === 0 && !showAddForm && (
        <div className="muted" style={{ padding: 20 }}>No agents in {orgDef.label}. Click "Add Agent" to create one.</div>
      )}

      <div className="org-grid" style={{ marginTop: showAddForm ? 20 : 0 }}>
        {agents.map((agent, i) => {
          const gradient = AGENT_GRADIENTS[i % AGENT_GRADIENTS.length];
          const role = inferRole(agent);
          const isConfirming = removeConfirm === agent.id;
          return (
            <div key={agent.id} className="org-card" style={{ position: "relative" }}>
              <div className="org-card__banner" style={{ background: gradient }} />
              <div className="org-card__avatar" style={{ background: gradient }}>{initials(agent.name)}</div>
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
              <div className="org-card__actions-row">
                <button className="btn btn--sm" onClick={() => onChat(agent.id)} title="Chat">
                  {Icons.messageSquare({ width: "14px", height: "14px" })}
                  <span style={{ marginLeft: 4 }}>Chat</span>
                </button>
                {!agent.isDefault && (
                  isConfirming ? (
                    <div className="org-card__confirm">
                      <span className="muted" style={{ fontSize: "0.75rem" }}>Remove?</span>
                      <button
                        className="btn btn--sm btn--danger"
                        onClick={() => handleRemove(agent.id)}
                        disabled={removing === agent.id}
                      >
                        {removing === agent.id ? "..." : "Yes"}
                      </button>
                      <button className="btn btn--sm" onClick={() => setRemoveConfirm(null)}>No</button>
                    </div>
                  ) : (
                    <button
                      className="btn btn--sm btn--danger-outline"
                      onClick={() => setRemoveConfirm(agent.id)}
                      title="Remove agent"
                    >
                      {Icons.trash({ width: "14px", height: "14px" })}
                    </button>
                  )
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ============================== Channels Tab ============================== */

function ChannelsTab({
  orgAgents,
  rpc,
  connected,
}: {
  orgAgents: AgentEntry[];
  rpc: RpcClient | null;
  connected: boolean;
}) {
  const [snapshot, setSnapshot] = useState<ChannelSnapshot | null>(null);
  const [allBindings, setAllBindings] = useState<Binding[]>([]);
  const [configHash, setConfigHash] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Which agent to bind/unbind
  const [bindAgent, setBindAgent] = useState<string>(orgAgents[0]?.id ?? "");

  const agentIds = new Set(orgAgents.map((a) => a.id));

  const loadData = useCallback(async () => {
    if (!connected || !rpc) return;
    setLoading(true);
    setError(null);
    try {
      const [channelsRes, configRes] = await Promise.all([
        rpc.request<ChannelSnapshot>("channels.status", {}),
        rpc.request<ConfigSnapshot>("config.get", {}),
      ]);
      setSnapshot(channelsRes);
      setConfigHash(configRes?.hash ?? null);

      if (configRes?.config) {
        const cfg = configRes.config as { bindings?: Binding[] };
        setAllBindings(cfg.bindings ?? []);
      }
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }, [connected, rpc]);

  useEffect(() => {
    loadData();
  }, [connected]);

  const orgBindings = allBindings.filter((b) => agentIds.has(b.agentId));

  const addBinding = async (channelId: string) => {
    if (!rpc || !bindAgent || busy) return;
    setBusy(true);
    setError(null);
    try {
      const newBinding: Binding = { agentId: bindAgent, match: { channel: channelId } };
      const updated = [...allBindings, newBinding];
      const patchJson = JSON.stringify({ bindings: updated });
      await rpc.request("config.patch", { raw: patchJson, baseHash: configHash });
      await loadData();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  const removeBinding = async (channelId: string, agentId: string) => {
    if (!rpc || busy) return;
    setBusy(true);
    setError(null);
    try {
      const updated = allBindings.filter(
        (b) => !(b.agentId === agentId && b.match.channel === channelId)
      );
      const patchJson = JSON.stringify({ bindings: updated });
      await rpc.request("config.patch", { raw: patchJson, baseHash: configHash });
      await loadData();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  const channelOrder = snapshot?.channelOrder ?? Object.keys(snapshot?.channels ?? {});
  const labels = snapshot?.channelLabels ?? {};
  const accounts = snapshot?.channelAccounts ?? {};

  const channelRows = channelOrder.map((chId) => {
    const accts = accounts[chId] ?? [];
    const anyConnected = accts.some((a) => a.connected || a.loggedIn);
    const boundAgents = orgBindings.filter((b) => b.match.channel === chId).map((b) => b.agentId);
    return { id: chId, label: labels[chId] ?? chId, connected: anyConnected, boundAgents, accounts: accts };
  });

  return (
    <div>
      <div className="org-detail__section-header">
        <h3>Channel Bindings</h3>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <select
            className="org-settings__input"
            style={{ padding: "4px 8px", fontSize: "0.82rem", width: "auto" }}
            value={bindAgent}
            onChange={(e) => setBindAgent(e.target.value)}
          >
            {orgAgents.map((a) => (
              <option key={a.id} value={a.id}>{a.name}</option>
            ))}
          </select>
          <button className="btn btn--sm" onClick={loadData} disabled={loading}>
            {Icons.refreshCw({ width: "12px", height: "12px" })}
          </button>
        </div>
      </div>

      {error && <div className="callout danger">{error}</div>}
      {loading && <div className="muted" style={{ padding: 16 }}>Loading channels...</div>}

      {!loading && channelRows.length === 0 && (
        <div className="muted" style={{ padding: 20 }}>No channels configured.</div>
      )}

      <div className="org-channels-list">
        {channelRows.map((ch) => (
          <div key={ch.id} className={`org-channel-row ${ch.boundAgents.length > 0 ? "org-channel-row--bound" : ""}`}>
            <div className="org-channel-row__main">
              <div className="org-channel-row__name" style={{ textTransform: "capitalize" }}>{ch.label}</div>
              <div className="org-channel-row__status">
                <span className={`chip ${ch.connected ? "chip--ok" : ""}`}>
                  {ch.connected ? "connected" : "offline"}
                </span>
              </div>
            </div>

            {/* Bound agents with remove buttons */}
            {ch.boundAgents.length > 0 && (
              <div className="org-channel-row__agents">
                <span className="muted" style={{ fontSize: "0.78rem" }}>Bound to: </span>
                {ch.boundAgents.map((aid) => {
                  const agent = orgAgents.find((a) => a.id === aid);
                  return (
                    <span key={aid} className="chip chip--accent" style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                      {agent?.name ?? aid}
                      <button
                        className="org-binding-remove"
                        onClick={() => removeBinding(ch.id, aid)}
                        disabled={busy}
                        title={`Unbind ${agent?.name ?? aid}`}
                      >
                        &times;
                      </button>
                    </span>
                  );
                })}
              </div>
            )}

            {/* Add binding button */}
            {!ch.boundAgents.includes(bindAgent) && (
              <div style={{ marginTop: 8 }}>
                <button
                  className="btn btn--sm"
                  onClick={() => addBinding(ch.id)}
                  disabled={busy || !bindAgent}
                >
                  + Bind {orgAgents.find((a) => a.id === bindAgent)?.name ?? bindAgent}
                </button>
              </div>
            )}

            {ch.accounts.length > 0 && (
              <div className="org-channel-row__accounts">
                {ch.accounts.map((acct, i) => (
                  <div key={acct.accountId ?? acct.id ?? i} className="org-channel-account">
                    <span className={`statusDot ${acct.connected || acct.loggedIn ? "ok" : ""}`} />
                    <span className="mono" style={{ fontSize: "0.78rem" }}>
                      {acct.label ?? acct.phone ?? acct.username ?? acct.accountId ?? acct.id ?? `Account ${i + 1}`}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

/* ============================== Settings Tab ============================== */

function SettingsTab({
  orgAgents,
  rpc,
  connected,
}: {
  orgAgents: AgentEntry[];
  rpc: RpcClient | null;
  connected: boolean;
}) {
  const [selectedAgent, setSelectedAgent] = useState<string | null>(orgAgents[0]?.id ?? null);
  const [agentConfig, setAgentConfig] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editModel, setEditModel] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  const loadAgentConfig = useCallback(async (agentId: string) => {
    if (!connected || !rpc) return;
    setLoading(true);
    setError(null);
    try {
      const configRes = await rpc.request<ConfigSnapshot>("config.get", {});
      if (configRes?.config) {
        const cfg = configRes.config as { agents?: { list?: Array<Record<string, unknown>> } };
        const entry = cfg.agents?.list?.find((a) => a.id === agentId) ?? null;
        setAgentConfig(entry as Record<string, unknown> | null);
        setEditName((entry?.name as string) ?? "");
        setEditModel((entry?.model as string) ?? "");
      }
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }, [connected, rpc]);

  useEffect(() => {
    if (selectedAgent) loadAgentConfig(selectedAgent);
  }, [selectedAgent, connected]);

  const handleSave = async () => {
    if (!selectedAgent || !rpc || !connected) return;
    setSaving(true);
    setSaveMsg(null);
    try {
      await rpc.request("agents.update", {
        agentId: selectedAgent,
        ...(editName && { name: editName }),
        ...(editModel && { model: editModel }),
      });
      setSaveMsg("Saved successfully");
      await loadAgentConfig(selectedAgent);
    } catch (err) {
      setSaveMsg(`Error: ${err}`);
    } finally {
      setSaving(false);
    }
  };

  const agent = orgAgents.find((a) => a.id === selectedAgent);

  return (
    <div>
      <div className="org-detail__section-header">
        <h3>Agent Settings</h3>
      </div>

      <div className="org-settings__agent-picker">
        {orgAgents.map((a) => (
          <button
            key={a.id}
            className={`org-settings__agent-btn ${selectedAgent === a.id ? "org-settings__agent-btn--active" : ""}`}
            onClick={() => setSelectedAgent(a.id)}
          >
            {a.name}
          </button>
        ))}
      </div>

      {error && <div className="callout danger">{error}</div>}
      {loading && <div className="muted" style={{ padding: 16 }}>Loading configuration...</div>}

      {!loading && agentConfig && agent && (
        <div className="org-settings__form">
          <div className="org-settings__field">
            <label className="org-settings__label">Agent ID</label>
            <input className="org-settings__input" value={agent.id} disabled />
          </div>
          <div className="org-settings__field">
            <label className="org-settings__label">Display Name</label>
            <input className="org-settings__input" value={editName} onChange={(e) => setEditName(e.target.value)} placeholder="Agent display name" />
          </div>
          <div className="org-settings__field">
            <label className="org-settings__label">Model</label>
            <input className="org-settings__input" value={editModel} onChange={(e) => setEditModel(e.target.value)} placeholder="e.g. claude-sonnet-4-6" />
          </div>
          {agentConfig.workspace && (
            <div className="org-settings__field">
              <label className="org-settings__label">Workspace</label>
              <input className="org-settings__input mono" value={String(agentConfig.workspace)} disabled />
            </div>
          )}

          {Object.keys(agentConfig).filter((k) => !["id", "name", "model", "workspace", "agentDir", "default"].includes(k)).length > 0 && (
            <details className="org-settings__extra">
              <summary className="org-settings__extra-summary">Additional configuration</summary>
              <pre className="org-settings__json">
                {JSON.stringify(
                  Object.fromEntries(Object.entries(agentConfig).filter(([k]) => !["id", "name", "model", "workspace", "agentDir", "default"].includes(k))),
                  null, 2,
                )}
              </pre>
            </details>
          )}

          <div className="org-settings__actions">
            <button className="btn" onClick={handleSave} disabled={saving}>{saving ? "Saving..." : "Save Changes"}</button>
            {saveMsg && (
              <span className={`org-settings__msg ${saveMsg.startsWith("Error") ? "org-settings__msg--error" : ""}`}>{saveMsg}</span>
            )}
          </div>
        </div>
      )}

      {!loading && !agentConfig && selectedAgent && (
        <div className="muted" style={{ padding: 16 }}>No configuration found for this agent.</div>
      )}
    </div>
  );
}

/* ============================== Files Tab ============================== */

function FilesTab({
  orgAgents,
  rpc,
  connected,
}: {
  orgAgents: AgentEntry[];
  rpc: RpcClient | null;
  connected: boolean;
}) {
  const [selectedAgent, setSelectedAgent] = useState<string | null>(orgAgents[0]?.id ?? null);
  const [files, setFiles] = useState<AgentFileEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [openFile, setOpenFile] = useState<AgentFileEntry | null>(null);
  const [fileContent, setFileContent] = useState("");
  const [fileLoading, setFileLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  const loadFiles = useCallback(async (agentId: string) => {
    if (!connected || !rpc) return;
    setLoading(true);
    setError(null);
    setOpenFile(null);
    try {
      const res = await rpc.request<{ files: AgentFileEntry[] }>("agents.files.list", { agentId });
      setFiles(res?.files ?? []);
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }, [connected, rpc]);

  useEffect(() => {
    if (selectedAgent) loadFiles(selectedAgent);
  }, [selectedAgent, connected]);

  const handleOpenFile = async (file: AgentFileEntry) => {
    if (!selectedAgent || !rpc || file.missing) return;
    setFileLoading(true);
    setSaveMsg(null);
    try {
      const res = await rpc.request<{ file: AgentFileEntry }>("agents.files.get", { agentId: selectedAgent, name: file.name });
      setOpenFile(res.file);
      setFileContent(res.file.content ?? "");
    } catch (err) {
      setError(String(err));
    } finally {
      setFileLoading(false);
    }
  };

  const handleSaveFile = async () => {
    if (!selectedAgent || !rpc || !openFile) return;
    setSaving(true);
    setSaveMsg(null);
    try {
      await rpc.request("agents.files.set", { agentId: selectedAgent, name: openFile.name, content: fileContent });
      setSaveMsg("File saved");
    } catch (err) {
      setSaveMsg(`Error: ${err}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <div className="org-detail__section-header">
        <h3>Workspace Files</h3>
      </div>

      <div className="org-settings__agent-picker">
        {orgAgents.map((a) => (
          <button
            key={a.id}
            className={`org-settings__agent-btn ${selectedAgent === a.id ? "org-settings__agent-btn--active" : ""}`}
            onClick={() => setSelectedAgent(a.id)}
          >
            {a.name}
          </button>
        ))}
      </div>

      {error && <div className="callout danger">{error}</div>}
      {loading && <div className="muted" style={{ padding: 16 }}>Loading files...</div>}

      {!loading && files.length === 0 && <div className="muted" style={{ padding: 16 }}>No workspace files found.</div>}

      {!loading && files.length > 0 && !openFile && (
        <div className="org-files-list">
          {files.map((f) => (
            <button key={f.name} className={`org-file-row ${f.missing ? "org-file-row--missing" : ""}`} onClick={() => handleOpenFile(f)} disabled={f.missing}>
              <span className="org-file-row__icon">{Icons.fileText({ width: "14px", height: "14px" })}</span>
              <span className="org-file-row__name">{f.name}</span>
              {f.missing ? <span className="chip">not created</span> : (
                <span className="muted" style={{ fontSize: "0.75rem" }}>{f.size != null ? `${(f.size / 1024).toFixed(1)} KB` : ""}</span>
              )}
            </button>
          ))}
        </div>
      )}

      {openFile && (
        <div className="org-file-editor">
          <div className="org-file-editor__header">
            <button className="btn btn--icon btn--sm" onClick={() => { setOpenFile(null); setSaveMsg(null); }} title="Back">
              {Icons.chevronLeft({ width: "14px", height: "14px" })}
            </button>
            <span className="org-file-editor__name">{openFile.name}</span>
            <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
              {saveMsg && <span className={`org-settings__msg ${saveMsg.startsWith("Error") ? "org-settings__msg--error" : ""}`}>{saveMsg}</span>}
              <button className="btn btn--sm" onClick={handleSaveFile} disabled={saving}>{saving ? "Saving..." : "Save"}</button>
            </div>
          </div>
          {fileLoading ? (
            <div className="muted" style={{ padding: 16 }}>Loading file...</div>
          ) : (
            <textarea className="org-file-editor__textarea mono" value={fileContent} onChange={(e) => setFileContent(e.target.value)} spellCheck={false} />
          )}
        </div>
      )}
    </div>
  );
}
