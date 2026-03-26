#!/usr/bin/env python3
"""Email Digest — batches all agent updates into one daily email.

Instead of 50 individual emails, Braun gets one digest at 08:00 and 18:00.

Agents write to the digest queue instead of emailing directly.
The digest cron compiles and sends.

CRITICAL emails (incidents, customer replies) still go immediately.
Everything else gets batched.
"""

import sys
import os
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/aielevate")

log = logging.getLogger("digest")

DIGEST_DIR = Path("/opt/ai-elevate/email-digest")
DIGEST_DIR.mkdir(parents=True, exist_ok=True)

QUEUE_FILE = DIGEST_DIR / "queue.jsonl"

# These subjects/types go immediately — everything else gets batched
IMMEDIATE_KEYWORDS = [
    "incident", "down", "crash", "urgent", "critical", "security",
    "customer reply", "payment", "invoice",
]


def queue_for_digest(subject, body, agent_id, priority="normal"):
    """Add an item to the digest queue instead of sending immediately."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "subject": subject,
        "body": body[:1000],
        "agent_id": agent_id,
        "priority": priority,
    }
    with open(QUEUE_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    log.info(f"Queued for digest: {subject[:50]} (from {agent_id})")


def should_send_immediately(subject):
    """Check if this email should bypass the digest and send now."""
    subject_lower = subject.lower()
    return any(kw in subject_lower for kw in IMMEDIATE_KEYWORDS)


def compile_and_send():
    """Compile all queued items into one digest email and send."""
    if not QUEUE_FILE.exists():
        return

    lines = QUEUE_FILE.read_text().strip().split("\n")
    if not lines or not lines[0]:
        return

    items = []
    for line in lines:
        try:
            items.append(json.loads(line))
        except Exception:
            pass

    if not items:
        return

    # Group by agent
    from collections import defaultdict
    by_agent = defaultdict(list)
    for item in items:
        by_agent[item["agent_id"]].append(item)

    # Build digest body
    body = f"Daily Digest — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
    body += f"{len(items)} updates from {len(by_agent)} agents\n"
    body += "=" * 60 + "\n\n"

    for agent_id, agent_items in sorted(by_agent.items()):
        body += f"--- {agent_id} ({len(agent_items)} updates) ---\n\n"
        for item in agent_items:
            ts = item["timestamp"][:16].replace("T", " ")
            body += f"[{ts}] {item['subject']}\n"
            # Include first 200 chars of body
            preview = item["body"][:200].replace("\n", " ")
            body += f"  {preview}...\n\n"
        body += "\n"

    # Send the digest
    from send_email import send_email
    send_email(
        to="braun.brelin@ai-elevate.ai",
        subject=f"Daily Digest — {len(items)} updates from {len(by_agent)} agents",
        body=body,
        agent_id="operations",
        cc="peter.munro@ai-elevate.ai",
    )

    # Clear the queue
    QUEUE_FILE.write_text("")
    log.info(f"Digest sent: {len(items)} items from {len(by_agent)} agents")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Email Digest")
    parser.add_argument("--send", action="store_true", help="Compile and send digest now")
    parser.add_argument("--status", action="store_true", help="Show queue status")
    args = parser.parse_args()

    if args.send:
        compile_and_send()
    elif args.status:
        if QUEUE_FILE.exists():
            lines = [l for l in QUEUE_FILE.read_text().strip().split("\n") if l]
            print(f"Queued: {len(lines)} items")
            for line in lines[-5:]:
                item = json.loads(line)
                print(f"  {item['agent_id']}: {item['subject'][:50]}")
        else:
            print("Queue empty")
