#!/usr/bin/env python3
"""Proposal Approval Queue — human-in-the-loop for platform submissions.

Agents draft proposals. Humans approve them. Nothing gets submitted without a click.

Flow:
  1. Scout finds a job → creates a queue entry with job details
  2. Sales drafts a proposal → attaches it to the queue entry
  3. Human receives email digest of pending proposals
  4. Human approves/rejects via email reply or web endpoint
  5. On approval: agent is notified to submit (manually or via API if approved)

Storage: RAG Postgres (proposal_queue table)

Usage:
  from proposal_queue import queue_proposal, approve, reject, get_pending, send_digest

  # Scout/Sales queues a proposal
  queue_proposal(
      platform="upwork",
      job_url="https://upwork.com/jobs/...",
      job_title="Build a React Dashboard",
      job_budget="$5,000",
      proposal_text="Hi, I noticed your project...",
      recommended_bid="$4,500",
      org="gigforge",
      drafted_by="gigforge-sales",
  )

  # Human approves
  approve(proposal_id=1, approved_by="braun")

  # Human rejects
  reject(proposal_id=1, reason="Too low budget")

  # Get pending proposals
  pending = get_pending()

  # Send email digest of pending proposals to the approval team
  send_digest()

CLI:
  python3 proposal_queue.py pending
  python3 proposal_queue.py approve --id 5
  python3 proposal_queue.py reject --id 5 --reason "Not a good fit"
  python3 proposal_queue.py digest
"""

import json
import sys
import subprocess
import base64
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras

sys.path.insert(0, "/home/aielevate")

DB_HOST = "127.0.0.1"
DB_PORT = 5434
DB_NAME = "rag"
DB_USER = "rag"
DB_PASS = "rag_vec_2026"


def _get_db():
    conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""CREATE TABLE IF NOT EXISTS proposal_queue (
        id SERIAL PRIMARY KEY,
        platform TEXT NOT NULL,
        job_url TEXT,
        job_title TEXT NOT NULL,
        job_budget TEXT,
        job_description TEXT,
        proposal_text TEXT NOT NULL,
        recommended_bid TEXT,
        org TEXT DEFAULT 'gigforge',
        drafted_by TEXT,
        status TEXT DEFAULT 'pending',
        approved_by TEXT,
        rejected_reason TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        reviewed_at TIMESTAMPTZ,
        submitted_at TIMESTAMPTZ,
        notes TEXT
    )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pq_status ON proposal_queue(status)")
    return conn, cur


def queue_proposal(
    platform: str,
    job_title: str,
    proposal_text: str,
    job_url: str = "",
    job_budget: str = "",
    job_description: str = "",
    recommended_bid: str = "",
    org: str = "gigforge",
    drafted_by: str = "",
    notes: str = "",
) -> dict:
    """Add a proposal to the approval queue."""
    conn, cur = _get_db()
    cur.execute(
        """INSERT INTO proposal_queue
        (platform, job_url, job_title, job_budget, job_description, proposal_text, recommended_bid, org, drafted_by, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, platform, job_title, status, created_at""",
        (platform, job_url, job_title, job_budget, job_description, proposal_text, recommended_bid, org, drafted_by, notes)
    )
    result = dict(cur.fetchone())
    conn.close()

    # Notify ops
    try:
        from ops_notify import ops_notify
        ops_notify("status_update", f"New proposal queued for approval: {job_title} on {platform} ({recommended_bid})", agent=drafted_by)
    except Exception:
        pass

    return result


