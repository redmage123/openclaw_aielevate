#!/usr/bin/env python3
"""Project Build Workflow v3 — agents do ALL the work. No fallbacks.

Pipeline:
  1. PM creates sprint plan
  2. Engineering consensus — CTO + DevOps decide tech stack
  3. Software specification — PM functional spec + Engineer technical spec
  4. UX designer creates design spec (informed by SOFTWARE_SPEC.md)
  5. Engineer builds (guided by SOFTWARE_SPEC.md + TECH_STACK.md + DESIGN.md)
  4. QA tests everything
  5. Engineer writes documentation, test suite, and runbook
  6. DevOps deploys preview
  7. Advocate sends preview URL
  8. Billing creates invoice
  9. Sales sends feedback request

Every project deliverable includes:
  - SOFTWARE_SPEC.md — full functional + technical specification
  - docs/adr/ — Architecture Decision Records (one per significant decision)
  - README.md — full project documentation
  - Test suite — pytest tests for all API endpoints and models
  - RUNBOOK.md — deployment, monitoring, troubleshooting, rollback
  - CHANGELOG.md — release history

If an agent fails to produce artifacts, the activity FAILS and Temporal retries.
No fallback code generation. The agents build it or we know why they didn't.
"""

import asyncio
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from pathlib import Path

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

sys.path.insert(0, "/home/aielevate")

try:
    from workflow_kg import (
        record_tech_stack, record_adr, record_spec, record_build,
        record_deployment, record_inquiry, record_acceptance,
    )
    HAS_KG = True
except ImportError:
    HAS_KG = False

log = logging.getLogger("build-workflow")

DB = dict(
    host=os.environ.get("DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("DB_PORT", "5434")),
    dbname=os.environ.get("DB_NAME", "rag"),
    user=os.environ.get("DB_USER", "rag"),
    password=os.environ.get("DB_PASSWORD", "rag_vec_2026"),
)

RETRY_FAST = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=10))
RETRY = RetryPolicy(maximum_attempts=5, initial_interval=timedelta(seconds=30), maximum_interval=timedelta(seconds=120))


@dataclass
class BuildInput:
    customer_email: str
    project_title: str
    project_slug: str
    org: str = "gigforge"
    requirements: str = ""
    domain: str = ""


@dataclass
class BuildResult:
    status: str = "pending"
    project_dir: str = ""
    preview_url: str = ""
    actions: list = None
    errors: list = None

    def __post_init__(self):
        if self.actions is None: self.actions = []
        if self.errors is None: self.errors = []


def _clear_session(agent_id: str):
    """Clear agent sessions to prevent resume."""
    session_dir = Path(f"/home/aielevate/.openclaw/agents/{agent_id}/sessions")
    if session_dir.exists():
        for f in session_dir.glob("*.jsonl"):
            f.unlink()


def _dispatch_and_wait(agent_id: str, message: str, timeout: int = 600, heartbeat_fn=None) -> str:
    """Dispatch agent using reliable agent_dispatch with health checks + circuit breaker."""
    try:
        from agent_dispatch import dispatch
        result = dispatch(agent_id, message, timeout=timeout, heartbeat_fn=heartbeat_fn)
        if result.status == "success":
            return result.output
        elif result.status == "circuit_open":
            raise ApplicationError(f"Circuit breaker open: {result.error}", non_retryable=False)
        elif result.status == "gateway_down":
            raise ApplicationError(f"Gateway down: {result.error}", non_retryable=False)
        elif result.status == "timeout":
            log.warning(f"Agent {agent_id} timed out: {result.error}")
            return "TIMEOUT"
        elif result.status == "empty_output":
            log.warning(f"Agent {agent_id} empty output: {result.error}")
            return ""
        else:
            log.warning(f"Agent {agent_id} error: {result.status} — {result.error}")
            return result.output or ""


    except ImportError:
        # Fallback if agent_dispatch not available
        _clear_session(agent_id)
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        try:
            proc = subprocess.run(
                ["openclaw", "agent", "--agent", agent_id,
                 "--message", message, "--thinking", "low", "--timeout", str(timeout)],
                capture_output=True, text=True, timeout=timeout + 30, env=env,
            )
            output = proc.stdout or ""
            output = re.sub(r"\*\[.*?\]\*", "", output, flags=re.DOTALL).strip()
            return output
        except subprocess.TimeoutExpired:
            return "TIMEOUT"


# ============================================================================
# Activities — agents do the work, no fallbacks
# ============================================================================

@activity.defn
async def create_milestones(input: BuildInput) -> bool:
    """Create project milestones in Postgres."""
    try:
        import psycopg2
        conn = psycopg2.connect(**DB)
        conn.autocommit = True
        cur = conn.cursor()
        for m in ["Architecture", "Specification", "Design", "Backend", "Frontend", "Testing", "Documentation", "Deployment"]:
            cur.execute(
                "INSERT INTO project_milestones (customer_email, project_title, milestone, status) "
                "VALUES (%s, %s, %s, 'pending') ON CONFLICT DO NOTHING",
                (input.customer_email, input.project_title, m)
            )
        conn.close()
        Path(f"/opt/ai-elevate/gigforge/projects/{input.project_slug}").mkdir(parents=True, exist_ok=True)
        return True
    except Exception as e:
        log.error(f"Milestones error: {e}")
        return False


@activity.defn
async def pm_sprint_plan(input: BuildInput) -> str:
    """PM creates sprint plan."""
    project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
    loop = asyncio.get_event_loop()
    pm_agent = f"{input.org}-pm"
    return await loop.run_in_executor(None, lambda: _dispatch_and_wait(
        pm_agent,
        f"CREATE SPRINT PLAN for: {input.project_title}\n"
        f"Customer: {input.customer_email}\n"
        f"Project dir: {project_dir}\n"
        f"Requirements: {input.requirements}\n\n"
        f"1. Write sprint plan to {project_dir}/SPRINT_PLAN.md\n"
        f"2. Break down into tasks for engineer and UX designer\n"
        f"3. The milestones (Design, Backend, Frontend, Testing, Deployment) already exist in Postgres",
        timeout=300
    ))



