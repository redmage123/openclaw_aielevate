#!/usr/bin/env python3
"""Temporal Workflows  - migrated from custom workflow engine.

Each workflow script becomes a Temporal Activity.
Each agent manifest becomes a Temporal Workflow.
The workflow engine REST API is replaced by Temporal Client calls.

Architecture:
  Gateway → Temporal Client → starts workflow
  Temporal Server → executes activities in order (with retry, timeout, durability)
  Worker → runs the activity functions

Activities are the same functions from workflow_engine.py, wrapped as Temporal activities.
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import re
import time
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Optional

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.worker import Worker
from temporalio.common import RetryPolicy
from dao import BaseDAO  # TODO: Replace inline DB calls with specific DAOs
from logging_config import get_logger  # Structured JSON logging
from exceptions import AiElevateError  # TODO: Use specific exception types, AgentError, DatabaseError
AgentError = Exception  # placeholder until agent_dispatch exports AgentError
import psycopg2
DatabaseError = psycopg2.DatabaseError
import argparse

sys.path.insert(0, "/home/aielevate")

try:
    from workflow_kg import customer_journey, record_inquiry
    HAS_KG = True
except ImportError:
    HAS_KG = False

log = get_logger("temporal-workflows")

# ============================================================================
# Data classes
# ============================================================================

@dataclass
class InteractionInput:
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    agent_id: str
    message: str
    sender_email: str
    subject: str
    timeout: int = 300


@dataclass
class InteractionResult:
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    agent_id: str = ""
    stdout: str = ""
    returncode: int = 0
    duration: int = 0
    sentiment: str = "neutral"
    response_agent: str = ""
    actions: list = None
    notified: list = None
    can_contact_customer: bool = True
    suppress_reply: bool = False

    def __post_init__(self):
        if self.actions is None:
            self.actions = []
        if self.notified is None:
            self.notified = []


# ============================================================================
# Helpers (shared by activities)
# ============================================================================

POSITIVE = ["thanks", "great", "love", "perfect", "excellent", "lets go ahead",
            "let's go ahead", "i accept", "go ahead", "approved", "sounds good"]
FRUSTRATED = ["disappointed", "frustrated", "unacceptable", "not happy", "terrible",
              "still waiting", "speak to someone", "waste of time"]
ACCEPTANCE = ["lets go ahead", "let's go ahead", "i accept", "go ahead", "lets proceed",
              "let's proceed", "approved", "start the project", "when can you start"]

def _detect_sentiment(text: str) -> tuple:
    lower = text.lower()
    if any(kw in lower for kw in FRUSTRATED): return "frustrated", "Customer expressed frustration"
    if any(kw in lower for kw in ACCEPTANCE): return "positive", "Customer accepted"
    if any(kw in lower for kw in POSITIVE): return "positive", "Positive tone"
    return "neutral", "Standard inquiry"

def _detect_acceptance(text: str) -> bool:
    return any(kw in text.lower() for kw in ACCEPTANCE)

def _get_org(agent_id: str) -> str:
    if agent_id.startswith("techuni"): return "techuni"
    return "gigforge"

def _dispatch(agent_id: str, message: str):
    subprocess.Popen(
        ["agent-queue", "--agent", agent_id, "--message", message,
         "--thinking", "low", "--timeout", "180"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

# ============================================================================
# Activities  - each is a durable, retryable unit of work
# ============================================================================

def _extract_email_body(message):
    """Extract just the email body, excluding injected context."""
    markers = ["\nCustomer context:", "\nTEAM ROSTER", "\nKG data:", "\nDB records:"]
    for marker in markers:
        if marker in message:
            return message[:message.index(marker)].strip()
    return message[:500]


# Agents allowed to email customers directly (others relay through CS/advocate)
DIRECT_CONTACT_AGENTS = {
    "gigforge-sales", "gigforge-advocate", "gigforge-csat", "gigforge-cs",
    "techuni-sales", "techuni-advocate", "techuni-csat", "techuni-cs",
    "operations",
}


@activity.defn
async def gate_customer_contact(input: InteractionInput) -> dict:
    """Check if this agent can email the customer directly."""
    return {
        "can_contact": input.agent_id in DIRECT_CONTACT_AGENTS,
        "agent_id": input.agent_id,
    }


@activity.defn
async def pull_customer_context(input: InteractionInput) -> str:
    """Pull customer context and return it for message enhancement."""
    try:
        from customer_context import context_summary
        ctx = context_summary(input.sender_email)
        return ctx if ctx and len(ctx) > 30 else ""
    except (AgentError, Exception) as e:
        return ""


@activity.defn
async def check_directives(input: InteractionInput) -> str:
    """Get active owner directives."""
    try:
        from directives import directives_summary
        d = directives_summary()
        return d if d else ""
    except (AgentError, Exception) as e:
        return ""


@activity.defn
async def read_kg(input: InteractionInput) -> str:
    """Read customer data from knowledge graph + semantic search across all sources."""
    parts = []
    org = _get_org(input.agent_id)

    # 1. KG lookup by sender
    try:
        from knowledge_graph import KG
        kg = KG(org)
        results = kg.search(input.sender_email)
        if results:
            parts.append("KG data:\n" + "\n".join(str(r)[:200] for r in results[:5]))
    except Exception:
        pass

    # 2. Semantic search across all data sources (emails, proposals, milestones, KG)
    try:
        from semantic_search import get_context_for_email
        context = get_context_for_email(
            input.sender_email, input.subject, input.message, org=org, max_results=8)
        if context:
            parts.append(context)
    except Exception:
        pass

    return "\n".join(parts) if parts else ""
    except (AgentError, Exception) as e:
        pass
    return ""


@activity.defn
async def read_db(input: InteractionInput) -> str:
    """Read customer records from Postgres."""
    try:
        import psycopg2, psycopg2.extras
        conn = psycopg2.connect(
            host=os.environ.get("DB_HOST", "127.0.0.1"),
            port=int(os.environ.get("DB_PORT", "5434")),
            dbname=os.environ.get("DB_NAME", "rag"),
            user=os.environ.get("DB_USER", "rag"),
            password=os.environ.get("DB_PASSWORD", "rag_vec_2026"),
        )
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        parts = []

        cur.execute("SELECT id,milestone,amount_eur,status FROM invoices WHERE customer_email=%s", (input.sender_email,))
        invoices = cur.fetchall()
        if invoices:
            parts.append("Invoices: " + ", ".join(f"#{i['id']} {i['milestone']} EUR{i['amount_eur']:.0f} ({i['status']})" for i in invoices))

        cur.execute("SELECT milestone,status FROM project_milestones WHERE customer_email=%s", (input.sender_email,))
        milestones = cur.fetchall()
        if milestones:
            parts.append("Milestones: " + ", ".join(f"{m['milestone']}({m['status']})" for m in milestones))

        conn.close()
        return "\n".join(parts)
    except (DatabaseError, Exception) as e:
        return ""


TEAM_ROSTER = """TEAM ROSTER (use ONLY these names and titles  - never invent names):
  Sam Carrington  - Sales Lead (gigforge-sales)
  Jordan Whitaker  - Customer Delivery Liaison (gigforge-advocate)
  Jamie Okafor  - Project Manager (gigforge-pm)
  Chris Novak  - Lead Engineer / CTO (gigforge-engineer)
  Casey Muller  - DevOps Engineer (gigforge-devops)
  Riley Svensson  - QA Engineer (gigforge-qa)
  Alex Reeves  - Operations Director (gigforge)
  Avery Tanaka  - Director of Customer Satisfaction (gigforge-csat)
  Pat Eriksen  - Finance Manager (gigforge-finance)
  Dana Whitmore  - Legal Counsel (gigforge-legal)
  Quinn Azevedo  - Business Development Scout (gigforge-scout)
  Drew Fontaine  - Creative Director (gigforge-creative)
  Morgan Dell  - Social Media Marketer (gigforge-social)
  Taylor Brooks  - Customer Support (gigforge-support)
  Kai Sorensen  - Operations Agent (operations)

