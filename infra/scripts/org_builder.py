#!/usr/bin/env python3
"""Build-Org - Organization Factory.

The Builder (meta-agent) takes a prompt describing a business and creates
a complete AI-powered organization from scratch:

1. Architect designs the org (agents, roles, departments)
2. Engineer creates agent files (AGENTS.md, bios, sessions dirs)
3. Infra sets up email routing (aliases, Mailgun domain)
4. Infra sets up DNS (Cloudflare)
5. Engineer creates the website
6. Infra deploys everything
7. Registers in the existing workflow infrastructure

Usage:
    from org_builder import build_organization

    await build_organization(
        name="AlphaDesk",
        domain="alphadesk.ai",
        description="AI-powered customer support platform. Handles tickets, live chat, knowledge base.",
        org_type="commercial",  # commercial, non-commercial, internal
    )
"""
import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

sys.path.insert(0, "/home/aielevate")

log = logging.getLogger("org-builder")

RETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=30))
DB_CONN = dict(
    host=os.environ.get("DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("DB_PORT", "5434")),
    dbname=os.environ.get("DB_NAME", "rag"),
    user=os.environ.get("DB_USER", "rag"),
    password=os.environ.get("DB_PASSWORD", "rag_vec_2026"),
)
AGENTS_BASE = Path("/home/aielevate/.openclaw/agents")
PROJECTS_BASE = Path("/opt/ai-elevate")


@dataclass
class OrgBuildInput:
    name: str  # e.g. "AlphaDesk"
    slug: str = ""  # e.g. "alphadesk" (auto-derived from name)
    domain: str = ""  # e.g. "alphadesk.ai"
    description: str = ""  # what the org does
    org_type: str = "commercial"  # commercial, non-commercial, internal
    agent_count: int = 0  # 0 = auto-determine
    departments: str = ""  # comma-separated, or empty for auto


@dataclass
class OrgBuildResult:
    status: str = "pending"
    agents_created: int = 0
    website_url: str = ""
    actions: list = field(default_factory=list)
    errors: list = field(default_factory=list)


def _db():
    import psycopg2
    conn = psycopg2.connect(**DB_CONN)
    conn.autocommit = True
    return conn


def _dispatch(agent_id, message, timeout=300):
    session_dir = AGENTS_BASE / agent_id / "sessions"
    if session_dir.exists():
        for f in session_dir.glob("*.jsonl"):
            f.unlink()
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    try:
        proc = subprocess.run(
            ["openclaw", "agent", "--agent", agent_id,
             "--message", message, "--thinking", "low", "--timeout", str(timeout)],
            capture_output=True, text=True, timeout=timeout + 30, env=env)
        output = proc.stdout or ""
        return re.sub(r'\*\[.*?\]\*', '', output, flags=re.DOTALL).strip()
    except subprocess.TimeoutExpired:
        return "TIMEOUT"


# ============================================================================
# Standard org templates
# ============================================================================

COMMERCIAL_DEPARTMENTS = {
    "sales": {"title": "Sales Lead", "desc": "Handles inbound inquiries, qualifies leads, sends proposals."},
    "advocate": {"title": "Customer Delivery Liaison", "desc": "Single point of contact for clients post-contract."},
    "pm": {"title": "Project Manager", "desc": "Sprint planning, milestone tracking, stakeholder coordination."},
    "engineer": {"title": "Lead Engineer / CTO", "desc": "Technical decisions, architecture, code reviews."},
    "dev-frontend": {"title": "Frontend Developer", "desc": "React/Vue/Svelte frontends, responsive design."},
    "dev-backend": {"title": "Backend Developer", "desc": "APIs, databases, server-side logic."},
    "devops": {"title": "DevOps Engineer", "desc": "Docker, CI/CD, deployment, monitoring."},
    "qa": {"title": "QA Engineer", "desc": "Testing, quality assurance, bug tracking."},
    "ux-designer": {"title": "UX Designer", "desc": "User research, wireframes, design specs."},
    "finance": {"title": "Finance Manager", "desc": "Billing, invoicing, financial reporting."},
    "legal": {"title": "Legal Counsel", "desc": "Contracts, compliance, IP protection."},
    "marketing": {"title": "Marketing Director", "desc": "Brand, content strategy, lead generation."},
    "social": {"title": "Social Media Manager", "desc": "Blog, newsletter, social media content."},
    "support": {"title": "Customer Support", "desc": "Ticket handling, FAQs, user assistance."},
    "csat": {"title": "Customer Satisfaction Director", "desc": "NPS, feedback, escalation handling."},
    "scout": {"title": "Business Development Scout", "desc": "Finds opportunities on Upwork, Freelancer, Fiverr."},
}

NON_COMMERCIAL_DEPARTMENTS = {
    "content": {"title": "Content Strategist", "desc": "Content planning, editorial calendar."},
    "writer": {"title": "Senior Writer", "desc": "Long-form articles, reports, documentation."},
    "editor": {"title": "Editor-in-Chief", "desc": "Quality control, fact checking, publishing."},
    "researcher": {"title": "Research Lead", "desc": "Data gathering, analysis, trend research."},
    "social": {"title": "Social Media Manager", "desc": "Blog, newsletter, social media content."},
    "legal": {"title": "Legal Counsel", "desc": "Compliance, policy, IP."},
    "finance": {"title": "Finance Manager", "desc": "Budget, accounting, reporting."},
    "monitor": {"title": "Infrastructure Monitor", "desc": "Uptime, security, performance."},
}


# ============================================================================
# Activities
# ============================================================================

@activity.defn
async def design_org_structure(input: OrgBuildInput) -> str:
    """The Architect designs the org - determines departments, agents, roles."""
    Path(f"/opt/ai-elevate/{input.slug}").mkdir(parents=True, exist_ok=True)

    loop = asyncio.get_event_loop()
    output = await loop.run_in_executor(None, lambda: _dispatch(
        "build-org-architect",
        f"DESIGN ORGANIZATION: {input.name}\n"
        f"Domain: {input.domain}\n"
        f"Type: {input.org_type}\n"
        f"Description: {input.description}\n\n"
        f"Design agents that match EXACTLY what this organization does.\n"
        f"Do NOT use generic business roles. Create domain-specific agents.\n\n"
        f"For a family support org, create agents like: meal-planner, calendar-coordinator, "
        f"homework-helper, budget-advisor, health-tracker - NOT content-writer, editor, researcher.\n\n"
        f"RULES:\n"
        f"1. One agent per distinct feature in the description.\n"
        f"2. Warm, human names (diverse backgrounds). Friendly bios.\n"
        f"3. Agent IDs: {input.slug}-role (lowercase, hyphens).\n"
        f"4. 10-20 agents total.\n"
        f"5. Root agent ({input.slug}) title should fit the domain, not 'Operations Director'.\n\n"
        f"Output ONLY a JSON object (no markdown, no explanation):\n"
        f'{{"org_name": "{input.name}", "slug": "{input.slug}",\n'
        f' "root_title": "domain-appropriate coordinator title",\n'
        f' "tone": "warm/professional/casual",\n'
        f' "culture": "1-2 sentences",\n'
        f' "departments": [\n'
        f'   {{"id": "{input.slug}-role", "title": "Role Title", "name": "First Last",\n'
        f'    "gender": "male/female", "nationality": "...",\n'
        f'    "bio_summary": "2-3 sentences"}}\n'
        f' ]}}',
        timeout=300))

    # Parse JSON from architect output
    design = None
    try:
        # Try to find JSON in the output
        json_match = re.search(r'\{[\s\S]*"departments"[\s\S]*\}', output)
        if json_match:
            design = json.loads(json_match.group())
    except Exception as e:
        log.warning(f"Failed to parse architect JSON: {e}")

    if not design or not design.get("departments"):
        log.warning("Architect did not produce valid JSON - using output as-is")
        # Try harder - maybe it's the whole output
        try:
            design = json.loads(output)
        except Exception:
            design = {"departments": [], "root_title": "Coordinator", "tone": "warm", "culture": ""}

    # Save the design
    design_file = Path(f"/opt/ai-elevate/{input.slug}/org-design.json")
    design_file.write_text(json.dumps(design, indent=2, ensure_ascii=False))
    log.info(f"Org design saved: {len(design.get('departments', []))} departments")

    # If design is empty, retry with stricter instructions
    if not design.get("departments"):
        log.warning("First architect attempt produced no departments, retrying...")
        output2 = await loop.run_in_executor(None, lambda: _dispatch(
            "build-org-architect",
            f"Output ONLY a JSON object. No markdown. No explanation. No code blocks.\n\n"
            f"Organization: {input.name} ({input.slug})\n"
            f"Domain: {input.domain}\n"
            f"Description: {input.description}\n\n"
            f"Create 12-15 agents specific to this domain. Output this exact JSON structure:\n"
            f'{{"departments": ['
            f'{{"id": "{input.slug}-agent1", "title": "Title", "name": "First Last", '
            f'"gender": "female", "nationality": "American", '
            f'"bio_summary": "Brief bio"}},'
            f'{{"id": "{input.slug}-agent2", "title": "Title2", "name": "First2 Last2", '
            f'"gender": "male", "nationality": "British", '
            f'"bio_summary": "Brief bio2"}}'
            f'], "root_title": "Coordinator Title", "tone": "warm", '
            f'"culture": "Friendly and supportive"}}',
            timeout=180))

        try:
            json_match2 = re.search(r'\{[\s\S]*"departments"[\s\S]*\}', output2)
            if json_match2:
                design = json.loads(json_match2.group())
        except Exception:
            pass

    # If still empty, generate from description keywords
    if not design.get("departments"):
        log.warning("Architect failed twice - generating from description keywords")
        desc_lower = input.description.lower()
        agents = []
        keyword_agents = [
            ("meal", "meal-planner", "Meal Planning Coordinator", "Plans weekly meals, creates grocery lists, manages dietary preferences"),
            ("calendar", "calendar", "Family Calendar Manager", "Coordinates schedules, appointments, school events, activities"),
            ("homework", "homework-helper", "Homework & Tutoring Helper", "Assists with homework, finds tutors, tracks academic progress"),
            ("chore", "chores", "Household Chore Coordinator", "Schedules chores fairly, tracks completion, manages household tasks"),
            ("budget", "budget-advisor", "Family Budget Advisor", "Tracks spending, manages bills, sets savings goals"),
            ("health", "health-tracker", "Health & Wellness Tracker", "Manages appointments, medications, vaccinations, wellness goals"),
            ("pet", "pet-care", "Pet Care Coordinator", "Schedules vet visits, feeding routines, grooming appointments"),
            ("maintenance", "home-maintenance", "Home Maintenance Manager", "Tracks repairs, seasonal tasks, filter replacements, inspections"),
            ("event", "event-planner", "Family Event Planner", "Plans birthdays, holidays, gatherings, coordinates guests"),
            ("child", "childcare", "Childcare Coordinator", "Manages babysitters, pickup schedules, daycare communication"),
            ("elder", "eldercare", "Eldercare Support Specialist", "Tracks medications, appointments, and care for aging parents"),
            ("recipe", "recipe-chef", "Recipe & Cooking Assistant", "Manages recipes, suggests meals, helps with cooking techniques"),
            ("travel", "travel-planner", "Travel Planning Assistant", "Plans trips, creates packing lists, books activities"),
            ("communicat", "family-hub", "Family Communication Hub", "Manages announcements, emergency contacts, shared notes"),
            ("goal", "goal-tracker", "Family Goal Tracker", "Sets and tracks family goals for savings, fitness, reading"),
        ]

        import random
        first_names_f = ["Maya", "Sofia", "Amara", "Luna", "Zara", "Nadia", "Leila", "Rosa", "Mei", "Ava", "Elena", "Priya", "Yuki", "Isla", "Nia"]
        first_names_m = ["Kai", "Leo", "Omar", "Mateo", "Finn", "Ravi", "Marco", "Noah", "Theo", "Sami", "Aiden", "Jin", "Lucas", "Ezra", "Rio"]
        last_names = ["Chen", "Santos", "Kim", "Okafor", "Larsson", "Patel", "Nakamura", "Reyes", "Kowalski", "Ibrahim", "Moreau", "Tanaka", "Rivera", "Johansson", "Osei"]
        nationalities = ["American", "Brazilian", "Korean", "Nigerian", "Swedish", "Indian", "Japanese", "Filipino", "Polish", "Egyptian", "French", "Japanese", "Mexican", "Swedish", "Ghanaian"]

        random.shuffle(first_names_f)
        random.shuffle(first_names_m)
        random.shuffle(last_names)
        random.shuffle(nationalities)

        idx = 0
        for keyword, role, title, bio in keyword_agents:
            if keyword in desc_lower:
                gender = "female" if idx % 2 == 0 else "male"
                name_list = first_names_f if gender == "female" else first_names_m
                name = f"{name_list[idx % len(name_list)]} {last_names[idx % len(last_names)]}"
                agents.append({
                    "id": f"{input.slug}-{role}",
                    "title": title,
                    "name": name,
                    "gender": gender,
                    "nationality": nationalities[idx % len(nationalities)],
                    "bio_summary": bio,
                })
                idx += 1

        design = {
            "departments": agents,
            "root_title": "Family Coordinator",
            "tone": "warm",
            "culture": "Supportive, non-judgmental, like a helpful family member",
        }

    # Save final design
    design_file = Path(f"/opt/ai-elevate/{input.slug}/org-design.json")
    design_file.write_text(json.dumps(design, indent=2, ensure_ascii=False))
    log.info(f"Final org design: {len(design.get('departments', []))} departments")

    return json.dumps(design)[:500]


@activity.defn
async def create_agent_files(input: OrgBuildInput) -> int:
    """Create AGENTS.md files and session directories for every agent."""
    slug = input.slug
    design_file = Path(f"/opt/ai-elevate/{slug}/org-design.json")

    # Load design or use template
    if design_file.exists():
        try:
            design = json.loads(design_file.read_text())
        except Exception:
            design = None
    else:
        design = None

    # Fallback: if no design file, create minimal structure
    if not design:
        log.warning(f"No org-design.json found for {slug} - creating minimal structure")
        design = {"departments": []}

    agents_created = 0

    # Create root agent - use domain-appropriate title from design
    root_title = design.get("root_title", "Coordinator")
    tone = design.get("tone", "professional")
    culture = design.get("culture", "")
    root_dir = AGENTS_BASE / slug / "agent"
    root_dir.mkdir(parents=True, exist_ok=True)
    (AGENTS_BASE / slug / "sessions").mkdir(exist_ok=True)
    (root_dir / "AGENTS.md").write_text(
        f"# {slug} - {root_title}\n\n"
        f"You are an AI agent ({slug}).\n\n"
        f"You are the {root_title} at {input.name}. "
        f"{input.description}\n\n"
        f"Tone: {tone}. {culture}\n\n"
        f"Your role: coordinate all agents, ensure everything runs smoothly.\n"
    )
    agents_created += 1

    # Create department agents
    for dept in design.get("departments", []):
        agent_id = dept.get("id", "")
        if not agent_id:
            continue
        title = dept.get("title", "Agent")
        name = dept.get("name", "")
        bio = dept.get("bio_summary", "")

        agent_dir = AGENTS_BASE / agent_id / "agent"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (AGENTS_BASE / agent_id / "sessions").mkdir(exist_ok=True)

        tone = design.get("tone", "professional")
        culture = design.get("culture", "")

        agents_md = (
            f"# {agent_id} - {title}\n\n"
            f"You are an AI agent ({agent_id}).\n\n"
            f"You are the {title} at {input.name}. Your name is {name}.\n\n"
            f"{bio}\n\n"
            f"Organization: {input.name} ({input.domain})\n"
            f"Tone: {tone}. {culture}\n\n"
            f"Rules:\n"
            f"- Always sign as {name}\n"
            f"- Be {tone} in all communications\n"
            f"- Never invent team member names\n"
        )

        if input.org_type == "non-commercial":
            agents_md += f"- {input.name} is non-commercial. No sales, no billing.\n"

        (agent_dir / "AGENTS.md").write_text(agents_md)
        agents_created += 1

    log.info(f"Created {agents_created} agent files for {slug}")
    return agents_created


@activity.defn
async def register_agents_in_db(input: OrgBuildInput) -> int:
    """Register all agents in the agent_bios DB table."""
    slug = input.slug
    design_file = Path(f"/opt/ai-elevate/{slug}/org-design.json")

    departments = []
    if design_file.exists():
        try:
            design = json.loads(design_file.read_text())
            departments = design.get("departments", [])
        except Exception:
            pass

    if not departments:
        # Scan agent dirs
        for d in AGENTS_BASE.iterdir():
            if d.name.startswith(f"{slug}-") and d.is_dir():
                md = d / "agent" / "AGENTS.md"
                if md.exists():
                    text = md.read_text()
                    name_match = re.search(r"Your name is (.+?)\.", text)
                    title_match = re.search(r"You are the (.+?) at", text)
                    departments.append({
                        "id": d.name,
                        "name": name_match.group(1) if name_match else "",
                        "title": title_match.group(1) if title_match else "",
                    })

    conn = _db()
    cur = conn.cursor()
    registered = 0

    # Root agent
    cur.execute(
        "INSERT INTO agent_bios (agent_id, name, role, bio) VALUES (%s, %s, %s, %s) ON CONFLICT (agent_id) DO UPDATE SET name=%s, role=%s, bio=%s",
        (slug, f"{input.name} Director", "Operations Director", f"Operations Director at {input.name}", f"{input.name} Director", "Operations Director", f"Operations Director at {input.name}"))
    registered += 1

    for dept in departments:
        agent_id = dept.get("id", "")
        name = dept.get("name", "")
        title = dept.get("title", "")
        if agent_id:
            cur.execute(
                "INSERT INTO agent_bios (agent_id, name, role, bio) VALUES (%s, %s, %s, %s) ON CONFLICT (agent_id) DO UPDATE SET name=%s, role=%s, bio=%s",
                (agent_id, name, title, f"{title} at {input.name}", name, title, f"{title} at {input.name}"))
            registered += 1

    conn.close()
    log.info(f"Registered {registered} agents in DB for {slug}")
    return registered


@activity.defn
async def setup_email_routing(input: OrgBuildInput) -> bool:
    """Add email aliases to the gateway for the new org."""
    slug = input.slug
    domain = input.domain

    # Build aliases map
    design_file = Path(f"/opt/ai-elevate/{slug}/org-design.json")
    aliases = {}
    if design_file.exists():
        try:
            design = json.loads(design_file.read_text())
            aliases = design.get("email_aliases", {})
        except Exception:
            pass

    if not aliases:
        # Default aliases
        for d in AGENTS_BASE.iterdir():
            if d.name.startswith(f"{slug}-"):
                role = d.name.replace(f"{slug}-", "")
                aliases[role] = d.name
        aliases["info"] = f"{slug}-sales" if f"{slug}-sales" in [d.name for d in AGENTS_BASE.iterdir()] else slug
        aliases["ceo"] = slug
        aliases["director"] = slug

    # Write aliases to a config file (gateway reads on restart)
    alias_file = Path(f"/opt/ai-elevate/{slug}/email-aliases.json")
    alias_file.parent.mkdir(parents=True, exist_ok=True)
    alias_file.write_text(json.dumps(aliases, indent=2))
    log.info(f"Email aliases written to {alias_file}")

    # Add Mailgun route for the domain
    # This would need Mailgun API access - for now, just log
    log.info(f"TODO: Create Mailgun route for *@{domain} -> gateway webhook")

    return True


@activity.defn
async def setup_team_roster(input: OrgBuildInput) -> bool:
    """Generate a TEAM_ROSTER string for temporal workflow injection."""
    slug = input.slug.upper().replace("-", "_")
    conn = _db()
    cur = conn.cursor()
    cur.execute(
        "SELECT agent_id, name, role FROM agent_bios WHERE agent_id LIKE %s OR agent_id = %s ORDER BY agent_id",
        (f"{input.slug}-%", input.slug))
    agents = cur.fetchall()
    conn.close()

    roster_lines = [f"TEAM ROSTER - {input.name} (use ONLY these names and titles):"]
    for agent_id, name, role in agents:
        if name:
            roster_lines.append(f"  {name} - {role} ({agent_id})")

    if input.org_type == "non-commercial":
        roster_lines.append(f"\n  NOTE: {input.name} is non-commercial. No sales, no billing.")

    roster_lines.append("\nRULES:")
    roster_lines.append("- Use YOUR name only. Never invent names.")
    roster_lines.append("- When referring to team members, use ONLY names from this roster.")

    roster = "\n".join(roster_lines)

    # Save roster
    roster_file = Path(f"/opt/ai-elevate/{input.slug}/team-roster.txt")
    roster_file.parent.mkdir(parents=True, exist_ok=True)
    roster_file.write_text(roster)
    log.info(f"Team roster written: {len(agents)} agents")
    return True


@activity.defn
async def create_org_website(input: OrgBuildInput) -> str:
    """Dispatch an engineer to create the org's website."""
    project_dir = f"/opt/ai-elevate/{input.slug}/projects/{input.slug}-website"
    Path(project_dir).mkdir(parents=True, exist_ok=True)

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch(
        "build-org-engineer",
        f"BUILD WEBSITE for: {input.name}\n"
        f"Domain: {input.domain}\n"
        f"Project dir: {project_dir}\n"
        f"Description: {input.description}\n"
        f"Type: {input.org_type}\n\n"
        f"Create a professional landing page website:\n"
        f"1. React + Vite + Tailwind CSS\n"
        f"2. Homepage with hero, services, about, contact\n"
        f"3. Responsive, dark mode support\n"
        f"4. Docker Compose for deployment\n"
        f"5. Professional color scheme appropriate for the industry\n"
        f"6. Contact form\n"
        f"7. Team section (pull from org roster)\n\n"
        f"Keep it simple - 1 page, well designed.",
        timeout=600))


