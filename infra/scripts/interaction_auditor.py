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
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta

import psycopg2
import psycopg2.extras

sys.path.insert(0, "/home/aielevate")

DB_HOST = "127.0.0.1"
DB_PORT = 5434
DB_NAME = "rag"
DB_USER = "rag"
DB_PASS = "rag_vec_2026"

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


def audit_interaction(sender_email: str, agent_id: str, email_body: str, response: str):
    """Audit a completed interaction and fix any gaps."""
    gaps = []
    now = datetime.now(timezone.utc)

    # 1. Check sentiment was updated
    try:
        conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT timestamp FROM customer_sentiment WHERE email=%s AND timestamp > %s ORDER BY timestamp DESC LIMIT 1",
            (sender_email, (now - timedelta(minutes=10)).isoformat())
        )
        if not cur.fetchone():
            gaps.append("NO_SENTIMENT")
            # Fix it: infer sentiment from email
            body_lower = email_body.lower()
            if any(kw in body_lower for kw in FRUSTRATION_KEYWORDS):
                rating = "frustrated"
                notes = "Auto-detected frustration in email (auditor)"
            elif any(kw in body_lower for kw in ACCEPTANCE_KEYWORDS):
                rating = "positive"
                notes = "Customer accepted/agreed (auditor)"
            else:
                rating = "neutral"
                notes = "Auto-set by auditor — agent did not update"

            from customer_context import update_sentiment
            update_sentiment(sender_email, rating, notes, agent=f"{agent_id}-auditor")
        conn.close()
    except Exception as e:
        gaps.append(f"SENTIMENT_CHECK_FAILED: {e}")

    # 2. Check for frustration that wasn't escalated
    body_lower = email_body.lower()
    is_frustrated = any(kw in body_lower for kw in FRUSTRATION_KEYWORDS)
    if is_frustrated:
        # Check if CSAT was dispatched (look in recent queue log)
        try:
            from pathlib import Path
            queue_log = Path("/var/log/openclaw/shared/agent-queue.log").read_text()
            recent_lines = queue_log.split("\n")[-20:]
            csat_dispatched = any("gigforge-csat" in line and "QUEUED" in line for line in recent_lines)
            if not csat_dispatched:
                gaps.append("NO_CSAT_ESCALATION")
                # Fix it: dispatch CSAT
                from customer_context import context_summary
                ctx = context_summary(sender_email)
                subprocess.Popen(
                    ["agent-queue", "--agent", "gigforge-csat",
                     "--message", f"ESCALATION: Customer {sender_email} expressed frustration. Email: {email_body[:500]}. Context: {ctx}",
                     "--thinking", "low", "--timeout", "180"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
        except Exception:
            pass

    # 3. Check for acceptance that wasn't handed off to advocate
    is_acceptance = any(kw in body_lower for kw in ACCEPTANCE_KEYWORDS)
    if is_acceptance and "sales" in agent_id:
        try:
            from pathlib import Path
            queue_log = Path("/var/log/openclaw/shared/agent-queue.log").read_text()
            recent_lines = queue_log.split("\n")[-30:]
            advocate_dispatched = any("gigforge-advocate" in line and "QUEUED" in line for line in recent_lines)
            if not advocate_dispatched:
                # Check dedup — don't dispatch if we already did recently
                _adv_dedup = Path(f"/tmp/auditor-advocate-{sender_email.replace('@','_')}.done")
                if _adv_dedup.exists():
                    pass  # Already dispatched by auditor
                else:
                    gaps.append("NO_ADVOCATE_HANDOFF")
                    _adv_dedup.write_text(datetime.now(timezone.utc).isoformat())
                    subprocess.Popen(
                        ["agent-queue", "--agent", "gigforge-advocate",
                     "--message", f"HANDOFF FROM SALES: Customer {sender_email} accepted a quote. "
                     f"Introduce yourself and take over the project relationship. "
                     f"CC braun.brelin@ai-elevate.ai on all emails. "
                     f"Email thread: {email_body[:500]}",
                     "--thinking", "low", "--timeout", "300"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
        except Exception:
            pass

    # 4. Check ops was notified
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
                # Fix it
                from ops_notify import ops_notify
                event_type = "escalation" if is_frustrated else "new_project" if is_acceptance else "status_update"
                ops_notify(event_type, f"Auditor: {agent_id} interacted with {sender_email} but did not notify ops",
                          agent=f"{agent_id}-auditor", customer_email=sender_email)
    except Exception:
        pass

    # 5. Check Plane ticket exists for this customer
    try:
        from plane_ops import Plane
        p = Plane("gigforge")
        found_ticket = False
        for proj in ["TKT", "GFWEB", "FEAT"]:
            try:
                issues = p.list_issues(proj)
                if isinstance(issues, dict):
                    issues = issues.get("results", [])
                if isinstance(issues, list):
                    for i in issues:
                        if sender_email in str(i.get("description", "") or ""):
                            found_ticket = True
                            break
            except Exception:
                pass
            if found_ticket:
                break

        if not found_ticket and is_acceptance:
            # Only create ticket for acceptances, not every email
            # And only if no ticket exists anywhere for this customer
            gaps.append("NO_PLANE_TICKET")
            # Check a dedup file to avoid creating multiple tickets
            _dedup_file = Path(f"/tmp/auditor-ticket-{sender_email.replace('@','_')}.done")
            if not _dedup_file.exists():
                try:
                    subject_match = re.search(r"Subject: (.+)", email_body)
                    subject = subject_match.group(1) if subject_match else "Customer inquiry"
                    p.create_issue(
                        project="TKT",
                        title=f"[LEAD] {sender_email} — {subject[:50]}",
                        description=f"Customer: {sender_email}\nAgent: {agent_id}\nCreated by: auditor\n\nEmail excerpt:\n{email_body[:300]}",
                        priority="high",
                    )
                    _dedup_file.write_text(datetime.now(timezone.utc).isoformat())
                except Exception:
                    pass
    except Exception:
        pass

    # Log audit results
    try:
        from pathlib import Path
        log = Path("/var/log/openclaw/shared/interaction-audit.log")
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a") as f:
            if gaps:
                f.write(f"{now.strftime('%Y-%m-%d %H:%M')} | {agent_id} | {sender_email} | GAPS: {', '.join(gaps)}\n")
            else:
                f.write(f"{now.strftime('%Y-%m-%d %H:%M')} | {agent_id} | {sender_email} | CLEAN\n")
    except Exception:
        pass

    return gaps