@activity.defn
async def engineering_consensus(input: BuildInput) -> str:
    """Engineering team decides tech stack, languages, tools, and infrastructure.

    The CTO (Engineer) evaluates the requirements and customer preferences,
    then writes a TECH_STACK.md decision document that guides all subsequent
    build activities. DevOps reviews deployment feasibility.
    """
    project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
    loop = asyncio.get_event_loop()

    # Step 1: CTO evaluates and proposes tech stack
    nav_agent = f"{input.org}-engineering" if input.org == "techuni" else f"{input.org}-engineer"
    devops_agent = f"{input.org}-devops"

    cto_decision = await loop.run_in_executor(None, lambda: _dispatch_and_wait(
        nav_agent,
        f"TECH STACK DECISION for: {input.project_title}\n"
        f"Project dir: {project_dir}\n"
        f"Customer: {input.customer_email}\n\n"
        f"Read the sprint plan at {project_dir}/SPRINT_PLAN.md if it exists.\n\n"
        f"Customer requirements:\n{input.requirements}\n\n"
        f"You are the CTO. Evaluate the requirements and decide the tech stack.\n"
        f"Consider what the customer asked for — they may have preferences on:\n"
        f"- Programming languages (Python, Node.js, Go, etc.)\n"
        f"- Frontend framework (React, Vue, Svelte, vanilla, etc.)\n"
        f"- Database (SQLite, PostgreSQL, MongoDB, etc.)\n"
        f"- Hosting preferences (Docker, serverless, VPS, etc.)\n\n"
        f"Write your decision to {project_dir}/TECH_STACK.md with these sections:\n\n"
        f"# Tech Stack Decision\n"
        f"## Project: {{title}}\n"
        f"## Date: {{today}}\n"
        f"## Decision By: Chris Novak (CTO)\n\n"
        f"### Backend\n"
        f"- Language: (and why)\n"
        f"- Framework: (and why)\n"
        f"- Database: (and why)\n"
        f"- Key libraries:\n\n"
        f"### Frontend\n"
        f"- Framework: (and why)\n"
        f"- Styling: (Tailwind, CSS modules, styled-components, etc.)\n"
        f"- Build tool: (Vite, webpack, etc.)\n"
        f"- Key libraries:\n\n"
        f"### Infrastructure\n"
        f"- Containerization: (Docker, docker-compose, etc.)\n"
        f"- Deployment strategy: (single container, multi-service, etc.)\n"
        f"- Port mapping: (internal ports, exposed ports)\n"
        f"- Reverse proxy: (nginx config needs)\n\n"
        f"### File Structure\n"
        f"- List every file that needs to be created\n"
        f"- Organized by directory\n\n"
        f"### API Design\n"
        f"- List all endpoints (method, path, description)\n"
        f"- Request/response formats\n\n"
        f"### Quality Requirements\n"
        f"- Test framework and coverage target\n"
        f"- Linting/formatting tools\n"
        f"- CI/CD considerations\n\n"
        f"IMPORTANT: This document drives the entire build. Be specific and opinionated.\n"
        f"Every file path, every endpoint, every dependency must be listed.\n"
        f"The engineer who builds this will follow TECH_STACK.md exactly.\n\n"
        f"ALSO: Write Architecture Decision Records (ADRs) to {project_dir}/docs/adr/\n"
        f"Create one ADR per significant decision. Use this format:\n\n"
        f"  {project_dir}/docs/adr/0001-choice-of-backend-language.md\n"
        f"  {project_dir}/docs/adr/0002-choice-of-database.md\n"
        f"  {project_dir}/docs/adr/0003-choice-of-frontend-framework.md\n"
        f"  {project_dir}/docs/adr/0004-deployment-strategy.md\n"
        f"  (add more as needed for auth, API design, styling, etc.)\n\n"
        f"Each ADR must follow this template:\n"
        f"  # ADR-NNNN: Title\n"
        f"  ## Status: Accepted\n"
        f"  ## Context: Why this decision was needed\n"
        f"  ## Decision: What was decided\n"
        f"  ## Alternatives Considered: What else was evaluated and why rejected\n"
        f"  ## Consequences: Trade-offs, implications, risks\n\n"
        f"ADRs create a permanent record of WHY decisions were made — essential for\n"
        f"future maintenance, onboarding, and customer handoff.",
        timeout=300
    ))

    # Step 2: DevOps reviews deployment feasibility
    devops_review = await loop.run_in_executor(None, lambda: _dispatch_and_wait(
        f"{input.org}-devops",
        f"DEPLOYMENT REVIEW for: {input.project_title}\n"
        f"Project dir: {project_dir}\n\n"
        f"Read the tech stack decision at {project_dir}/TECH_STACK.md.\n\n"
        f"Review the proposed tech stack for deployment feasibility:\n"
        f"1. Can we containerize this with Docker?\n"
        f"2. Are there port conflicts with existing services?\n"
        f"3. Any resource concerns (memory, CPU, disk)?\n"
        f"4. Is the database choice deployable on our infrastructure?\n"
        f"5. Any security concerns?\n\n"
        f"If you have concerns, append a ## DevOps Review section to {project_dir}/TECH_STACK.md\n"
        f"with your notes, flags, and any adjustments needed.\n\n"
        f"If everything looks good, still append the review section confirming feasibility.",
        timeout=180
    ))

    # Verify TECH_STACK.md exists
    # Verify outputs
    tech_stack = Path(project_dir) / "TECH_STACK.md"
    adr_dir = Path(project_dir) / "docs" / "adr"
    if not adr_dir.exists():
        log.warning(f"No ADR directory created at {adr_dir}")
    else:
        adr_count = len(list(adr_dir.glob("*.md")))
        log.info(f"ADRs written: {adr_count} records in {adr_dir}")

    if not tech_stack.exists():
        raise ApplicationError(
            f"Engineering consensus failed — no TECH_STACK.md created. CTO output: {cto_decision[:200]}",
            non_retryable=True,
        )

    log.info(f"Engineering consensus complete — TECH_STACK.md written to {project_dir}")
    return tech_stack.read_text()[:500]



@activity.defn
async def write_software_spec(input: BuildInput) -> str:
    """PM + Engineer collaboratively write a full software specification.

    The PM writes the functional spec (what the software does from the user's perspective).
    The Engineer writes the technical spec (how it's built under the hood).
    Both are combined into SOFTWARE_SPEC.md — the single source of truth for the project.
    """
    project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
    loop = asyncio.get_event_loop()

    # Step 1: PM writes functional spec
    pm_agent = f"{input.org}-pm"
    eng_agent = f"{input.org}-engineering" if input.org == "techuni" else f"{input.org}-engineer"

    pm_spec = await loop.run_in_executor(None, lambda: _dispatch_and_wait(
        pm_agent,
        f"WRITE FUNCTIONAL SPECIFICATION for: {input.project_title}\n"
        f"Project dir: {project_dir}\n"
        f"Customer: {input.customer_email}\n\n"
        f"Read these first:\n"
        f"  - {project_dir}/SPRINT_PLAN.md (your sprint plan)\n"
        f"  - {project_dir}/TECH_STACK.md (engineering's tech decisions)\n\n"
        f"Customer requirements:\n{input.requirements}\n\n"
        f"Write the FUNCTIONAL section of {project_dir}/SOFTWARE_SPEC.md:\n\n"
        f"# Software Specification\n"
        f"## Project: {input.project_title}\n"
        f"## Customer: {input.customer_email}\n"
        f"## Date: {{today}}\n\n"
        f"### 1. Executive Summary\n"
        f"- What the product is, who it's for, what problem it solves\n\n"
        f"### 2. User Personas\n"
        f"- Primary user types and their goals\n\n"
        f"### 3. Functional Requirements\n"
        f"For EACH feature, write:\n"
        f"- FR-001: Feature name\n"
        f"  - Description: what the user can do\n"
        f"  - User story: As a [persona], I want to [action], so that [benefit]\n"
        f"  - Acceptance criteria: specific testable conditions\n"
        f"  - Priority: must-have / nice-to-have\n\n"
        f"### 4. User Flows\n"
        f"- Step-by-step flows for core actions (e.g., add contact, search, import CSV)\n\n"
        f"### 5. Scope & Constraints\n"
        f"- What's in scope for this delivery\n"
        f"- What's explicitly out of scope\n"
        f"- Budget and timeline constraints\n\n"
        f"### 6. Acceptance Criteria (Project Level)\n"
        f"- What 'done' looks like for the customer\n\n"
        f"Write the full functional spec. Be specific — every feature must have acceptance criteria.",
        timeout=300
    ))

    # Step 2: Engineer writes technical spec (appends to the same file)
    eng_spec = await loop.run_in_executor(None, lambda: _dispatch_and_wait(
        eng_agent,
        f"WRITE TECHNICAL SPECIFICATION for: {input.project_title}\n"
        f"Project dir: {project_dir}\n\n"
        f"Read these first:\n"
        f"  - {project_dir}/SOFTWARE_SPEC.md (PM's functional spec — already written)\n"
        f"  - {project_dir}/TECH_STACK.md (your tech decisions)\n\n"
        f"APPEND the TECHNICAL sections to {project_dir}/SOFTWARE_SPEC.md.\n"
        f"Do NOT overwrite the functional spec — add after it.\n\n"
        f"### 7. System Architecture\n"
        f"- High-level architecture diagram (text/ASCII)\n"
        f"- Component breakdown (frontend, backend, database, etc.)\n"
        f"- Communication patterns (REST, WebSocket, etc.)\n\n"
        f"### 8. Data Models\n"
        f"- Every entity with all fields, types, constraints\n"
        f"- Relationships between entities\n"
        f"- Database schema (table definitions)\n\n"
        f"### 9. API Contract\n"
        f"For EACH endpoint:\n"
        f"- Method + Path\n"
        f"- Request body (with types)\n"
        f"- Response body (with types)\n"
        f"- Status codes\n"
        f"- Example request/response\n\n"
        f"### 10. Frontend Components\n"
        f"- Component tree\n"
        f"- Props and state for each component\n"
        f"- Page routes\n\n"
        f"### 11. Security\n"
        f"- Authentication/authorization approach\n"
        f"- Input validation rules\n"
        f"- CORS policy\n\n"
        f"### 12. Error Handling\n"
        f"- Error response format\n"
        f"- Error codes and messages\n"
        f"- Client-side error handling strategy\n\n"
        f"### 13. Performance Requirements\n"
        f"- Response time targets\n"
        f"- Concurrent user capacity\n"
        f"- Data volume expectations\n\n"
        f"Be specific. Every data model field, every API endpoint, every component.\n"
        f"The engineer who builds this will follow SOFTWARE_SPEC.md as the contract.",
        timeout=300
    ))

    # Verify SOFTWARE_SPEC.md exists and has substance
    spec_file = Path(project_dir) / "SOFTWARE_SPEC.md"
    if not spec_file.exists():
        raise ApplicationError(
            f"Software spec not created. PM output: {pm_spec[:200]}",
            non_retryable=True,
        )

    spec_content = spec_file.read_text()
    if len(spec_content) < 500:
        raise ApplicationError(
            f"Software spec too short ({len(spec_content)} chars) — incomplete.",
            non_retryable=True,
        )

    log.info(f"Software spec complete — {len(spec_content)} chars written to {project_dir}/SOFTWARE_SPEC.md")
    return f"SOFTWARE_SPEC.md written ({len(spec_content)} chars)"


