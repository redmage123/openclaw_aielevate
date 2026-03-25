#!/usr/bin/env python3
"""AI Elevate Unified Notification System

Priority-based routing across Telegram and Email:

  CRITICAL  → Telegram IMMEDIATELY + Email IMMEDIATELY
              (gateway down, data loss, security breach, system failure)

  HIGH      → Telegram IMMEDIATELY + Email within 1 minute
              (infrastructure failures, agent errors, project blockers, urgent alerts)

  MEDIUM    → Email IMMEDIATELY
              (daily reports, sprint updates, milestone completions, content digests)

  LOW       → Email BATCHED (queued, sent in next digest)
              (weekly summaries, cost reports, non-urgent updates, informational)

Usage:
  python3 /home/aielevate/notify.py --priority critical --title "Gateway Down" --body "Details..."
  python3 /home/aielevate/notify.py --priority high --title "Sprint Complete" --body "Details..."
  python3 /home/aielevate/notify.py --priority medium --title "Daily Report" --body "Details..." --to peter
  python3 /home/aielevate/notify.py --priority low --title "Cost Report" --body "Details..."

  # From Python:
  from notify import send
  send("Title", "Body", priority="high", to=["braun", "peter"])
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
import base64
from datetime import datetime, UTC
from pathlib import Path
from exceptions import AiElevateError  # TODO: Use specific exception types, EmailError

# ── Configuration ────────────────────────────────────────────────────────

MAILGUN_API_KEY = os.environ.get("MAILGUN_API_KEY", "")
MAILGUN_DOMAIN = "ai-elevate.ai"

# Telegram (set these once bot is created)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CONFIG_FILE = "/opt/ai-elevate/credentials/telegram.json"

# Load Telegram config from file if env not set
if not TELEGRAM_BOT_TOKEN and os.path.exists(TELEGRAM_CONFIG_FILE):
    try:
        with open(TELEGRAM_CONFIG_FILE) as f:
            tg_config = json.load(f)
        TELEGRAM_BOT_TOKEN = tg_config.get("bot_token", "")
    except (EmailError, Exception) as e:
        pass

# ntfy (push notifications — backup channel)
NTFY_URL = "http://127.0.0.1:8025"
NTFY_TOPIC = "aielevate-alerts"

# Team directory
TEAM = {
    "braun": {
        "name": "Braun Brelin",
        "email": "braun.brelin@ai-elevate.ai",
        "telegram_chat_id": "",  # Set after bot setup
        "role": "owner",
    },
    "peter": {
        "name": "Peter Munro",
        "email": "peter.munro@ai-elevate.ai",
        "telegram_chat_id": "",
        "role": "team",
    },
    "mike": {
        "name": "Mike Burton",
        "email": "mike.burton@ai-elevate.ai",
        "telegram_chat_id": "",
        "role": "team",
    },
    "charlie": {
        "name": "Charlotte Turking",
        "email": "charlie.turking@ai-elevate.ai",
        "telegram_chat_id": "",
        "role": "team",
    },
}

# Load chat IDs from config file
if os.path.exists(TELEGRAM_CONFIG_FILE):
    try:
        with open(TELEGRAM_CONFIG_FILE) as f:
            tg_config = json.load(f)
        for name, chat_id in tg_config.get("chat_ids", {}).items():
            if name in TEAM:
                TEAM[name]["telegram_chat_id"] = str(chat_id)
        # Group chat for team-wide alerts
        TEAM_GROUP_CHAT_ID = tg_config.get("group_chat_id", "")
    except (AiElevateError, Exception) as e:
        TEAM_GROUP_CHAT_ID = ""
else:
    TEAM_GROUP_CHAT_ID = ""

# Priority routing rules
PRIORITY_ROUTING = {
    "critical": {"telegram": True, "email": True, "ntfy": True, "emoji": "🚨", "default_recipients": "all"},
    "high":     {"telegram": True, "email": True, "ntfy": True, "emoji": "⚠️", "default_recipients": "all"},
    "medium":   {"telegram": False, "email": True, "ntfy": False, "emoji": "📋", "default_recipients": "all"},
    "low":      {"telegram": False, "email": True, "ntfy": False, "emoji": "ℹ️", "default_recipients": ["braun"]},
}

# Org-specific notification recipients — these people get ALL notifications for their org
ORG_NOTIFY = {
    "techuni": ["braun", "mike"],      # Mike Burton is on all TechUni events
    "gigforge": ["braun"],              # Braun gets all GigForge events
    "ai-elevate": ["braun"],            # Braun gets all AI Elevate events
}

LOG_DIR = Path("/opt/ai-elevate/notifications/logs")
BATCH_DIR = Path("/opt/ai-elevate/notifications/batch")
LOG_DIR.mkdir(parents=True, exist_ok=True)
BATCH_DIR.mkdir(parents=True, exist_ok=True)


# ── Telegram ─────────────────────────────────────────────────────────────

def send_telegram(chat_id: str, text: str, parse_mode: str = "HTML") -> bool:
    """Send a Telegram message."""
    # Unified comms — scrub outbound
    try:
        from unified_comms import process_telegram_outbound
        text = process_telegram_outbound(text, agent_id="ops", chat_id=chat_id)
    except Exception:
        pass
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": "true",
        }).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        resp = urllib.request.urlopen(req, timeout=10)
        return resp.status == 200
    except (AiElevateError, Exception) as e:
        print(f"telegram error: {e}", file=sys.stderr)
        return False


def send_telegram_to_team(title: str, body: str, recipients: list, priority: str) -> int:
    """Send Telegram message to specified team members. Returns count sent."""
    routing = PRIORITY_ROUTING.get(priority, PRIORITY_ROUTING["medium"])
    emoji = routing["emoji"]

    # Format message
    text = f"{emoji} <b>{title}</b>\n\n{body[:3000]}"
    if priority in ("critical", "high"):
        text = f"{emoji} <b>[{priority.upper()}]</b> {title}\n\n{body[:3000]}"

    sent = 0

    # Send to group chat if exists (for critical/high)
    if TEAM_GROUP_CHAT_ID and priority in ("critical", "high"):
        if send_telegram(TEAM_GROUP_CHAT_ID, text):
            sent += 1
    else:
        # Send to individual recipients
        for name in recipients:
            member = TEAM.get(name)
            if member and member.get("telegram_chat_id"):
                if send_telegram(member["telegram_chat_id"], text):
                    sent += 1

    return sent


# ── Email ────────────────────────────────────────────────────────────────

def send_email(title: str, body: str, recipients: list, cc: list = None,
               from_name: str = "AI Elevate", from_addr: str = "alerts", org: str = "gigforge") -> bool:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Send email via Mailgun."""
    to_addrs = [TEAM[r]["email"] for r in recipients if r in TEAM]
    cc_addrs = [TEAM[r]["email"] for r in (cc or []) if r in TEAM]

    if not to_addrs:
        return False

    # Determine the correct team domain for the from address
    if "gigforge" in from_name.lower() or from_addr.startswith("gigforge") or org == "gigforge":
        from_domain = "ai-elevate.ai"
        reply_domain = "team.gigforge.ai"
    elif "techuni" in from_name.lower() or from_addr.startswith("techuni") or org == "techuni":
        from_domain = "ai-elevate.ai"
        reply_domain = "team.techuni.ai"
    else:
        from_domain = "ai-elevate.ai"
        reply_domain = "team.gigforge.ai"

    params = {
        "from": f"{from_name} <{from_addr}@{from_domain}>",
        "to": ", ".join(to_addrs),
        "subject": title,
        "text": body,
        "h:Reply-To": f"{org}-{from_addr}@ai-elevate.ai" if from_addr in ("ceo", "ops", "director") else f"{from_addr}@ai-elevate.ai",
    }
    if cc_addrs:
        params["cc"] = ", ".join(cc_addrs)

    try:
        data = urllib.parse.urlencode(params).encode("utf-8")
        creds = base64.b64encode(f"api:{MAILGUN_API_KEY}".encode()).decode()
        req = urllib.request.Request(
            f"https://api.mailgun.net/v3/{MAILGUN_DOMAIN}/messages",
            data=data, method="POST"
        )
        req.add_header("Authorization", f"Basic {creds}")
        urllib.request.urlopen(req, timeout=15)
        return True
    except (EmailError, Exception) as e:
        print(f"email error: {e}", file=sys.stderr)
        return False