RULES:
- You are the agent identified in your AGENTS.md. Use YOUR name only.
- When referring to other team members, use ONLY names from this roster.
- NEVER invent names. If you don't know who handles something, say "our team" not a made-up name.
- NEVER claim a title or role that isn't yours.
- NEVER make pricing or timeline commitments unless explicitly authorized in your AGENTS.md.
"""


TECHUNI_ROSTER = """TEAM ROSTER  - TechUni (use ONLY these names and titles):


  Robin Callister  - CEO (techuni-ceo)
  Ellis Kovac  - Sales Lead (techuni-sales)
  Sam Nakamura  - Customer Delivery Liaison (techuni-advocate)
  Avery Nakamura  - Director of Customer Satisfaction (techuni-csat)
  Chris Park  - Lead Engineer / CTO (techuni-engineering)
  Jordan Patel  - DevOps Engineer (techuni-devops)
  Cameron Zhao  - Project Manager (techuni-pm)
  Finley Gomes  - QA Engineer (techuni-qa)
  Pat Chen  - Finance Manager (techuni-finance)
  Dana Leclerc  - Legal Counsel (techuni-legal)
  Morgan Ellis  - Marketing Director (techuni-marketing)
  Blake Moreno  - Social Media Marketer (techuni-social)
  Reese Tanaka  - Customer Support (techuni-support)
  Sasha Koval  - UX Designer (techuni-ux-designer)
  Mika Torres  - Frontend Developer (techuni-dev-frontend)
  Arin Osei  - Backend Developer (techuni-dev-backend)
  Yuki Sato  - AI Engineer (techuni-dev-ai)

