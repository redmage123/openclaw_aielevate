#!/usr/bin/env python3
"""GigForge Project Lifecycle Workflows — 7 Temporal workflows covering the full lifecycle.

Workflows:
  1. RevisionCycleWorkflow — customer reviews → requests changes → fix → retest → redeploy
  2. ScopeChangeWorkflow — change request → impact assessment → pricing → approval → execute
  3. ClientOnboardingWorkflow — advocate intro → asset collection → workspace → kickoff
  4. ProposalToContractWorkflow — scout → feasibility → proposal → approval → send → negotiate
  5. PaymentMilestoneWorkflow — deposit → milestone invoices → final payment → receipt
  6. PostDeliverySupportWorkflow — 30-day bug fix → triage → fix → redeploy → close
  7. ProjectClosureWorkflow — sign-off → handover → production → archive → case study

All workflows use the same agent dispatch pattern and share DB infrastructure.
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
from typing import Optional

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from dao import BaseDAO  # TODO: Replace inline DB calls with specific DAOs
from logging_config import get_logger  # Structured JSON logging
from exceptions import AiElevateError  # TODO: Use specific exception types, AgentError, DatabaseError

sys.path.insert(0, "/home/aielevate")

try:
    from workflow_kg import (
        record_revision, record_scope_change, record_onboarding,
        record_proposal, record_payment, record_bug_report,
        record_bug_fix, record_closure, record_case_study,
        record_satisfaction, record_acceptance,
    )
    HAS_KG = True
except ImportError:
    HAS_KG = False

log = get_logger("project-workflows")

RETRY = RetryPolicy(maximum_attempts=5, initial_interval=timedelta(seconds=30),
                     maximum_interval=timedelta(seconds=120))
RETRY_FAST = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=10))

DB_CONN = dict(
    host=os.environ.get("DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("DB_PORT", "5434")),
    dbname=os.environ.get("DB_NAME", "rag"),
    user=os.environ.get("DB_USER", "rag"),
    password=os.environ.get("DB_PASSWORD", "rag_vec_2026"),
)


# ============================================================================
# Shared data classes
# ============================================================================

@dataclass
class ProjectInput:
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    customer_email: str
    project_title: str
    project_slug: str
    org: str = "gigforge"
    requirements: str = ""
    revision_number: int = 0
    change_description: str = ""
    bug_description: str = ""
    amount_eur: float = 0.0
    milestone_name: str = ""
    job_data: str = ""  # JSON string for proposal workflow


@dataclass
class WorkflowResult:
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    status: str = "pending"
    actions: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    output: str = ""


# ============================================================================
# Shared helpers
# ============================================================================

def _db():
    import psycopg2
    conn = psycopg2.connect(**DB_CONN)
    conn.autocommit = True
    return conn

def _clear_session(agent_id: str):
    session_dir = Path(f"/home/aielevate/.openclaw/agents/{agent_id}/sessions")
    if session_dir.exists():
        for f in session_dir.glob("*.jsonl"):
            f.unlink()

def _dispatch_and_wait(agent_id: str, message: str, timeout: int = 600) -> str:
    """Dispatch agent using reliable agent_dispatch."""
    try:
        from agent_dispatch import dispatch
        result = dispatch(agent_id, message, timeout=timeout)
        if result.status == "success":
            return result.output
        elif result.status in ("circuit_open", "gateway_down"):
            from temporalio.exceptions import ApplicationError
            raise ApplicationError(f"{result.status}: {result.error}", non_retryable=False)
        else:
            return result.output or ""
    except ImportError:
        _clear_session(agent_id)
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        try:
            proc = subprocess.run(
                ["openclaw", "agent", "--agent", agent_id,
                 "--message", message, "--thinking", "low", "--timeout", str(timeout)],
                capture_output=True, text=True, timeout=timeout + 30, env=env,
            )
            output = proc.stdout or ""
            return re.sub(r"\*\[.*?\]\*", "", output, flags=re.DOTALL).strip()
        except subprocess.TimeoutExpired:
            return "TIMEOUT"

def _send_email(to, subject, body, agent_id="gigforge-advocate", org="gigforge"):
    try:
        from send_email import send_email
        send_email(to=to, subject=subject, body=body, agent_id=agent_id)
        return True
    except (AgentError, Exception) as e:
        log.error(f"Email failed: {e}")
        return False

def _get_agent_name(agent_id):
    try:
        conn = _db()
        cur = conn.cursor()
        cur.execute("SELECT name, role FROM agent_bios WHERE agent_id=%s", (agent_id,))
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            return row[0], row[1] or ""
    except (DatabaseError, Exception) as e:
        pass
    return "The GigForge Team", ""

def _customer_first_name(email):
    return email.split("@")[0].split(".")[0].title()

def _notify_ops(event, message, agent="workflow", customer_email=""):
    try:
        from ops_notify import ops_notify
        ops_notify(event, message, agent=agent, customer_email=customer_email)
    except (AgentError, Exception) as e:
        pass

def _log_interaction(customer_email, agent_id, subject, direction="outbound"):
    try:
        conn = _db()
        conn.cursor().execute(
            "INSERT INTO email_interactions (sender_email, agent_id, subject, direction, status) "
            "VALUES (%s,%s,%s,%s,'sent')",
            (customer_email, agent_id, subject, direction))
        conn.close()
    except (DatabaseError, Exception) as e:
        pass


# ============================================================================
# DB schema for new workflows
# ============================================================================

def init_workflow_tables():
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    try:
        conn = _db()
        cur = conn.cursor()
        sql_path = Path(__file__).parent / "sql" / "project_workflow_tables.sql"
        cur.execute(sql_path.read_text())
        conn.close()
        log.info("Workflow tables initialized")
    except (DatabaseError, Exception) as e:
        log.error(f"Table init error: {e}")

init_workflow_tables()


# ============================================================================
# WORKFLOW 1: Revision Cycle
# ============================================================================

@activity.defn
async def log_revision_request(input: ProjectInput) -> int:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    conn = _db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO revision_cycles (customer_email, project_slug, revision_number, change_requests) "
        "VALUES (%s,%s,%s,%s) RETURNING id",
        (input.customer_email, input.project_slug, input.revision_number, input.change_description))
    rid = cur.fetchone()[0]
    conn.close()
    return rid

@activity.defn
async def engineer_revision(input: ProjectInput) -> str:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch_and_wait(
        "gigforge-engineer",
        f"REVISION {input.revision_number} for: {input.project_title}\n"
        f"Project dir: {project_dir}\n\n"
        f"Read SOFTWARE_SPEC.md, TECH_STACK.md, and the existing source code.\n\n"
        f"Customer requested these changes:\n{input.change_description}\n\n"
        f"Make the changes. Do NOT break existing functionality.\n"
        f"After changes, verify the app still builds: docker compose build\n"
        f"Update CHANGELOG.md with the revision details.",
        timeout=600
    ))

@activity.defn
async def qa_revision(input: ProjectInput) -> str:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch_and_wait(
        "gigforge-qa",
        f"QA REVISION {input.revision_number} for: {input.project_title}\n"
        f"Project dir: {project_dir}\n\n"
        f"Changes made:\n{input.change_description}\n\n"
        f"1. Verify the changes were implemented correctly\n"
        f"2. Regression test — make sure nothing else broke\n"
        f"3. Build and run: docker compose up -d --build\n"
        f"4. Test affected endpoints\n"
        f"5. Report PASS or FAIL",
        timeout=300
    ))

@activity.defn
async def redeploy_preview(input: ProjectInput) -> str:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch_and_wait(
        "gigforge-devops",
        f"REDEPLOY after revision {input.revision_number}: {input.project_title}\n"
        f"Project dir: {project_dir}\n\n"
        f"Rebuild and redeploy: cd {project_dir} && docker compose up -d --build\n"
        f"Verify all services are healthy.\n"
        f"Report the preview URL.",
        timeout=300
    ))

@activity.defn
async def send_revision_ready(input: ProjectInput) -> bool:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    name = _customer_first_name(input.customer_email)
    advocate_name, advocate_title = _get_agent_name(f"{input.org}-advocate")
    return _send_email(
        to=input.customer_email,
        subject=f"Revision {input.revision_number} ready — {input.project_title}",
        body=f"Hi {name},\n\n"
             f"We've made the changes you requested and the updated version is live.\n\n"
             f"Changes applied:\n{input.change_description}\n\n"
             f"Please review and let us know if everything looks good, "
             f"or if you'd like any further adjustments.\n\n"
             f"Best regards,\n{advocate_name}\n{advocate_title}, GigForge",
        agent_id=f"{input.org}-advocate",
    )

@activity.defn
async def complete_revision(input: ProjectInput) -> bool:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    conn = _db()
    conn.cursor().execute(
        "UPDATE revision_cycles SET status='completed', completed_at=NOW() "
        "WHERE customer_email=%s AND project_slug=%s AND revision_number=%s",
        (input.customer_email, input.project_slug, input.revision_number))
    conn.close()
    return True




@activity.defn
async def update_spec_with_scope_change(input: ProjectInput) -> bool:
    """Append approved scope change to SOFTWARE_SPEC.md."""
    project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
    spec_file = Path(project_dir) / "SOFTWARE_SPEC.md"
    scope_file = Path(project_dir) / f"SCOPE_CHANGE_{input.revision_number}.md"

    if not spec_file.exists() or not scope_file.exists():
        return False

    scope_content = scope_file.read_text()
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    appendix = f"""