# ── ntfy (push) ──────────────────────────────────────────────────────────

def send_ntfy(title: str, body: str, priority: str = "high") -> bool:
    """Send push notification via self-hosted ntfy."""
    ntfy_priority = {"critical": "urgent", "high": "high", "medium": "default", "low": "low"}.get(priority, "default")
    try:
        req = urllib.request.Request(
            f"{NTFY_URL}/{NTFY_TOPIC}",
            data=body.encode("utf-8"),
            headers={
                "Title": title.encode("utf-8"),
                "Priority": ntfy_priority,
                "Tags": "rotating_light" if priority in ("critical", "high") else "information_source",
            },
        )
        urllib.request.urlopen(req, timeout=10)
        return True
    except (AiElevateError, Exception) as e:
        return False


# ── Batch queue (for LOW priority) ───────────────────────────────────────

def queue_batch(title: str, body: str, recipients: list):
    """Queue a notification for the next batch digest."""
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "title": title,
        "body": body[:500],
        "recipients": recipients,
    }
    batch_file = BATCH_DIR / f"batch-{datetime.now(UTC).strftime('%Y-%m-%d')}.jsonl"
    with open(batch_file, "a") as f:
        f.write(json.dumps(entry) + "\n")


def flush_batch():
    """Send all queued batch notifications as a single digest email."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    batch_file = BATCH_DIR / f"batch-{today}.jsonl"
    if not batch_file.exists():
        return

    entries = []
    with open(batch_file) as f:
        for line in f:
            try:
                entries.append(json.loads(line.strip()))
            except (AiElevateError, Exception) as e:
                pass

    if not entries:
        return

    # Build digest
    digest = f"AI Elevate Notification Digest — {today}\n\n"
    digest += f"{len(entries)} notifications:\n\n"
    for i, entry in enumerate(entries, 1):
        digest += f"{i}. {entry['title']}\n   {entry['body'][:200]}\n   ({entry['timestamp']})\n\n"

    # Collect all recipients
    all_recipients = set()
    for entry in entries:
        all_recipients.update(entry.get("recipients", []))

    send_email(
        f"AI Elevate Digest — {len(entries)} notifications",
        digest,
        list(all_recipients) or ["braun"],
        from_name="AI Elevate Digest",
        from_addr="digest",
    )

    # Archive the batch file
    batch_file.rename(BATCH_DIR / f"sent-{today}.jsonl")


# ── Main send function ───────────────────────────────────────────────────

def send(title: str, body: str, priority: str = "medium",
         to: list = None, cc: list = None,
         from_name: str = "AI Elevate", from_addr: str = "alerts",
         org: str = "gigforge") -> dict:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Send a notification with priority-based routing.

    Args:
        title: Notification title/subject
        body: Notification body
        priority: critical, high, medium, low
        to: List of recipient names (braun, peter, mike, charlie) or "all"
        cc: List of CC recipient names
        from_name: Sender display name
        from_addr: Sender local part (before @ai-elevate.ai)

    Returns:
        Dict with delivery status per channel
    """
    routing = PRIORITY_ROUTING.get(priority, PRIORITY_ROUTING["medium"])

    # Resolve recipients
    if to is None or to == "all" or to == ["all"]:
        default = routing["default_recipients"]
        if default == "all":
            recipients = list(TEAM.keys())
        else:
            recipients = default if isinstance(default, list) else [default]
    elif isinstance(to, str):
        recipients = [to]
    else:
        recipients = to

    # Merge org-specific recipients (e.g. Mike gets all TechUni events)
    for name in ORG_NOTIFY.get(org, []):
        if name not in recipients:
            recipients.append(name)

    result = {"priority": priority, "recipients": recipients, "channels": {}}

    # Log
    log_entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "priority": priority,
        "title": title,
        "recipients": recipients,
        "body_length": len(body),
    }

    # Route based on priority
    if routing["telegram"]:
        tg_sent = send_telegram_to_team(title, body, recipients, priority)
        result["channels"]["telegram"] = tg_sent
        log_entry["telegram"] = tg_sent

    if routing["email"]:
        if priority == "low":
            queue_batch(title, body, recipients)
            result["channels"]["email"] = "queued"
            log_entry["email"] = "queued"
        else:
            email_ok = send_email(title, body, recipients, cc, from_name, from_addr, org=org)
            result["channels"]["email"] = email_ok
            log_entry["email"] = email_ok

    if routing["ntfy"]:
        ntfy_ok = send_ntfy(title, body, priority)
        result["channels"]["ntfy"] = ntfy_ok
        log_entry["ntfy"] = ntfy_ok

    # Write log
    log_file = LOG_DIR / f"notifications-{datetime.now(UTC).strftime('%Y-%m-%d')}.jsonl"
    with open(log_file, "a") as f:
        f.write(json.dumps(log_entry) + "\n")

    return result


