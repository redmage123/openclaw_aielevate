#!/usr/bin/env python3
from logging_config import get_logger  # Structured JSON logging
from exceptions import AiElevateError  # TODO: Use specific exception types
"""Workflow Engine — per-agent orchestrator manifests with ordered script execution.

Each agent has a manifest defining what scripts to run, in what order, with conditions.
The engine loads the manifest, executes scripts sequentially, and determines the
response agent.

Manifests stored in Postgres (agent_manifests table).
Scripts are Python functions registered in the SCRIPT_REGISTRY.

Architecture:
  Gateway → workflow_engine.execute(agent_id, message, sender, subject)
    → loads manifest for agent_id
    → runs pre scripts in order
    → runs main (call LLM)
    → runs post scripts in order
    → determines response agent (may differ from handling agent)
    → returns result

REST API on port 8068.
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel
from dao import MilestoneDAO  # TODO: Replace inline DB calls with DAO methods
import argparse

sys.path.insert(0, "/home/aielevate")

app = FastAPI(title="Workflow Engine")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [workflow] %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("/var/log/openclaw/shared/workflow-engine.log"),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger("workflow")

# ============================================================================
# Script Registry — all available workflow scripts
# ============================================================================

def pull_customer_context(ctx: dict) -> dict:
    """Inject customer context into the message."""
    try:
        from customer_context import context_summary
        cs = context_summary(ctx["sender"])
        if cs and len(cs) > 30:
            ctx["enhanced_message"] += f"\n\nCustomer context:\n{cs}"
            ctx["actions"].append("context_injected")
    except Exception as e:
        ctx["actions"].append(f"context_error:{e}")
    return ctx


def check_directives(ctx: dict) -> dict:
    """Inject active directives."""
    try:
        from directives import directives_summary
        d = directives_summary()
        if d:
            ctx["enhanced_message"] += f"\n\n{d}"
            ctx["actions"].append("directives_checked")
    except Exception:
        pass
    return ctx


def check_proposal_queue(ctx: dict) -> dict:
    """Check if this is a proposal approval reply."""
    try:
        from proposal_queue import process_approval_email
        body = ctx.get("message", "")
        if "APPROVE" in body.upper() or "REJECT" in body.upper():
            actions = process_approval_email(ctx["sender"], body)
            if actions:
                ctx["actions"].append(f"proposal_actions:{len(actions)}")
                ctx["skip_llm"] = True  # No need to call LLM for approval replies
                ctx["stdout"] = f"Processed {len(actions)} proposal actions."
    except Exception:
        pass
    return ctx


def call_llm(ctx: dict) -> dict:
    """Call the openclaw agent LLM."""
    if ctx.get("skip_llm"):
        return ctx

    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    start = time.time()
    try:
        proc = subprocess.run(
            ["openclaw", "agent", "--agent", ctx["agent_id"],
             "--message", ctx["enhanced_message"],
             "--thinking", "low", "--timeout", str(ctx.get("timeout", 300))],
            capture_output=True, text=True, timeout=ctx.get("timeout", 300) + 30, env=env,
        )
        ctx["stdout"] = proc.stdout or ""
        ctx["returncode"] = proc.returncode
    except subprocess.TimeoutExpired:
        ctx["stdout"] = ""
        ctx["returncode"] = -1
    ctx["duration"] = int(time.time() - start)
    ctx["actions"].append(f"llm:{ctx['duration']}s")
    return ctx


def update_sentiment(ctx: dict) -> dict:
    """Update customer sentiment based on email tone."""
    try:
        from customer_context import update_sentiment as _update
        rating, reason = _detect_sentiment(ctx["message"])
        _update(ctx["sender"], rating, reason, agent=ctx["agent_id"])
        ctx["sentiment"] = rating
        ctx["actions"].append(f"sentiment:{rating}")
    except Exception as e:
        ctx["actions"].append(f"sentiment_error:{e}")
    return ctx


def update_plane(ctx: dict) -> dict:
    """Add comment to customer's Plane ticket."""
    try:
        from plane_ops import Plane
        org = _get_org(ctx["agent_id"])
        p = Plane(org)
        for proj in list(p.projects.keys())[:5]:
            try:
                issues = p.list_issues(proj)
                if isinstance(issues, dict): issues = issues.get("results", [])
                if isinstance(issues, list):
                    for issue in issues:
                        if ctx["sender"] in str(issue.get("description", "") or ""):
                            seq = issue.get("sequence_id")
                            if seq:
                                p.add_comment(proj, seq,
                                    f"Email handled by {ctx['agent_id']}. "
                                    f"Sentiment: {ctx.get('sentiment', '?')}. "
                                    f"Subject: {ctx['subject'][:40]}")
                                ctx["actions"].append(f"plane:{proj}-{seq}")
                                return ctx
            except Exception:
                continue
    except Exception as e:
        ctx["actions"].append(f"plane_error:{e}")
    return ctx


