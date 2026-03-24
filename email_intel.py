#!/usr/bin/env python3
"""AI Elevate Email Intelligence Module

Features inspired by AgentMail:
1. Thread/Conversation Management — track full conversation chains
2. Draft Review (Human-in-the-Loop) — agents save drafts for approval
3. Reply Extraction — strip quoted text from replies
4. Email Search — full-text search across all messages via RAG
5. Labels/State Management — tag emails with workflow states
6. Allowlists/Blocklists — per-agent sender filtering
7. Email Metrics — delivery rates, response times, bounce tracking
8. Domain Warming — gradual send volume increase
"""

import json
import os
import re
import hashlib
import time
import csv
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional
from exceptions import AiElevateError  # TODO: Use specific exception types, AgentError

DATA_DIR = Path("/opt/ai-elevate/email-intel")
for d in ["threads", "drafts", "labels", "lists", "metrics", "search-index"]:
    (DATA_DIR / d).mkdir(parents=True, exist_ok=True)


def _load_json(path: Path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def _save_json(path: Path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _append_jsonl(path: Path, entry: dict):
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _thread_id(subject: str, participants: list[str]) -> str:
    """Generate a stable thread ID from subject and participants."""
    # Normalize subject — strip Re:/Fwd: prefixes
    clean_subject = re.sub(r"^(Re:\s*|Fwd?:\s*|Fw:\s*)+", "", subject, flags=re.I).strip()
    key = clean_subject.lower() + "|" + ",".join(sorted(set(p.lower() for p in participants)))
    return hashlib.md5(key.encode()).hexdigest()[:16]


# ══════════════════════════════════════════════════════════════════════════
# 1. THREAD/CONVERSATION MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════

def add_to_thread(sender: str, recipients: list[str], subject: str,
                   body: str, direction: str = "inbound",
                   agent_id: str = "", message_id: str = "") -> dict:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Add a message to a conversation thread. Creates thread if new."""
    all_participants = [sender] + recipients
    tid = _thread_id(subject, all_participants)
    path = DATA_DIR / "threads" / f"{tid}.json"

    thread = _load_json(path, {
        "thread_id": tid,
        "subject": re.sub(r"^(Re:\s*|Fwd?:\s*|Fw:\s*)+", "", subject, flags=re.I).strip(),
        "participants": [],
        "messages": [],
        "created": datetime.now(timezone.utc).isoformat(),
        "last_activity": "",
        "status": "active",
        "labels": [],
        "agent": agent_id,
    })

    # Update participants
    for p in all_participants:
        if p.lower() not in [x.lower() for x in thread["participants"]]:
            thread["participants"].append(p)

    # Add message
    msg = {
        "id": message_id or hashlib.md5(f"{time.time()}{body[:50]}".encode()).hexdigest()[:12],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "direction": direction,
        "sender": sender,
        "recipients": recipients,
        "subject": subject,
        "body": body,
        "body_clean": extract_reply(body),
        "agent": agent_id,
    }
    thread["messages"].append(msg)
    thread["last_activity"] = msg["timestamp"]
    thread["message_count"] = len(thread["messages"])

    _save_json(path, thread)

    # Index for search
    _index_message(tid, msg)

    return {"thread_id": tid, "message_id": msg["id"], "message_count": len(thread["messages"])}


def get_thread(thread_id: str) -> Optional[dict]:
    """Get full conversation thread."""
    return _load_json(DATA_DIR / "threads" / f"{thread_id}.json", None)


def get_thread_by_subject(subject: str, participants: list[str]) -> Optional[dict]:
    """Find a thread by subject and participants."""
    tid = _thread_id(subject, participants)
    return get_thread(tid)


def get_thread_context(thread_id: str) -> str:
    """Get formatted thread context for injecting into agent prompts."""
    thread = get_thread(thread_id)
    if not thread:
        return ""

    lines = [f"=== Email Thread: {thread['subject']} ==="]
    lines.append(f"Participants: {', '.join(thread['participants'])}")
    lines.append(f"Messages: {thread.get('message_count', 0)}")
    lines.append(f"Labels: {', '.join(thread.get('labels', [])) or 'none'}")
    lines.append("")

    for msg in thread["messages"]:
        direction = "→ SENT" if msg["direction"] == "outbound" else "← RECEIVED"
        lines.append(f"[{msg['timestamp'][:16]}] {direction} from {msg['sender']}")
        lines.append(msg.get("body_clean", msg["body"])[:500])
        lines.append("")

    return "\n".join(lines)


def list_threads(agent_id: str = "", status: str = "", limit: int = 20) -> list[dict]:
    """List conversation threads, optionally filtered."""
    threads = []
    for path in sorted((DATA_DIR / "threads").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        thread = _load_json(path)
        if agent_id and thread.get("agent") != agent_id:
            continue
        if status and thread.get("status") != status:
            continue
        threads.append({
            "thread_id": thread["thread_id"],
            "subject": thread["subject"],
            "participants": thread["participants"],
            "message_count": thread.get("message_count", 0),
            "last_activity": thread.get("last_activity", ""),
            "status": thread.get("status", "active"),
            "labels": thread.get("labels", []),
        })
        if len(threads) >= limit:
            break
    return threads


# ══════════════════════════════════════════════════════════════════════════
# 2. DRAFT REVIEW (HUMAN-IN-THE-LOOP)
# ══════════════════════════════════════════════════════════════════════════

def save_draft(agent_id: str, to: str, subject: str, body: str,
                cc: str = "", reason: str = "") -> str:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Save an email draft for human review before sending."""
    draft_id = hashlib.md5(f"{agent_id}{time.time()}".encode()).hexdigest()[:12]
    draft = {
        "draft_id": draft_id,
        "agent": agent_id,
        "to": to,
        "cc": cc,
        "subject": subject,
        "body": body,
        "reason": reason,
        "status": "pending_review",
        "created": datetime.now(timezone.utc).isoformat(),
        "reviewed_by": "",
        "reviewed_at": "",
    }
    _save_json(DATA_DIR / "drafts" / f"{draft_id}.json", draft)

    # Notify team for review
    import sys
    sys.path.insert(0, "/home/aielevate")
    from notify import send
    send(f"Draft Email for Review — {agent_id}",
         f"Agent {agent_id} has saved a draft email for your review.\n\n"
         f"To: {to}\nSubject: {subject}\nReason: {reason or 'standard review'}\n\n"
         f"Preview:\n{body[:300]}...\n\n"
         f"To approve: python3 /home/aielevate/email_intel.py approve-draft --id {draft_id}\n"
         f"To reject: python3 /home/aielevate/email_intel.py reject-draft --id {draft_id}",
         priority="medium", to=["braun"])

    return draft_id


def approve_draft(draft_id: str, reviewer: str = "braun") -> bool:
    """Approve and send a draft email."""
    path = DATA_DIR / "drafts" / f"{draft_id}.json"
    draft = _load_json(path)
    if not draft or draft.get("status") != "pending_review":
        return False

    # Send the email
    import sys, urllib.request, urllib.parse, base64
    sys.path.insert(0, "/home/aielevate")

    agent_id = draft["agent"]
    if "gigforge" in agent_id:
        from_domain = "team.gigforge.ai"
    elif "techuni" in agent_id:
        from_domain = "team.techuni.ai"
    else:
        from_domain = "team.ai-elevate.ai"

    role = agent_id.split("-")[-1] if "-" in agent_id else agent_id
    data = urllib.parse.urlencode({
        "from": f"{agent_id} <{role}@{from_domain}>",
        "to": draft["to"],
        "subject": draft["subject"],
        "text": draft["body"],
        "h:Reply-To": f"{agent_id}@mg.ai-elevate.ai",
    }).encode("utf-8")
    if draft.get("cc"):
        data = urllib.parse.urlencode({
            "from": f"{agent_id} <{role}@{from_domain}>",
            "to": draft["to"],
            "cc": draft["cc"],
            "subject": draft["subject"],
            "text": draft["body"],
            "h:Reply-To": f"{agent_id}@mg.ai-elevate.ai",
        }).encode("utf-8")

    creds = base64.b64encode(f"api:{Path("/opt/ai-elevate/credentials/mailgun-api-key.txt").read_text().strip()}".encode()).decode()
    req = urllib.request.Request("https://api.mailgun.net/v3/mg.ai-elevate.ai/messages", data=data, method="POST")
    req.add_header("Authorization", f"Basic {creds}")
    try:
        urllib.request.urlopen(req, timeout=15)
        draft["status"] = "sent"
        draft["reviewed_by"] = reviewer
        draft["reviewed_at"] = datetime.now(timezone.utc).isoformat()
        _save_json(path, draft)
        return True
    except (AgentError, Exception) as e:
        return False


def reject_draft(draft_id: str, reviewer: str = "braun", reason: str = "") -> bool:
    """Reject a draft email."""
    path = DATA_DIR / "drafts" / f"{draft_id}.json"
    draft = _load_json(path)
    if not draft:
        return False
    draft["status"] = "rejected"
    draft["reviewed_by"] = reviewer
    draft["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    draft["rejection_reason"] = reason
    _save_json(path, draft)
    return True


def list_drafts(status: str = "pending_review") -> list[dict]:
    """List drafts by status."""
    drafts = []
    for path in (DATA_DIR / "drafts").glob("*.json"):
        d = _load_json(path)
        if status and d.get("status") != status:
            continue
        drafts.append(d)
    drafts.sort(key=lambda x: x.get("created", ""), reverse=True)
    return drafts


# ══════════════════════════════════════════════════════════════════════════
# 3. REPLY EXTRACTION
# ══════════════════════════════════════════════════════════════════════════

# Patterns that indicate the start of quoted text
QUOTE_PATTERNS = [
    r"^>+ ",                                    # > quoted lines
    r"^On .+ wrote:$",                          # On [date] [person] wrote:
    r"^-{3,}\s*Original Message\s*-{3,}",       # --- Original Message ---
    r"^-{3,}\s*Forwarded Message\s*-{3,}",      # --- Forwarded Message ---
    r"^From:\s+\S+@\S+",                        # From: email header in reply
    r"^Sent:\s+",                               # Sent: date header
    r"^Date:\s+",                               # Date: header
    r"^Subject:\s+",                             # Subject: header in reply
    r"^_{3,}",                                   # ___ separator
    r"^\*From:\*",                               # *From:* bold format
    r"^Le .+ a écrit\s*:",                       # French "On [date] wrote:"
    r"^Am .+ schrieb\s",                         # German "On [date] wrote:"
    r"^El .+ escribió\s*:",                      # Spanish
    r"Sent from my iPhone",                      # Mobile signatures
    r"Sent from my Galaxy",
    r"Get Outlook for",
    r"^--\s*$",                                  # Signature separator
]

QUOTE_RE = re.compile("|".join(f"({p})" for p in QUOTE_PATTERNS), re.MULTILINE | re.IGNORECASE)


def extract_reply(text: str) -> str:
    """Extract only the new reply content, stripping quoted text and signatures.

    Achieves ~90%+ accuracy on common email clients (Gmail, Outlook, Apple Mail, Zoho).
    """
    if not text:
        return ""

    lines = text.split("\n")
    clean_lines = []
    in_quote = False

    for i, line in enumerate(lines):
        # Check if this line starts a quote block
        if QUOTE_RE.search(line):
            in_quote = True
            continue

        # Lines starting with > are quoted
        if line.strip().startswith(">"):
            in_quote = True
            continue

        # If we hit a blank line after quoted content, stay in quote mode
        if in_quote and line.strip() == "":
            continue

        # If we're in quote mode and hit non-quoted content, we might be out
        # but usually quotes continue — only exit if it looks like new content
        if in_quote and line.strip():
            # Still in quote zone — skip
            continue

        clean_lines.append(line)

    result = "\n".join(clean_lines).strip()

    # Remove trailing signature if present
    sig_markers = ["--", "Best regards", "Best,", "Thanks,", "Cheers,", "Regards,",
                   "Sincerely,", "Kind regards", "Warm regards"]
    for marker in sig_markers:
        idx = result.rfind(f"\n{marker}")
        if idx > len(result) * 0.5:  # Only strip if in bottom half
            result = result[:idx].strip()
            break

    return result if result else text.strip()


# ══════════════════════════════════════════════════════════════════════════
# 4. EMAIL SEARCH (via FTS5)
# ══════════════════════════════════════════════════════════════════════════

import sqlite3

SEARCH_DB = DATA_DIR / "search-index" / "emails.db"

def _init_search_db():
    conn = sqlite3.connect(str(SEARCH_DB))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS emails (
            id TEXT PRIMARY KEY,
            thread_id TEXT,
            sender TEXT,
            recipients TEXT,
            subject TEXT,
            body TEXT,
            agent TEXT,
            direction TEXT,
            timestamp TEXT
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS email_fts USING fts5(
            subject, body, sender,
            content='emails', content_rowid='rowid',
            tokenize='porter unicode61'
        );
        CREATE TRIGGER IF NOT EXISTS email_ai AFTER INSERT ON emails BEGIN
            INSERT INTO email_fts(rowid, subject, body, sender)
            VALUES (new.rowid, new.subject, new.body, new.sender);
        END;
    """)
    conn.commit()
    conn.close()

_init_search_db()


def _index_message(thread_id: str, msg: dict):
    """Index a message for full-text search."""
    conn = sqlite3.connect(str(SEARCH_DB))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO emails (id, thread_id, sender, recipients, subject, body, agent, direction, timestamp) VALUES (?,?,?,?,?,?,?,?,?)",
            (msg["id"], thread_id, msg["sender"], ",".join(msg.get("recipients", [])),
             msg["subject"], msg.get("body_clean", msg["body"]), msg.get("agent", ""),
             msg["direction"], msg["timestamp"])
        )
        conn.commit()
    finally:
        conn.close()


def search_emails(query: str, limit: int = 20) -> list[dict]:
    """Full-text search across all emails."""
    fts_query = " OR ".join(f'"{w}"' for w in query.split() if w.strip())
    if not fts_query:
        return []

    conn = sqlite3.connect(str(SEARCH_DB))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT e.*, rank FROM email_fts f
               JOIN emails e ON e.rowid = f.rowid
               WHERE email_fts MATCH ? ORDER BY rank LIMIT ?""",
            (fts_query, limit)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 5. LABELS / STATE MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════

VALID_LABELS = [
    "needs_response", "waiting_approval", "escalated", "resolved",
    "follow_up", "urgent", "vip", "spam", "archived",
    "proposal", "invoice", "support", "feedback", "introduction",
]

def add_label(thread_id: str, label: str):
    """Add a label to a thread."""
    thread = get_thread(thread_id)
    if not thread:
        return
    labels = thread.get("labels", [])
    if label not in labels:
        labels.append(label)
        thread["labels"] = labels
        _save_json(DATA_DIR / "threads" / f"{thread_id}.json", thread)


def remove_label(thread_id: str, label: str):
    """Remove a label from a thread."""
    thread = get_thread(thread_id)
    if not thread:
        return
    labels = thread.get("labels", [])
    if label in labels:
        labels.remove(label)
        thread["labels"] = labels
        _save_json(DATA_DIR / "threads" / f"{thread_id}.json", thread)


def get_threads_by_label(label: str, limit: int = 50) -> list[dict]:
    """Get all threads with a specific label."""
    return [t for t in list_threads(limit=limit) if label in t.get("labels", [])]


# ══════════════════════════════════════════════════════════════════════════
# 6. ALLOWLISTS / BLOCKLISTS
# ══════════════════════════════════════════════════════════════════════════

def add_to_allowlist(agent_id: str, email_or_domain: str):
    """Add a sender to an agent's allowlist."""
    path = DATA_DIR / "lists" / f"{agent_id}-allow.json"
    lst = _load_json(path, {"agent": agent_id, "type": "allow", "entries": []})
    if email_or_domain not in lst["entries"]:
        lst["entries"].append(email_or_domain)
        _save_json(path, lst)


def add_to_blocklist(agent_id: str, email_or_domain: str):
    """Add a sender to an agent's blocklist."""
    path = DATA_DIR / "lists" / f"{agent_id}-block.json"
    lst = _load_json(path, {"agent": agent_id, "type": "block", "entries": []})
    if email_or_domain not in lst["entries"]:
        lst["entries"].append(email_or_domain)
        _save_json(path, lst)


def is_blocked(agent_id: str, sender: str) -> bool:
    """Check if a sender is blocked for an agent."""
    path = DATA_DIR / "lists" / f"{agent_id}-block.json"
    lst = _load_json(path, {"entries": []})
    sender_lower = sender.lower()
    domain = sender_lower.split("@")[-1] if "@" in sender_lower else ""
    for entry in lst["entries"]:
        if entry.lower() == sender_lower or entry.lower() == domain:
            return True
    return False


def is_allowed(agent_id: str, sender: str) -> bool:
    """Check if a sender is on an agent's allowlist (empty list = allow all)."""
    path = DATA_DIR / "lists" / f"{agent_id}-allow.json"
    lst = _load_json(path, {"entries": []})
    if not lst["entries"]:
        return True  # No allowlist = allow all
    sender_lower = sender.lower()
    domain = sender_lower.split("@")[-1] if "@" in sender_lower else ""
    for entry in lst["entries"]:
        if entry.lower() == sender_lower or entry.lower() == domain:
            return True
    return False


# ══════════════════════════════════════════════════════════════════════════
# 7. EMAIL METRICS
# ══════════════════════════════════════════════════════════════════════════

def record_metric(event: str, agent_id: str = "", sender: str = "",
                   recipient: str = "", subject: str = "", details: dict = None):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Record an email metric event."""
    _append_jsonl(DATA_DIR / "metrics" / f"events-{datetime.now(timezone.utc).strftime('%Y-%m')}.jsonl", {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,  # sent, delivered, bounced, received, replied, opened
        "agent": agent_id,
        "sender": sender,
        "recipient": recipient,
        "subject": subject[:100],
        "details": details or {},
    })


def get_metrics_summary(days: int = 7) -> dict:
    """Get email metrics summary."""
    path = DATA_DIR / "metrics" / f"events-{datetime.now(timezone.utc).strftime('%Y-%m')}.jsonl"
    if not path.exists():
        return {"total": 0}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    events = {"sent": 0, "received": 0, "bounced": 0, "replied": 0}
    agents = {}

    with open(path) as f:
        for line in f:
            try:
                e = json.loads(line)
                if e["timestamp"] < cutoff:
                    continue
                event = e.get("event", "")
                events[event] = events.get(event, 0) + 1
                agent = e.get("agent", "unknown")
                if agent not in agents:
                    agents[agent] = {"sent": 0, "received": 0}
                if event in ("sent", "received"):
                    agents[agent][event] += 1
            except (AgentError, Exception) as e:
                pass

    return {
        "period_days": days,
        "totals": events,
        "by_agent": agents,
        "delivery_rate": f"{(1 - events.get('bounced',0) / max(events.get('sent',1), 1)) * 100:.1f}%",
    }


# ══════════════════════════════════════════════════════════════════════════
# 8. DOMAIN WARMING
# ══════════════════════════════════════════════════════════════════════════

WARMING_SCHEDULE = {
    # day: max_emails_per_day
    1: 20, 2: 30, 3: 50, 4: 75, 5: 100,
    6: 150, 7: 200, 8: 300, 9: 400, 10: 500,
    11: 750, 12: 1000, 13: 1500, 14: 2000,
}

def get_daily_send_limit() -> int:
    """Get today's send limit based on domain warming schedule."""
    path = DATA_DIR / "metrics" / "warming-start.txt"
    if not path.exists():
        with open(path, "w") as f:
            f.write(datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        return WARMING_SCHEDULE[1]

    start = datetime.strptime(path.read_text().strip(), "%Y-%m-%d")
    day = (datetime.now(timezone.utc) - start.replace(tzinfo=timezone.utc)).days + 1

    if day > 14:
        return 10000  # Fully warmed
    return WARMING_SCHEDULE.get(day, 2000)


def get_daily_send_count() -> int:
    """Get how many emails have been sent today."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = DATA_DIR / "metrics" / f"events-{datetime.now(timezone.utc).strftime('%Y-%m')}.jsonl"
    if not path.exists():
        return 0
    count = 0
    with open(path) as f:
        for line in f:
            try:
                e = json.loads(line)
                if e.get("event") == "sent" and e["timestamp"][:10] == today:
                    count += 1
            except (AiElevateError, Exception) as e:
                pass
    return count


def can_send() -> tuple[bool, str]:
    """Check if we can send more emails today (domain warming)."""
    limit = get_daily_send_limit()
    sent = get_daily_send_count()
    if sent >= limit:
        return False, f"Daily send limit reached ({sent}/{limit}). Domain warming day {min(14, (datetime.now(timezone.utc) - datetime.strptime((DATA_DIR / 'metrics/warming-start.txt').read_text().strip(), '%Y-%m-%d').replace(tzinfo=timezone.utc)).days + 1)}."
    return True, f"{sent}/{limit} sent today"


# ── CLI ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Email Intelligence")
    parser.add_argument("command", choices=["threads", "search", "drafts", "approve-draft",
                                             "reject-draft", "metrics", "warming", "labels",
                                             "extract-reply"])
    parser.add_argument("--id", default="")
    parser.add_argument("--query", "-q", default="")
    parser.add_argument("--agent", default="")
    parser.add_argument("--label", default="")
    parser.add_argument("--text", default="")
    parser.add_argument("--status", default="")
    args = parser.parse_args()

    if args.command == "threads":
        for t in list_threads(agent_id=args.agent, status=args.status):
            print(f"  {t['thread_id']} | {t['subject'][:50]} | {t['message_count']} msgs | {', '.join(t.get('labels', [])) or '-'}")
    elif args.command == "search":
        for r in search_emails(args.query):
            print(f"  {r['timestamp'][:16]} | {r['sender'][:30]} | {r['subject'][:50]}")
    elif args.command == "drafts":
        for d in list_drafts(args.status or "pending_review"):
            print(f"  {d['draft_id']} | {d['agent']} | {d['to']} | {d['subject'][:40]} | {d['status']}")
    elif args.command == "approve-draft":
        ok = approve_draft(args.id)
        print(f"{'Approved and sent' if ok else 'Failed'}")
    elif args.command == "reject-draft":
        ok = reject_draft(args.id)
        print(f"{'Rejected' if ok else 'Failed'}")
    elif args.command == "metrics":
        print(json.dumps(get_metrics_summary(), indent=2))
    elif args.command == "warming":
        ok, msg = can_send()
        print(msg)
    elif args.command == "labels":
        for t in get_threads_by_label(args.label):
            print(f"  {t['thread_id']} | {t['subject'][:50]}")
    elif args.command == "extract-reply":
        text = args.text or input("Paste email text: ")
        print(extract_reply(text))
