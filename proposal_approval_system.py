#!/usr/bin/env python3
"""Proposal Approval System

Sends Braun an email digest of pending proposals. He replies APPROVE or REJECT
with the proposal number. Approved proposals get an email with the pre-filled
text and a direct link to the Upwork apply page — one click to open, paste, submit.

Flow:
  1. Cron runs every 2 hours during business hours
  2. Checks proposal_queue for status='pending'
  3. Sends Braun a digest email with numbered proposals
  4. Braun replies: "APPROVE 1,3,5" or "REJECT 2"
  5. Approved proposals: sends individual emails with proposal text + apply link
  6. Status updated in DB

Usage:
  python3 proposal_approval_system.py --digest     # Send pending digest
  python3 proposal_approval_system.py --process    # Process approval replies
  python3 proposal_approval_system.py --status     # Show queue status
"""

import sys
import os
import json
import argparse
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone

sys.path.insert(0, "/home/aielevate")
from send_email import send_email
from logging_config import get_logger

log = get_logger("proposal-approval")

DB = dict(
    host="127.0.0.1", port=5434, dbname="rag",
    user="rag", password=os.environ.get("DB_PASSWORD", "rag_vec_2026"),
)

BRAUN_EMAIL = "braun.brelin@ai-elevate.ai"


def get_pending():
    """Get all pending proposals."""
    conn = psycopg2.connect(**DB)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT id, job_title, job_url, job_budget, proposal_text, "
        "recommended_bid, platform, created_at "
        "FROM proposal_queue WHERE status = 'pending' ORDER BY created_at DESC"
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def update_status(proposal_id, status):
    """Update proposal status."""
    conn = psycopg2.connect(**DB)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "UPDATE proposal_queue SET status = %s, updated_at = NOW() WHERE id = %s",
        (status, proposal_id)
    )
    conn.close()


def send_digest():
    """Send Braun an email digest of pending proposals."""
    pending = get_pending()
    if not pending:
        print("No pending proposals.")
        return

    lines = []
    for i, p in enumerate(pending, 1):
        lines.append(
            f"#{i}. [{p['platform'].upper()}] {p['job_title']}\n"
            f"   Budget: {p['job_budget']}  |  Our bid: ${p['recommended_bid']}\n"
            f"   URL: {p['job_url']}\n"
        )

    body = f"""Braun,

You have {len(pending)} proposals waiting for review:

{chr(10).join(lines)}

To approve, reply with: APPROVE 1,2,3 (comma-separated numbers)
To reject, reply with: REJECT 4,5
To approve all: APPROVE ALL

Once approved, I'll send you individual emails with the proposal text
and direct apply links so you can submit them on Upwork.

— GigForge Proposal Queue"""

    result = send_email(
        to=BRAUN_EMAIL,
        subject=f"[ACTION REQUIRED] {len(pending)} proposals awaiting your approval",
        body=body,
        agent_id="gigforge-sales",
        cc="",
    )
    log.info(f"Digest sent: {len(pending)} proposals ({result.get('status')})")
    print(f"Digest sent to {BRAUN_EMAIL}: {len(pending)} proposals")


def send_approved_proposal(proposal):
    """Send Braun the approved proposal text with apply link."""
    body = f"""Braun,

APPROVED — here's the proposal to submit:

Platform: {proposal['platform'].upper()}
Job: {proposal['job_title']}
Bid: ${proposal['recommended_bid']}

APPLY HERE: {proposal['job_url']}

--- PROPOSAL TEXT (copy-paste below this line) ---

{proposal['proposal_text']}

--- END PROPOSAL TEXT ---

Steps:
1. Click the apply link above
2. Set bid to ${proposal['recommended_bid']}
3. Paste the proposal text
4. Submit

— GigForge Sales"""

    result = send_email(
        to=BRAUN_EMAIL,
        subject=f"[SUBMIT] {proposal['platform'].upper()}: {proposal['job_title'][:50]}",
        body=body,
        agent_id="gigforge-sales",
        cc="",
    )
    log.info(f"Approved proposal sent: {proposal['job_title'][:50]} ({result.get('status')})")
    print(f"  Sent: {proposal['job_title'][:50]}")


def process_approval(text):
    """Process an approval/rejection reply.

    Expects text like:
      APPROVE 1,2,3
      APPROVE ALL
      REJECT 4,5
    """
    pending = get_pending()
    if not pending:
        print("No pending proposals to process.")
        return

    text = text.strip().upper()

    if text.startswith("APPROVE ALL"):
        for p in pending:
            update_status(p["id"], "approved")
            send_approved_proposal(p)
        print(f"All {len(pending)} proposals approved and sent.")
        return

    if text.startswith("APPROVE"):
        nums_str = text.replace("APPROVE", "").strip()
        nums = [int(n.strip()) for n in nums_str.split(",") if n.strip().isdigit()]
        for n in nums:
            if 1 <= n <= len(pending):
                p = pending[n - 1]
                update_status(p["id"], "approved")
                send_approved_proposal(p)
            else:
                print(f"  Skipped #{n} (out of range)")
        print(f"Approved {len(nums)} proposals.")

    elif text.startswith("REJECT"):
        nums_str = text.replace("REJECT", "").strip()
        nums = [int(n.strip()) for n in nums_str.split(",") if n.strip().isdigit()]
        for n in nums:
            if 1 <= n <= len(pending):
                p = pending[n - 1]
                update_status(p["id"], "rejected")
                print(f"  Rejected: {p['job_title'][:50]}")
            else:
                print(f"  Skipped #{n} (out of range)")
        print(f"Rejected {len(nums)} proposals.")


def show_status():
    """Show all proposals and their status."""
    conn = psycopg2.connect(**DB)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT job_title, platform, status, recommended_bid, created_at "
        "FROM proposal_queue ORDER BY created_at DESC LIMIT 20"
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        print("No proposals in queue.")
        return

    print(f"{'Status':12s} | {'Platform':10s} | {'Bid':>8s} | Title")
    print("-" * 80)
    for r in rows:
        print(f"{r['status']:12s} | {r['platform']:10s} | ${r['recommended_bid']:>6} | {r['job_title'][:45]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Proposal Approval System")
    parser.add_argument("--digest", action="store_true", help="Send pending proposals digest to Braun")
    parser.add_argument("--process", type=str, metavar="TEXT", help='Process approval: "APPROVE 1,2,3" or "REJECT 4"')
    parser.add_argument("--approve-all", action="store_true", help="Approve all pending proposals")
    parser.add_argument("--status", action="store_true", help="Show queue status")
    args = parser.parse_args()

    if args.digest:
        send_digest()
    elif args.process:
        process_approval(args.process)
    elif args.approve_all:
        process_approval("APPROVE ALL")
    elif args.status:
        show_status()
    else:
        parser.print_help()