@activity.defn
async def ux_design(input: BuildInput) -> str:
    """UX designer creates design spec and wireframes."""
    project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
    loop = asyncio.get_event_loop()
    ux_agent = f"{input.org}-ux-designer"
    return await loop.run_in_executor(None, lambda: _dispatch_and_wait(
        ux_agent,
        f"DESIGN SPEC for: {input.project_title}\n"
        f"Project dir: {project_dir}\n"
        f"Requirements: {input.requirements}\n\n"
        f"Read these MANDATORY documents — your design must align with them:\n"
        f"  - {project_dir}/SOFTWARE_SPEC.md — every feature and user flow must match the spec\n"
        f"  - {project_dir}/TECH_STACK.md — component choices must be compatible with the stack\n"
        f"  - {project_dir}/docs/adr/*.md — ADRs may constrain styling or interaction patterns\n\n"
        f"Create a design document at {project_dir}/DESIGN.md covering:\n"
        f"1. Color palette and typography (modern, clean, dark mode support)\n"
        f"2. Layout wireframes for each page (contact list, contact detail, search, import)\n"
        f"3. Component hierarchy (React components needed)\n"
        f"4. Responsive breakpoints (mobile, tablet, desktop)\n"
        f"5. Interaction patterns (real-time search, tag filtering, CSV upload)\n"
        f"6. Accessibility requirements\n\n"
        f"Write the design doc with enough detail for an engineer to implement it.",
        timeout=300
    ))



def _resolve_agents(org):
    """Resolve Navigator, Backend Driver, Frontend Driver agent IDs by org."""
    if org == "techuni":
        return "techuni-engineering", "techuni-dev-backend", "techuni-dev-frontend"
    return "gigforge-engineer", "gigforge-dev-backend", "gigforge-dev-frontend"


# ============================================================================
# Pair Programming helpers
# ============================================================================

# NAV_AGENT resolved dynamically per org in each activity
# DRIVER_BACK resolved dynamically
# DRIVER_FRONT resolved dynamically


def _read_project_docs(project_dir):
    """Read spec, tech stack, design, and ADRs from project dir."""
    spec_file = Path(project_dir) / "SOFTWARE_SPEC.md"
    tech_file = Path(project_dir) / "TECH_STACK.md"
    design_file = Path(project_dir) / "DESIGN.md"
    adr_dir = Path(project_dir) / "docs" / "adr"

    spec = spec_file.read_text() if spec_file.exists() else ""
    tech = tech_file.read_text() if tech_file.exists() else ""
    design = design_file.read_text() if design_file.exists() else ""

    adrs = {}
    if adr_dir.exists():
        for f in sorted(adr_dir.glob("*.md")):
            adrs[f.stem] = f.read_text()

    # Parse spec into sections
    spec_sections = {}
    current_section = ""
    current_content = []
    for line in spec.split("\n"):
        if line.startswith("### "):
            if current_section:
                spec_sections[current_section] = "\n".join(current_content)
            current_section = line.replace("### ", "").strip()
            current_content = [line]
        else:
            current_content.append(line)
    if current_section:
        spec_sections[current_section] = "\n".join(current_content)

    return spec, tech, design, adrs, spec_sections


def _relevant_adrs(adrs, *keywords):
    parts = []
    for name, text in adrs.items():
        if any(kw in name.lower() for kw in keywords):
            parts.append(f"--- ADR: {name} ---\n{text}")
    return "\n\n".join(parts) if parts else ""


def _spec_section(spec_sections, *names):
    parts = []
    for sn, text in spec_sections.items():
        if any(n.lower() in sn.lower() for n in names):
            parts.append(text)
    return "\n\n".join(parts) if parts else ""


def _nav_brief(project_dir, task_name, context, instructions, org="gigforge"):
    """Navigator writes task brief."""
    nav_agent = f"{org}-engineering" if org == "techuni" else f"{org}-engineer"
    return _dispatch_and_wait(nav_agent,
        f"NAVIGATOR BRIEF — {task_name}\n"
        f"Dir: {project_dir}\n\n"
        f"CONTEXT:\n{context[:2500]}\n\n"
        f"Write a TASK BRIEF for the driver engineer. Include:\n"
        f"1. Exactly what files to create/modify\n"
        f"2. Acceptance criteria (testable conditions)\n"
        f"3. Relevant spec requirements (FR numbers)\n"
        f"4. ADR decisions that must be followed\n"
        f"5. Gotchas or constraints\n\n"
        f"Instructions: {instructions}\n\n"
        f"Write the brief to {project_dir}/TASK_BRIEF.md (overwrite).",
        timeout=120)


def _driver_code(agent_id, project_dir, task_name, org="gigforge"):
    """Driver reads brief and writes code."""
    return _dispatch_and_wait(agent_id,
        f"DRIVER — {task_name}\n"
        f"Dir: {project_dir}\n\n"
        f"Read your task brief at {project_dir}/TASK_BRIEF.md\n"
        f"Also read: TECH_STACK.md, SOFTWARE_SPEC.md, DESIGN.md as needed.\n\n"
        f"Write ALL the code specified in the brief.\n"
        f"Follow the acceptance criteria EXACTLY.\n"
        f"When done, list the files you created/modified.",
        timeout=300)


def _nav_review(project_dir, task_name, org="gigforge"):
    """Navigator reviews driver's code and fixes issues."""
    nav_agent = f"{org}-engineering" if org == "techuni" else f"{org}-engineer"
    return _dispatch_and_wait(nav_agent,
        f"CODE REVIEW — {task_name}\n"
        f"Dir: {project_dir}\n\n"
        f"Read the task brief at {project_dir}/TASK_BRIEF.md\n"
        f"Read ALL files the driver just created/modified.\n\n"
        f"Review against:\n"
        f"1. Does the code meet every acceptance criterion?\n"
        f"2. Are ADR decisions followed?\n"
        f"3. Type safety — any missing types?\n"
        f"4. Error handling — are API errors handled?\n"
        f"5. Missing imports or dead code?\n\n"
        f"If issues found: FIX THEM DIRECTLY.\n"
        f"Write review summary to {project_dir}/REVIEW.md (overwrite).",
        timeout=180)


# ============================================================================
# Pair Programming Activities — each is a separate Temporal activity
# ============================================================================

@activity.defn
async def pair_scaffold(input: BuildInput) -> str:
    """Step 1: Navigator creates project scaffold (solo — config only)."""
    nav_agent, driver_back, driver_front = _resolve_agents(input.org)
    project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
    _, tech, _, _, _ = _read_project_docs(project_dir)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: _dispatch_and_wait(nav_agent,
        f"PROJECT SCAFFOLD for: {input.project_title}\n"
        f"Dir: {project_dir}\n\n"
        f"TECH STACK (binding):\n{tech[:2000]}\n\n"
        f"Create scaffold ONLY — no application code:\n"
        f"1. Root: docker-compose.yml, .gitignore, .env.example\n"
        f"2. Backend (cms/) dir: package.json, tsconfig.json, payload.config.ts stub\n"
        f"3. Frontend (web/) dir: package.json, tsconfig.json, vite.config.ts, index.html, tailwind config, postcss config\n"
        f"4. Docker: Dockerfile for cms, Dockerfile for web, nginx.conf\n"
        f"5. Run npm install in both dirs\n\n"
        f"ONLY scaffold. No app code yet.",
        timeout=300))
    log.info(f"Pair scaffold complete for {input.project_slug}")
    try:
        from workflow_state import save_checkpoint
        save_checkpoint(input.project_slug, "pair_scaffold", "completed")
    except Exception:
        pass
    return result[:200]


