import { useEffect, useState, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useGatewayStore } from "../stores/gateway.ts";
import { useAgentsStore } from "../stores/agents.ts";
import { Icons } from "../icons.tsx";
import {
  type AgentEntry,
  type OrgGroup,
  AGENT_GRADIENTS,
  ORG_DEFS,
  initials,
  inferRole,
  groupAgents,
  parseAgentsList,
  findOrgById,
} from "./org-shared.ts";

type Tab = "team" | "channels" | "settings" | "files";

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

  const [tab, setTab] = useState<Tab>("team");

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
        <div className="org-detail__back">
          <button className="btn btn--icon" onClick={() => navigate("/org")} title="Back to organizations">
            {Icons.chevronLeft({ width: "16px", height: "16px" })}
          </button>
          <span>Organization not found</span>
        </div>
      </section>
    );
  }

  const orgAgents = org?.agents ?? [];

  return (
    <section className="org-detail">
      {/* Header */}
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

      {/* Tab bar */}
      <div className="org-detail__tabs">
        {(["team", "channels", "settings", "files"] as Tab[]).map((t) => (
          <button
            key={t}
            className={`org-detail__tab ${tab === t ? "org-detail__tab--active" : ""}`}
            onClick={() => setTab(t)}
          >
            {t === "team" && Icons.users({ width: "14px", height: "14px" })}
            {t === "channels" && Icons.radio({ width: "14px", height: "14px" })}
            {t === "settings" && Icons.settings({ width: "14px", height: "14px" })}
            {t === "files" && Icons.fileText({ width: "14px", height: "14px" })}
            <span style={{ marginLeft: 6, textTransform: "capitalize" }}>{t}</span>
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="org-detail__content">
        {tab === "team" && (
          <TeamTab org={org} orgDef={orgDef} agents={orgAgents} onChat={(id) => navigate(`/org/${id}/chat`)} />
        )}
        {tab === "channels" && (
          <ChannelsTab orgAgents={orgAgents} client={client} connected={connected} />
        )}
        {tab === "settings" && (
          <SettingsTab orgAgents={orgAgents} client={client} connected={connected} />
        )}
        {tab === "files" && (
          <FilesTab orgAgents={orgAgents} client={client} connected={connected} />
        )}
      </div>
    </section>
  );
}

/* ============================== Team Tab ============================== */

function TeamTab({
  org,
  orgDef,
  agents,
  onChat,
}: {
  org: OrgGroup | undefined;
  orgDef: { label: string };
  agents: AgentEntry[];
  onChat: (id: string) => void;
}) {
  if (agents.length === 0) {
    return <div className="muted" style={{ padding: 20 }}>No agents in {orgDef.label}.</div>;
  }

  return (
    <div className="org-grid">
      {agents.map((agent, i) => {
        const gradient = AGENT_GRADIENTS[i % AGENT_GRADIENTS.length];
        const role = inferRole(agent);
        return (
          <button key={agent.id} className="org-card" onClick={() => onChat(agent.id)} title={`Chat with ${agent.name}`}>
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
            <div className="org-card__action">
              {Icons.messageSquare({ width: "16px", height: "16px" })}
              <span>Chat</span>
            </div>
          </button>
        );
      })}
    </div>
  );
}

/* ============================== Channels Tab ============================== */

function ChannelsTab({
  orgAgents,
  client,
  connected,
}: {
  orgAgents: AgentEntry[];
  client: unknown;
  connected: boolean;
}) {
  const [snapshot, setSnapshot] = useState<ChannelSnapshot | null>(null);
  const [bindings, setBindings] = useState<Record<string, string[]>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const agentIds = new Set(orgAgents.map((a) => a.id));

  const loadData = useCallback(async () => {
    if (!connected || !client) return;
    setLoading(true);
    setError(null);
    try {
      const rpc = client as { request: <T>(method: string, params: unknown) => Promise<T> };
      const [channelsRes, configRes] = await Promise.all([
        rpc.request<ChannelSnapshot>("channels.status", {}),
        rpc.request<ConfigSnapshot>("config.get", {}),
      ]);
      setSnapshot(channelsRes);

      // Parse bindings from config to find which channels are bound to this org's agents
      if (configRes?.config) {
        const cfg = configRes.config as { bindings?: Array<{ agentId: string; match: { channel: string } }> };
        const map: Record<string, string[]> = {};
        for (const b of cfg.bindings ?? []) {
          if (agentIds.has(b.agentId)) {
            if (!map[b.match.channel]) map[b.match.channel] = [];
            if (!map[b.match.channel].includes(b.agentId)) {
              map[b.match.channel].push(b.agentId);
            }
          }
        }
        setBindings(map);
      }
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }, [connected, client, agentIds.size]);

  useEffect(() => {
    loadData();
  }, [connected]);

  const channelOrder = snapshot?.channelOrder ?? Object.keys(snapshot?.channels ?? {});
  const labels = snapshot?.channelLabels ?? {};
  const accounts = snapshot?.channelAccounts ?? {};

  // Show all channels, marking which ones are bound to this org
  const channelRows = channelOrder.map((chId) => {
    const accts = accounts[chId] ?? [];
    const anyConnected = accts.some((a) => a.connected || a.loggedIn);
    const boundAgents = bindings[chId] ?? [];
    const isBound = boundAgents.length > 0;
    return { id: chId, label: labels[chId] ?? chId, connected: anyConnected, isBound, boundAgents, accounts: accts };
  });

  return (
    <div>
      <div className="org-detail__section-header">
        <h3>Channel Connections</h3>
        <button className="btn btn--sm" onClick={loadData} disabled={loading}>
          {Icons.refreshCw({ width: "12px", height: "12px" })}
          <span style={{ marginLeft: 4 }}>{loading ? "Loading..." : "Refresh"}</span>
        </button>
      </div>
      {error && <div className="callout danger">{error}</div>}

      {channelRows.length === 0 && !loading && (
        <div className="muted" style={{ padding: 20 }}>No channels configured.</div>
      )}

      <div className="org-channels-list">
        {channelRows.map((ch) => (
          <div key={ch.id} className={`org-channel-row ${ch.isBound ? "org-channel-row--bound" : ""}`}>
            <div className="org-channel-row__main">
              <div className="org-channel-row__name" style={{ textTransform: "capitalize" }}>{ch.label}</div>
              <div className="org-channel-row__status">
                <span className={`chip ${ch.connected ? "chip--ok" : ""}`}>
                  {ch.connected ? "connected" : "offline"}
                </span>
                {ch.isBound ? (
                  <span className="chip chip--accent">bound</span>
                ) : (
                  <span className="chip">unbound</span>
                )}
              </div>
            </div>
            {ch.isBound && (
              <div className="org-channel-row__agents">
                <span className="muted" style={{ fontSize: "0.78rem" }}>Routed to: </span>
                {ch.boundAgents.map((aid) => {
                  const agent = orgAgents.find((a) => a.id === aid);
                  return (
                    <span key={aid} className="chip chip--sm">{agent?.name ?? aid}</span>
                  );
                })}
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
  client,
  connected,
}: {
  orgAgents: AgentEntry[];
  client: unknown;
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
    if (!connected || !client) return;
    setLoading(true);
    setError(null);
    try {
      const rpc = client as { request: <T>(method: string, params: unknown) => Promise<T> };
      // Load full config and extract this agent's entry
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
  }, [connected, client]);

  useEffect(() => {
    if (selectedAgent) loadAgentConfig(selectedAgent);
  }, [selectedAgent, connected]);

  const handleSave = async () => {
    if (!selectedAgent || !client || !connected) return;
    setSaving(true);
    setSaveMsg(null);
    try {
      const rpc = client as { request: <T>(method: string, params: unknown) => Promise<T> };
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

      {/* Agent selector */}
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
            <input
              className="org-settings__input"
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              placeholder="Agent display name"
            />
          </div>
          <div className="org-settings__field">
            <label className="org-settings__label">Model</label>
            <input
              className="org-settings__input"
              value={editModel}
              onChange={(e) => setEditModel(e.target.value)}
              placeholder="e.g. claude-sonnet-4-6"
            />
          </div>
          {agentConfig.workspace && (
            <div className="org-settings__field">
              <label className="org-settings__label">Workspace</label>
              <input className="org-settings__input mono" value={String(agentConfig.workspace)} disabled />
            </div>
          )}

          {/* Read-only summary of other config keys */}
          {Object.keys(agentConfig).filter((k) => !["id", "name", "model", "workspace", "agentDir", "default"].includes(k)).length > 0 && (
            <details className="org-settings__extra">
              <summary className="org-settings__extra-summary">Additional configuration</summary>
              <pre className="org-settings__json">
                {JSON.stringify(
                  Object.fromEntries(
                    Object.entries(agentConfig).filter(([k]) => !["id", "name", "model", "workspace", "agentDir", "default"].includes(k))
                  ),
                  null,
                  2,
                )}
              </pre>
            </details>
          )}

          <div className="org-settings__actions">
            <button className="btn" onClick={handleSave} disabled={saving}>
              {saving ? "Saving..." : "Save Changes"}
            </button>
            {saveMsg && (
              <span className={`org-settings__msg ${saveMsg.startsWith("Error") ? "org-settings__msg--error" : ""}`}>
                {saveMsg}
              </span>
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
  client,
  connected,
}: {
  orgAgents: AgentEntry[];
  client: unknown;
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
    if (!connected || !client) return;
    setLoading(true);
    setError(null);
    setOpenFile(null);
    try {
      const rpc = client as { request: <T>(method: string, params: unknown) => Promise<T> };
      const res = await rpc.request<{ files: AgentFileEntry[] }>("agents.files.list", { agentId });
      setFiles(res?.files ?? []);
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }, [connected, client]);

  useEffect(() => {
    if (selectedAgent) loadFiles(selectedAgent);
  }, [selectedAgent, connected]);

  const handleOpenFile = async (file: AgentFileEntry) => {
    if (!selectedAgent || !client || file.missing) return;
    setFileLoading(true);
    setSaveMsg(null);
    try {
      const rpc = client as { request: <T>(method: string, params: unknown) => Promise<T> };
      const res = await rpc.request<{ file: AgentFileEntry }>("agents.files.get", {
        agentId: selectedAgent,
        name: file.name,
      });
      setOpenFile(res.file);
      setFileContent(res.file.content ?? "");
    } catch (err) {
      setError(String(err));
    } finally {
      setFileLoading(false);
    }
  };

  const handleSaveFile = async () => {
    if (!selectedAgent || !client || !openFile) return;
    setSaving(true);
    setSaveMsg(null);
    try {
      const rpc = client as { request: <T>(method: string, params: unknown) => Promise<T> };
      await rpc.request("agents.files.set", {
        agentId: selectedAgent,
        name: openFile.name,
        content: fileContent,
      });
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

      {/* Agent selector */}
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

      {!loading && files.length === 0 && (
        <div className="muted" style={{ padding: 16 }}>No workspace files found.</div>
      )}

      {!loading && files.length > 0 && !openFile && (
        <div className="org-files-list">
          {files.map((f) => (
            <button
              key={f.name}
              className={`org-file-row ${f.missing ? "org-file-row--missing" : ""}`}
              onClick={() => handleOpenFile(f)}
              disabled={f.missing}
            >
              <span className="org-file-row__icon">
                {Icons.fileText({ width: "14px", height: "14px" })}
              </span>
              <span className="org-file-row__name">{f.name}</span>
              {f.missing ? (
                <span className="chip">not created</span>
              ) : (
                <span className="muted" style={{ fontSize: "0.75rem" }}>
                  {f.size != null ? `${(f.size / 1024).toFixed(1)} KB` : ""}
                </span>
              )}
            </button>
          ))}
        </div>
      )}

      {/* File editor */}
      {openFile && (
        <div className="org-file-editor">
          <div className="org-file-editor__header">
            <button className="btn btn--icon btn--sm" onClick={() => { setOpenFile(null); setSaveMsg(null); }} title="Back to file list">
              {Icons.chevronLeft({ width: "14px", height: "14px" })}
            </button>
            <span className="org-file-editor__name">{openFile.name}</span>
            <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
              {saveMsg && (
                <span className={`org-settings__msg ${saveMsg.startsWith("Error") ? "org-settings__msg--error" : ""}`}>
                  {saveMsg}
                </span>
              )}
              <button className="btn btn--sm" onClick={handleSaveFile} disabled={saving}>
                {saving ? "Saving..." : "Save"}
              </button>
            </div>
          </div>
          {fileLoading ? (
            <div className="muted" style={{ padding: 16 }}>Loading file...</div>
          ) : (
            <textarea
              className="org-file-editor__textarea mono"
              value={fileContent}
              onChange={(e) => setFileContent(e.target.value)}
              spellCheck={false}
            />
          )}
        </div>
      )}
    </div>
  );
}