@activity.defn
async def setup_dns(input: OrgBuildInput) -> bool:
    """Set up DNS records in Cloudflare for the new domain."""
    # This needs Cloudflare API access for the specific domain
    # For now, log what needs to be done
    log.info(f"DNS setup needed for {input.domain}:")
    log.info(f"  A record: {input.domain} -> 78.47.104.139")
    log.info(f"  A record: www.{input.domain} -> 78.47.104.139")
    log.info(f"  MX record: {input.domain} -> mxa.mailgun.org / mxb.mailgun.org")

    # Save DNS requirements
    dns_file = Path(f"/opt/ai-elevate/{input.slug}/dns-requirements.md")
    dns_file.parent.mkdir(parents=True, exist_ok=True)
    dns_file.write_text(
        f"# DNS Requirements for {input.domain}\n\n"
        f"## A Records\n"
        f"- `{input.domain}` -> 78.47.104.139 (proxied via Cloudflare)\n"
        f"- `www.{input.domain}` -> 78.47.104.139 (proxied)\n\n"
        f"## MX Records (for Mailgun)\n"
        f"- `{input.domain}` -> mxa.mailgun.org (priority 10)\n"
        f"- `{input.domain}` -> mxb.mailgun.org (priority 10)\n\n"
        f"## TXT Records\n"
        f"- SPF: `v=spf1 include:mailgun.org ~all`\n"
        f"- DKIM: (get from Mailgun dashboard after domain verification)\n"
    )
    return True