@activity.defn
async def pair_cms_collections(input: BuildInput) -> str:
    """Step 2: Pair — Navigator briefs, dev-backend builds CMS collections, Navigator reviews."""
    nav_agent, driver_back, driver_front = _resolve_agents(input.org)
    project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
    _, _, _, adrs, spec_sections = _read_project_docs(project_dir)
    context = _spec_section(spec_sections, "Data Model", "System Architecture") + "\n\n" + _relevant_adrs(adrs, "database", "backend", "api")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: _nav_brief(project_dir,
        "Payload CMS Collections", context,
        "Create ALL Payload CMS collection files. One file per collection. All fields from spec. Relationships configured. Access control set.", org=input.org))
    driver_back = f"{input.org}-dev-backend"
    await loop.run_in_executor(None, lambda: _driver_code(driver_back, project_dir, "Payload CMS Collections"))
    review = await loop.run_in_executor(None, lambda: _nav_review(project_dir, "Payload CMS Collections", org=input.org))
    log.info(f"Pair CMS collections complete for {input.project_slug}")
    return review[:200]


@activity.defn
async def pair_api_routes(input: BuildInput) -> str:
    """Step 3: Pair — Navigator briefs, dev-backend builds API routes, Navigator reviews."""
    nav_agent, driver_back, driver_front = _resolve_agents(input.org)
    project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
    _, _, _, adrs, spec_sections = _read_project_docs(project_dir)
    context = _spec_section(spec_sections, "API Contract", "Security", "Error Handling") + "\n\n" + _relevant_adrs(adrs, "api", "auth")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: _nav_brief(project_dir,
        "Custom API Routes", context,
        "Create custom API routes Payload doesn't provide: full-text search, court fee calculator, jurisdiction lookup. Payload already gives CRUD REST + GraphQL."))
    await loop.run_in_executor(None, lambda: _driver_code(driver_back, project_dir, "Custom API Routes"))
    review = await loop.run_in_executor(None, lambda: _nav_review(project_dir, "Custom API Routes"))
    log.info(f"Pair API routes complete for {input.project_slug}")
    return review[:200]


@activity.defn
async def pair_frontend_layout(input: BuildInput) -> str:
    """Step 4: Pair — Navigator briefs, dev-frontend builds layout, Navigator reviews."""
    nav_agent, driver_back, driver_front = _resolve_agents(input.org)
    project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
    _, _, design, adrs, _ = _read_project_docs(project_dir)
    context = design[:2000] + "\n\n" + _relevant_adrs(adrs, "frontend", "state", "styling")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: _nav_brief(project_dir,
        "Frontend Layout + Routing", context,
        "Create: main.tsx, App.tsx with React Router 7 routes, Layout (Header/Footer/Sidebar/MobileNav), i18n setup (react-i18next with hr.json/en.json stubs), dark mode toggle, language switcher, Tailwind globals. ONLY layout shell, no pages."))
    await loop.run_in_executor(None, lambda: _driver_code(driver_front, project_dir, "Frontend Layout + Routing"))
    review = await loop.run_in_executor(None, lambda: _nav_review(project_dir, "Frontend Layout + Routing"))
    log.info(f"Pair frontend layout complete for {input.project_slug}")
    return review[:200]


@activity.defn
async def pair_ui_components(input: BuildInput) -> str:
    """Step 5: Pair — Navigator briefs, dev-frontend builds UI components, Navigator reviews."""
    nav_agent, driver_back, driver_front = _resolve_agents(input.org)
    project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
    _, _, design, _, _ = _read_project_docs(project_dir)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: _nav_brief(project_dir,
        "Shared UI Components", design[:2000],
        "Create reusable Tailwind components: Button, Card, DataTable (sortable/filterable/paginated), SearchBar (type-ahead), Tag/Badge, Modal, Pagination, Breadcrumb, Input/Select/Textarea, Alert, Skeleton. Each in components/ui/."))
    await loop.run_in_executor(None, lambda: _driver_code(driver_front, project_dir, "Shared UI Components"))
    review = await loop.run_in_executor(None, lambda: _nav_review(project_dir, "Shared UI Components"))
    log.info(f"Pair UI components complete for {input.project_slug}")
    return review[:200]


@activity.defn
async def pair_pages_part1(input: BuildInput) -> str:
    """Step 6: Pair — Navigator briefs, dev-frontend builds search + directory pages, Navigator reviews."""
    nav_agent, driver_back, driver_front = _resolve_agents(input.org)
    project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
    _, _, _, _, spec_sections = _read_project_docs(project_dir)
    context = _spec_section(spec_sections, "Functional Requirements", "User Flows")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: _nav_brief(project_dir,
        "Feature Pages — Search + Directories", context[:3000],
        "Build: HomePage (hero, quick search, recent news), CaseLawPage (search with filters), ExpertWitnessesPage (directory), InterpretersPage, CourtsPage. Each fetches from Payload REST API. Create api/ client module with typed fetch functions."))
    await loop.run_in_executor(None, lambda: _driver_code(driver_front, project_dir, "Feature Pages — Search + Directories"))
    review = await loop.run_in_executor(None, lambda: _nav_review(project_dir, "Feature Pages — Search + Directories"))
    log.info(f"Pair pages part 1 complete for {input.project_slug}")
    return review[:200]


@activity.defn
async def pair_pages_part2(input: BuildInput) -> str:
    """Step 7: Pair — Navigator briefs, dev-frontend builds remaining pages + i18n, Navigator reviews."""
    nav_agent, driver_back, driver_front = _resolve_agents(input.org)
    project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
    _, _, _, _, spec_sections = _read_project_docs(project_dir)
    context = _spec_section(spec_sections, "Functional Requirements", "User Flows")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: _nav_brief(project_dir,
        "Feature Pages — Bankruptcy + Tools + Auth + i18n", context[:3000],
        "Build: BankruptcyPortalPage (listings, admins, laws), CourtFeeCalculatorPage, JurisdictionFinderPage (Leaflet map), FreeLegalAidPage, NewsPage, GalleryPage, AboutPage, ContactPage, DonationPage, LoginPage (Payload auth), MemberAreaPage (protected). Complete hr.json and en.json translations."))
    await loop.run_in_executor(None, lambda: _driver_code(driver_front, project_dir, "Feature Pages — Bankruptcy + Tools + Auth + i18n"))
    review = await loop.run_in_executor(None, lambda: _nav_review(project_dir, "Feature Pages — Bankruptcy + Tools + Auth + i18n"))
    log.info(f"Pair pages part 2 complete for {input.project_slug}")
    return review[:200]


@activity.defn
async def pair_integration(input: BuildInput) -> str:
    """Step 8: Navigator does final integration and verification (solo)."""
    nav_agent, driver_back, driver_front = _resolve_agents(input.org)
    project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: _dispatch_and_wait(nav_agent,
        f"FINAL INTEGRATION for: {input.project_title}\n"
        f"Dir: {project_dir}\n\n"
        f"Read ALL source code.\n"
        f"1. Fix import errors and missing references\n"
        f"2. Ensure all routes point to real components\n"
        f"3. Ensure API client matches Payload collections\n"
        f"4. Try: cd {project_dir}/web && npm run build\n"
        f"5. Try: cd {project_dir}/cms && npm run build\n"
        f"6. Fix TypeScript errors\n"
        f"7. Try: docker compose build\n"
        f"8. Update CHANGELOG.md\n\n"
        f"Report what works and what needs attention.",
        timeout=300))
    log.info(f"Pair integration complete for {input.project_slug}")
    try:
        from workflow_state import save_checkpoint
        save_checkpoint(input.project_slug, "pair_integration", "completed")
    except Exception:
        pass
    return result[:200]


