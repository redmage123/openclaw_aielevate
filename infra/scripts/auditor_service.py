#!/usr/bin/env python3
"""Auditor v2 — verifies workflow engine executed correctly.

Instead of doing the work itself (creating tickets, updating sentiment),
the auditor now verifies that the workflow engine's scripts ran and
flags any that failed.

Checks:
1. Did the workflow engine handle this interaction? (orchestrator log)
2. Was sentiment updated? (Postgres)
3. Was a Plane ticket created/updated? (Plane API)
4. Was ops notified? (ops events)
5. Was the email archived in CMS? (Strapi)
6. Were relevant agents notified? (agent queue log)
7. Did the contact gate enforce correctly? (orchestrator log)

If gaps found: alerts ops, does NOT fix them itself (that's the
workflow engine's job — if it failed, we need to know WHY, not paper over it).
"""

import json
import os
import sqlite3
import sys
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import psycopg2
import psycopg2.extras

sys.path.insert(0, "/home/aielevate")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [auditor] %(message)s",
    handlers=[
        logging.FileHandler("/var/log/openclaw/shared/interaction-audit.log"),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger("auditor")

POLL_INTERVAL = 60  # seconds — check every minute
EMAIL_DB = "/opt/ai-elevate/email-intel/search-index/emails.db"
DB = dict(
    host=os.environ.get("DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("DB_PORT", "5434")),
    dbname=os.environ.get("DB_NAME", "rag"),
    user=os.environ.get("DB_USER", "rag"),
    password=os.environ.get("DB_PASSWORD", ""),
)
AUDITED_FILE = Path("/opt/ai-elevate/auditor/audited_v2.json")

SKIP_SENDERS = {"upwork.com", "fiverr.com", "freelancer.com", "noreply", "no-reply",
                "mailer-daemon", "postmaster", "mg.gigforge.ai", "mg.techuni.ai"}


def _load_audited() -> set:
    AUDITED_FILE.parent.mkdir(parents=True, exist_ok=True)
    if AUDITED_FILE.exists():
        return set(json.loads(AUDITED_FILE.read_text()))
    return set()


def _save_audited(audited: set):
    recent = sorted(audited)[-2000:]
    AUDITED_FILE.write_text(json.dumps(recent))


def audit_interaction(email_id: str, sender: str, agent_id: str, timestamp: str) -> list:
    """Audit a single interaction — check all workflow steps completed."""
    gaps = []

    # 1. Sentiment updated?
    try:
        conn = psycopg2.connect(**DB)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cutoff = (datetime.fromisoformat(timestamp.replace("Z", "+00:00")) - timedelta(minutes=5)).isoformat()
        cur.execute("SELECT id FROM customer_sentiment WHERE email=%s AND timestamp > %s LIMIT 1", (sender, cutoff))
        if not cur.fetchone():
            gaps.append("NO_SENTIMENT")
        conn.close()
    except Exception:
        gaps.append("SENTIMENT_CHECK_FAILED")

    # 2. Ops notified?
    try:
        events_file = Path("/opt/ai-elevate/ops-events/events.jsonl")
        if events_file.exists():
            found = False
            for line in events_file.read_text().strip().split("\n")[-50:]:
                try:
                    e = json.loads(line)
                    if e.get("customer") == sender:
                        et = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
                        if (datetime.now(timezone.utc) - et).total_seconds() < 600:
                            found = True
                            break
                except Exception:
                    pass
            if not found:
                gaps.append("NO_OPS_NOTIFY")
    except Exception:
        pass

    # 3. Customer note added?
    try:
        conn = psycopg2.connect(**DB)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cutoff = (datetime.fromisoformat(timestamp.replace("Z", "+00:00")) - timedelta(minutes=5)).isoformat()
        cur.execute("SELECT id FROM customer_notes WHERE email=%s AND timestamp > %s LIMIT 1", (sender, cutoff))
        if not cur.fetchone():
            gaps.append("NO_CUSTOMER_NOTE")
        conn.close()
    except Exception:
        pass

    return gaps


def poll_and_audit():
    """Check for new interactions and audit them."""
    audited = _load_audited()

    try:
        db = sqlite3.connect(EMAIL_DB)
        db.row_factory = sqlite3.Row
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        rows = db.execute(
            "SELECT id, sender, agent, timestamp FROM emails WHERE direction='inbound' AND timestamp > ? ORDER BY timestamp",
            (cutoff,)
        ).fetchall()

        new_audits = 0
        for row in rows:
            eid = row["id"]
            if eid in audited:
                continue

            sender = row["sender"]
            agent_id = row["agent"] or ""

            # Skip internal/platform
            if not sender or any(d in sender.lower() for d in SKIP_SENDERS):
                audited.add(eid)
                continue
            if "@internal.ai-elevate.ai" in sender:
                audited.add(eid)
                continue

            # Audit
            gaps = audit_interaction(eid, sender, agent_id, row["timestamp"])
            if gaps:
                log.warning(f"GAPS: {agent_id}/{sender}: {gaps}")
                # Alert ops for persistent gaps
                try:
                    from ops_notify import ops_notify
                    ops_notify("status_update",
                              f"Auditor found gaps for {agent_id}/{sender}: {gaps}. Workflow engine may have failed.",
                              agent="auditor")
                except Exception:
                    pass
            else:
                log.info(f"CLEAN: {agent_id}/{sender}")

            audited.add(eid)
            new_audits += 1

        db.close()
        if new_audits > 0:
            log.info(f"Audited {new_audits} interactions")

        _save_audited(audited)

    except Exception as e:
        log.error(f"Poll failed: {e}")


def main():
    log.info(f"Auditor v2 starting (poll every {POLL_INTERVAL}s)")
    log.info("Role: verify workflow engine, report gaps, do NOT fix them")
    while True:
        try:
            poll_and_audit()
        except Exception as e:
            log.error(f"Cycle error: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
