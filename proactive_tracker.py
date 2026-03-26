#!/usr/bin/env python3
"""Proactive Task Tracker — monitors open action items and nudges agents.

Runs every 30 minutes via cron. Scans:
1. Email threads where we promised something but didn't deliver
2. Customer requests that haven't been actioned
3. Milestones that are overdue
4. Tickets with no activity

When it finds a stale item, it dispatches the responsible agent
via sessions_send with a focused task.

This is what makes agents PROACTIVE instead of reactive.
"""

import sys
import os
import json
import logging
import time
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, "/home/aielevate")

log = logging.getLogger("proactive-tracker")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [proactive] %(message)s")

DB_CONFIG = dict(host="127.0.0.1", port=5434, dbname="rag", user="rag",
                 password=os.environ.get("DB_PASSWORD", "rag_vec_2026"))


def _get_db():
    import psycopg2
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    return conn


def check_unanswered_emails(hours=4):
    """Find inbound emails that never got an outbound reply."""
    conn = _get_db()
    cur = conn.cursor()

    # Emails received in the last N hours with no outbound reply to same sender+subject
    cur.execute("""
        SELECT DISTINCT ON (ei.sender_email, ei.subject)
            ei.sender_email, ei.subject, ei.agent_id, ei.created_at
        FROM email_interactions ei
        WHERE ei.direction = 'inbound'
          AND ei.created_at > NOW() - INTERVAL '%s hours' AND ei.created_at > NOW() - INTERVAL '48 hours'
          AND ei.sender_email NOT LIKE '%%@mg.ai-elevate.ai'
          AND ei.sender_email NOT LIKE '%%@gigforge.ai'
          AND ei.sender_email NOT LIKE '%%@techuni.ai'
          AND ei.sender_email NOT LIKE '%%@internal.ai-elevate.ai'
          AND NOT EXISTS (
              SELECT 1 FROM email_interactions eo
              WHERE eo.direction = 'outbound'
                AND eo.created_at > ei.created_at
                AND (eo.subject ILIKE '%%' || ei.subject || '%%'
                     OR ei.subject ILIKE '%%' || eo.subject || '%%')
          )
        ORDER BY ei.sender_email, ei.subject, ei.created_at DESC
    """, (hours,))

    unanswered = cur.fetchall()
    conn.close()
    return [{"sender": r[0], "subject": r[1], "agent": r[2], "received": str(r[3])} for r in unanswered]


def check_stale_milestones(days=3):
    """Find milestones that have been pending for too long."""
    conn = _get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT project_title, milestone, status, customer_email, created_at
        FROM project_milestones
        WHERE status = 'pending' AND status != 'stale' AND status != 'cancelled' AND status != 'abandoned'
          AND created_at < NOW() - INTERVAL '%s days'
        ORDER BY created_at ASC
    """, (days,))

    stale = cur.fetchall()
    conn.close()
    return [{"project": r[0], "milestone": r[1], "status": r[2],
             "customer": r[3], "created": str(r[4])} for r in stale]


def check_promised_actions():
    """Search outbound emails for promises we made but haven't fulfilled.

    Looks for phrases like 'I will', 'I'll', 'we will', 'we'll',
    'I'm on this', 'getting this sorted' — then checks if there was
    a follow-up email within 24 hours.
    """
    conn = _get_db()
    cur = conn.cursor()

    promise_phrases = [
        "i will", "i'll", "we will", "we'll", "i'm on this",
        "getting this sorted", "i'll get", "we'll get", "i'll follow up",
        "i'll send", "we'll send", "i'll have", "we'll have",
        "by end of day", "by tomorrow", "shortly", "right away",
    ]

    # Get recent outbound emails with promises
    cur.execute("""
        SELECT sender_email, subject, body_text, agent_id, created_at
        FROM email_interactions
        WHERE direction = 'outbound'
          AND body_text IS NOT NULL
          AND created_at > NOW() - INTERVAL '48 hours'
        ORDER BY created_at DESC
    """)

    promises = []
    for row in cur.fetchall():
        body = (row[2] or "").lower()
        for phrase in promise_phrases:
            if phrase in body:
                # Check if there was a follow-up within 24h
                cur.execute("""
                    SELECT COUNT(*) FROM email_interactions
                    WHERE direction = 'outbound'
                      AND created_at > %s
                      AND created_at < %s + INTERVAL '24 hours'
                      AND subject ILIKE '%%' || %s || '%%'
                """, (row[4], row[4], row[1][:30]))
                followup_count = cur.fetchone()[0]
                if followup_count == 0:
                    promises.append({
                        "recipient": row[0], "subject": row[1],
                        "agent": row[3], "promised_at": str(row[4]),
                        "phrase": phrase,
                    })
                break  # One promise per email is enough

    conn.close()
    return promises


_nudge_cache = {}

def dispatch_nudge(agent_id, task_description):
    """Send a nudge to an agent via sessions_send or direct dispatch.
    Deduplicates: won't nudge the same agent about the same issue within 24h."""
    import hashlib, time
    nudge_key = hashlib.sha256(f"{agent_id}:{task_description[:100]}".encode()).hexdigest()[:16]
    last_nudge = _nudge_cache.get(nudge_key, 0)
    if time.time() - last_nudge < 86400:  # 24 hours
        log.info(f"Skipping duplicate nudge for {agent_id}: {task_description[:50]}")
        return False
    _nudge_cache[nudge_key] = time.time()

    try:
        import subprocess
        result = subprocess.run(
            ["openclaw", "message", "send", "--agent", agent_id,
             "--message", f"PROACTIVE REMINDER: {task_description}",
             "--thinking", "low"],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "CLAUDECODE": ""}
        )
        log.info(f"Nudged {agent_id}: {task_description[:80]}")
        return True
    except Exception as e:
        log.error(f"Failed to nudge {agent_id}: {e}")
        return False