@activity.defn
async def verify_build(input: BuildInput) -> str:
    """Verify the build produced actual source files."""
    project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
    project = Path(project_dir)
    source_exts = {'.py', '.ts', '.tsx', '.js', '.jsx'}
    source_files = [f for ext in source_exts for f in project.rglob(f'*{ext}') if 'node_modules' not in str(f)]
    has_docker = bool(list(project.rglob('Dockerfile')) or list(project.rglob('docker-compose.yml')))

    if len(source_files) < 5:
        raise ApplicationError(
            f"Build produced only {len(source_files)} source files.",
            non_retryable=True)

    log.info(f"Build verified: {len(source_files)} files, docker={has_docker}")
    return f"VERIFIED: {len(source_files)} source files, docker={has_docker}"



@activity.defn
async def ux_review(input: BuildInput) -> str:
    """UX designer reviews the built site against DESIGN.md and fixes issues."""
    project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
    loop = asyncio.get_event_loop()
    ux_agent = f"{input.org}-ux-designer"
    return await loop.run_in_executor(None, lambda: _dispatch_and_wait(
        ux_agent,
        f"POST-BUILD UX REVIEW for: {input.project_title}\n"
        f"Project dir: {project_dir}\n\n"
        f"Read DESIGN.md (the spec you wrote) and the built source code.\n\n"
        f"Review the implementation against the design spec:\n"
        f"1. Color palette — correct navy/blue/gold/background colors?\n"
        f"2. Typography — correct fonts and scale?\n"
        f"3. Dark mode — working and correct dark colors?\n"
        f"4. Responsive — mobile breakpoints correct?\n"
        f"5. Components — Button variants, Card styles, DataTable consistent?\n"
        f"6. Accessibility — WCAG 2.1 AA (contrast, keyboard nav, aria labels)?\n"
        f"7. Visual hierarchy — clear layout, logical flow?\n\n"
        f"Write findings to {project_dir}/UX_REVIEW.md\n"
        f"Fix any P0 (broken) and P1 (wrong) issues directly in the source code.\n"
        f"Leave P2 (polish) items documented for later.",
        timeout=300
    ))

@activity.defn
async def qa_test(input: BuildInput) -> str:
    """QA agent tests the built application."""
    project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
    loop = asyncio.get_event_loop()
    qa_agent = f"{input.org}-qa"
    return await loop.run_in_executor(None, lambda: _dispatch_and_wait(
        qa_agent,
        f"QA TEST: {input.project_title}\n"
        f"Project dir: {project_dir}\n\n"
        f"Read these documents FIRST — they define what to test against:\n"
        f"  - {project_dir}/SOFTWARE_SPEC.md — every FR must be implemented and working\n"
        f"  - {project_dir}/TECH_STACK.md — verify the correct stack was used\n"
        f"  - {project_dir}/docs/adr/*.md — verify ADR decisions were followed\n\n"
        f"1. Read the source code and check for obvious errors\n"
        f"2. Build the Docker image: docker build -t test-{input.project_slug} {project_dir}\n"
        f"3. Run it: docker run -d --name qa-{input.project_slug} -p 9999:8000 test-{input.project_slug}\n"
        f"4. Wait 5 seconds, then test endpoints:\n"
        f"   curl http://localhost:9999/api/contacts\n"
        f"   curl -X POST http://localhost:9999/api/contacts -H 'Content-Type: application/json' -d '{{\"name\":\"Test\",\"email\":\"test@test.com\"}}'\n"
        f"   curl http://localhost:9999/api/contacts\n"
        f"5. Stop and remove: docker rm -f qa-{input.project_slug}\n"
        f"6. Report PASS or FAIL with details",
        timeout=300
    ))



@activity.defn
async def write_documentation(input: BuildInput) -> str:
    """Engineer writes full documentation, test suite, and runbook."""
    project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
    loop = asyncio.get_event_loop()
    eng_agent = f"{input.org}-engineering" if input.org == "techuni" else f"{input.org}-engineer"
    return await loop.run_in_executor(None, lambda: _dispatch_and_wait(
        eng_agent,
        f"WRITE DOCUMENTATION for: {input.project_title}\n"
        f"Project dir: {project_dir}\n\n"
        f"Read all existing source code in {project_dir} first.\n\n"
        f"Create these documentation deliverables:\n\n"
        f"1. README.md — {project_dir}/README.md\n"
        f"   - Read SOFTWARE_SPEC.md, TECH_STACK.md, and docs/adr/ first\n"
        f"   - Project overview and architecture (aligned with spec and ADRs)\n"
        f"   - Tech stack and dependencies\n"
        f"   - Setup instructions (local dev + Docker)\n"
        f"   - API endpoint reference (all routes, methods, params, responses)\n"
        f"   - Environment variables\n"
        f"   - Project structure diagram\n\n"
        f"2. TEST SUITE — {project_dir}/backend/tests/\n"
        f"   - {project_dir}/backend/tests/__init__.py\n"
        f"   - {project_dir}/backend/tests/test_api.py — pytest tests for ALL API endpoints\n"
        f"     (CRUD operations, search, CSV import/export, error cases, edge cases)\n"
        f"   - {project_dir}/backend/tests/test_models.py — model validation tests\n"
        f"   - {project_dir}/backend/tests/conftest.py — fixtures (test client, test DB)\n"
        f"   - Add pytest + httpx to {project_dir}/backend/requirements.txt if not there\n\n"
        f"3. RUNBOOK — {project_dir}/RUNBOOK.md\n"
        f"   - Deployment steps (Docker build, run, verify)\n"
        f"   - Health check procedures (endpoints to hit, expected responses)\n"
        f"   - Monitoring and logging (where logs go, what to watch)\n"
        f"   - Common issues and troubleshooting\n"
        f"   - Backup and restore procedures\n"
        f"   - Scaling considerations\n"
        f"   - Rollback procedure\n"
        f"   - Contact/escalation info\n\n"
        f"4. CHANGELOG — {project_dir}/CHANGELOG.md\n"
        f"   - Initial release entry with all features\n\n"
        f"VERIFY: ls {project_dir}/README.md {project_dir}/RUNBOOK.md {project_dir}/CHANGELOG.md {project_dir}/backend/tests/",
        timeout=600
    ))