@activity.defn
async def setup_knowledge_graph(input: OrgBuildInput) -> bool:
    """Initialize the knowledge graph for the new org."""
    try:
        from knowledge_graph import KG
        kg = KG(input.slug)
        kg.add("organization", input.slug, {
            "name": input.name,
            "domain": input.domain,
            "type": input.org_type,
            "description": input.description,
            "created": datetime.now(timezone.utc).isoformat()[:19],
        })
        log.info(f"Knowledge graph initialized for {input.slug}")
        return True
    except Exception as e:
        log.warning(f"KG init failed: {e}")
        return False


@activity.defn
async def deploy_org(input: OrgBuildInput) -> str:
    """Deploy the org's website and set up nginx."""
    project_dir = f"/opt/ai-elevate/{input.slug}/projects/{input.slug}-website"
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch(
        "build-org-devops",
        f"DEPLOY WEBSITE for: {input.name}\n"
        f"Project dir: {project_dir}\n"
        f"Domain: {input.domain}\n\n"
        f"1. Find a free port (4102-4199 range)\n"
        f"2. docker compose up -d --build\n"
        f"3. Verify it responds\n"
        f"4. Report the URL\n"
        f"5. If build fails, diagnose and fix",
        timeout=300))


@activity.defn
async def generate_org_summary(input: OrgBuildInput) -> str:
    """Generate a summary report of the new org."""
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM agent_bios WHERE agent_id LIKE %s", (f"{input.slug}%",))
    agent_count = cur.fetchone()[0]
    conn.close()

    # Count agent dirs
    agent_dirs = len([d for d in AGENTS_BASE.iterdir() if d.name.startswith(f"{input.slug}") and d.is_dir()])

    summary = (
        f"# Organization Created: {input.name}\n\n"
        f"- **Domain:** {input.domain}\n"
        f"- **Type:** {input.org_type}\n"
        f"- **Agents:** {agent_count} registered in DB, {agent_dirs} with AGENTS.md\n"
        f"- **Description:** {input.description}\n\n"
        f"## Next Steps\n"
        f"1. Set up DNS for {input.domain} (see dns-requirements.md)\n"
        f"2. Verify Mailgun domain\n"
        f"3. Add email aliases to gateway (see email-aliases.json)\n"
        f"4. Deploy website\n"
        f"5. Run first test email\n"
    )

    summary_file = Path(f"/opt/ai-elevate/{input.slug}/ORG_SUMMARY.md")
    summary_file.write_text(summary)

    # Email to Braun
    try:
        from send_email import send_email
        send_email(
            to="braun.brelin@ai-elevate.ai",
            subject=f"New org created: {input.name} ({input.domain})",
            body=summary,
            agent_id="build-org")
    except Exception:
        pass

    return summary


