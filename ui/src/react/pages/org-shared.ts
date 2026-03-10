/** Shared types, constants, and helpers for Organization pages. */

export type AgentEntry = {
  id: string;
  name: string;
  workspace?: string;
  model?: string;
  isDefault?: boolean;
};

export type OrgDef = {
  id: string;
  prefix: string;
  label: string;
  description: string;
  gradient: string;
};

export const ORG_DEFS: OrgDef[] = [
  { id: "techuni", prefix: "techuni-", label: "TechUni AI", description: "Course-creator SaaS platform team", gradient: "linear-gradient(135deg, #667eea 0%, #764ba2 100%)" },
  { id: "gigforge", prefix: "gigforge-", label: "GigForge", description: "Freelance fulfillment team", gradient: "linear-gradient(135deg, #f093fb 0%, #f5576c 100%)" },
  { id: "ai-elevate", prefix: "*", label: "AI Elevate", description: "Core platform agents and utilities", gradient: "linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)" },
];

export type OrgGroup = OrgDef & { agents: AgentEntry[] };

export const AGENT_GRADIENTS = [
  "linear-gradient(135deg, #667eea 0%, #764ba2 100%)",
  "linear-gradient(135deg, #f093fb 0%, #f5576c 100%)",
  "linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)",
  "linear-gradient(135deg, #43e97b 0%, #38f9d7 100%)",
  "linear-gradient(135deg, #fa709a 0%, #fee140 100%)",
  "linear-gradient(135deg, #a18cd1 0%, #fbc2eb 100%)",
  "linear-gradient(135deg, #fccb90 0%, #d57eeb 100%)",
  "linear-gradient(135deg, #e0c3fc 0%, #8ec5fc 100%)",
];

export function initials(name: string): string {
  return (name || "?")
    .split(/[\s-_]+/)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("");
}

export function inferRole(agent: AgentEntry): string {
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

export function groupAgents(agents: AgentEntry[]): OrgGroup[] {
  const remaining = [...agents];
  const orgs: OrgGroup[] = [];

  for (const def of ORG_DEFS) {
    if (def.prefix === "*") {
      if (remaining.length > 0) {
        orgs.push({ ...def, agents: [...remaining] });
        remaining.length = 0;
      }
      continue;
    }
    const matched = remaining.filter((a) => a.id.startsWith(def.prefix));
    if (matched.length > 0) {
      orgs.push({ ...def, agents: matched });
      for (const m of matched) {
        const idx = remaining.indexOf(m);
        if (idx >= 0) remaining.splice(idx, 1);
      }
    }
  }

  return orgs;
}

export function parseAgentsList(agentsList: unknown): AgentEntry[] {
  if (!agentsList) return [];
  const result = agentsList as { agents?: AgentEntry[]; defaultId?: string };
  if (Array.isArray(result.agents)) {
    return result.agents.map((a) => ({
      ...a,
      name: a.name || a.id,
      isDefault: a.id === result.defaultId,
    }));
  }
  if (Array.isArray(agentsList)) return (agentsList as AgentEntry[]).map((a) => ({ ...a, name: a.name || a.id }));
  return [];
}

export function findOrgById(orgId: string): OrgDef | undefined {
  return ORG_DEFS.find((d) => d.id === orgId);
}

export function findOrgForAgent(agentId: string): OrgDef | undefined {
  for (const def of ORG_DEFS) {
    if (def.prefix === "*") continue;
    if (agentId.startsWith(def.prefix)) return def;
  }
  return ORG_DEFS.find((d) => d.prefix === "*");
}