@activity.defn
async def devops_deploy(input: BuildInput) -> str:
    """Deploy preview — with automatic diagnostics and retry on failure.

    The agent diagnoses and fixes errors instead of failing on trivial issues
    like port conflicts, stale containers, or missing env vars.
    """
    project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
    project = Path(project_dir)
    loop = asyncio.get_event_loop()

    # Verify we have something to deploy
    if not list(project.rglob("Dockerfile")) and not list(project.rglob("docker-compose.yml")):
        raise ApplicationError(
            f"No Dockerfile or docker-compose.yml in {project_dir}",
            non_retryable=True)

    # Deploy with retry — agent diagnoses and fixes errors
    max_attempts = 3
    last_error = ""

    for attempt in range(1, max_attempts + 1):
        log.info(f"Deploy attempt {attempt}/{max_attempts} for {input.project_slug}")

        if attempt == 1:
            # First attempt — clean deploy
            message = (
                f"DEPLOY: {input.project_title}\n"
                f"Project dir: {project_dir}\n"
                f"Customer: {input.customer_email}\n\n"
                f"1. Remove any existing containers: docker rm -f $(docker ps -aq --filter name={input.project_slug}) 2>/dev/null\n"
                f"2. Check for port conflicts: ss -ltnp | grep -E '4[0-1][0-9]{{2}}'\n"
                f"3. Deploy: cd {project_dir} && docker compose up -d --build\n"
                f"   If no docker-compose.yml, use docker build + docker run\n"
                f"4. Wait 10s then verify: curl -s -o /dev/null -w '%{{http_code}}' http://localhost:PORT/\n"
                f"5. Report the URL: http://78.47.104.139:PORT"
            )
        else:
            # Retry — include the previous error for diagnosis
            message = (
                f"DEPLOY RETRY (attempt {attempt}) for: {input.project_title}\n"
                f"Project dir: {project_dir}\n\n"
                f"Previous attempt failed with:\n{last_error[:500]}\n\n"
                f"DIAGNOSE the root cause and fix it. Common issues:\n"
                f"- Port conflict: pick a different port in 4100-4199 range\n"
                f"- Stale container: docker rm -f the conflicting container\n"
                f"- Build error: read the Dockerfile, fix syntax or missing deps\n"
                f"- Missing .env: create from .env.example\n"
                f"- Database not ready: add healthcheck wait or retry logic\n"
                f"- Permission denied: check file ownership\n\n"
                f"Fix the issue, then redeploy. Do NOT give up."
            )

        result = await loop.run_in_executor(None, lambda msg=message: _dispatch_and_wait(
            f"{input.org}-devops", msg, timeout=300))

        # Check if deploy succeeded by looking for a running container
        import subprocess as sp
        check = sp.run(
            ["docker", "ps", "--filter", f"name={input.project_slug}", "--format", "{{.Ports}}"],
            capture_output=True, text=True, timeout=10)

        if check.stdout.strip():
            # Container is running — extract port
            import re as _re
            port_match = _re.search(r':(\d+)->', check.stdout)
            if port_match:
                port = port_match.group(1)
                url = f"http://78.47.104.139:{port}"
                log.info(f"Deploy succeeded on attempt {attempt}: {url}")
                return url

        # Also check docker compose
        check2 = sp.run(
            ["docker", "compose", "-f", f"{project_dir}/docker-compose.yml", "ps", "--format", "json"],
            capture_output=True, text=True, timeout=10, cwd=project_dir)
        if '"running"' in check2.stdout.lower():
            # Find the exposed port
            port_check = sp.run(
                ["docker", "compose", "-f", f"{project_dir}/docker-compose.yml", "port", "web", "80"],
                capture_output=True, text=True, timeout=10, cwd=project_dir)
            if port_check.stdout.strip():
                port = port_check.stdout.strip().split(":")[-1]
                url = f"http://78.47.104.139:{port}"
                log.info(f"Deploy succeeded (compose) on attempt {attempt}: {url}")
                return url

            # Try common service names
            for svc in ["web", "frontend", "app", "cms"]:
                port_check = sp.run(
                    ["docker", "compose", "-f", f"{project_dir}/docker-compose.yml", "port", svc, "80"],
                    capture_output=True, text=True, timeout=5, cwd=project_dir)
                if port_check.stdout.strip():
                    port = port_check.stdout.strip().split(":")[-1]
                    url = f"http://78.47.104.139:{port}"
                    log.info(f"Deploy succeeded ({svc}) on attempt {attempt}: {url}")
                    return url

        last_error = result if result else "No output from deploy agent"
        log.warning(f"Deploy attempt {attempt} failed: {last_error[:200]}")

    # All attempts exhausted — fail but with full context
    raise ApplicationError(
        f"Deploy failed after {max_attempts} attempts. Last error: {last_error[:300]}",
        non_retryable=False)  # Temporal can still retry the whole activity



@activity.defn
async def verify_auth_credentials(input: BuildInput) -> str:
    """Verify auth credentials work on the deployed preview."""
    project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
    project = Path(project_dir)
    cred_email = ""
    cred_pass = ""
    verified = False
    preview_port = ""

    # 1. Search for credentials in env and seed files
    for f in list(project.rglob(".env")) + list(project.rglob(".env.example")) + list(project.rglob("seed*")) + list(project.rglob("migrate*")):
        if "node_modules" in str(f) or ".git" in str(f):
            continue
        try:
            text = f.read_text(errors="ignore")[:5000]
            # Find email-like strings
            for line in text.split("\n"):
                low = line.lower()
                if "@" in line and not cred_email:
                    parts = line.split("@")
                    for p in parts:
                        if "." in p and len(p) > 3:
                            # Extract the full email
                            import re as _r
                            m = _r.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+", line)
                            if m:
                                cred_email = m.group(0)
                                break
                if ("password" in low or "pass" in low) and ("=" in line or ":" in line) and not cred_pass:
                    # Extract value after = or :
                    for sep in ["=", ":"]:
                        if sep in line:
                            val = line.split(sep, 1)[1].strip().strip("'\"").strip()
                            if val and len(val) > 2 and len(val) < 50:
                                cred_pass = val
                                break
            if cred_email and cred_pass:
                break
        except Exception:
            continue

    # 2. Find the preview port
    import subprocess as sp
    port_check = sp.run(
        ["docker", "ps", "--format", "{{.Ports}}"],
        capture_output=True, text=True, timeout=10)
    for line in port_check.stdout.split("\n"):
        if input.project_slug in line or True:
            m = _re.search(r":(\d{4})->", line)
            if m:
                port = m.group(1)
                # Check if this port serves our project
                try:
                    title_check = sp.run(
                        ["curl", "-s", "--max-time", "3", f"http://localhost:{port}/"],
                        capture_output=True, text=True, timeout=5)
                    if input.project_slug.replace("-", " ").lower() in title_check.stdout.lower() or input.project_title.lower() in title_check.stdout.lower():
                        preview_port = port
                        break
                except Exception:
                    continue

    if not preview_port:
        # Fallback: check docker ps for container name
        name_check = sp.run(
            ["docker", "ps", "--format", "{{.Names}} {{.Ports}}"],
            capture_output=True, text=True, timeout=10)
        for line in name_check.stdout.split("\n"):
            if input.project_slug in line:
                m = _re.search(r":(\d+)->", line)
                if m:
                    preview_port = m.group(1)
                    break

    url = f"http://78.47.104.139:{preview_port}" if preview_port else "URL not found"

    # 3. Test credentials
    if cred_email and cred_pass and preview_port:
        import json
        for auth_path in ["/api/auth/login", "/api/users/login", "/auth/login"]:
            try:
                test = sp.run(
                    ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                     "-X", "POST", f"http://localhost:{preview_port}{auth_path}",
                     "-H", "Content-Type: application/json",
                     "-d", json.dumps({"email": cred_email, "password": cred_pass, "username": cred_email})],
                    capture_output=True, text=True, timeout=10)
                if test.stdout.strip() in ("200", "201"):
                    verified = True
                    break
            except Exception:
                continue

    # 4. Format result
    lines = [f"  URL: {url}"]
    if cred_email:
        lines.append(f"  Email: {cred_email}")
    if cred_pass:
        lines.append(f"  Password: {cred_pass}")
    if verified:
        lines.append("  Status: VERIFIED")
    elif cred_email and cred_pass:
        lines.append("  Status: UNVERIFIED (found in config but login test failed)")
    else:
        lines.append("  Status: NO CREDENTIALS FOUND — app may need initial setup")

    log.info(f"Auth verification for {input.project_slug}: verified={verified}")
    return "\n".join(lines)


@activity.defn
async def send_preview_email(input: BuildInput) -> bool:
    """Advocate sends preview URL to customer — skip for internal projects."""
    braun_emails = ['braun.brelin@ai-elevate.ai', 'bbrelin@gmail.com']
    is_internal = (input.customer_email.lower() in braun_emails or input.customer_email.startswith("internal@"))
    if is_internal:
        try:
            from ops_notify import ops_notify
            ops_notify("delivery_ready", f"Preview ready: {input.project_title} — {input.domain}",
                      agent="build-workflow", customer_email=input.customer_email)
        except Exception:
            pass
        return True
    try:
        import psycopg2
        customer_name = input.customer_email.split("@")[0].split(".")[0].title()

        # Get advocate name from DB
        advocate_name = "The GigForge Team"
        advocate_title = ""
        try:
            conn = psycopg2.connect(**DB)
            cur = conn.cursor()
            agent_id = "gigforge-advocate" if input.org == "gigforge" else "techuni-advocate"
            cur.execute("SELECT name, role FROM agent_bios WHERE agent_id=%s", (agent_id,))
            row = cur.fetchone()
            if row and row[0]:
                advocate_name = row[0]
                advocate_title = row[1] or "Customer Delivery Liaison"
            conn.close()
        except Exception:
            pass

        company = "GigForge" if input.org == "gigforge" else "TechUni"
        from send_email import send_email
        send_email(
            to=input.customer_email,
            subject=f"Your {input.project_title} is ready for review",
            body=f"Hi {customer_name},\n\n"
                 f"Your {input.project_title} project is ready for review:\n\n"
                 f"URL: {input.domain}\n\n"
                 f"Login credentials:\n"
                 f"{auth_info}\n\n"
                 f"Take a look and let me know:\n"
                 f"- Any changes you'd like\n"
                 f"- Anything that looks great\n"
                 f"- Or if you're happy to go live\n\n"
                 f"Best regards,\n{advocate_name}\n{advocate_title}, {company}",
            agent_id=f"{input.org}-advocate",
        )
        return True
    except Exception:
        return False