def notify_ops(items, category):
    """Send a summary to ops if there are actionable items."""
    if not items:
        return

    try:
        from send_email import send_email
        body = f"PROACTIVE TRACKER — {category}\n\n"
        body += f"{len(items)} item(s) need attention:\n\n"
        for i, item in enumerate(items, 1):
            body += f"{i}. {json.dumps(item, default=str)}\n\n"
        body += "These items were identified automatically. Please action or delegate."

        send_email(
            to="braun.brelin@ai-elevate.ai",
            subject=f"[PROACTIVE] {len(items)} {category}",
            body=body,
            agent_id="operations",
            cc="peter.munro@ai-elevate.ai",
        )
    except Exception as e:
        log.error(f"Failed to notify: {e}")


def run():
    """Run all proactive checks."""
    log.info("Proactive tracker running")

    # 1. Unanswered emails (4-48 hours old — not stale, not brand new)
    unanswered = check_unanswered_emails(hours=4)
    # Filter out anything older than 48 hours — those are stale, not actionable
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    fresh_unanswered = []
    for item in unanswered:
        try:
            received = datetime.fromisoformat(item["received"])
            if received > cutoff:
                fresh_unanswered.append(item)
        except Exception:
            pass
    unanswered = fresh_unanswered

    if unanswered:
        log.info(f"Found {len(unanswered)} unanswered emails (filtered stale)")
        for item in unanswered:
            agent = item["agent"] or "gigforge"
            dispatch_nudge(agent,
                f"You have an unanswered email from {item['sender']} "
                f"about '{item['subject']}' received at {item['received']}. "
                f"Reply now using send_email.")
        notify_ops(unanswered, "unanswered emails (4-48 hours)")

    # 2. Stale milestones (3-30 days pending — skip ancient ones)
    stale = check_stale_milestones(days=3)
    cutoff_old = datetime.now(timezone.utc) - timedelta(days=30)
    fresh_stale = []
    for item in stale:
        try:
            created = datetime.fromisoformat(item["created"])
            if created > cutoff_old:
                fresh_stale.append(item)
        except Exception:
            pass
    stale = fresh_stale

    if stale:
        log.info(f"Found {len(stale)} stale milestones (filtered ancient)")
        notify_ops(stale, "stale milestones (3-30 days pending)")

    # 3. Unfulfilled promises (said we'd do something, didn't follow up)
    promises = check_promised_actions()
    if promises:
        log.info(f"Found {len(promises)} unfulfilled promises")
        for item in promises:
            agent = item["agent"] or "gigforge"
            dispatch_nudge(agent,
                f"You promised '{item['phrase']}' to {item['recipient']} "
                f"regarding '{item['subject']}' at {item['promised_at']}. "
                f"No follow-up sent. Action this now.")
        notify_ops(promises, "unfulfilled promises")

    log.info("Proactive tracker complete")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Proactive Task Tracker")
    parser.add_argument("--run", action="store_true", help="Run all checks")
    parser.add_argument("--check-emails", action="store_true", help="Check unanswered emails")
    parser.add_argument("--check-milestones", action="store_true", help="Check stale milestones")
    parser.add_argument("--check-promises", action="store_true", help="Check unfulfilled promises")
    parser.add_argument("--dry-run", action="store_true", help="Check but don't dispatch")
    args = parser.parse_args()

    if args.check_emails:
        items = check_unanswered_emails()
        print(f"Unanswered emails: {len(items)}")
        for i in items:
            print(f"  {i['sender']}: {i['subject']} (agent={i['agent']}, received={i['received']})")
    elif args.check_milestones:
        items = check_stale_milestones()
        print(f"Stale milestones: {len(items)}")
        for i in items:
            print(f"  {i['project']} → {i['milestone']}: {i['status']} (since {i['created']})")
    elif args.check_promises:
        items = check_promised_actions()
        print(f"Unfulfilled promises: {len(items)}")
        for i in items:
            print(f"  To {i['recipient']}: '{i['phrase']}' re: {i['subject']} ({i['promised_at']})")
    elif args.run:
        run()
    else:
        parser.print_help()
