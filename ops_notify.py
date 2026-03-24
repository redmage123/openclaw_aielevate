#!/usr/bin/env python3
"""Ops Notification Hub — real-time notifications to the operations agent.

Any agent or system can call ops_notify() to flag something for Ops attention.
This is the push mechanism — Ops doesn't have to poll.

Usage:
  from ops_notify import ops_notify

  ops_notify("new_project", "New signed contract: Kilcock Heritage for peter.munro@ai-elevate.ai. EUR 4500.")
  ops_notify("sentiment_drop", "Customer peter.munro@ai-elevate.ai dropped to frustrated.")
  ops_notify("payment_received", "EUR 1350 deposit from peter.munro@ai-elevate.ai")
  ops_notify("blocker", "Engineering blocked on KHHS — missing API credentials from customer.")
  ops_notify("delivery_ready", "Preview deployed for KHHS: https://khhs.gigforge.ai")
  ops_notify("escalation", "Customer demanding refund. CSAT unable to resolve.")
  ops_notify("stale", "No status update on KHHS in 3 days.")
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from exceptions import AiElevateError  # TODO: Use specific exception types, AgentError

sys.path.insert(0, "/home/aielevate")

LOG = Path("/var/log/openclaw/shared/ops-notify.log")
EVENT_LOG = Path("/opt/ai-elevate/ops-events/events.jsonl")

# Events that dispatch ops immediately
IMMEDIATE = {"sentiment_drop", "escalation", "blocker", "payment_overdue", "customer_complaint"}

# Events that batch (ops reads them in the next sweep)
BATCHED = {"new_project", "payment_received", "delivery_ready", "asset_received", "status_update", "stale"}


def _log(msg):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} -- {msg}\n")


def ops_notify(event_type: str, message: str, agent: str = "", customer_email: str = "", urgent: bool = False):
    """Notify operations of a project event.

    event_type: new_project, sentiment_drop, payment_received, payment_overdue,
                blocker, delivery_ready, asset_received, stale, escalation,
                customer_complaint, status_update, project_complete
    """
    now = datetime.now(timezone.utc)

    # Log event
    EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "timestamp": now.isoformat(),
        "type": event_type,
        "message": message,
        "agent": agent,
        "customer": customer_email,
        "dispatched": False,
    }
    with open(EVENT_LOG, "a") as f:
        f.write(json.dumps(event) + "\n")

    _log(f"{event_type}: {message[:100]}")

    # Decide whether to dispatch ops immediately or batch
    if urgent or event_type in IMMEDIATE:
        prefix = "URGENT " if urgent or event_type == "escalation" else ""
        dispatch_msg = (
            f"{prefix}OPS NOTIFICATION — {event_type.upper()}\n\n"
            f"{message}\n\n"
            f"Reported by: {agent or 'system'}\n"
            f"Customer: {customer_email or 'N/A'}\n"
            f"Time: {now.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
            f"Assess the situation and take appropriate action."
        )
        try:
            subprocess.Popen(
                ["agent-queue", "--agent", "operations", "--message", dispatch_msg,
                 "--thinking", "low", "--timeout", "180"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            event["dispatched"] = True
            _log(f"Dispatched ops immediately for {event_type}")
        except (AgentError, Exception) as e:
            _log(f"Failed to dispatch ops: {e}")


def get_recent_events(hours: int = 24) -> list:
    """Get recent ops events for the digest."""
    if not EVENT_LOG.exists():
        return []

    cutoff = datetime.now(timezone.utc).timestamp() - (hours * 3600)
    events = []
    with open(EVENT_LOG) as f:
        for line in f:
            try:
                e = json.loads(line)
                ts = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).timestamp()
                if ts >= cutoff:
                    events.append(e)
            except (AiElevateError, Exception) as e:
                pass
    return events