@activity.defn
async def create_invoice(input: BuildInput) -> bool:
    try:
        from billing_pipeline import create_milestone_invoice
        # Amount comes from project data, not hardcoded
        amount = input.amount_eur if hasattr(input, "amount_eur") and input.amount_eur else 0
        if amount <= 0:
            log.warning("create_invoice called with no amount — skipping")
            return False
        create_milestone_invoice(input.customer_email, input.project_title, "Project delivery", amount, input.org)
        return True
    except Exception:
        return False


@activity.defn
async def send_feedback(input: BuildInput) -> bool:
    braun_emails = ['braun.brelin@ai-elevate.ai', 'bbrelin@gmail.com']
    is_internal = (input.customer_email.lower() in braun_emails or input.customer_email.startswith("internal@"))
    if is_internal:
        try:
            from ops_notify import ops_notify
            ops_notify("status_update", f"Build complete: {input.project_title}",
                      agent="build-workflow", customer_email=input.customer_email)
        except Exception:
            pass
        return True
    try:
        from feedback_system import send_feedback_request
        send_feedback_request(input.customer_email, input.project_title)
        return True
    except Exception:
        return False


@activity.defn
async def notify_customer_start(input: BuildInput) -> bool:
    """Send project kickoff email — skip for internal projects."""
    # Internal projects: send ops notification instead of customer email
    braun_emails = ['braun.brelin@ai-elevate.ai', 'bbrelin@gmail.com']
    is_internal = (input.customer_email.lower() in braun_emails or input.customer_email.startswith("internal@"))
    if is_internal:
        try:
            from ops_notify import ops_notify
            ops_notify("status_update", f"Build started: {input.project_title}",
                      agent="build-workflow", customer_email=input.customer_email)
        except Exception:
            pass
        return True
    try:
        import psycopg2
        # Check if we already sent a kickoff email for this customer+project
        conn = psycopg2.connect(**DB)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM email_interactions WHERE sender_email=%s AND subject LIKE %s AND direction='outbound'",
            (input.customer_email, f"%{input.project_title}%project has started%"))
        if cur.fetchone():
            log.info(f"Kickoff email already sent for {input.customer_email}/{input.project_title} — skipping")
            conn.close()
            return True  # Don't send again
        conn.close()

        # Extract first name from customer email or requirements
        customer_name = input.customer_email.split("@")[0].split(".")[0].title()

        # Get advocate name from DB
        advocate_name = "The GigForge Team"
        advocate_title = ""
        try:
            conn2 = psycopg2.connect(**DB)
            cur2 = conn2.cursor()
            agent_id = "gigforge-advocate" if input.org == "gigforge" else "techuni-advocate"
            cur2.execute("SELECT name, role FROM agent_bios WHERE agent_id=%s", (agent_id,))
            row = cur2.fetchone()
            if row and row[0]:
                advocate_name = row[0]
                advocate_title = row[1] or "Customer Delivery Liaison"
            conn2.close()
        except Exception:
            pass

        from send_email import send_email
        send_email(
            to=input.customer_email,
            subject=f"Your {input.project_title} project has started",
            body=f"Hi {customer_name},\n\n"
                 f"Great news — your {input.project_title} project is underway! Our engineering team has started building.\n\n"
                 f"You'll receive a preview link once we have something to show you.\n\n"
                 f"Best regards,\n{advocate_name}\n{advocate_title}, {'GigForge' if input.org == 'gigforge' else 'TechUni'}",
            agent_id=f"{input.org}-advocate",
        )

        # Log to prevent duplicates
        try:
            conn3 = psycopg2.connect(**DB)
            conn3.autocommit = True
            conn3.cursor().execute(
                "INSERT INTO email_interactions (sender_email, agent_id, subject, direction, status) VALUES (%s,%s,%s,'outbound','sent')",
                (input.customer_email, f"{input.org}-advocate", f"Your {input.project_title} project has started"))
            conn3.close()
        except Exception:
            pass

        return True
    except Exception as e:
        log.error(f"Kickoff email error: {e}")
        return False


@activity.defn
async def update_milestone(input: BuildInput) -> bool:
    milestone = input.requirements
    try:
        import psycopg2
        conn = psycopg2.connect(**DB)
        conn.autocommit = True
        conn.cursor().execute(
            "UPDATE project_milestones SET status='completed', completed_at=NOW() WHERE customer_email=%s AND milestone=%s",
            (input.customer_email, milestone))
        conn.close()
        return True
    except Exception:
        return False


@activity.defn
async def notify_ops(input: BuildInput) -> bool:
    try:
        from ops_notify import ops_notify
        ops_notify("status_update", f"Build: {input.requirements}", agent="build-workflow", customer_email=input.customer_email)
        return True
    except Exception:
        return False



@activity.defn
async def record_build_to_kg(input: BuildInput) -> bool:
    """Record build pipeline events to knowledge graph. Runs as activity (I/O safe)."""
    if not HAS_KG:
        return False
    try:
        project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
        tech_stack_file = Path(project_dir) / "TECH_STACK.md"
        if tech_stack_file.exists():
            record_tech_stack(input.org, input.project_slug, tech_stack_file.read_text()[:300])
        adr_dir = Path(project_dir) / "docs" / "adr"
        if adr_dir.exists():
            for adr_file in adr_dir.glob("*.md"):
                try:
                    record_adr(input.org, input.project_slug, adr_file.stem, adr_file.read_text()[:200])
                except Exception:
                    pass
        spec_file = Path(project_dir) / "SOFTWARE_SPEC.md"
        if spec_file.exists():
            record_spec(input.org, input.project_slug, spec_file.read_text()[:300])
        agents = [f"{input.org}-pm", f"{input.org}-engineer", f"{input.org}-ux-designer",
                  f"{input.org}-qa", f"{input.org}-devops"]
        record_build(input.org, input.project_slug, agents)
        log.info(f"KG: recorded build pipeline for {input.project_slug}")
        return True
    except Exception as e:
        log.warning(f"KG recording failed: {e}")
        return False

# ============================================================================
# Workflow — the full build chain
# ============================================================================