---

## Scope Change #{input.revision_number} (Approved {timestamp})

{scope_content}
"""
    with open(spec_file, "a") as f:
        f.write(appendix)

    log.info(f"SOFTWARE_SPEC.md updated with scope change #{input.revision_number}")
    return True

@workflow.defn
class RevisionCycleWorkflow:
    """Customer requests changes → engineer fixes → QA retests → redeploy → notify."""

    @workflow.run
    async def run(self, input: ProjectInput) -> WorkflowResult:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        result = WorkflowResult()
        tl = timedelta(seconds=660)
        ts = timedelta(seconds=60)

        await workflow.execute_activity(log_revision_request, input,
            start_to_close_timeout=ts, retry_policy=RETRY_FAST)
        result.actions.append(f"revision_{input.revision_number}_logged")
        if HAS_KG:
            record_revision(input.org, input.customer_email, input.project_slug,
                           input.revision_number, input.change_description)

        # Engineer makes the changes
        fix = await workflow.execute_activity(engineer_revision, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.actions.append("engineer_fixed")

        # QA regression test
        qa = await workflow.execute_activity(qa_revision, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.actions.append(f"qa:{qa[:50]}")

        # Redeploy
        deploy = await workflow.execute_activity(redeploy_preview, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.actions.append("redeployed")

        # Notify customer
        await workflow.execute_activity(send_revision_ready, input,
            start_to_close_timeout=ts, retry_policy=RETRY_FAST)
        result.actions.append("customer_notified")

        await workflow.execute_activity(complete_revision, input,
            start_to_close_timeout=ts, retry_policy=RETRY_FAST)

        result.status = "completed"
        result.output = f"Revision {input.revision_number} delivered"
        return result


# ============================================================================
# WORKFLOW 2: Scope Change
# ============================================================================

@activity.defn
async def log_scope_change(input: ProjectInput) -> int:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    conn = _db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO scope_changes (customer_email, project_slug, description) "
        "VALUES (%s,%s,%s) RETURNING id",
        (input.customer_email, input.project_slug, input.change_description))
    sid = cur.fetchone()[0]
    conn.close()
    return sid

@activity.defn
async def pm_impact_assessment(input: ProjectInput) -> str:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch_and_wait(
        "gigforge-pm",
        f"SCOPE CHANGE IMPACT ASSESSMENT for: {input.project_title}\n"
        f"Project dir: {project_dir}\n\n"
        f"Read SOFTWARE_SPEC.md and SPRINT_PLAN.md.\n\n"
        f"Customer is requesting:\n{input.change_description}\n\n"
        f"Assess:\n"
        f"1. What existing features are affected?\n"
        f"2. Is this additive (new feature) or modifying (change existing)?\n"
        f"3. Timeline impact — how many days does this add?\n"
        f"4. Risk — does this introduce breaking changes?\n"
        f"5. Dependencies — does this need new libraries or infrastructure?\n\n"
        f"Write your assessment to {project_dir}/SCOPE_CHANGE_{input.revision_number}.md",
        timeout=300
    ))

@activity.defn
async def engineer_effort_estimate(input: ProjectInput) -> str:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch_and_wait(
        "gigforge-engineer",
        f"EFFORT ESTIMATE for scope change: {input.project_title}\n"
        f"Project dir: {project_dir}\n\n"
        f"Read the PM's impact assessment at {project_dir}/SCOPE_CHANGE_{input.revision_number}.md\n"
        f"Read the existing codebase.\n\n"
        f"Change requested:\n{input.change_description}\n\n"
        f"Estimate:\n"
        f"1. Files that need to change\n"
        f"2. New files needed\n"
        f"3. Estimated hours of engineering work\n"
        f"4. Estimated hours of QA work\n"
        f"5. Technical risks or blockers\n\n"
        f"Append your estimate to {project_dir}/SCOPE_CHANGE_{input.revision_number}.md",
        timeout=300
    ))

@activity.defn
async def sales_price_change(input: ProjectInput) -> str:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch_and_wait(
        "gigforge-sales",
        f"PRICE SCOPE CHANGE for: {input.project_title}\n"
        f"Project dir: {project_dir}\n\n"
        f"Read the scope change assessment at {project_dir}/SCOPE_CHANGE_{input.revision_number}.md\n"
        f"(includes PM impact assessment and engineer effort estimate)\n\n"
        f"Change requested:\n{input.change_description}\n\n"
        f"Determine:\n"
        f"1. Is this within the original scope (no extra charge)?\n"
        f"2. If not, what's the additional cost?\n"
        f"3. Should we offer it as goodwill (relationship investment)?\n\n"
        f"Append pricing decision to {project_dir}/SCOPE_CHANGE_{input.revision_number}.md",
        timeout=300
    ))

@activity.defn
async def send_scope_change_proposal(input: ProjectInput) -> bool:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
    # Read the scope change doc for summary
    scope_doc = Path(project_dir) / f"SCOPE_CHANGE_{input.revision_number}.md"
    summary = scope_doc.read_text()[:1000] if scope_doc.exists() else input.change_description

    name = _customer_first_name(input.customer_email)
    advocate_name, advocate_title = _get_agent_name(f"{input.org}-advocate")
    return _send_email(
        to=input.customer_email,
        subject=f"Scope change assessment — {input.project_title}",
        body=f"Hi {name},\n\n"
             f"We've reviewed your change request and here's our assessment:\n\n"
             f"{summary}\n\n"
             f"Please reply to confirm you'd like us to proceed, or let us know "
             f"if you'd like to discuss further.\n\n"
             f"Best regards,\n{advocate_name}\n{advocate_title}, GigForge",
        agent_id=f"{input.org}-advocate",
    )


@workflow.defn
class ScopeChangeWorkflow:
    """Change request → PM assessment → Engineer estimate → Sales pricing → customer notification."""

    @workflow.run
    async def run(self, input: ProjectInput) -> WorkflowResult:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        result = WorkflowResult()
        tl = timedelta(seconds=660)
        ts = timedelta(seconds=60)

        await workflow.execute_activity(log_scope_change, input,
            start_to_close_timeout=ts, retry_policy=RETRY_FAST)
        result.actions.append("scope_change_logged")
        if HAS_KG:
            record_scope_change(input.org, input.customer_email, input.project_slug,
                               input.change_description)

        _notify_ops("scope_change", f"Scope change requested: {input.project_title}",
                    customer_email=input.customer_email)

        # PM assesses impact
        await workflow.execute_activity(pm_impact_assessment, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.actions.append("pm_assessed")

        # Engineer estimates effort
        await workflow.execute_activity(engineer_effort_estimate, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.actions.append("engineer_estimated")

        # Sales prices the change
        await workflow.execute_activity(sales_price_change, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.actions.append("sales_priced")

        # Send proposal to customer
        await workflow.execute_activity(send_scope_change_proposal, input,
            start_to_close_timeout=ts, retry_policy=RETRY_FAST)
        result.actions.append("customer_notified")

        result.status = "awaiting_approval"
        result.output = "Scope change assessment sent to customer"

        # When scope change is approved, update the master SOFTWARE_SPEC.md
        # This happens asynchronously when the customer replies with approval
        # The spec update is logged so the next build uses the updated spec
        result.actions.append("spec_update_pending:on_approval")

        return result


# ============================================================================
# WORKFLOW 3: Client Onboarding
# ============================================================================

@activity.defn
async def create_onboarding_record(input: ProjectInput) -> int:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    conn = _db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO client_onboarding (customer_email, project_slug) "
        "VALUES (%s,%s) RETURNING id",
        (input.customer_email, input.project_slug))
    oid = cur.fetchone()[0]
    conn.close()
    return oid

@activity.defn
async def advocate_introduction(input: ProjectInput) -> bool:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    name = _customer_first_name(input.customer_email)
    advocate_name, advocate_title = _get_agent_name(f"{input.org}-advocate")
    company = "GigForge" if input.org == "gigforge" else "TechUni"

    sent = _send_email(
        to=input.customer_email,
        subject=f"Welcome aboard — your {input.project_title} project",
        body=f"Hi {name},\n\n"
             f"I'm {advocate_name}, your dedicated {advocate_title} at {company}. "
             f"I'll be your single point of contact throughout this project.\n\n"
             f"Here's what happens next:\n\n"
             f"1. I'll need a few things from you (brand assets, access credentials, etc.)\n"
             f"2. Our engineering team is already reviewing your requirements\n"
             f"3. You'll get regular progress updates from me\n"
             f"4. When the first preview is ready, I'll send you the link\n\n"
             f"If you have any questions at any point, just reply to this email.\n\n"
             f"Looking forward to building something great together.\n\n"
             f"Best regards,\n{advocate_name}\n{advocate_title}, {company}",
        agent_id=f"{input.org}-advocate",
    )

    if sent:
        conn = _db()
        conn.cursor().execute(
            "UPDATE client_onboarding SET advocate_intro_sent=TRUE WHERE customer_email=%s AND project_slug=%s",
            (input.customer_email, input.project_slug))
        conn.close()
    return sent

@activity.defn
async def request_assets(input: ProjectInput) -> bool:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    name = _customer_first_name(input.customer_email)
    advocate_name, _ = _get_agent_name(f"{input.org}-advocate")

    return _send_email(
        to=input.customer_email,
        subject=f"Quick checklist — {input.project_title}",
        body=f"Hi {name},\n\n"
             f"To get the best result, we'll need a few things from you. "
             f"No rush — send whatever you have when you're ready:\n\n"
             f"- Company logo (SVG or high-res PNG preferred)\n"
             f"- Brand colors (hex codes if you have them)\n"
             f"- Any existing content, copy, or text you'd like included\n"
             f"- Access credentials (if we need to integrate with existing systems)\n"
             f"- Reference sites or apps you like the look of\n"
             f"- Any other assets or materials you think would be helpful\n\n"
             f"You can reply to this email with attachments, or share a Google Drive / "
             f"Dropbox link if that's easier.\n\n"
             f"Best regards,\n{advocate_name}",
        agent_id=f"{input.org}-advocate",
    )

@activity.defn
async def create_project_workspace(input: ProjectInput) -> bool:
    """Create project workspace in Plane and notify PM."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: _dispatch_and_wait(
        "gigforge-pm",
        f"CREATE PROJECT WORKSPACE for: {input.project_title}\n"
        f"Customer: {input.customer_email}\n"
        f"Slug: {input.project_slug}\n\n"
        f"1. Create a Plane project for this customer if one doesn't exist\n"
        f"2. Create initial issues: Design, Backend, Frontend, Testing, Deployment\n"
        f"3. Assign yourself as project lead\n"
        f"4. Add a note with customer requirements: {input.requirements[:500]}",
        timeout=180
    ))

    conn = _db()
    conn.cursor().execute(
        "UPDATE client_onboarding SET workspace_created=TRUE WHERE customer_email=%s AND project_slug=%s",
        (input.customer_email, input.project_slug))
    conn.close()
    return True