def approve(proposal_id: int, approved_by: str = "braun", notes: str = "") -> dict:
    """Approve a proposal for submission."""
    conn, cur = _get_db()
    cur.execute(
        """UPDATE proposal_queue SET status='approved', approved_by=%s, reviewed_at=NOW(), notes=COALESCE(notes,'') || %s
        WHERE id=%s AND status='pending'
        RETURNING id, platform, job_title, drafted_by""",
        (approved_by, f"\nApproved by {approved_by}: {notes}" if notes else "", proposal_id)
    )
    row = cur.fetchone()
    conn.close()

    if row:
        result = dict(row)
        # Notify the drafting agent to submit
        try:
            agent = result.get("drafted_by", "gigforge-sales")
            subprocess.Popen(
                ["agent-queue", "--agent", agent,
                 "--message", f"PROPOSAL APPROVED — Submit now.\n\n"
                 f"Proposal #{result['id']} for '{result['job_title']}' on {result['platform']} "
                 f"has been approved by {approved_by}.\n\n"
                 f"Submit it on the platform. If API submission is available, use it. "
                 f"Otherwise, flag it for manual submission.\n\n"
                 f"After submission, update the status: "
                 f"python3 -c \"import sys;sys.path.insert(0,'/home/aielevate');"
                 f"from proposal_queue import mark_submitted;mark_submitted({result['id']})\"",
                 "--thinking", "low", "--timeout", "180"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass
        return result
    return {"error": "Not found or already reviewed"}


def reject(proposal_id: int, reason: str = "", rejected_by: str = "braun") -> dict:
    """Reject a proposal."""
    conn, cur = _get_db()
    cur.execute(
        """UPDATE proposal_queue SET status='rejected', rejected_reason=%s, approved_by=%s, reviewed_at=NOW()
        WHERE id=%s AND status='pending'
        RETURNING id, job_title""",
        (reason, rejected_by, proposal_id)
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else {"error": "Not found or already reviewed"}


def mark_submitted(proposal_id: int) -> dict:
    """Mark a proposal as submitted on the platform."""
    conn, cur = _get_db()
    cur.execute(
        "UPDATE proposal_queue SET status='submitted', submitted_at=NOW() WHERE id=%s RETURNING id, job_title",
        (proposal_id,)
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else {"error": "Not found"}


def get_pending(platform: str = None) -> list:
    """Get all pending proposals."""
    conn, cur = _get_db()
    if platform:
        cur.execute("SELECT * FROM proposal_queue WHERE status='pending' AND platform=%s ORDER BY created_at", (platform,))
    else:
        cur.execute("SELECT * FROM proposal_queue WHERE status='pending' ORDER BY created_at")
    results = [dict(r) for r in cur.fetchall()]
    conn.close()
    return results


def get_stats() -> dict:
    """Get queue statistics."""
    conn, cur = _get_db()
    cur.execute("SELECT status, COUNT(*) as count FROM proposal_queue GROUP BY status")
    stats = {r["status"]: r["count"] for r in cur.fetchall()}
    conn.close()
    return stats


def send_digest(to_email: str = "braun.brelin@ai-elevate.ai"):
    """Send an email digest of pending proposals for approval."""
    pending = get_pending()
    if not pending:
        return {"status": "no_pending"}

    lines = [f"You have {len(pending)} proposals awaiting your approval:\n"]

    for p in pending:
        lines.append(f"--- Proposal #{p['id']} ---")
        lines.append(f"Platform: {p['platform']}")
        lines.append(f"Job: {p['job_title']}")
        lines.append(f"Budget: {p['job_budget'] or 'Not specified'}")
        lines.append(f"Our bid: {p['recommended_bid'] or 'Not specified'}")
        lines.append(f"URL: {p['job_url'] or 'N/A'}")
        lines.append(f"Drafted by: {p['drafted_by']}")
        lines.append(f"Queued: {str(p['created_at'])[:19]}")
        lines.append(f"\nProposal preview:\n{p['proposal_text'][:300]}...")
        lines.append(f"\nTo approve: reply with 'APPROVE {p['id']}'")
        lines.append(f"To reject: reply with 'REJECT {p['id']} reason here'")
        lines.append("")

    lines.append("---")
    lines.append("Reply to this email with APPROVE or REJECT for each proposal.")
    lines.append("You can also approve/reject at the command line:")
    lines.append("  python3 /home/aielevate/proposal_queue.py approve --id N")
    lines.append("  python3 /home/aielevate/proposal_queue.py reject --id N --reason 'reason'")

    body = "\n".join(lines)

    try:
        import urllib.request
        key = open("/opt/ai-elevate/credentials/mailgun-api-key.txt").read().strip()
        creds = base64.b64encode(("api:" + key).encode()).decode()

        from urllib.parse import urlencode
        data = urlencode({
            "from": "GigForge Proposals <proposals@gigforge.ai>",
            "to": to_email,
            "h:Reply-To": "ops@gigforge.ai",
            "subject": f"[ACTION REQUIRED] {len(pending)} proposals awaiting your approval",
            "text": body,
        }).encode()

        req = urllib.request.Request("https://api.mailgun.net/v3/gigforge.ai/messages", data=data, method="POST")
        req.add_header("Authorization", "Basic " + creds)
        urllib.request.urlopen(req, timeout=15)
        return {"status": "sent", "count": len(pending)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def process_approval_email(sender: str, body: str) -> list:
    """Process an approval/rejection email. Returns list of actions taken."""
    import re
    actions = []

    # Check sender is authorized
    from directives import AUTHORIZED_ISSUERS
    sender_lower = sender.lower().strip()
    if not any(auth in sender_lower for auth in AUTHORIZED_ISSUERS):
        return [{"error": f"Unauthorized sender: {sender}"}]

    approver = sender_lower.split("@")[0] if "@" in sender_lower else sender_lower

    # Find APPROVE N and REJECT N patterns
    for match in re.finditer(r'APPROVE\s+(\d+)', body, re.IGNORECASE):
        pid = int(match.group(1))
        result = approve(pid, approved_by=approver)
        actions.append({"action": "approved", "id": pid, "result": result})

    for match in re.finditer(r'REJECT\s+(\d+)\s*(.*)', body, re.IGNORECASE):
        pid = int(match.group(1))
        reason = match.group(2).strip() or "No reason given"
        result = reject(pid, reason=reason, rejected_by=approver)
        actions.append({"action": "rejected", "id": pid, "reason": reason, "result": result})

    return actions


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Proposal Approval Queue")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("pending")
    sub.add_parser("stats")
    sub.add_parser("digest")

    ap = sub.add_parser("approve")
    ap.add_argument("--id", type=int, required=True)
    ap.add_argument("--by", default="braun")
    ap.add_argument("--notes", default="")

    rp = sub.add_parser("reject")
    rp.add_argument("--id", type=int, required=True)
    rp.add_argument("--reason", default="")
    rp.add_argument("--by", default="braun")

    args = parser.parse_args()

    if args.command == "pending":
        for p in get_pending():
            print(f"  #{p['id']} [{p['platform']}] {p['job_title']} — bid: {p['recommended_bid']} — by: {p['drafted_by']}")
        if not get_pending():
            print("  No pending proposals")

    elif args.command == "stats":
        for status, count in get_stats().items():
            print(f"  {status}: {count}")

    elif args.command == "digest":
        r = send_digest()
        print(f"Digest: {r}")

    elif args.command == "approve":
        r = approve(args.id, args.by, args.notes)
        print(f"Approved: {r}")

    elif args.command == "reject":
        r = reject(args.id, args.reason, args.by)
        print(f"Rejected: {r}")

    else:
        parser.print_help()