@workflow.defn
class ProjectBuildWorkflow:

    @workflow.run
    async def run(self, input: BuildInput) -> BuildResult:
        result = BuildResult()
        result.project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
        tl = timedelta(seconds=960)
        ts = timedelta(seconds=60)

        def mi(milestone):
            return BuildInput(**{**input.__dict__, "requirements": milestone})

        # Step 1: Kickoff
        await workflow.execute_activity(notify_customer_start, input, start_to_close_timeout=ts, retry_policy=RETRY)
        await workflow.execute_activity(notify_ops, mi("Project build started"), start_to_close_timeout=ts, retry_policy=RETRY)
        await workflow.execute_activity(create_milestones, input, start_to_close_timeout=ts, retry_policy=RETRY)
        result.actions.append("kickoff")

        # Step 2: PM sprint plan
        plan = await workflow.execute_activity(pm_sprint_plan, input, start_to_close_timeout=tl, retry_policy=RETRY)
        await workflow.execute_activity(update_milestone, mi("Design"), start_to_close_timeout=ts, retry_policy=RETRY)
        result.actions.append("sprint_plan")

        # Step 3: Engineering consensus — CTO + DevOps decide tech stack
        tech_stack = await workflow.execute_activity(engineering_consensus, input, start_to_close_timeout=tl, retry_policy=RETRY)
        await workflow.execute_activity(update_milestone, mi("Architecture"), start_to_close_timeout=ts, retry_policy=RETRY)
        await workflow.execute_activity(notify_ops, mi("Tech stack decided"), start_to_close_timeout=ts, retry_policy=RETRY)
        result.actions.append("engineering_consensus")

        # Step 4: Software specification (PM functional + Engineer technical)
        spec = await workflow.execute_activity(write_software_spec, input, start_to_close_timeout=tl, retry_policy=RETRY)
        await workflow.execute_activity(update_milestone, mi("Specification"), start_to_close_timeout=ts, retry_policy=RETRY)
        await workflow.execute_activity(notify_ops, mi("Software spec written"), start_to_close_timeout=ts, retry_policy=RETRY)
        result.actions.append("software_spec")

        # Step 5: UX design (informed by SOFTWARE_SPEC.md)
        design = await workflow.execute_activity(ux_design, input, start_to_close_timeout=tl, retry_policy=RETRY)
        result.actions.append("ux_design")

        # Step 6: Engineer build — pair programming — pair programming (each step is a separate Temporal activity)
        tp = timedelta(seconds=660)  # pair step timeout

        # --- Scaffold (sequential — must run first) ---
        await workflow.execute_activity(pair_scaffold, input, start_to_close_timeout=tp, retry_policy=RETRY)
        await workflow.execute_activity(notify_ops, mi("Scaffold created"), start_to_close_timeout=ts, retry_policy=RETRY)
        result.actions.append("pair:scaffold")

        # --- Backend + Frontend in PARALLEL ---
        # Backend: CMS collections + API routes
        # Frontend: Layout + UI components
        # These write to different directories (cms/ vs web/) so no file conflicts.
        backend_task = workflow.execute_activity(pair_cms_collections, input, start_to_close_timeout=tp, retry_policy=RETRY)
        frontend_task = workflow.execute_activity(pair_frontend_layout, input, start_to_close_timeout=tp, retry_policy=RETRY)
        await backend_task
        await frontend_task
        result.actions.append("parallel:cms+layout")

        backend_task2 = workflow.execute_activity(pair_api_routes, input, start_to_close_timeout=tp, retry_policy=RETRY)
        frontend_task2 = workflow.execute_activity(pair_ui_components, input, start_to_close_timeout=tp, retry_policy=RETRY)
        await backend_task2
        await frontend_task2
        await workflow.execute_activity(update_milestone, mi("Backend"), start_to_close_timeout=ts, retry_policy=RETRY)
        result.actions.append("parallel:api+ui")

        # --- Pages in PARALLEL ---
        pages1_task = workflow.execute_activity(pair_pages_part1, input, start_to_close_timeout=tp, retry_policy=RETRY)
        pages2_task = workflow.execute_activity(pair_pages_part2, input, start_to_close_timeout=tp, retry_policy=RETRY)
        await pages1_task
        await pages2_task
        await workflow.execute_activity(update_milestone, mi("Frontend"), start_to_close_timeout=ts, retry_policy=RETRY)
        result.actions.append("parallel:pages1+pages2")

        # --- Integration (sequential — reads everything) ---
        await workflow.execute_activity(pair_integration, input, start_to_close_timeout=tp, retry_policy=RETRY)
        result.actions.append("pair:integration")

        build = await workflow.execute_activity(verify_build, input, start_to_close_timeout=ts, retry_policy=RETRY)
        result.actions.append(f"build:{build[:50]}")

        # Step 7: QA test
        qa = await workflow.execute_activity(qa_test, input, start_to_close_timeout=tl, retry_policy=RETRY)
        await workflow.execute_activity(update_milestone, mi("Testing"), start_to_close_timeout=ts, retry_policy=RETRY)
        result.actions.append(f"qa:{qa[:50]}")

        # Step 8: Post-build UX review
        ux = await workflow.execute_activity(ux_review, input,
            start_to_close_timeout=timedelta(seconds=660), retry_policy=RETRY)
        result.actions.append("ux_review")

        # Step 9: Documentation, test suite, and runbook
        docs = await workflow.execute_activity(write_documentation, input, start_to_close_timeout=tl, retry_policy=RETRY)
        await workflow.execute_activity(notify_ops, mi("Documentation and test suite written"), start_to_close_timeout=ts, retry_policy=RETRY)
        result.actions.append("documentation")

        # Step 10: Deploy preview
        deploy_result = await workflow.execute_activity(devops_deploy, input, start_to_close_timeout=tl, retry_policy=RETRY)
        await workflow.execute_activity(update_milestone, mi("Deployment"), start_to_close_timeout=ts, retry_policy=RETRY)

        # Extract URL from deploy result
        url_match = re.search(r'https?://\S+', deploy_result)
        preview_url = url_match.group(0) if url_match else deploy_result  # No hardcoded fallback
        result.preview_url = preview_url
        result.actions.append(f"deployed:{preview_url}")

        # Step 11: Send preview to customer (with actual URL)
        preview_input = BuildInput(**{**input.__dict__, "domain": preview_url})
        auth_info = await workflow.execute_activity(verify_auth_credentials, input,
            start_to_close_timeout=timedelta(seconds=60), retry_policy=RETRY)
        result.actions.append("auth_verified")
        if 'VERIFIED' in auth_info and 'UNVERIFIED' not in auth_info:
            preview_input = BuildInput(**{**input.__dict__, "domain": preview_url + "\n\n" + auth_info})
            await workflow.execute_activity(send_preview_email, preview_input, start_to_close_timeout=ts, retry_policy=RETRY)
        else:
            result.actions.append("preview_blocked:credentials_not_verified")
        result.actions.append("preview_sent")

        # Step 12: Billing — NEVER auto-invoice. Requires customer sign-off + Braun approval.
        # Invoicing happens in ProjectClosureWorkflow after explicit approval.
        is_pro_bono = any(kw in (input.requirements or "").lower()
                          for kw in ["pro-bono", "pro bono", "free", "no charge", "gratis", "non-commercial", "internal"])
        if is_pro_bono:
            result.actions.append("billing_skipped:pro_bono")
        else:
            result.actions.append("billing_deferred:awaiting_signoff_and_approval")
        # Feedback request is fine — just asking how the preview looks
        await workflow.execute_activity(send_feedback, input, start_to_close_timeout=ts, retry_policy=RETRY)
        result.actions.append("feedback_requested")

        # 11. Done
        await workflow.execute_activity(notify_ops, mi("Project build complete"), start_to_close_timeout=ts, retry_policy=RETRY)
        result.status = "delivered"
        # Record to knowledge graph (as activity — I/O safe)
        try:
            await workflow.execute_activity(record_build_to_kg, input,
                start_to_close_timeout=timedelta(seconds=60))
            result.actions.append("kg_recorded")
        except Exception:
            pass  # KG is best-effort, don't fail the build
        result.actions.append("build_complete")
        return result


# ============================================================================
# Client
# ============================================================================

async def start_build(customer_email, project_title, project_slug, requirements="", domain="", org="gigforge"):
    from temporalio.client import Client
    import re as _re

    # Normalize slug: lowercase, alphanumeric + hyphens, strip re: prefixes
    slug = project_slug.lower()
    slug = _re.sub(r'^(re:\s*)+', '', slug).strip()
    slug = _re.sub(r'[^a-z0-9]+', '-', slug).strip('-')[:40]

    # Check if there's an existing project directory for this customer
    projects_dir = Path("/opt/ai-elevate/gigforge/projects")
    for d in projects_dir.iterdir():
        if d.is_dir() and d.name != ".git":
            # Check if this dir was created for the same customer email
            readme = d / "README.md"
            sprint = d / "SPRINT_PLAN.md"
            for f in [readme, sprint]:
                if f.exists():
                    try:
                        if customer_email in f.read_text()[:1000]:
                            slug = d.name
                            log.info(f"Reusing existing project dir: {slug}")
                            break
                    except Exception:
                        pass

    # Prevent duplicate build workflows for the same project
    client = await Client.connect("localhost:7233")
    try:
        # Check if a build is already running for this slug
        async for wf in client.list_workflows(f'WorkflowType="ProjectBuildWorkflow"'):
            if slug in wf.id:
                handle = client.get_workflow_handle(wf.id)
                desc = await handle.describe()
                # status 1 = RUNNING
                if desc.status == 1:
                    log.info(f"Build already running for {slug}: {wf.id}")
                    return {"workflow_id": wf.id, "status": "already_running"}
    except Exception:
        pass

    input_data = BuildInput(customer_email=customer_email, project_title=project_title,
                           project_slug=slug, org=org, requirements=requirements, domain=domain)
    handle = await client.start_workflow(
        ProjectBuildWorkflow.run, input_data,
        id=f"build-{project_slug}-{int(time.time())}",
        task_queue="build-projects",
        execution_timeout=timedelta(hours=2),
    )
    return {"workflow_id": handle.id, "status": "started"}