@activity.defn
async def confirm_kickoff(input: ProjectInput) -> bool:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    conn = _db()
    conn.cursor().execute(
        "UPDATE client_onboarding SET kickoff_confirmed=TRUE, status='completed', completed_at=NOW() "
        "WHERE customer_email=%s AND project_slug=%s",
        (input.customer_email, input.project_slug))
    conn.close()
    _notify_ops("new_project", f"Client onboarding complete: {input.project_title}",
                agent="onboarding-workflow", customer_email=input.customer_email)
    return True


@workflow.defn
class ClientOnboardingWorkflow:
    """Advocate intro → asset request → workspace creation → kickoff confirmation."""

    @workflow.run
    async def run(self, input: ProjectInput) -> WorkflowResult:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        result = WorkflowResult()
        ts = timedelta(seconds=60)
        tl = timedelta(seconds=300)

        await workflow.execute_activity(create_onboarding_record, input,
            start_to_close_timeout=ts, retry_policy=RETRY_FAST)
        result.actions.append("onboarding_started")

        # Advocate introduces herself
        await workflow.execute_activity(advocate_introduction, input,
            start_to_close_timeout=ts, retry_policy=RETRY_FAST)
        result.actions.append("advocate_intro_sent")

        # Request assets (sent 30s after intro so it doesn't feel like a form letter blast)
        await asyncio.sleep(30)
        await workflow.execute_activity(request_assets, input,
            start_to_close_timeout=ts, retry_policy=RETRY_FAST)
        result.actions.append("assets_requested")

        # Create workspace in Plane
        await workflow.execute_activity(create_project_workspace, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.actions.append("workspace_created")

        # Mark kickoff confirmed
        await workflow.execute_activity(confirm_kickoff, input,
            start_to_close_timeout=ts, retry_policy=RETRY_FAST)
        result.actions.append("kickoff_confirmed")
        if HAS_KG:
            record_onboarding(input.org, input.customer_email, input.project_slug,
                             f"{input.org}-advocate")

        result.status = "completed"
        result.output = "Client onboarded"
        return result


# ============================================================================
# WORKFLOW 4: Proposal to Contract
# ============================================================================

@activity.defn
async def engineering_feasibility(input: ProjectInput) -> str:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    job = json.loads(input.job_data) if input.job_data else {}
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch_and_wait(
        "gigforge-engineer",
        f"FEASIBILITY ASSESSMENT for potential project:\n"
        f"Title: {job.get('title', input.project_title)}\n"
        f"Description: {job.get('description', input.requirements)[:1000]}\n"
        f"Skills needed: {job.get('skills', '')}\n"
        f"Budget: {job.get('budget_min', 0)}-{job.get('budget_max', 0)} EUR\n\n"
        f"Assess:\n"
        f"1. Can we build this? (YES/NO/PARTIAL)\n"
        f"2. Estimated effort (hours)\n"
        f"3. Tech stack recommendation\n"
        f"4. Risks and unknowns\n"
        f"5. Is the budget realistic for the scope?\n\n"
        f"Write a brief assessment (200-400 words).",
        timeout=180
    ))