def update_kg(ctx: dict) -> dict:
    """Update customer entity in knowledge graph."""
    try:
        from knowledge_graph import KG
        org = _get_org(ctx["agent_id"])
        kg = KG(org)
        cid = ctx["sender"].replace("@", "_at_").replace(".", "_")
        props = {
            "email": ctx["sender"],
            "last_interaction": datetime.now(timezone.utc).isoformat()[:19],
            "last_agent": ctx["agent_id"],
            "sentiment": ctx.get("sentiment", "neutral"),
        }
        try:
            kg.update("customer", cid, props)
        except Exception:
            kg.add("customer", cid, props)
        ctx["actions"].append("kg_updated")
    except Exception as e:
        ctx["actions"].append(f"kg_error:{e}")
    return ctx


def add_customer_note(ctx: dict) -> dict:
    """Log the interaction as a customer note."""
    try:
        from customer_context import add_note
        add_note(ctx["sender"],
                f"Email handled by {ctx['agent_id']}. Subject: {ctx['subject'][:40]}. "
                f"Sentiment: {ctx.get('sentiment', 'neutral')}.",
                agent=ctx["agent_id"])
        ctx["actions"].append("note_added")
    except Exception:
        pass
    return ctx


def notify_ops(ctx: dict) -> dict:
    """Notify operations."""
    try:
        from ops_notify import ops_notify
        event = "status_update"
        if ctx.get("is_acceptance"): event = "new_project"
        if ctx.get("sentiment") in ("frustrated", "at_risk"): event = "escalation"
        ops_notify(event,
                  f"{ctx['agent_id']} handled {ctx['sender']}: {ctx['subject'][:40]}",
                  agent=ctx["agent_id"], customer_email=ctx["sender"])
        ctx["actions"].append(f"ops:{event}")
    except Exception as e:
        ctx["actions"].append(f"ops_error:{e}")
    return ctx


def notify_cs(ctx: dict) -> dict:
    """Notify CS/Advocate (unless they're the handling agent)."""
    agent_id = ctx["agent_id"]
    if "advocate" in agent_id or "csat" in agent_id:
        return ctx  # Don't notify yourself
    advocate = "gigforge-advocate" if "gigforge" in agent_id or agent_id in ("operations", "devops") else "techuni-advocate"
    try:
        _dispatch(advocate,
            f"INTERACTION NOTIFICATION from {agent_id}: {ctx['sender']} — "
            f"Subject: {ctx['subject'][:40]}. Sentiment: {ctx.get('sentiment', '?')}. "
            f"Excerpt: {ctx['message'][:200]}")
        ctx["notified"].append(advocate)
    except Exception:
        pass
    return ctx


def notify_pm(ctx: dict) -> dict:
    """Notify PM if there's an active project."""
    # Only notify if customer has an active project
    try:
        from customer_context import get_context
        full = get_context(ctx["sender"])
        if full and full.get("active_project"):
            pm = "gigforge-pm" if "gigforge" in ctx["agent_id"] or ctx["agent_id"] in ("operations",) else "techuni-pm"
            _dispatch(pm,
                f"CUSTOMER UPDATE from {ctx['agent_id']}: {ctx['sender']} — "
                f"Subject: {ctx['subject'][:40]}. Sentiment: {ctx.get('sentiment', '?')}.")
            ctx["notified"].append(pm)
    except Exception:
        pass
    return ctx