# ============================================================================
# Workflow
# ============================================================================

@workflow.defn
class OrganizationBuildWorkflow:
    """Meta-workflow: takes a prompt and creates an entire AI organization."""

    @workflow.run
    async def run(self, input: OrgBuildInput) -> OrgBuildResult:
        result = OrgBuildResult()
        tl = timedelta(seconds=660)
        ts = timedelta(seconds=60)

        # Normalize slug
        if not input.slug:
            input = OrgBuildInput(**{**input.__dict__, "slug": re.sub(r'[^a-z0-9]+', '-', input.name.lower()).strip('-')})


        # 1. Architect designs the org
        await workflow.execute_activity(design_org_structure, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.actions.append("org_designed")

        # 2. Create agent files
        agent_count = await workflow.execute_activity(create_agent_files, input,
            start_to_close_timeout=ts, retry_policy=RETRY)
        result.agents_created = agent_count
        result.actions.append(f"agents_created:{agent_count}")

        # 3. Register in DB
        await workflow.execute_activity(register_agents_in_db, input,
            start_to_close_timeout=ts, retry_policy=RETRY)
        result.actions.append("db_registered")

        # 4. Email routing
        await workflow.execute_activity(setup_email_routing, input,
            start_to_close_timeout=ts, retry_policy=RETRY)
        result.actions.append("email_routing")

        # 5. Team roster
        await workflow.execute_activity(setup_team_roster, input,
            start_to_close_timeout=ts, retry_policy=RETRY)
        result.actions.append("roster_created")

        # 6. Knowledge graph
        await workflow.execute_activity(setup_knowledge_graph, input,
            start_to_close_timeout=ts, retry_policy=RETRY)
        result.actions.append("kg_initialized")

        # 7. DNS requirements
        await workflow.execute_activity(setup_dns, input,
            start_to_close_timeout=ts, retry_policy=RETRY)
        result.actions.append("dns_documented")

        # 8. Create website
        await workflow.execute_activity(create_org_website, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.actions.append("website_created")

        # 9. Deploy
        deploy_result = await workflow.execute_activity(deploy_org, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.website_url = deploy_result
        result.actions.append(f"deployed:{deploy_result[:50]}")

        # 10. Summary
        await workflow.execute_activity(generate_org_summary, input,
            start_to_close_timeout=ts, retry_policy=RETRY)
        result.actions.append("summary_sent")

        result.status = "created"
        return result


# ============================================================================
# Client
# ============================================================================

async def build_organization(name, domain="", description="", org_type="commercial", slug=""):
    from temporalio.client import Client
    client = await Client.connect("localhost:7233")
    s = slug or re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    d = domain or f"{s}.ai"
    inp = OrgBuildInput(name=name, slug=s, domain=d, description=description, org_type=org_type)
    handle = await client.start_workflow(
        OrganizationBuildWorkflow.run, inp,
        id=f"build-org-{s}-{int(time.time())}",
        task_queue="org-builder",
        execution_timeout=timedelta(hours=2))
    return {"workflow_id": handle.id, "status": "started"}