@activity.defn
async def draft_proposal(input: ProjectInput) -> str:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    job = json.loads(input.job_data) if input.job_data else {}
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch_and_wait(
        "gigforge-sales",
        f"DRAFT PROPOSAL for:\n"
        f"Title: {job.get('title', input.project_title)}\n"
        f"Customer: {input.customer_email}\n"
        f"Description: {job.get('description', input.requirements)[:1000]}\n"
        f"Budget: {job.get('budget_min', 0)}-{job.get('budget_max', 0)} EUR\n"
        f"Platform: {job.get('platform', 'direct')}\n\n"
        f"Engineering says: {input.requirements[:500]}\n\n"
        f"Write a professional proposal that:\n"
        f"1. Shows we understand their problem\n"
        f"2. Outlines our approach (not just tech specs)\n"
        f"3. References relevant portfolio items\n"
        f"4. Proposes a realistic timeline\n"
        f"5. Includes pricing with payment milestones\n"
        f"6. Has a clear call to action\n\n"
        f"Save to /opt/ai-elevate/gigforge/memory/proposals/{input.project_slug}.md",
        timeout=300
    ))

@activity.defn
async def queue_proposal_approval(input: ProjectInput) -> bool:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    try:
        from proposal_queue import queue_proposal
        job = json.loads(input.job_data) if input.job_data else {}
        queue_proposal(
            customer_email=input.customer_email or job.get("client_email", ""),
            project_title=input.project_title or job.get("title", ""),
            proposal_summary=input.requirements[:300],
            price_eur=input.amount_eur or job.get("budget_max", 0),
            platform=job.get("platform", "direct"),
        )
        return True
    except (AiElevateError, Exception) as e:
        log.error(f"Proposal queue error: {e}")
        return False