RULES:
- Use YOUR name only. Never invent names.
- When referring to team members, use ONLY names from this roster.
"""

AI_ELEVATE_ROSTER = """
TEAM ROSTER - AI Elevate (use ONLY these names and titles):
  Arin Blackwell - Director of Operations (ai-elevate)
  Kai Sorensen - Operations Agent (operations)
  Remy Volkov - CISO (cybersecurity)
  Luca Fontana - Content Strategist (ai-elevate-content)
  Noor Abbasi - Senior Writer (ai-elevate-writer)
  Cleo Marchetti - Editor-in-Chief (ai-elevate-editor)
  Tomas Lindgren - Research Lead (ai-elevate-researcher)
  Priya Mehta - Fact Checker (ai-elevate-factchecker)
  Jin Takahashi - Publishing Manager (ai-elevate-publisher)
  Dana Vasquez - Legal Counsel (ai-elevate-legal)
  Elin Strand - Finance (ai-elevate-billing)
  Sven Eriksen - Infrastructure Monitor (ai-elevate-monitor)

  NOTE: AI Elevate is non-commercial. No sales, no client billing.

RULES:
- Use YOUR name only. Never invent names.
- When referring to team members, use ONLY names from this roster.
"""

@activity.defn
async def call_llm(input: InteractionInput) -> dict:
    """Call the openclaw agent LLM."""
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    start = time.time()
    try:
        from agent_dispatch import async_dispatch
        result = await async_dispatch(input.agent_id, input.message, timeout=input.timeout)
        return {"stdout": result.output, "returncode": result.exit_code,
                "duration": int(result.duration), "status": result.status}
    except ImportError:
        # Fallback to direct subprocess
        try:
            proc = await asyncio.get_event_loop().run_in_executor(None, lambda: subprocess.run(
                ["openclaw", "agent", "--agent", input.agent_id,
                 "--message", input.message, "--thinking", "low", "--timeout", str(input.timeout)],
                capture_output=True, text=True, timeout=input.timeout + 30, env=env,
            ))
            return {"stdout": proc.stdout or "", "returncode": proc.returncode, "duration": int(time.time() - start)}
        except subprocess.TimeoutExpired:
            return {"stdout": "", "returncode": -1, "duration": input.timeout}


@activity.defn
async def update_sentiment(input: InteractionInput) -> str:
    """Update customer sentiment  - analyze ONLY the raw email, not thread context."""
    # Strip any injected context (everything after "Customer context:" or "KG data:")
    raw_msg = input.message
    for marker in ["\nCustomer context:", "\nKG data:", "\nDB records:", "\nTEAM ROSTER"]:
        if marker in raw_msg:
            raw_msg = raw_msg[:raw_msg.index(marker)]
    # Also strip the "INBOUND EMAIL" prefix
    if "Email body:" in raw_msg:
        raw_msg = raw_msg[raw_msg.index("Email body:") + 11:]
    rating, reason = _detect_sentiment(raw_msg)
    try:
        from customer_context import update_sentiment as _update
        _update(input.sender_email, rating, reason, agent=input.agent_id)
        return rating
    except (DatabaseError, Exception) as e:
        return rating


@activity.defn
async def update_plane(input: InteractionInput) -> bool:
    """Update Plane ticket with interaction comment."""
    try:
        from plane_ops import Plane
        p = Plane(_get_org(input.agent_id))
        for proj in list(p.projects.keys())[:5]:
            try:
                issues = p.list_issues(proj)
                if isinstance(issues, dict): issues = issues.get("results", [])
                if isinstance(issues, list):
                    for issue in issues:
                        if input.sender_email in str(issue.get("description", "") or ""):
                            seq = issue.get("sequence_id")
                            if seq:
                                p.add_comment(proj, seq, f"Email from {input.sender_email} handled by {input.agent_id}")
                                return True
            except (DatabaseError, Exception) as e:
                continue
    except (AgentError, Exception) as e:
        pass
    return False


@activity.defn
async def update_kg(input: InteractionInput) -> bool:
    """Update customer entity in KG."""
    try:
        from knowledge_graph import KG
        kg = KG(_get_org(input.agent_id))
        cid = input.sender_email.replace("@", "_at_").replace(".", "_")
        props = {"email": input.sender_email, "last_agent": input.agent_id,
                "last_interaction": datetime.now(timezone.utc).isoformat()[:19]}
        try:
            kg.update("customer", cid, props)
        except (DatabaseError, Exception) as e:
            kg.add("customer", cid, props)
        return True
    except (DatabaseError, Exception) as e:
        return False


@activity.defn
async def add_customer_note(input: InteractionInput) -> bool:
    """Add interaction note."""
    try:
        from customer_context import add_note
        add_note(input.sender_email, f"Handled by {input.agent_id}: {input.subject[:40]}", agent=input.agent_id)
        return True
    except (DatabaseError, Exception) as e:
        return False


@activity.defn
async def create_plane_ticket(input: InteractionInput) -> bool:
    """Create Plane ticket for new customers."""
    try:
        from plane_ops import Plane
        p = Plane(_get_org(input.agent_id))
        for proj in ["TKT", "GFWEB"]:
            try:
                issues = p.list_issues(proj)
                if isinstance(issues, dict): issues = issues.get("results", [])
                if isinstance(issues, list):
                    for i in issues:
                        if input.sender_email in str(i.get("description", "") or ""):
                            return False  # Already exists
            except (AgentError, Exception) as e:
                continue
        p.create_issue(project="TKT", title=f"[LEAD] {input.sender_email}  - {input.subject[:40]}",
                      description=f"Customer: {input.sender_email}\nAgent: {input.agent_id}\nSubject: {input.subject}",
                      priority="medium")
        return True
    except (AgentError, Exception) as e:
        return False


@activity.defn
async def notify_ops(input: InteractionInput) -> bool:
    """Notify operations."""
    try:
        from ops_notify import ops_notify
        event = "new_project" if _detect_acceptance(input.message) else "status_update"
        rating, _ = _detect_sentiment(_extract_email_body(input.message))
        if rating in ("frustrated", "at_risk"): event = "escalation"
        ops_notify(event, f"{input.agent_id} handled {input.sender_email}: {input.subject[:40]}",
                  agent=input.agent_id, customer_email=input.sender_email)
        return True
    except (DatabaseError, Exception) as e:
        return False


@activity.defn
async def notify_cs(input: InteractionInput) -> bool:
    """Notify CS/Advocate."""
    if "advocate" in input.agent_id or "csat" in input.agent_id:
        return False
    advocate = "gigforge-advocate" if "gigforge" in input.agent_id or input.agent_id in ("operations",) else "techuni-advocate"
    _dispatch(advocate, f"NOTIFICATION from {input.agent_id}: {input.sender_email}  - {input.subject[:40]}")
    return True


@activity.defn
async def notify_pm(input: InteractionInput) -> bool:
    """Notify PM if active project."""
    try:
        from customer_context import get_context
        ctx = get_context(input.sender_email)
        if ctx and ctx.get("active_project"):
            pm = "gigforge-pm" if "gigforge" in input.agent_id else "techuni-pm"
            _dispatch(pm, f"CUSTOMER UPDATE from {input.agent_id}: {input.sender_email}  - {input.subject[:40]}")
            return True
    except (DatabaseError, Exception) as e:
        pass
    return False


@activity.defn
async def check_acceptance_handoff(input: InteractionInput) -> str:
    """Check for acceptance and trigger advocate handoff."""
    if _detect_acceptance(input.message) and "sales" in input.agent_id:
        advocate = "gigforge-advocate" if "gigforge" in input.agent_id else "techuni-advocate"
        _dispatch(advocate, f"HANDOFF FROM SALES: {input.sender_email} accepted. Subject: {input.subject}. Take over.")
        return advocate
    return ""


@activity.defn
async def check_frustration_escalation(input: InteractionInput) -> str:
    """Check for frustration and escalate to CSAT."""
    rating, _ = _detect_sentiment(_extract_email_body(input.message))
    if rating in ("frustrated", "at_risk"):
        csat = "gigforge-csat" if "gigforge" in input.agent_id else "techuni-csat"
        if csat != input.agent_id:
            _dispatch(csat, f"ESCALATION: {input.sender_email} is {rating}. Subject: {input.subject}")
            return csat
    return ""


@activity.defn
async def store_to_cms(input: InteractionInput) -> bool:
    """Archive interaction in CMS."""
    try:
        from cms_workflows import archive_email
        archive_email(_get_org(input.agent_id), input.sender_email, input.agent_id,
                     input.subject, input.message)
        return True
    except (AgentError, Exception) as e:
        return False


@activity.defn
async def relay_through_cs(input: InteractionInput) -> bool:
    """Relay non-authorized agent responses through CS."""
    # This is handled in the workflow logic, not as a standalone activity
    return True


# ============================================================================
# Workflows  - per-agent execution plans
# ============================================================================

RETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=5))


@workflow.defn
class EmailInteractionWorkflow:
    """Main workflow for handling an inbound email interaction."""

    @workflow.run
    async def run(self, input: InteractionInput) -> InteractionResult:
        """"""
        result = InteractionResult(agent_id=input.agent_id, response_agent=input.agent_id)

        # === PRE: Gate ===
        gate = await workflow.execute_activity(
            gate_customer_contact, input, start_to_close_timeout=timedelta(seconds=10), retry_policy=RETRY)
        result.can_contact_customer = gate["can_contact"]
        result.actions.append(f"gate:{'direct' if gate['can_contact'] else 'relay'}")

        # === PRE: Context ===
        ctx_text = await workflow.execute_activity(
            pull_customer_context, input, start_to_close_timeout=timedelta(seconds=15), retry_policy=RETRY)
        directives_text = await workflow.execute_activity(
            check_directives, input, start_to_close_timeout=timedelta(seconds=10), retry_policy=RETRY)
        kg_text = await workflow.execute_activity(
            read_kg, input, start_to_close_timeout=timedelta(seconds=15), retry_policy=RETRY)
        db_text = await workflow.execute_activity(
            read_db, input, start_to_close_timeout=timedelta(seconds=15), retry_policy=RETRY)

        # Enhance message
        roster = AI_ELEVATE_ROSTER if "ai-elevate" in input.agent_id or input.agent_id in ("operations", "cybersecurity") else TECHUNI_ROSTER if "techuni" in input.agent_id else TEAM_ROSTER
        enhanced = input.message + "\n\n" + roster
        if ctx_text: enhanced += f"\n\nCustomer context:\n{ctx_text}"
        if directives_text: enhanced += f"\n\n{directives_text}"
        if kg_text: enhanced += f"\n\nKG data:\n{kg_text}"
        if db_text: enhanced += f"\n\nDB records:\n{db_text}"

        enhanced_input = InteractionInput(
            agent_id=input.agent_id, message=enhanced,
            sender_email=input.sender_email, subject=input.subject, timeout=input.timeout)

        # === MAIN: Call LLM ===
        llm_result = await workflow.execute_activity(
            call_llm, enhanced_input, start_to_close_timeout=timedelta(seconds=input.timeout + 60), retry_policy=RETRY)
        result.stdout = llm_result.get("stdout", "")
        result.returncode = llm_result.get("returncode", -1)
        result.duration = llm_result.get("duration", 0)
        result.actions.append(f"llm:{result.duration}s")

        # === POST: All updates in parallel ===
        sentiment_task = workflow.execute_activity(
            update_sentiment, input, start_to_close_timeout=timedelta(seconds=15), retry_policy=RETRY)
        plane_task = workflow.execute_activity(
            update_plane, input, start_to_close_timeout=timedelta(seconds=20), retry_policy=RETRY)
        kg_task = workflow.execute_activity(
            update_kg, input, start_to_close_timeout=timedelta(seconds=15), retry_policy=RETRY)
        note_task = workflow.execute_activity(
            add_customer_note, input, start_to_close_timeout=timedelta(seconds=10), retry_policy=RETRY)
        ticket_task = workflow.execute_activity(
            create_plane_ticket, input, start_to_close_timeout=timedelta(seconds=20), retry_policy=RETRY)
        ops_task = workflow.execute_activity(
            notify_ops, input, start_to_close_timeout=timedelta(seconds=15), retry_policy=RETRY)
        cms_task = workflow.execute_activity(
            store_to_cms, input, start_to_close_timeout=timedelta(seconds=15), retry_policy=RETRY)

        # Wait for all
        sentiment = await sentiment_task
        result.sentiment = sentiment
        result.actions.append(f"sentiment:{sentiment}")

        if await plane_task: result.actions.append("plane_updated")
        if await kg_task: result.actions.append("kg_updated")
        if await note_task: result.actions.append("note_added")
        if await ticket_task: result.actions.append("ticket_created")
        if await ops_task: result.actions.append("ops_notified")
        if await cms_task: result.actions.append("cms_archived")

        # === POST: Notifications (sequential  - depend on sentiment) ===
        if await workflow.execute_activity(notify_cs, input, start_to_close_timeout=timedelta(seconds=15)):
            result.notified.append("cs")
        if await workflow.execute_activity(notify_pm, input, start_to_close_timeout=timedelta(seconds=15)):
            result.notified.append("pm")

        # === POST: Acceptance/frustration checks ===
        handoff = await workflow.execute_activity(
            check_acceptance_handoff, input, start_to_close_timeout=timedelta(seconds=15))
        if handoff:
            result.response_agent = handoff
            result.actions.append(f"handoff:{handoff}")

        escalation = await workflow.execute_activity(
            check_frustration_escalation, input, start_to_close_timeout=timedelta(seconds=15))
        if escalation:
            result.response_agent = escalation
            result.actions.append(f"escalation:{escalation}")

        # === POST: Relay gate ===
        if not result.can_contact_customer:
            result.suppress_reply = True
            result.actions.append("reply_suppressed:relay_through_cs")

        return result


# ============================================================================
# Worker + Client
# ============================================================================

async def start_worker():
    """Start the Temporal worker for email interactions."""
    client = await Client.connect("localhost:7233")

    email_worker = Worker(
        client,
        task_queue="email-interactions",
        workflows=[EmailInteractionWorkflow],
        activities=[
            gate_customer_contact, pull_customer_context, check_directives,
            read_kg, read_db, call_llm, update_sentiment, update_plane,
            update_kg, add_customer_note, create_plane_ticket, notify_ops,
            notify_cs, notify_pm, check_acceptance_handoff,
            check_frustration_escalation, store_to_cms, relay_through_cs,
        ],
    )
    log.info("Temporal email worker started on task queue: email-interactions")
    await email_worker.run()


async def execute_workflow(agent_id: str, message: str, sender_email: str,
                          subject: str, timeout: int = 300) -> dict:
    """"""
    """Execute an email interaction workflow via Temporal."""
    client = await Client.connect("localhost:7233")
    input_data = InteractionInput(
        agent_id=agent_id, message=message,
        sender_email=sender_email, subject=subject, timeout=timeout)

    result = await client.execute_workflow(
        EmailInteractionWorkflow.run,
        input_data,
        id=f"email-{agent_id}-{int(time.time())}",
        task_queue="email-interactions",
        execution_timeout=timedelta(seconds=timeout + 120),
    )

    return {
        "stdout": result.stdout,
        "returncode": result.returncode,
        "duration": result.duration,
        "sentiment": result.sentiment,
        "response_agent": result.response_agent,
        "actions": result.actions,
        "notified": result.notified,
        "can_contact_customer": result.can_contact_customer,
        "suppress_reply": result.suppress_reply,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Temporal Workflows")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose output")
    args = parser.parse_args()

    # logging.basicConfig removed — using get_logger() from logging_config
    asyncio.run(start_worker())