def check_acceptance(ctx: dict) -> dict:
    """Check if customer accepted — trigger advocate handoff."""
    if _detect_acceptance(ctx["message"]):
        ctx["is_acceptance"] = True
        if "sales" in ctx["agent_id"]:
            advocate = "gigforge-advocate" if "gigforge" in ctx["agent_id"] else "techuni-advocate"
            _dispatch(advocate,
                f"HANDOFF FROM SALES: Customer {ctx['sender']} accepted. "
                f"Subject: {ctx['subject']}. Take over the relationship. "
                f"CC braun.brelin@ai-elevate.ai on all emails.")
            ctx["response_agent"] = advocate
            ctx["actions"].append(f"handoff:{advocate}")
    return ctx


def check_frustration(ctx: dict) -> dict:
    """Check for frustration — escalate to CSAT."""
    if ctx.get("sentiment") in ("frustrated", "at_risk"):
        agent_id = ctx["agent_id"]
        csat = "gigforge-csat" if "gigforge" in agent_id or agent_id in ("operations",) else "techuni-csat"
        if csat != agent_id:
            _dispatch(csat,
                f"ESCALATION: Customer {ctx['sender']} sentiment={ctx['sentiment']}. "
                f"Subject: {ctx['subject']}. Reason: email tone. "
                f"Excerpt: {ctx['message'][:500]}. Take action.")
            ctx["response_agent"] = csat
            ctx["actions"].append(f"csat_escalation:{csat}")
    return ctx


# ============================================================================
# Script registry mapping
# ============================================================================


def create_plane_ticket(ctx: dict) -> dict:
    """Create a Plane ticket for new customers if one doesn't exist."""
    if not ctx["sender"]:
        return ctx
    try:
        from plane_ops import Plane
        org = _get_org(ctx["agent_id"])
        p = Plane(org)
        # Check if ticket already exists
        found = False
        for proj in ["TKT", "GFWEB"]:
            try:
                issues = p.list_issues(proj)
                if isinstance(issues, dict): issues = issues.get("results", [])
                if isinstance(issues, list):
                    for i in issues:
                        if ctx["sender"] in str(i.get("description", "") or ""):
                            found = True
                            break
            except Exception:
                continue
            if found:
                break

        if not found and len(ctx["message"]) > 50:
            p.create_issue(
                project="TKT",
                title=f"[LEAD] {ctx['sender']} — {ctx['subject'][:40]}",
                description=f"Customer: {ctx['sender']}\nAgent: {ctx['agent_id']}\nSubject: {ctx['subject']}\n\nFirst contact excerpt:\n{ctx['message'][:300]}",
                priority="medium",
            )
            ctx["actions"].append("plane_ticket_created")
    except Exception as e:
        ctx["actions"].append(f"plane_create_error:{e}")
    return ctx