@activity.defn
async def send_proposal_to_customer(input: ProjectInput) -> bool:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    proposal_file = Path(f"/opt/ai-elevate/gigforge/memory/proposals/{input.project_slug}.md")
    proposal_text = proposal_file.read_text()[:2000] if proposal_file.exists() else ""

    name = _customer_first_name(input.customer_email)
    sales_name, sales_title = _get_agent_name(f"{input.org}-sales")

    return _send_email(
        to=input.customer_email,
        subject=f"Proposal — {input.project_title}",
        body=f"Hi {name},\n\n"
             f"Thank you for your interest. Here is our proposal for your project:\n\n"
             f"{proposal_text}\n\n"
             f"To move forward, simply reply to this email confirming you'd like to proceed.\n\n"
             f"Best regards,\n{sales_name}\n{sales_title}, GigForge",
        agent_id=f"{input.org}-sales",
    )


@workflow.defn
class ProposalToContractWorkflow:
    """Engineering feasibility → draft proposal → approval queue → send to customer."""

    @workflow.run
    async def run(self, input: ProjectInput) -> WorkflowResult:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        result = WorkflowResult()
        tl = timedelta(seconds=660)
        ts = timedelta(seconds=60)

        # Engineering assesses feasibility
        feasibility = await workflow.execute_activity(engineering_feasibility, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.actions.append("feasibility_assessed")

        # Update input with feasibility for the proposal drafter
        proposal_input = ProjectInput(**{**input.__dict__, "requirements": feasibility[:500]})

        # Sales drafts proposal
        await workflow.execute_activity(draft_proposal, proposal_input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.actions.append("proposal_drafted")
        if HAS_KG:
            record_proposal(input.org, input.customer_email, input.project_title,
                           platform="direct", price_eur=input.amount_eur)

        # Queue for approval
        await workflow.execute_activity(queue_proposal_approval, input,
            start_to_close_timeout=ts, retry_policy=RETRY_FAST)
        result.actions.append("approval_queued")

        _notify_ops("new_proposal", f"Proposal ready for approval: {input.project_title}",
                    agent="proposal-workflow", customer_email=input.customer_email)

        result.status = "awaiting_approval"
        result.output = "Proposal drafted and queued for approval"
        return result


# ============================================================================
# WORKFLOW 5: Payment Milestones
# ============================================================================

@activity.defn
async def create_payment_schedule(input: ProjectInput) -> bool:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    conn = _db()
    cur = conn.cursor()
    total = input.amount_eur
    milestones = [
        ("Deposit (30%)", total * 0.30),
        ("Design approval (20%)", total * 0.20),
        ("Delivery (40%)", total * 0.40),
        ("Final sign-off (10%)", total * 0.10),
    ]
    for name, amount in milestones:
        cur.execute(
            "INSERT INTO payment_milestones (customer_email, project_slug, milestone_name, amount_eur) "
            "VALUES (%s,%s,%s,%s)",
            (input.customer_email, input.project_slug, name, amount))
    conn.close()
    log.info(f"Payment schedule created: {len(milestones)} milestones, total {total} EUR")
    return True

@activity.defn
async def send_milestone_invoice(input: ProjectInput) -> bool:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    try:
        from billing_pipeline import create_milestone_invoice, send_invoice_email
        invoice_id = create_milestone_invoice(
            input.customer_email, input.project_title,
            input.milestone_name, input.amount_eur, input.org)
        if invoice_id:
            send_invoice_email(invoice_id)
            conn = _db()
            conn.cursor().execute(
                "UPDATE payment_milestones SET invoice_id=%s, status='invoiced' "
                "WHERE customer_email=%s AND project_slug=%s AND milestone_name=%s",
                (invoice_id, input.customer_email, input.project_slug, input.milestone_name))
            conn.close()
            return True
    except (DatabaseError, Exception) as e:
        log.error(f"Invoice error: {e}")
    return False

@activity.defn
async def send_payment_receipt(input: ProjectInput) -> bool:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    name = _customer_first_name(input.customer_email)
    finance_name, finance_title = _get_agent_name(f"{input.org}-finance")
    return _send_email(
        to=input.customer_email,
        subject=f"Payment received — {input.milestone_name} — {input.project_title}",
        body=f"Hi {name},\n\n"
             f"We've received your payment of EUR {input.amount_eur:.2f} "
             f"for {input.milestone_name}.\n\n"
             f"Thank you!\n\n"
             f"Best regards,\n{finance_name}\n{finance_title}, GigForge",
        agent_id=f"{input.org}-finance",
    )


@workflow.defn
class PaymentMilestoneWorkflow:
    """Create payment schedule → send deposit invoice → track milestones."""

    @workflow.run
    async def run(self, input: ProjectInput) -> WorkflowResult:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        result = WorkflowResult()
        ts = timedelta(seconds=60)

        # Create the payment schedule
        await workflow.execute_activity(create_payment_schedule, input,
            start_to_close_timeout=ts, retry_policy=RETRY_FAST)
        result.actions.append("schedule_created")

        # Send deposit invoice
        # Queue invoice for Braun approval — do NOT send automatically
        deposit_input = ProjectInput(**{**input.__dict__,
            "milestone_name": "Deposit (30%)", "amount_eur": input.amount_eur * 0.30})
        try:
            from proposal_queue import queue_proposal
            queue_proposal(
                customer_email=input.customer_email,
                project_title=f"INVOICE: {input.project_title} - Deposit EUR {input.amount_eur * 0.30:.0f}",
                proposal_summary=f"Awaiting Braun approval before sending to customer.",
                price_eur=input.amount_eur * 0.30,
                platform="direct",
            )
            result.actions.append("deposit_queued_for_approval")
        except (AiElevateError, Exception) as e:
            result.actions.append("deposit_queue_failed")
        if HAS_KG:
            record_payment(input.org, input.customer_email, input.project_slug,
                          "Deposit (30%)", input.amount_eur * 0.30, status="invoiced")

        _notify_ops("payment", f"Deposit invoice sent: {input.project_title} EUR {input.amount_eur * 0.30:.0f}",
                    agent="payment-workflow", customer_email=input.customer_email)

        result.status = "deposit_invoiced"
        result.output = f"Payment schedule created, deposit invoice sent (EUR {input.amount_eur * 0.30:.0f})"
        return result


# ============================================================================
# WORKFLOW 6: Post-Delivery Support
# ============================================================================

@activity.defn
async def create_support_ticket(input: ProjectInput) -> int:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    # Check if within 30-day warranty
    conn = _db()
    cur = conn.cursor()
    cur.execute(
        "SELECT completed_at FROM project_milestones "
        "WHERE customer_email=%s AND milestone='Deployment' AND status='completed' "
        "ORDER BY completed_at DESC LIMIT 1",
        (input.customer_email,))
    row = cur.fetchone()
    within_warranty = True
    if row and row[0]:
        delivery_date = row[0]
        within_warranty = (datetime.now(timezone.utc) - delivery_date).days <= 30

    cur.execute(
        "INSERT INTO support_tickets (customer_email, project_slug, description, within_warranty) "
        "VALUES (%s,%s,%s,%s) RETURNING id",
        (input.customer_email, input.project_slug, input.bug_description, within_warranty))
    tid = cur.fetchone()[0]
    conn.close()

    if not within_warranty:
        log.info(f"Support ticket {tid} is outside 30-day warranty")
    return tid

@activity.defn
async def qa_triage_bug(input: ProjectInput) -> str:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch_and_wait(
        "gigforge-qa",
        f"BUG TRIAGE for: {input.project_title}\n"
        f"Project dir: {project_dir}\n\n"
        f"Customer reported:\n{input.bug_description}\n\n"
        f"1. Can you reproduce the issue?\n"
        f"2. What's the severity? (critical / high / medium / low)\n"
        f"3. What component is affected? (backend / frontend / infra)\n"
        f"4. Is this a regression from a recent change?\n"
        f"5. Suggested fix approach\n\n"
        f"Write your triage to {project_dir}/BUG_TRIAGE.md",
        timeout=300
    ))

@activity.defn
async def engineer_fix_bug(input: ProjectInput) -> str:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch_and_wait(
        "gigforge-engineer",
        f"BUG FIX for: {input.project_title}\n"
        f"Project dir: {project_dir}\n\n"
        f"Read QA triage at {project_dir}/BUG_TRIAGE.md\n\n"
        f"Bug report:\n{input.bug_description}\n\n"
        f"Fix the bug. Update CHANGELOG.md.\n"
        f"Verify the fix doesn't break anything else.\n"
        f"Rebuild: docker compose build",
        timeout=600
    ))

