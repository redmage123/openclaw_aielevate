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
  status: "active" | "completed" | "blocked" | "planned";
  description: string;
  lastUpdate?: string;
  source: string;
};

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
  const [standups, setStandups] = useState<{ date: string; content: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedProject, setExpandedProject] = useState<string | null>(null);
  const [expandedStandup, setExpandedStandup] = useState<string | null>(null);

  const loadProjects = useCallback(async () => {
    if (!connected || !rpc) return;
    setLoading(true);
    setError(null);
    try {
      const parsed: ProjectEntry[] = [];
      const standupEntries: { date: string; content: string }[] = [];

      // Load workspace files for the first agent (usually the org lead) to find project data
      for (const agent of orgAgents.slice(0, 3)) {
        try {
          const filesRes = await rpc.request<{ files: AgentFileEntry[]; workspace: string }>("agents.files.list", { agentId: agent.id });
          const ws = filesRes?.workspace ?? "";

          // Look for strategic/project files
          for (const fname of ["STRATEGIC-PLAN.md", "MASTER-STRATEGY-2026-Q1.md", "PRODUCT.md", "ROADMAP.md"]) {
            try {
              const fRes = await rpc.request<{ file: AgentFileEntry }>("agents.files.get", { agentId: agent.id, name: fname });
              if (fRes?.file?.content) {
                const lines = fRes.file.content.split("\n");
                const title = lines.find((l: string) => l.startsWith("# "))?.replace(/^#\s+/, "") ?? fname;
                parsed.push({
                  name: title,
                  status: "active",
                  description: lines.slice(0, 5).filter((l: string) => l.trim() && !l.startsWith("#")).join(" ").slice(0, 200),
                  source: `${agent.name} / ${fname}`,
                });
              }
            } catch { /* file doesn't exist */ }
          }
        } catch { /* agent files not accessible */ }
      }

      // Try to load standup/memory files for schedule context
      for (const agent of orgAgents.slice(0, 1)) {
        try {
          // Read recent standups via the MEMORY.md or similar
          const memRes = await rpc.request<{ file: AgentFileEntry }>("agents.files.get", { agentId: agent.id, name: "MEMORY.md" });
          if (memRes?.file?.content) {
            standupEntries.push({ date: "Latest", content: memRes.file.content.slice(0, 1500) });
          }
        } catch { /* no memory file */ }
      }

      // If no project files found, try reading from the config for workspace paths and infer
      if (parsed.length === 0) {
        const configRes = await rpc.request<ConfigSnapshot>("config.get", {});
        if (configRes?.config) {
          const cfg = configRes.config as { agents?: { list?: Array<{ id: string; workspace?: string }> } };
          for (const entry of cfg.agents?.list ?? []) {
            const isOurs = orgAgents.some((a) => a.id === entry.id);
            if (isOurs && entry.workspace) {
              parsed.push({
                name: entry.id,
                status: "active",
                description: `Workspace: ${entry.workspace}`,
                source: "config",
              });
            }
          }
        }
      }

      setProjects(parsed);
      setStandups(standupEntries);
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
    completed: "chip--ok",
    blocked: "chip--danger",
    planned: "",
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
      {loading && <div className="muted" style={{ padding: 16 }}>Scanning workspace for projects...</div>}

      {!loading && projects.length === 0 && (
        <div className="muted" style={{ padding: 20 }}>
          No project files found in agent workspaces. Add STRATEGIC-PLAN.md, ROADMAP.md, or PRODUCT.md to an agent workspace to see them here.
        </div>
      )}

      {projects.length > 0 && (
        <div className="org-projects-list">
          {projects.map((p) => (
            <button
              key={p.name + p.source}
              className={`org-project-row ${expandedProject === p.name ? "org-project-row--expanded" : ""}`}
              onClick={() => setExpandedProject(expandedProject === p.name ? null : p.name)}
            >
              <div className="org-project-row__main">
                <span className={`chip ${statusColors[p.status] ?? ""}`}>{p.status}</span>
                <span className="org-project-row__name">{p.name}</span>
                <span className="org-project-row__source muted">{p.source}</span>
              </div>
              {expandedProject === p.name && p.description && (
                <div className="org-project-row__desc">{p.description}</div>
              )}
              {p.lastUpdate && <div className="org-project-row__date muted">Updated: {p.lastUpdate}</div>}
            </button>
          ))}
        </div>
      )}

      {standups.length > 0 && (
        <div style={{ marginTop: 24 }}>
          <h4 style={{ margin: "0 0 12px", fontSize: "0.95rem" }}>Recent Activity</h4>
          {standups.map((s) => (
            <div key={s.date} className="org-standup-entry">
              <button
                className="org-standup-entry__header"
                onClick={() => setExpandedStandup(expandedStandup === s.date ? null : s.date)}
              >
                <span className="org-standup-entry__date">{s.date}</span>
                <span className="muted" style={{ fontSize: "0.75rem" }}>
                  {expandedStandup === s.date ? "collapse" : "expand"}
                </span>
              </button>
              {expandedStandup === s.date && (
                <pre className="org-standup-entry__content">{s.content}</pre>
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
