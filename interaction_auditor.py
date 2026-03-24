#!/usr/bin/env python3
"""Interaction Auditor — verifies agents actually performed their mandatory workflow steps.

Runs after each email interaction to check:
1. Did the agent update sentiment?
2. Did the agent create/update a Plane ticket?
3. Did the agent notify ops?
4. Did the agent hand off when it should have?
5. Did the agent escalate when the customer was frustrated?

If gaps are found, the auditor dispatches corrective actions.

Designed to be called from the email gateway AFTER the agent responds,
NOT instead of the agent doing it. This is the safety net.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta

import psycopg2
import psycopg2.extras
from dao import BaseDAO  # TODO: Replace inline DB calls with specific DAOs
from exceptions import AiElevateError  # TODO: Use specific exception types, PlaneError, DatabaseError, AgentError

sys.path.insert(0, "/home/aielevate")

DB_CONN = dict(
    host=os.environ.get("DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("DB_PORT", "5434")),
    dbname=os.environ.get("DB_NAME", "rag"),
    user=os.environ.get("DB_USER", "rag"),
    password=os.environ.get("DB_PASSWORD", "rag_vec_2026"),
)

# Frustration keywords
FRUSTRATION_KEYWORDS = [
    "speak to someone", "talk to a manager", "senior", "supervisor",
    "not happy", "disappointed", "frustrated", "unacceptable",
    "terrible", "worst", "refund", "cancel", "waste of time",
    "holdup", "what is taking", "still waiting", "no response",
    "speak to a human", "real person",
]

# Acceptance keywords
ACCEPTANCE_KEYWORDS = [
    "lets go ahead", "let's go ahead", "sounds good", "i accept",
    "lets proceed", "let's proceed", "we accept", "approved",
    "go ahead", "start the project", "when can you start",
    "ready to move forward", "lets do it", "let's do it",
    "i agree", "deal", "accepted",
]


def _audit_sentiment(sender_email: str, email_body: str, agent_id: str, now: datetime, gaps: list) -> None:
    """Check sentiment was recorded; infer and record it if missing."""
    try:
        conn = psycopg2.connect(**DB_CONN)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT timestamp FROM customer_sentiment WHERE email=%s AND timestamp > %s ORDER BY timestamp DESC LIMIT 1",
            (sender_email, (now - timedelta(minutes=10)).isoformat()))
        if not cur.fetchone():
            gaps.append("NO_SENTIMENT")
            body_lower = email_body.lower()
            if any(kw in body_lower for kw in FRUSTRATION_KEYWORDS):
                rating, notes = "frustrated", "Auto-detected frustration in email (auditor)"
            elif any(kw in body_lower for kw in ACCEPTANCE_KEYWORDS):
                rating, notes = "positive", "Customer accepted/agreed (auditor)"
            else:
                rating, notes = "neutral", "Auto-set by auditor — agent did not update"
            from customer_context import update_sentiment
            update_sentiment(sender_email, rating, notes, agent=f"{agent_id}-auditor")
        conn.close()
    except Exception as e:
        gaps.append(f"SENTIMENT_CHECK_FAILED: {e}")


def _audit_frustration_escalation(sender_email: str, email_body: str, is_frustrated: bool, gaps: list) -> None:
    """Check CSAT was dispatched for frustrated customers; dispatch if missing."""
    if not is_frustrated:
        return
    try:
        from pathlib import Path
        queue_log = Path("/var/log/openclaw/shared/agent-queue.log").read_text()
        recent_lines = queue_log.split("\n")[-20:]
        if not any("gigforge-csat" in line and "QUEUED" in line for line in recent_lines):
            gaps.append("NO_CSAT_ESCALATION")
            from customer_context import context_summary
            ctx = context_summary(sender_email)
            subprocess.Popen(
                ["agent-queue", "--agent", "gigforge-csat",
                 "--message", f"ESCALATION: Customer {sender_email} expressed frustration. Email: {email_body[:500]}. Context: {ctx}",
                 "--thinking", "low", "--timeout", "180"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _audit_acceptance_handoff(sender_email: str, email_body: str, is_acceptance: bool, agent_id: str, gaps: list) -> None:
    """Check advocate handoff was made for sales acceptances; dispatch if missing."""
    if not is_acceptance or "sales" not in agent_id:
        return
    try:
        from pathlib import Path
        queue_log = Path("/var/log/openclaw/shared/agent-queue.log").read_text()
        recent_lines = queue_log.split("\n")[-30:]
        if not any("gigforge-advocate" in line and "QUEUED" in line for line in recent_lines):
            _adv_dedup = Path(f"/tmp/auditor-advocate-{sender_email.replace('@','_')}.done")
            if not _adv_dedup.exists():
                gaps.append("NO_ADVOCATE_HANDOFF")
                _adv_dedup.write_text(datetime.now(timezone.utc).isoformat())
                subprocess.Popen(
                    ["agent-queue", "--agent", "gigforge-advocate",
                     "--message", f"HANDOFF FROM SALES: Customer {sender_email} accepted a quote. "
                     f"Introduce yourself and take over the project relationship. "
                     f"CC braun.brelin@ai-elevate.ai on all emails. Email thread: {email_body[:500]}",
                     "--thinking", "low", "--timeout", "300"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _audit_ops_notify(sender_email: str, agent_id: str, is_frustrated: bool, is_acceptance: bool,
                      now: datetime, gaps: list) -> None:
    """Check ops was notified; send notification if missing."""
    try:
        from pathlib import Path
        events_file = Path("/opt/ai-elevate/ops-events/events.jsonl")
        if events_file.exists():
            recent_events = []
            for line in events_file.read_text().strip().split("\n")[-20:]:
                try:
                    e = json.loads(line)
                    if e.get("customer") == sender_email:
                        ts = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
                        if (now - ts).total_seconds() < 600:
                            recent_events.append(e)
                except Exception:
                    pass
            if not recent_events:
                gaps.append("NO_OPS_NOTIFY")
                from ops_notify import ops_notify
                event_type = "escalation" if is_frustrated else "new_project" if is_acceptance else "status_update"
                ops_notify(event_type, f"Auditor: {agent_id} interacted with {sender_email} but did not notify ops",
                           agent=f"{agent_id}-auditor", customer_email=sender_email)
    except Exception:
        pass


def _audit_plane_ticket(sender_email: str, email_body: str, is_acceptance: bool, gaps: list) -> None:
    """Check Plane ticket exists for accepted customers; create if missing."""
    try:
        from plane_ops import Plane
        p = Plane("gigforge")
        found_ticket = any(
            sender_email in str(i.get("description", "") or "")
            for proj in ["TKT", "GFWEB", "FEAT"]
            for issues_raw in [p.list_issues(proj)]
            for issues in [issues_raw.get("results", []) if isinstance(issues_raw, dict) else issues_raw or []]
            if isinstance(issues, list)
            for i in issues
        )
        if not found_ticket and is_acceptance:
            gaps.append("NO_PLANE_TICKET")
            from pathlib import Path
            _dedup_file = Path(f"/tmp/auditor-ticket-{sender_email.replace('@','_')}.done")
            if not _dedup_file.exists():
                subject_match = re.search(r"Subject: (.+)", email_body)
                subject = subject_match.group(1) if subject_match else "Customer inquiry"
                p.create_issue(project="TKT", title=f"[LEAD] {sender_email} — {subject[:50]}",
                               description=f"Customer: {sender_email}\nAgent: auditor\n\nEmail excerpt:\n{email_body[:300]}",
                               priority="high")
                _dedup_file.write_text(datetime.now(timezone.utc).isoformat())
    except Exception:
        pass


def audit_interaction(sender_email: str, agent_id: str, email_body: str, response: str):
    """Audit a completed interaction and dispatch corrective actions for any workflow gaps found."""
    gaps: list = []
    now = datetime.now(timezone.utc)
    body_lower = email_body.lower()
    is_frustrated = any(kw in body_lower for kw in FRUSTRATION_KEYWORDS)
    is_acceptance = any(kw in body_lower for kw in ACCEPTANCE_KEYWORDS)

    _audit_sentiment(sender_email, email_body, agent_id, now, gaps)
    _audit_frustration_escalation(sender_email, email_body, is_frustrated, gaps)
    _audit_acceptance_handoff(sender_email, email_body, is_acceptance, agent_id, gaps)
    _audit_ops_notify(sender_email, agent_id, is_frustrated, is_acceptance, now, gaps)
    _audit_plane_ticket(sender_email, email_body, is_acceptance, gaps)

    try:
        from pathlib import Path
        log_file = Path("/var/log/openclaw/shared/interaction-audit.log")
        log_file.parent.mkdir(parents=True, exist_ok=True)
        status = f"GAPS: {', '.join(gaps)}" if gaps else "CLEAN"
        with open(log_file, "a") as f:
            f.write(f"{now.strftime('%Y-%m-%d %H:%M')} | {agent_id} | {sender_email} | {status}\n")
    except Exception:
        pass

    return gaps