@activity.defn
async def send_bug_fix_notification(input: ProjectInput) -> bool:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    name = _customer_first_name(input.customer_email)
    advocate_name, _ = _get_agent_name(f"{input.org}-advocate")
    return _send_email(
        to=input.customer_email,
        subject=f"Bug fix deployed — {input.project_title}",
        body=f"Hi {name},\n\n"
             f"We've fixed the issue you reported and deployed the update.\n\n"
             f"Issue: {input.bug_description[:200]}\n\n"
             f"The fix is live now. Please check and let us know if everything "
             f"is working as expected.\n\n"
             f"Best regards,\n{advocate_name}",
        agent_id=f"{input.org}-advocate",
    )

@activity.defn
async def close_support_ticket(input: ProjectInput) -> bool:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    conn = _db()
    conn.cursor().execute(
        "UPDATE support_tickets SET status='resolved', resolved_at=NOW() "
        "WHERE customer_email=%s AND project_slug=%s AND status='open' "
        "ORDER BY created_at DESC LIMIT 1",
        (input.customer_email, input.project_slug))
    conn.close()
    return True


@workflow.defn
class PostDeliverySupportWorkflow:
    """Bug report → QA triage → engineer fix → redeploy → notify customer."""

    @workflow.run
    async def run(self, input: ProjectInput) -> WorkflowResult:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        result = WorkflowResult()
        tl = timedelta(seconds=660)
        ts = timedelta(seconds=60)

        ticket_id = await workflow.execute_activity(create_support_ticket, input,
            start_to_close_timeout=ts, retry_policy=RETRY_FAST)
        result.actions.append(f"ticket_{ticket_id}")
        if HAS_KG:
            record_bug_report(input.org, input.customer_email, input.project_slug,
                             input.bug_description, ticket_id)

        _notify_ops("support_ticket", f"Bug report: {input.project_title} — {input.bug_description[:100]}",
                    agent="support-workflow", customer_email=input.customer_email)

        # QA triages
        triage = await workflow.execute_activity(qa_triage_bug, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.actions.append("qa_triaged")

        # Engineer fixes
        fix = await workflow.execute_activity(engineer_fix_bug, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.actions.append("engineer_fixed")
        if HAS_KG:
            record_bug_fix(input.org, input.project_slug, ticket_id, f"{input.org}-engineer")

        # Redeploy
        deploy = await workflow.execute_activity(redeploy_preview, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.actions.append("redeployed")

        # Notify customer
        await workflow.execute_activity(send_bug_fix_notification, input,
            start_to_close_timeout=ts, retry_policy=RETRY_FAST)
        result.actions.append("customer_notified")

        # Close ticket
        await workflow.execute_activity(close_support_ticket, input,
            start_to_close_timeout=ts, retry_policy=RETRY_FAST)
        result.actions.append("ticket_closed")

        result.status = "resolved"
        result.output = f"Bug fixed and deployed (ticket #{ticket_id})"
        return result


# ============================================================================
# WORKFLOW 7: Project Closure
# ============================================================================

@activity.defn
async def create_closure_record(input: ProjectInput) -> int:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    conn = _db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO project_closures (customer_email, project_slug) "
        "VALUES (%s,%s) RETURNING id",
        (input.customer_email, input.project_slug))
    cid = cur.fetchone()[0]
    conn.close()
    return cid

@activity.defn
async def generate_handover_docs(input: ProjectInput) -> str:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch_and_wait(
        "gigforge-engineer",
        f"GENERATE HANDOVER DOCUMENTATION for: {input.project_title}\n"
        f"Project dir: {project_dir}\n\n"
        f"Create {project_dir}/HANDOVER.md covering:\n\n"
        f"1. Access credentials (where they are, how to rotate)\n"
        f"2. Environment variables and their purpose\n"
        f"3. How to run locally (dev setup)\n"
        f"4. How to deploy to production\n"
        f"5. Database backup/restore procedures\n"
        f"6. Monitoring and health checks\n"
        f"7. Known limitations and technical debt\n"
        f"8. Contact info for support during warranty period\n"
        f"9. Recommended next steps (features to add, scaling considerations)\n\n"
        f"This is the document the customer's team will use to maintain the app.",
        timeout=300
    ))

@activity.defn
async def deploy_to_production(input: ProjectInput) -> str:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch_and_wait(
        "gigforge-devops",
        f"PRODUCTION DEPLOYMENT for: {input.project_title}\n"
        f"Project dir: {project_dir}\n"
        f"Customer: {input.customer_email}\n\n"
        f"If the customer has provided a production domain:\n"
        f"  1. Update nginx config for the production domain\n"
        f"  2. Set up SSL with certbot if not already done\n"
        f"  3. Promote the preview to production\n\n"
        f"If no production domain yet:\n"
        f"  1. Ensure the preview is stable and production-ready\n"
        f"  2. Document what the customer needs to do for DNS\n\n"
        f"Report the production URL.",
        timeout=300
    ))