def track_milestone(ctx: dict) -> dict:
    """PM tracks project milestones. Stores milestone status in Postgres."""
    import psycopg2, psycopg2.extras
    try:
        conn = psycopg2.connect(  # TODO: Replace with DAO from dao.py
            host=os.environ.get("DB_HOST", "127.0.0.1"),
            port=int(os.environ.get("DB_PORT", "5434")),
            dbname=os.environ.get("DB_NAME", "rag"),
            user=os.environ.get("DB_USER", "rag"),
            password=os.environ.get("DB_PASSWORD", ""),
        )
        conn.autocommit = True
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""CREATE TABLE IF NOT EXISTS project_milestones (
            id SERIAL PRIMARY KEY,
            customer_email TEXT NOT NULL,
            project_title TEXT,
            milestone TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            assigned_to TEXT,
            notes TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            completed_at TIMESTAMPTZ,
            UNIQUE(customer_email, milestone)
        )""")

        # Check if there are milestones for this customer
        cur.execute("SELECT * FROM project_milestones WHERE customer_email=%s ORDER BY created_at", (ctx["sender"],))
        milestones = [dict(r) for r in cur.fetchall()]

        if milestones:
            # Inject milestone status into the message so the agent knows
            ms_text = "\n\nProject milestones:\n"
            for m in milestones:
                ms_text += f"  [{m['status'].upper()}] {m['milestone']}"
                if m.get('notes'): ms_text += f" — {m['notes']}"
                ms_text += "\n"
            ctx["enhanced_message"] += ms_text
            ctx["milestones"] = milestones
            ctx["actions"].append(f"milestones_injected:{len(milestones)}")

        conn.close()
    except Exception as e:
        ctx["actions"].append(f"milestone_error:{e}")
    return ctx


def send_progress_report(ctx: dict) -> dict:
    """After PM interaction, generate a progress summary for the customer."""
    if "pm" not in ctx["agent_id"]:
        return ctx
    # Only trigger if milestones exist
    milestones = ctx.get("milestones", [])
    if not milestones:
        return ctx

    completed = sum(1 for m in milestones if m["status"] == "completed")
    total = len(milestones)
    pct = completed * 100 // total if total > 0 else 0

    # Add progress info to the context for the LLM to include in its reply
    progress = f"\n\nPROGRESS REPORT TO INCLUDE IN REPLY:\n"
    progress += f"Project progress: {completed}/{total} milestones complete ({pct}%)\n"
    for m in milestones:
        emoji = "✅" if m["status"] == "completed" else "🔄" if m["status"] == "in_progress" else "⏳"
        progress += f"  {emoji} {m['milestone']} — {m['status']}\n"
    ctx["enhanced_message"] += progress
    ctx["actions"].append("progress_report_injected")
    return ctx


def check_billing(ctx: dict) -> dict:
    """Check if any invoices are due or overdue for this customer."""
    try:
        from billing_pipeline import get_outstanding
        outstanding = [inv for inv in get_outstanding() if inv.get("customer_email") == ctx["sender"]]
        if outstanding:
            inv_text = f"\n\nOUTSTANDING INVOICES for this customer:\n"
            for inv in outstanding:
                inv_text += f"  #{inv['id']}: {inv['milestone']} — EUR {inv['amount_eur']:.2f} ({inv['status']})\n"
            ctx["enhanced_message"] += inv_text
            ctx["actions"].append(f"billing_checked:{len(outstanding)}")
    except Exception:
        pass
    return ctx



def kg_create(ctx: dict) -> dict:
    """Create an entity in the knowledge graph."""
    try:
        from knowledge_graph import KG
        org = _get_org(ctx["agent_id"])
        kg = KG(org)
        entity_type = ctx.get("kg_entity_type", "customer")
        entity_id = ctx.get("kg_entity_id", ctx["sender"].replace("@", "_at_").replace(".", "_"))
        props = ctx.get("kg_props", {"email": ctx["sender"]})
        kg.add(entity_type, entity_id, props)
        ctx["actions"].append(f"kg_created:{entity_type}/{entity_id}")
    except Exception as e:
        ctx["actions"].append(f"kg_create_error:{e}")
    return ctx


def kg_read(ctx: dict) -> dict:
    """Read an entity from the knowledge graph and inject into context."""
    try:
        from knowledge_graph import KG
        org = _get_org(ctx["agent_id"])
        kg = KG(org)
        results = kg.search(ctx["sender"])
        if results:
            kg_text = "\n\nKnowledge graph data:\n"
            for r in results[:5]:
                if isinstance(r, dict):
                    kg_text += f"  {r.get('type', '?')}/{r.get('id', '?')}: {str(r)[:200]}\n"
            ctx["enhanced_message"] += kg_text
            ctx["actions"].append(f"kg_read:{len(results)}")
    except Exception as e:
        ctx["actions"].append(f"kg_read_error:{e}")
    return ctx


def db_read_customer(ctx: dict) -> dict:
    """Read full customer record from all DB tables."""
    if not ctx["sender"]:
        return ctx
    try:
        import psycopg2, psycopg2.extras
        conn = psycopg2.connect(  # TODO: Replace with DAO from dao.py
            host=os.environ.get("DB_HOST", "127.0.0.1"),
            port=int(os.environ.get("DB_PORT", "5434")),
            dbname=os.environ.get("DB_NAME", "rag"),
            user=os.environ.get("DB_USER", "rag"),
            password=os.environ.get("DB_PASSWORD", ""),
        )
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        db_text = ""

        # Invoices
        cur.execute("SELECT id,milestone,amount_eur,status FROM invoices WHERE customer_email=%s ORDER BY created_at", (ctx["sender"],))
        invoices = cur.fetchall()
        if invoices:
            db_text += "\nInvoices:\n"
            for inv in invoices:
                db_text += f"  #{inv['id']}: {inv['milestone']} — EUR {inv['amount_eur']:.2f} ({inv['status']})\n"

        # Milestones
        cur.execute("SELECT milestone,status,notes FROM project_milestones WHERE customer_email=%s ORDER BY created_at", (ctx["sender"],))
        milestones = cur.fetchall()
        if milestones:
            db_text += "\nMilestones:\n"
            for m in milestones:
                db_text += f"  [{m['status']}] {m['milestone']}{' — ' + m['notes'] if m.get('notes') else ''}\n"

        # Proposals
        cur.execute("SELECT id,platform,job_title,status FROM proposal_queue WHERE job_budget LIKE %s OR notes LIKE %s ORDER BY created_at DESC LIMIT 5",
                   (f"%{ctx['sender']}%", f"%{ctx['sender']}%"))
        proposals = cur.fetchall()
        if proposals:
            db_text += "\nProposals:\n"
            for p in proposals:
                db_text += f"  #{p['id']}: {p['job_title'][:40]} ({p['status']})\n"

        if db_text:
            ctx["enhanced_message"] += f"\n\nDatabase records for this customer:{db_text}"
            ctx["actions"].append("db_read")

        conn.close()
    except Exception as e:
        ctx["actions"].append(f"db_read_error:{e}")
    return ctx



# Agents allowed to email customers directly
DIRECT_CONTACT_AGENTS = {
    # Pre-contract phase
    "gigforge-sales", "techuni-sales",
    # Post-contract phase (CS owns the relationship)
    "gigforge-advocate", "techuni-advocate",
    # Escalation authority
    "operations",
    "gigforge", "techuni-ceo",  # Directors for escalation only
    # CSAT for satisfaction recovery
    "gigforge-csat", "techuni-csat",
}


def gate_customer_contact(ctx: dict) -> dict:
    """Check if this agent is allowed to email the customer directly.
    If not, flag the response for relay through CS."""
    agent_id = ctx["agent_id"]

    if agent_id in DIRECT_CONTACT_AGENTS:
        ctx["can_contact_customer"] = True
        ctx["actions"].append("gate:direct_contact_allowed")
    else:
        ctx["can_contact_customer"] = False
        ctx["relay_through_cs"] = True
        ctx["actions"].append(f"gate:must_relay_through_cs")
    return ctx


def relay_through_cs(ctx: dict) -> dict:
    """If the agent can't contact the customer directly, send its output
    to CS/Advocate who will relay it appropriately."""
    if not ctx.get("relay_through_cs"):
        return ctx

    # Don't send the LLM's output as an email to the customer
    # Instead, send it to CS as an internal update
    agent_id = ctx["agent_id"]
    reply = ctx.get("stdout", "")

    if not reply or len(reply) < 20:
        return ctx

    # Determine which CS agent to relay through
    if "techuni" in agent_id:
        cs_agent = "techuni-advocate"
    else:
        cs_agent = "gigforge-advocate"

    # Send to CS as an internal update — CS decides what to tell the customer
    try:
        _dispatch(cs_agent,
            f"INTERNAL UPDATE FROM {agent_id} — relay to customer {ctx['sender']} if appropriate.\n\n"
            f"Subject: {ctx['subject']}\n"
            f"Agent's response (for your judgment — do NOT forward verbatim):\n{reply[:1000]}\n\n"
            f"Use your judgment: incorporate relevant information into your next customer email. "
            f"Do not forward this message directly to the customer.")
        ctx["actions"].append(f"relayed_to:{cs_agent}")
        ctx["notified"].append(cs_agent)

        # Clear the stdout so the gateway doesn't email it to the customer
        ctx["stdout"] = ""
        ctx["suppress_reply"] = True
    except Exception as e:
        ctx["actions"].append(f"relay_error:{e}")

    return ctx



def send_post_delivery_feedback(ctx: dict) -> dict:
    """After project delivery, trigger feedback request from sales."""
    # This only runs when explicitly called after delivery confirmation
    try:
        from feedback_system import send_feedback_request
        send_feedback_request(
            customer_email=ctx["sender"],
            project_title=ctx.get("subject", "Your project"),
            sales_agent=ctx["agent_id"] if "sales" in ctx["agent_id"] else "gigforge-sales",
        )
        ctx["actions"].append("feedback_request_sent")
    except Exception as e:
        ctx["actions"].append(f"feedback_error:{e}")
    return ctx



def store_to_cms(ctx: dict) -> dict:
    """Store interaction documents in Strapi CMS for retrieval."""
    if not ctx.get("stdout") or len(ctx.get("stdout", "")) < 50:
        return ctx
    try:
        import sys; sys.path.insert(0, "/home/aielevate")
        from cms_ops import CMS
        cms = CMS()

        agent_id = ctx["agent_id"]
        org = _get_org(agent_id)

        # Determine document category based on context
        category = "correspondence"
        subject_lower = ctx.get("subject", "").lower()
        if "invoice" in subject_lower or "payment" in subject_lower:
            category = "invoice"
        elif "proposal" in subject_lower or "quote" in subject_lower:
            category = "proposal"
        elif "feedback" in subject_lower or "how was" in subject_lower:
            category = "feedback"
        elif "progress" in subject_lower or "update" in subject_lower:
            category = "progress-report"

        # Store as a CMS post
        title = f"[{agent_id}] {ctx.get('subject', 'No subject')[:60]}"
        content_body = (
            f"Agent: {agent_id}\n"
            f"Customer: {ctx['sender']}\n"
            f"Date: {datetime.now(timezone.utc).isoformat()[:19]}\n"
            f"Sentiment: {ctx.get('sentiment', 'unknown')}\n\n"
            f"Customer message:\n{ctx['message'][:500]}\n\n"
            f"Agent response:\n{ctx.get('stdout', '')[:1000]}"
        )

        cms.create_post(
            org=org,
            title=title,
            content=content_body,
            category=category,
            author=agent_id,
            tags=[agent_id, ctx["sender"].split("@")[0], category],
        )
        ctx["actions"].append(f"cms:{category}")
    except Exception as e:
        ctx["actions"].append(f"cms_error:{e}")
    return ctx


SCRIPTS = {
    "pull_customer_context": pull_customer_context,
    "check_directives": check_directives,
    "check_proposal_queue": check_proposal_queue,
    "check_billing": check_billing,
    "track_milestone": track_milestone,
    "call_llm": call_llm,
    "update_sentiment": update_sentiment,
    "update_plane": update_plane,
    "update_kg": update_kg,
    "add_customer_note": add_customer_note,
    "create_plane_ticket": create_plane_ticket,
    "notify_ops": notify_ops,
    "notify_cs": notify_cs,
    "notify_pm": notify_pm,
    "check_acceptance": check_acceptance,
    "check_frustration": check_frustration,
    "kg_create": kg_create,
    "kg_read": kg_read,
    "db_read_customer": db_read_customer,
    "send_progress_report": send_progress_report,
    "gate_customer_contact": gate_customer_contact,
    "relay_through_cs": relay_through_cs,
    "send_post_delivery_feedback": send_post_delivery_feedback,
    "store_to_cms": store_to_cms,
}

# ============================================================================
# Agent Manifests — what each agent runs and in what order
# ============================================================================

DEFAULT_MANIFEST = {
    "pre": ["gate_customer_contact", "pull_customer_context", "check_directives", "kg_read", "db_read_customer"],
    "main": ["call_llm"],
    "post": ["update_sentiment", "update_plane", "update_kg", "add_customer_note",
             "create_plane_ticket", "notify_ops", "notify_cs", "notify_pm", "check_acceptance", "check_frustration", "store_to_cms", "relay_through_cs"],
}

AGENT_MANIFESTS = {
    "gigforge-sales": {
        "pre": ["gate_customer_contact", "pull_customer_context", "check_directives", "check_proposal_queue", "check_billing"],
        "main": ["call_llm"],
        "post": ["update_sentiment", "update_plane", "update_kg", "add_customer_note",
                 "create_plane_ticket", "notify_ops", "notify_cs", "notify_pm", "check_acceptance", "check_frustration", "send_post_delivery_feedback", "store_to_cms"],
    },
    "gigforge-advocate": {
        "pre": ["gate_customer_contact", "pull_customer_context", "check_directives"],
        "main": ["call_llm"],
        "post": ["update_sentiment", "update_plane", "update_kg", "add_customer_note",
                 "notify_ops", "notify_pm", "check_frustration", "store_to_cms"],
        # Advocate doesn't notify CS (it IS CS) or check acceptance (already past that)
    },
    "gigforge-csat": {
        "pre": ["gate_customer_contact", "pull_customer_context", "check_directives"],
        "main": ["call_llm"],
        "post": ["update_sentiment", "update_plane", "update_kg", "add_customer_note",
                 "notify_ops", "notify_pm"],
        # CSAT doesn't notify CS (it IS CS) or escalate to itself
    },
    "gigforge-support": {
        "pre": ["gate_customer_contact", "pull_customer_context", "check_directives"],
        "main": ["call_llm"],
        "post": ["update_sentiment", "update_plane", "update_kg", "add_customer_note",
                 "notify_ops", "notify_cs", "notify_pm", "check_frustration"],
    },
    "operations": {
        "pre": ["gate_customer_contact", "pull_customer_context", "check_directives"],
        "main": ["call_llm"],
        "post": ["update_sentiment", "update_plane", "update_kg", "add_customer_note",
                 "notify_cs", "notify_pm"],
    },
    "gigforge-pm": {
        "pre": ["gate_customer_contact", "pull_customer_context", "check_directives", "track_milestone", "check_billing"],
        "main": ["call_llm"],
        "post": ["update_sentiment", "update_plane", "update_kg", "add_customer_note",
                 "notify_ops", "notify_cs", "send_progress_report", "check_frustration", "relay_through_cs"],
    },
    "gigforge-engineer": {
        "pre": ["gate_customer_contact", "pull_customer_context", "check_directives"],
        "main": ["call_llm"],
        "post": ["update_plane", "update_kg", "add_customer_note", "notify_ops", "notify_pm", "relay_through_cs"],
    },
    "gigforge-billing": {
        "pre": ["gate_customer_contact", "pull_customer_context", "check_directives", "check_billing"],
        "main": ["call_llm"],
        "post": ["update_sentiment", "update_plane", "update_kg", "add_customer_note",
                 "notify_ops", "notify_cs", "relay_through_cs"],
    },
}


    # TechUni mirrors GigForge
for gf_agent, manifest in list(AGENT_MANIFESTS.items()):
    tu_agent = gf_agent.replace("gigforge", "techuni")
    if tu_agent not in AGENT_MANIFESTS:
        AGENT_MANIFESTS[tu_agent] = manifest


# ============================================================================
# Helpers
# ============================================================================

POSITIVE = ["thanks", "great", "love", "perfect", "excellent", "awesome", "lets go ahead",
            "let's go ahead", "i accept", "go ahead", "approved", "looks good", "sounds good"]
FRUSTRATED = ["disappointed", "frustrated", "unacceptable", "not happy", "terrible",
              "still waiting", "no response", "speak to someone", "waste of time"]
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
# Execution Engine
# ============================================================================

def execute(agent_id: str, message: str, sender: str, subject: str, timeout: int = 300) -> dict:
    """Execute the full workflow for an agent interaction."""

    manifest = AGENT_MANIFESTS.get(agent_id, DEFAULT_MANIFEST)

    ctx = {
        "agent_id": agent_id,
        "message": message,
        "enhanced_message": message,
        "sender": sender,
        "subject": subject,
        "timeout": timeout,
        "stdout": "",
        "returncode": 0,
        "duration": 0,
        "sentiment": "neutral",
        "is_acceptance": False,
        "response_agent": agent_id,  # May change during post-processing
        "actions": [],
        "notified": [],
        "skip_llm": False,
    }

    log.info(f"Executing workflow for {agent_id}, sender={sender}, subject={subject[:30]}")

    # Run pre scripts
    for script_name in manifest.get("pre", []):
        fn = SCRIPTS.get(script_name)
        if fn:
            try:
                ctx = fn(ctx)
            except Exception as e:
                ctx["actions"].append(f"pre_error:{script_name}:{e}")
                log.warning(f"Pre script {script_name} failed: {e}")

    # Run main scripts
    for script_name in manifest.get("main", []):
        fn = SCRIPTS.get(script_name)
        if fn:
            try:
                ctx = fn(ctx)
            except Exception as e:
                ctx["actions"].append(f"main_error:{script_name}:{e}")
                log.error(f"Main script {script_name} failed: {e}")

    # Run post scripts
    for script_name in manifest.get("post", []):
        fn = SCRIPTS.get(script_name)
        if fn:
            try:
                ctx = fn(ctx)
            except Exception as e:
                ctx["actions"].append(f"post_error:{script_name}:{e}")
                log.warning(f"Post script {script_name} failed: {e}")

    log.info(f"Workflow complete for {agent_id}: actions={ctx['actions']}, notified={ctx['notified']}, response_agent={ctx['response_agent']}")

    return {
        "stdout": ctx["stdout"],
        "returncode": ctx["returncode"],
        "duration": ctx["duration"],
        "sentiment": ctx["sentiment"],
        "response_agent": ctx["response_agent"],
        "actions": ctx["actions"],
        "notified": ctx["notified"],
        "is_acceptance": ctx["is_acceptance"],
    }


# ============================================================================
# REST API
# ============================================================================

class WorkflowRequest(BaseModel):
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    agent_id: str
    message: str
    sender_email: str = ""
    subject: str = ""
    timeout: int = 300


@app.post("/handle")
async def handle(req: WorkflowRequest):
    """"""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: execute(req.agent_id, req.message, req.sender_email, req.subject, req.timeout)
    )
    return result


@app.get("/health")
async def health():
    """"""
    return {"status": "ok", "service": "workflow-engine", "agents": len(AGENT_MANIFESTS)}


@app.get("/manifest/{agent_id}")
async def get_manifest(agent_id: str):
    """"""
    manifest = AGENT_MANIFESTS.get(agent_id, DEFAULT_MANIFEST)
    return {"agent_id": agent_id, "manifest": manifest, "is_default": agent_id not in AGENT_MANIFESTS}


@app.get("/manifests")
async def list_manifests():
    """"""
    return {aid: {"pre": len(m["pre"]), "main": len(m["main"]), "post": len(m["post"])}
            for aid, m in AGENT_MANIFESTS.items()}


@app.get("/scripts")
async def list_scripts():
    """"""
    return {"scripts": sorted(SCRIPTS.keys()), "count": len(SCRIPTS)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Workflow Engine")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose output")
    args = parser.parse_args()

    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8068)
