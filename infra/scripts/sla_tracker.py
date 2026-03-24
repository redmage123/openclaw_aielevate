#!/usr/bin/env python3
"""SLA Tracking for AI Elevate Email Gateway.

Tracks email response times and SLA compliance per org and agent.

SLA Thresholds:
  urgent: 3600s  (1 hour)
  high:   14400s (4 hours)
  normal: 28800s (8 hours)

Usage:
  # Programmatic (called from email-gateway.py):
  from sla_tracker import log_received, log_responded

  # CLI:
  python3 sla_tracker.py --report       # SLA compliance report
  python3 sla_tracker.py --alert        # Check for breached SLAs and alert
"""

import argparse
import json
import logging
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [sla] %(levelname)s %(message)s",
)
logger = logging.getLogger("sla_tracker")

DB_PATH = "/opt/ai-elevate/data/sla.db"

SLA_THRESHOLDS = {
    "urgent": 3600,
    "high": 14400,
    "normal": 28800,
}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS sla_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender TEXT NOT NULL,
        recipient TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        org TEXT NOT NULL,
        subject TEXT DEFAULT '',
        received_at TEXT NOT NULL,
        responded_at TEXT,
        response_time_seconds REAL,
        priority TEXT NOT NULL DEFAULT 'normal',
        sla_met INTEGER
    )""")
    conn.commit()
    return conn


def detect_priority(subject, body=""):
    """Auto-detect priority from subject/body keywords."""
    text = (subject + " " + body).lower()
    if any(w in text for w in ("urgent", "emergency", "critical", "asap", "immediately", "down", "outage")):
        return "urgent"
    if any(w in text for w in ("important", "high priority", "time-sensitive", "deadline", "blocker")):
        return "high"
    return "normal"


# Domains used by agents — never track SLA for these senders
_AGENT_DOMAINS = {"gigforge.ai", "techuni.ai", "internal.ai-elevate.ai"}
_HUMAN_EMAILS = {"braun.brelin@ai-elevate.ai", "peter.munro@ai-elevate.ai",
                 "mike.burton@ai-elevate.ai", "charlie.turking@ai-elevate.ai"}

def _is_agent_sender(sender):
    email = sender.strip().lower()
    if "<" in email:
        email = email.split("<")[1].rstrip(">")
    if email in _HUMAN_EMAILS:
        return False
    if "bounce+" in email or "postmaster@" in email:
        return True
    domain = email.split("@")[-1] if "@" in email else ""
    return domain in _AGENT_DOMAINS

def log_received(sender, recipient, agent_id, subject, priority=None, org=None):
    """Log an inbound email. Called when email arrives at gateway."""
    if priority is None:
        priority = detect_priority(subject)
    if org is None:
        if "gigforge" in agent_id:
            org = "gigforge"
        elif "techuni" in agent_id:
            org = "techuni"
        else:
            org = "ai-elevate"

    db = get_db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT INTO sla_events (sender, recipient, agent_id, org, subject, received_at, priority) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sender.lower().strip(), recipient.lower().strip(), agent_id, org, subject, now, priority),
        )
        db.commit()
        logger.info("SLA: logged received from %s to %s (agent=%s, priority=%s)", sender, recipient, agent_id, priority)
    finally:
        db.close()


def log_responded(sender, agent_id):
    """Log that an agent responded to a sender. Matches most recent open event."""
    db = get_db()
    try:
        now = datetime.now(timezone.utc)
        # Find the most recent unresponded event for this sender+agent
        row = db.execute(
            "SELECT id, received_at, priority FROM sla_events WHERE sender = ? AND agent_id = ? AND responded_at IS NULL ORDER BY received_at DESC LIMIT 1",
            (sender.lower().strip(), agent_id),
        ).fetchone()

        if row is None:
            logger.warning("SLA: no open event for sender=%s agent=%s", sender, agent_id)
            return

        received = datetime.fromisoformat(row["received_at"])
        if received.tzinfo is None:
            received = received.replace(tzinfo=timezone.utc)
        response_time = (now - received).total_seconds()
        threshold = SLA_THRESHOLDS.get(row["priority"], SLA_THRESHOLDS["normal"])
        sla_met = 1 if response_time <= threshold else 0

        db.execute(
            "UPDATE sla_events SET responded_at = ?, response_time_seconds = ?, sla_met = ? WHERE id = ?",
            (now.isoformat(), response_time, sla_met, row["id"]),
        )
        db.commit()
        logger.info("SLA: logged response for sender=%s agent=%s time=%.0fs sla_met=%s",
                     sender, agent_id, response_time, bool(sla_met))
    finally:
        db.close()


def generate_report():
    """Generate SLA compliance report."""
    db = get_db()
    lines = []
    lines.append("=" * 60)
    lines.append("SLA Compliance Report -- " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    lines.append("=" * 60)

    # Overall stats
    total = db.execute("SELECT COUNT(*) as c FROM sla_events WHERE responded_at IS NOT NULL").fetchone()["c"]
    met = db.execute("SELECT COUNT(*) as c FROM sla_events WHERE sla_met = 1").fetchone()["c"]
    breached = db.execute("SELECT COUNT(*) as c FROM sla_events WHERE sla_met = 0 AND responded_at IS NOT NULL").fetchone()["c"]
    pending = db.execute("SELECT COUNT(*) as c FROM sla_events WHERE responded_at IS NULL").fetchone()["c"]
    avg_time = db.execute("SELECT AVG(response_time_seconds) as a FROM sla_events WHERE responded_at IS NOT NULL").fetchone()["a"]

    lines.append("")
    lines.append("OVERALL:")
    lines.append("  Total responded: %d" % total)
    lines.append("  SLA met: %d (%.1f%%)" % (met, (met / total * 100) if total > 0 else 0))
    lines.append("  SLA breached: %d" % breached)
    lines.append("  Pending response: %d" % pending)
    lines.append("  Avg response time: %s" % format_seconds(avg_time or 0))

    # Per-org breakdown
    lines.append("")
    lines.append("BY ORG:")
    for org in ("gigforge", "techuni", "ai-elevate"):
        org_total = db.execute("SELECT COUNT(*) as c FROM sla_events WHERE org = ? AND responded_at IS NOT NULL", (org,)).fetchone()["c"]
        org_met = db.execute("SELECT COUNT(*) as c FROM sla_events WHERE org = ? AND sla_met = 1", (org,)).fetchone()["c"]
        org_avg = db.execute("SELECT AVG(response_time_seconds) as a FROM sla_events WHERE org = ? AND responded_at IS NOT NULL", (org,)).fetchone()["a"]
        if org_total > 0:
            lines.append("  %s: %d/%d met (%.1f%%) -- avg %s" % (
                org, org_met, org_total, org_met / org_total * 100, format_seconds(org_avg or 0)))

    # Per-priority breakdown
    lines.append("")
    lines.append("BY PRIORITY:")
    for prio in ("urgent", "high", "normal"):
        p_total = db.execute("SELECT COUNT(*) as c FROM sla_events WHERE priority = ? AND responded_at IS NOT NULL", (prio,)).fetchone()["c"]
        p_met = db.execute("SELECT COUNT(*) as c FROM sla_events WHERE priority = ? AND sla_met = 1", (prio,)).fetchone()["c"]
        p_avg = db.execute("SELECT AVG(response_time_seconds) as a FROM sla_events WHERE priority = ? AND responded_at IS NOT NULL", (prio,)).fetchone()["a"]
        threshold_str = format_seconds(SLA_THRESHOLDS[prio])
        if p_total > 0:
            lines.append("  %s (SLA: %s): %d/%d met (%.1f%%) -- avg %s" % (
                prio, threshold_str, p_met, p_total, p_met / p_total * 100, format_seconds(p_avg or 0)))

    # Recent breaches (last 10)
    breaches = db.execute(
        "SELECT sender, agent_id, org, subject, received_at, response_time_seconds, priority FROM sla_events WHERE sla_met = 0 AND responded_at IS NOT NULL ORDER BY received_at DESC LIMIT 10"
    ).fetchall()
    if breaches:
        lines.append("")
        lines.append("RECENT BREACHES:")
        for b in breaches:
            lines.append("  %s | %s | %s | %s | %s (took %s, SLA: %s)" % (
                b["received_at"][:16], b["org"], b["agent_id"], b["sender"],
                b["subject"][:40], format_seconds(b["response_time_seconds"]),
                format_seconds(SLA_THRESHOLDS.get(b["priority"], 28800))))

    lines.append("")
    lines.append("=" * 60)

    db.close()
    report = "\n".join(lines)
    print(report)
    return report


def check_alerts():
    """Check for currently breached SLAs (open events past threshold) and alert."""
    db = get_db()
    now = datetime.now(timezone.utc)

    # Find open events (no response yet) that are past their SLA threshold
    rows = db.execute("SELECT * FROM sla_events WHERE responded_at IS NULL ORDER BY received_at ASC").fetchall()
    breached = []

    for row in rows:
        received = datetime.fromisoformat(row["received_at"])
        if received.tzinfo is None:
            received = received.replace(tzinfo=timezone.utc)
        elapsed = (now - received).total_seconds()
        threshold = SLA_THRESHOLDS.get(row["priority"], SLA_THRESHOLDS["normal"])

        if elapsed > threshold:
            breached.append({
                "sender": row["sender"],
                "agent_id": row["agent_id"],
                "org": row["org"],
                "subject": row["subject"],
                "priority": row["priority"],
                "elapsed": elapsed,
                "threshold": threshold,
                "received_at": row["received_at"],
            })

    db.close()

    if not breached:
        logger.info("SLA alert check: no breaches found")
        return

    # Build alert message
    lines = ["SLA BREACH ALERT -- %d emails past SLA threshold:\n" % len(breached)]
    for b in breached:
        lines.append("  From: %s" % b["sender"])
        lines.append("  Agent: %s (%s)" % (b["agent_id"], b["org"]))
        lines.append("  Subject: %s" % b["subject"])
        lines.append("  Priority: %s (SLA: %s)" % (b["priority"], format_seconds(b["threshold"])))
        lines.append("  Waiting: %s (breached by %s)" % (
            format_seconds(b["elapsed"]),
            format_seconds(b["elapsed"] - b["threshold"])))
        lines.append("  Received: %s" % b["received_at"][:19])
        lines.append("")

    alert_body = "\n".join(lines)
    print(alert_body)

    # Send alert via notify.py
    try:
        priority = "high" if any(b["priority"] == "urgent" for b in breached) else "medium"
        subprocess.run(
            ["python3", "/home/aielevate/notify.py",
             "--priority", priority, "--to", "braun",
             "--title", "SLA Breach: %d emails past threshold" % len(breached),
             "--body", alert_body],
            timeout=30,
        )
        logger.info("SLA breach alert sent for %d events", len(breached))
    except Exception as e:
        logger.error("Failed to send SLA breach alert: %s", e)


def format_seconds(s):
    """Format seconds into human-readable string."""
    if s is None or s == 0:
        return "0s"
    s = int(s)
    if s < 60:
        return "%ds" % s
    if s < 3600:
        return "%dm %ds" % (s // 60, s % 60)
    hours = s // 3600
    mins = (s % 3600) // 60
    if hours < 24:
        return "%dh %dm" % (hours, mins)
    days = hours // 24
    hours = hours % 24
    return "%dd %dh" % (days, hours)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SLA Tracker for AI Elevate")
    parser.add_argument("--report", action="store_true", help="Print SLA compliance report")
    parser.add_argument("--alert", action="store_true", help="Check for SLA breaches and alert")
    parser.add_argument("--stats", action="store_true", help="Quick stats summary")
    args = parser.parse_args()

    if args.report:
        generate_report()
    elif args.alert:
        check_alerts()
    elif args.stats:
        db = get_db()
        total = db.execute("SELECT COUNT(*) as c FROM sla_events").fetchone()["c"]
        pending = db.execute("SELECT COUNT(*) as c FROM sla_events WHERE responded_at IS NULL").fetchone()["c"]
        met = db.execute("SELECT COUNT(*) as c FROM sla_events WHERE sla_met = 1").fetchone()["c"]
        breached = db.execute("SELECT COUNT(*) as c FROM sla_events WHERE sla_met = 0 AND responded_at IS NOT NULL").fetchone()["c"]
        print("Total: %d | Pending: %d | Met: %d | Breached: %d" % (total, pending, met, breached))
        db.close()
    else:
        parser.print_help()