@activity.defn
async def generate_case_study(input: ProjectInput) -> str:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch_and_wait(
        "gigforge-creative",
        f"GENERATE CASE STUDY for: {input.project_title}\n"
        f"Customer: {input.customer_email}\n"
        f"Project dir: /opt/ai-elevate/gigforge/projects/{input.project_slug}\n\n"
        f"Read the SOFTWARE_SPEC.md, TECH_STACK.md, and README.md.\n\n"
        f"Write a case study for the GigForge portfolio:\n"
        f"1. Client challenge (what problem they had)\n"
        f"2. Our approach (how we solved it)\n"
        f"3. Technical highlights (interesting implementation details)\n"
        f"4. Results (what the client got)\n"
        f"5. Client quote placeholder (to be filled with real testimonial)\n\n"
        f"Save to /opt/ai-elevate/gigforge/memory/case-studies/{input.project_slug}.md",
        timeout=300
    ))

@activity.defn
async def request_testimonial(input: ProjectInput) -> bool:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    name = _customer_first_name(input.customer_email)
    advocate_name, _ = _get_agent_name(f"{input.org}-advocate")
    return _send_email(
        to=input.customer_email,
        subject=f"Thank you — would you recommend us?",
        body=f"Hi {name},\n\n"
             f"Now that your {input.project_title} project is complete, we'd love to hear "
             f"how the experience was.\n\n"
             f"Would you be willing to share a brief testimonial? Even a sentence or two "
             f"about your experience working with us would mean a lot.\n\n"
             f"If you're happy with what we built, a review on our GigForge profile "
             f"would also be incredibly helpful.\n\n"
             f"Thank you for choosing GigForge. We're here for the next 30 days "
             f"if anything comes up with the app.\n\n"
             f"Best regards,\n{advocate_name}",
        agent_id=f"{input.org}-advocate",
    )

@activity.defn
async def archive_project(input: ProjectInput) -> bool:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    try:
        from cms_workflows import store_progress_report
        store_progress_report(
            input.org, input.customer_email, input.project_title,
            "Project completed and closed", "completed")
    except (AgentError, Exception) as e:
        pass

    conn = _db()
    conn.cursor().execute(
        "UPDATE project_closures SET archived=TRUE, case_study_generated=TRUE, "
        "testimonial_requested=TRUE, status='completed', completed_at=NOW() "
        "WHERE customer_email=%s AND project_slug=%s",
        (input.customer_email, input.project_slug))
    conn.close()

    _notify_ops("project_complete", f"Project closed: {input.project_title}",
                agent="closure-workflow", customer_email=input.customer_email)
    return True

@activity.defn
async def send_delivery_complete(input: ProjectInput) -> bool:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    name = _customer_first_name(input.customer_email)
    advocate_name, advocate_title = _get_agent_name(f"{input.org}-advocate")
    return _send_email(
        to=input.customer_email,
        subject=f"Project complete — {input.project_title}",
        body=f"Hi {name},\n\n"
             f"Your {input.project_title} project is now complete and delivered.\n\n"
             f"What's included:\n"
             f"- Full source code\n"
             f"- Documentation (README, RUNBOOK, Software Spec)\n"
             f"- Handover document with everything your team needs to maintain it\n"
             f"- 30 days of bug fix support from today\n\n"
             f"If anything comes up during the support period, just email us.\n\n"
             f"It's been a pleasure working with you.\n\n"
             f"Best regards,\n{advocate_name}\n{advocate_title}, GigForge",
        agent_id=f"{input.org}-advocate",
    )