# ── CLI ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Elevate Notification System")
    parser.add_argument("--title", "-t", required=True, help="Notification title")
    parser.add_argument("--body", "-b", required=True, help="Notification body")
    parser.add_argument("--priority", "-p", default="medium",
                       choices=["critical", "high", "medium", "low"])
    parser.add_argument("--to", nargs="+", default=None,
                       help="Recipients: braun peter mike charlie all")
    parser.add_argument("--cc", nargs="+", default=None)
    parser.add_argument("--from-name", default="AI Elevate")
    parser.add_argument("--from-addr", default="alerts")
    parser.add_argument("--flush-batch", action="store_true",
                       help="Flush queued low-priority notifications")

    args = parser.parse_args()

    if args.flush_batch:
        flush_batch()
        print("Batch flushed")
        sys.exit(0)

    result = send(
        title=args.title,
        body=args.body,
        priority=args.priority,
        to=args.to,
        cc=args.cc,
        from_name=args.from_name,
        from_addr=args.from_addr,
    )

    # Print status
    channels = result["channels"]
    parts = []
    for ch, status in channels.items():
        if status is True:
            parts.append(f"{ch}: sent")
        elif status == "queued":
            parts.append(f"{ch}: queued")
        elif isinstance(status, int) and status > 0:
            parts.append(f"{ch}: sent ({status})")
        else:
            parts.append(f"{ch}: failed")

    print(", ".join(parts) if parts else "no channels")
