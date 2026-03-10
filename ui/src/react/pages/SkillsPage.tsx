import { useEffect } from "react";
import { useGatewayStore } from "../stores/gateway.ts";
import { useSkillsStore } from "../stores/skills.ts";
import PageHeader from "../components/PageHeader.tsx";
import EmptyState from "../components/EmptyState.tsx";
import { Icons } from "../icons.tsx";

type SkillEntry = {
  skillKey: string;
  name: string;
  description?: string;
  source?: string;
  enabled: boolean;
  version?: string;
  installed: boolean;
};

export default function SkillsPage() {
  const connected = useGatewayStore((s) => s.connected);
  const skillsReport = useSkillsStore((s) => s.skillsReport);
  const skillsLoading = useSkillsStore((s) => s.skillsLoading);
  const skillsError = useSkillsStore((s) => s.skillsError);
  const skillsFilter = useSkillsStore((s) => s.skillsFilter);
  const load = useSkillsStore((s) => s.load);
  const setFilter = useSkillsStore((s) => s.setFilter);

  useEffect(() => {
    if (connected) load();
  }, [connected, load]);

  const skills: SkillEntry[] = Array.isArray(skillsReport)
    ? (skillsReport as SkillEntry[])
    : [];

  const needle = skillsFilter.trim().toLowerCase();
  const filtered = needle
    ? skills.filter((s) =>
        [s.name, s.description, s.source].join(" ").toLowerCase().includes(needle),
      )
    : skills;

  const enabledCount = skills.filter((s) => s.enabled).length;

  return (
    <section>
      <PageHeader
        title="Skills"
        description={skills.length > 0 ? `${enabledCount} of ${skills.length} enabled` : "Bundled, managed, and workspace skills."}
        actions={
          <button className="btn" onClick={() => load()} disabled={skillsLoading}>
            {Icons.refreshCw({ width: "14px", height: "14px" })}
            <span style={{ marginLeft: 6 }}>{skillsLoading ? "Loading..." : "Refresh"}</span>
          </button>
        }
      />

      <div className="card">
        {/* Filter */}
        <div className="filters">
          <div className="search-field">
            <input
              value={skillsFilter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Search skills..."
              className="search-input"
            />
          </div>
          <span className="muted" style={{ fontSize: 12 }}>{filtered.length} of {skills.length}</span>
        </div>

        {skillsError && <div className="callout danger" style={{ marginTop: 12 }}>{skillsError}</div>}

        {/* Skills list */}
        <div className="list" style={{ marginTop: 16 }}>
          {filtered.length === 0 && !skillsLoading && (
            <EmptyState
              icon={Icons.zap}
              title={needle ? "No matching skills" : "No skills found"}
              description={needle ? "Try a different search term." : "Skills extend your assistant's capabilities."}
            />
          )}
          {skillsLoading && skills.length === 0 && (
            <div className="list-loading">
              <div className="spinner" />
              <span className="muted">Loading skills...</span>
            </div>
          )}
          {filtered.map((skill) => (
            <div className="list-item" key={skill.skillKey}>
              <div className="list-main">
                <div className="list-title">
                  {skill.name}
                  {skill.version && (
                    <span className="muted" style={{ marginLeft: 8, fontSize: 11 }}>
                      v{skill.version}
                    </span>
                  )}
                </div>
                {skill.description && <div className="list-sub">{skill.description}</div>}
                {skill.source && <div className="list-sub muted" style={{ fontSize: 11 }}>{skill.source}</div>}
              </div>
              <div className="list-meta">
                <div className="chip-row">
                  <span className={`chip ${skill.installed ? "chip--ok" : ""}`}>
                    {skill.installed ? "installed" : "available"}
                  </span>
                  <span className={`chip ${skill.enabled ? "chip--ok" : ""}`}>
                    {skill.enabled ? "enabled" : "disabled"}
                  </span>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