@workflow.defn
class ProjectClosureWorkflow:
    """Handover docs → production deploy → delivery email → case study → testimonial → archive."""

    @workflow.run
    async def run(self, input: ProjectInput) -> WorkflowResult:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        result = WorkflowResult()
        tl = timedelta(seconds=660)
        ts = timedelta(seconds=60)

        await workflow.execute_activity(create_closure_record, input,
            start_to_close_timeout=ts, retry_policy=RETRY_FAST)
        result.actions.append("closure_started")

        # Generate handover documentation
        await workflow.execute_activity(generate_handover_docs, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.actions.append("handover_docs")

        # Deploy to production (or confirm preview is production-ready)
        await workflow.execute_activity(deploy_to_production, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.actions.append("production_deployed")

        # Send delivery complete email
        await workflow.execute_activity(send_delivery_complete, input,
            start_to_close_timeout=ts, retry_policy=RETRY_FAST)
        result.actions.append("delivery_email_sent")

        # Generate case study for portfolio
        await workflow.execute_activity(generate_case_study, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.actions.append("case_study_generated")

        # Request testimonial (wait a bit so it doesn't feel like a blast)
        await asyncio.sleep(60)
        await workflow.execute_activity(request_testimonial, input,
            start_to_close_timeout=ts, retry_policy=RETRY_FAST)
        result.actions.append("testimonial_requested")

        # Archive
        await workflow.execute_activity(archive_project, input,
            start_to_close_timeout=ts, retry_policy=RETRY_FAST)
        result.actions.append("archived")
        if HAS_KG:
            record_closure(input.org, input.customer_email, input.project_slug)
            record_case_study(input.org, input.project_slug)

        result.status = "completed"
        result.output = "Project closed, archived, case study generated"
        return result


# ============================================================================
# Client functions — start any workflow
# ============================================================================

async def start_revision(customer_email, project_title, project_slug, revision_number, change_description, org="gigforge"):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    from temporalio.client import Client
    client = await Client.connect("localhost:7233")
    input_data = ProjectInput(customer_email=customer_email, project_title=project_title,
        project_slug=project_slug, org=org, revision_number=revision_number,
        change_description=change_description)
    h = await client.start_workflow(RevisionCycleWorkflow.run, input_data,
        id=f"revision-{project_slug}-r{revision_number}-{int(time.time())}",
        task_queue="project-workflows", execution_timeout=timedelta(hours=2))
    return {"workflow_id": h.id, "status": "started"}

async def start_scope_change(customer_email, project_title, project_slug, change_description, org="gigforge"):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    from temporalio.client import Client
    client = await Client.connect("localhost:7233")
    input_data = ProjectInput(customer_email=customer_email, project_title=project_title,
        project_slug=project_slug, org=org, change_description=change_description,
        revision_number=int(time.time()) % 10000)
    h = await client.start_workflow(ScopeChangeWorkflow.run, input_data,
        id=f"scope-change-{project_slug}-{int(time.time())}",
        task_queue="project-workflows", execution_timeout=timedelta(hours=2))
    return {"workflow_id": h.id, "status": "started"}

async def start_onboarding(customer_email, project_title, project_slug, requirements="", org="gigforge"):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    from temporalio.client import Client
    client = await Client.connect("localhost:7233")
    input_data = ProjectInput(customer_email=customer_email, project_title=project_title,
        project_slug=project_slug, org=org, requirements=requirements)
    h = await client.start_workflow(ClientOnboardingWorkflow.run, input_data,
        id=f"onboard-{project_slug}-{int(time.time())}",
        task_queue="project-workflows", execution_timeout=timedelta(hours=1))
    return {"workflow_id": h.id, "status": "started"}

async def start_proposal(customer_email, project_title, project_slug, job_data="", amount_eur=0, org="gigforge"):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    from temporalio.client import Client
    client = await Client.connect("localhost:7233")
    input_data = ProjectInput(customer_email=customer_email, project_title=project_title,
        project_slug=project_slug, org=org, job_data=job_data, amount_eur=amount_eur)
    h = await client.start_workflow(ProposalToContractWorkflow.run, input_data,
        id=f"proposal-{project_slug}-{int(time.time())}",
        task_queue="project-workflows", execution_timeout=timedelta(hours=2))
    return {"workflow_id": h.id, "status": "started"}

async def start_payment_milestones(customer_email, project_title, project_slug, amount_eur, org="gigforge"):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    from temporalio.client import Client
    client = await Client.connect("localhost:7233")
    input_data = ProjectInput(customer_email=customer_email, project_title=project_title,
        project_slug=project_slug, org=org, amount_eur=amount_eur)
    h = await client.start_workflow(PaymentMilestoneWorkflow.run, input_data,
        id=f"payment-{project_slug}-{int(time.time())}",
        task_queue="project-workflows", execution_timeout=timedelta(hours=24))
    return {"workflow_id": h.id, "status": "started"}

async def start_support(customer_email, project_title, project_slug, bug_description, org="gigforge"):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    from temporalio.client import Client
    client = await Client.connect("localhost:7233")
    input_data = ProjectInput(customer_email=customer_email, project_title=project_title,
        project_slug=project_slug, org=org, bug_description=bug_description)
    h = await client.start_workflow(PostDeliverySupportWorkflow.run, input_data,
        id=f"support-{project_slug}-{int(time.time())}",
        task_queue="project-workflows", execution_timeout=timedelta(hours=4))
    return {"workflow_id": h.id, "status": "started"}

async def start_closure(customer_email, project_title, project_slug, org="gigforge"):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    from temporalio.client import Client
    client = await Client.connect("localhost:7233")
    input_data = ProjectInput(customer_email=customer_email, project_title=project_title,
        project_slug=project_slug, org=org)
    h = await client.start_workflow(ProjectClosureWorkflow.run, input_data,
        id=f"closure-{project_slug}-{int(time.time())}",
        task_queue="project-workflows", execution_timeout=timedelta(hours=4))
    return {"workflow_id": h.id, "status": "started"}
