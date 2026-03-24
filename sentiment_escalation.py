#!/usr/bin/env python3
"""Sentiment auto-escalation — hooks into customer_context.update_sentiment().

When sentiment is set to 'at_risk' or 'frustrated', automatically:
1. Dispatch CSAT agent with full customer context
2. Notify Ops Director
3. Log incident in audit trail

This replaces the plain DB write in customer_context.update_sentiment()
with a version that triggers action.
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from exceptions import AiElevateError  # TODO: Use specific exception types, DatabaseError, AgentError

sys.path.insert(0, "/home/aielevate")

LOG = Path("/var/log/openclaw/shared/sentiment-escalation.log")

def _log(msg):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} -- {msg}\n")

def escalate_if_needed(email: str, rating: str, notes: str = "", agent: str = ""):
    """Called after every sentiment update. Triggers escalation for frustrated/at_risk."""

    if rating not in ("frustrated", "at_risk"):
        return

    _log(f"ESCALATION: {email} sentiment={rating} notes={notes} reported_by={agent}")

    # Get full context for the CSAT agent
    try:
        from customer_context import context_summary
        ctx = context_summary(email)
    except (DatabaseError, Exception) as e:
        ctx = f"Customer: {email}, Sentiment: {rating}, Notes: {notes}"

    severity = "URGENT" if rating == "at_risk" else "HIGH"

    # Dispatch CSAT agent
    msg = (
        f"{severity} CUSTOMER ESCALATION\n\n"
        f"Customer {email} sentiment has dropped to {rating}.\n"
        f"Reported by: {agent}\n"
        f"Reason: {notes}\n\n"
        f"FULL CONTEXT:\n{ctx}\n\n"
        f"Take immediate action: review the situation, contact the customer, "
        f"and report back to the Ops Director (gigforge) with your resolution plan."
    )

    try:
        subprocess.Popen(
            ["agent-queue", "--agent", "gigforge-csat", "--message", msg,
             "--thinking", "low", "--timeout", "300"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        _log(f"Dispatched gigforge-csat for {email}")
    except (AgentError, Exception) as e:
        _log(f"Failed to dispatch CSAT: {e}")

    # Notify Ops via the event hub (dispatches ops agent immediately for sentiment drops)
    try:
        from ops_notify import ops_notify
        ops_notify(
            "sentiment_drop",
            f"Customer {email} sentiment dropped to {rating}. Reason: {notes}. CSAT dispatched.",
            agent=agent,
            customer_email=email,
        )
        _log(f"Notified ops via event hub for {email}")
    except (AgentError, Exception) as e:
        _log(f"Failed to notify ops via event hub: {e}")

    # Also notify Braun directly
    try:
        subprocess.Popen(
            ["python3", "/home/aielevate/notify.py",
             "--priority", "high" if rating == "at_risk" else "medium",
             "--to", "braun",
             "--title", f"Customer {rating}: {email}",
             "--body", f"Customer: {email}\nSentiment: {rating}\nReason: {notes}\nReported by: {agent}\n\nCSAT agent has been dispatched."],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except (AgentError, Exception) as e:
        pass

    # Log incident
    try:
        from audit_log import log_incident
        log_incident(
            severity="high" if rating == "at_risk" else "medium",
            title=f"Customer sentiment: {rating}",
            description=f"Customer {email} — {notes}",
            agent_id=agent or "system",
            org="gigforge",
        )
    except (AgentError, Exception) as e:
        pass
