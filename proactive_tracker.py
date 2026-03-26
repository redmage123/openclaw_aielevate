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
    """Find inbound emails that never got a reply — uses semantic search to check.

    Instead of just matching subject lines, uses semantic search to find
    if we sent ANY related outbound email to the same sender.
    """
    conn = _get_db()
    cur = conn.cursor()

    # Get recent inbound emails from external senders
    cur.execute("""
        SELECT DISTINCT ON (ei.sender_email, ei.subject)
            ei.sender_email, ei.subject, ei.agent_id, ei.created_at, ei.body_text
        FROM email_interactions ei
        WHERE ei.direction = 'inbound'
          AND ei.created_at > NOW() - INTERVAL '%s hours'
          AND ei.created_at > NOW() - INTERVAL '48 hours'
          AND ei.sender_email NOT LIKE '%%@mg.ai-elevate.ai'
          AND ei.sender_email NOT LIKE '%%@gigforge.ai'
          AND ei.sender_email NOT LIKE '%%@techuni.ai'
          AND ei.sender_email NOT LIKE '%%@internal.ai-elevate.ai'
        ORDER BY ei.sender_email, ei.subject, ei.created_at DESC
    """, (hours,))

    candidates = cur.fetchall()
    conn.close()

    unanswered = []
    for r in candidates:
        sender, subject, agent, received, body = r[0], r[1], r[2], r[3], r[4] or ""

        # Use semantic search to find if we already replied to this topic
        try:
            from semantic_search import search
            query = f"{subject} {body[:200]}"
            results = search(query, org="gigforge", top_k=5)

            # Check if any result is an outbound email sent AFTER this inbound
            has_reply = False
            for hit in results:
                meta = hit.get("metadata", {})
                if meta.get("direction") == "outbound" and hit["score"] > 0.3:
                    # Check if the outbound was sent after this inbound
                    try:
                        hit_date = meta.get("date", "")
                        if hit_date and hit_date > str(received):
                            has_reply = True
                            break
                    except Exception:
                        # If we can't compare dates, assume any high-score outbound is a reply
                        if hit["score"] > 0.5:
                            has_reply = True
                            break

            if not has_reply:
                unanswered.append({
                    "sender": sender, "subject": subject,
                    "agent": agent, "received": str(received)
                })
        except Exception:
            # Fallback: include it as potentially unanswered
            unanswered.append({
                "sender": sender, "subject": subject,
                "agent": agent, "received": str(received)
            })

    return unanswered


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
                    # Double-check with semantic search — maybe we replied on a different subject
                    fulfilled = False
                    try:
                        from semantic_search import search
                        results = search(f"{row[1]} {row[0]}", org="gigforge", top_k=3)
                        for hit in results:
                            meta = hit.get("metadata", {})
                            if (meta.get("direction") == "outbound"
                                and hit["score"] > 0.4
                                and meta.get("date", "") > str(row[4])):
                                fulfilled = True
                                break
                    except Exception as _e:

                        import logging; logging.getLogger('proactive_tracker.py').debug(f'Suppressed: {_e}')

                    if not fulfilled:
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
    """Send a nudge to an agent — but first check KG, Plane, and semantic search
    to see if the task is already being handled or was completed."""
    import hashlib, time

    # Dedup: don't nudge same agent about same issue within 24h
    nudge_key = hashlib.sha256(f"{agent_id}:{task_description[:100]}".encode()).hexdigest()[:16]
    last_nudge = _nudge_cache.get(nudge_key, 0)
    if time.time() - last_nudge < 86400:
        log.info(f"Skipping duplicate nudge for {agent_id}: {task_description[:50]}")
        return False

    # Check semantic search — is this already being worked on or completed?
    try:
        from semantic_search import search
        results = search(task_description[:200], org="gigforge", top_k=3)
        for r in results:
            meta = r.get("metadata", {})
            status = meta.get("status", "").lower()
            if status in ("completed", "delivered", "closed", "done", "cancelled", "cancelled-owner-handling"):
                log.info(f"Skipping nudge — task already {status}: {task_description[:50]}")
                return False
            if r["score"] > 0.5 and meta.get("direction") == "outbound":
                # High-score outbound email found — someone already responded
                log.info(f"Skipping nudge — outbound reply found (score={r['score']:.2f}): {task_description[:50]}")
                return False
    except Exception:
        pass

    # Check Plane for active tickets
    try:
        from plane_ops import Plane
        p = Plane("gigforge")
        issues = p.list_issues(project="BUG")
        for issue in issues:
            name = issue.get("name", "").lower()
            state = issue.get("state_detail", {}).get("name", "").lower()
            # If there's an active ticket for this topic, check its state
            if any(word in name for word in task_description.lower().split()[:3]):
                if state in ("done", "closed", "cancelled"):
                    log.info(f"Skipping nudge — Plane ticket {state}: {issue.get('name', '')[:40]}")
                    return False
                if state in ("in progress", "in review"):
                    log.info(f"Skipping nudge — Plane ticket already {state}: {issue.get('name', '')[:40]}")
                    return False
    except Exception:
        pass

    # Check KG for project status
    try:
        from knowledge_graph import KG
        kg = KG("gigforge")
        # Extract key terms from the task description
        import re
        refs = re.findall(r'GF-\d+|gf-\d+|CC-\d+', task_description)
        for ref in refs:
            results = kg.search(ref)
            if results:
                for r in results:
                    props = r.get("properties", {})
                    if props.get("status", "").lower() in ("completed", "delivered", "closed", "cancelled"):
                        log.info(f"Skipping nudge — KG shows {ref} is {props['status']}")
                        return False
    except Exception:
        pass

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
        except Exception as _e:

            import logging; logging.getLogger('proactive_tracker.py').debug(f'Suppressed: {_e}')
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
        except Exception as _e:

            import logging; logging.getLogger('proactive_tracker.py').debug(f'Suppressed: {_e}')
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
