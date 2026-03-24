#!/usr/bin/env python3
"""send_email() — single function all agents use to send email.

Automatically selects the correct sending domain based on the agent ID.
No templates to copy wrong. No domain decisions for the agent to make.

Usage:
  from send_email import send_email

  send_email(
      to="peter.munro@ai-elevate.ai",
      subject="Blockers before go-live",
      body="Hi Peter, ...",
      agent_id="operations",
      cc="braun.brelin@ai-elevate.ai",
  )

That's it. The function figures out the From address, Reply-To, and Mailgun endpoint.
"""

import base64
import urllib.request
import urllib.parse
from pathlib import Path
from exceptions import AiElevateError  # TODO: Use specific exception types, EmailError
import hashlib
import time
import json

# ── Outbound dedup — prevents agents from sending multiple replies to the same
# recipient+subject within a 5-minute window. Agents sometimes call send_email()
# multiple times in one session (LLM loop), causing duplicate emails.
_OUTBOUND_DEDUP_FILE = "/opt/ai-elevate/email-gateway/outbound-dedup.json"
_OUTBOUND_DEDUP_TTL = 300  # 5 minutes

def _outbound_dedup_check(to: str, subject: str) -> bool:
    """Returns True if this email was already sent recently (should be skipped)."""
    key = hashlib.sha256(f"{to.lower().strip()}|{subject.strip().lower()}".encode()).hexdigest()[:16]
    now = time.time()
    try:
        with open(_OUTBOUND_DEDUP_FILE, "r") as f:
            cache = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        cache = {}
    # Clean expired entries
    cache = {k: v for k, v in cache.items() if now - v < _OUTBOUND_DEDUP_TTL}
    if key in cache:
        return True  # Already sent recently
    cache[key] = now
    try:
        with open(_OUTBOUND_DEDUP_FILE, "w") as f:
            json.dump(cache, f)
    except Exception:
        pass
    return False


def _get_key():
    return Path("/opt/ai-elevate/credentials/mailgun-api-key.txt").read_text().strip()


def _resolve_domain(agent_id: str) -> tuple:
    """Returns (from_address, reply_to, mailgun_domain) based on agent ID."""
    if agent_id.startswith("gigforge") or agent_id in ("operations", "devops", "cybersecurity", "security-engineer"):
        # Map agent to a friendly from name
        names = {
            "operations": ("GigForge Operations", "ops"),
            "gigforge": ("GigForge", "ops"),
            "gigforge-sales": ("GigForge Sales", "sales"),
            "gigforge-advocate": ("GigForge Client Services", "clientservices"),
            "gigforge-support": ("GigForge Support", "support"),
            "gigforge-pm": ("GigForge Projects", "pm"),
            "gigforge-engineer": ("GigForge Engineering", "engineer"),
            "gigforge-devops": ("GigForge DevOps", "devops"),
            "gigforge-finance": ("GigForge Finance", "finance"),
            "gigforge-legal": ("GigForge Legal", "legal"),
            "gigforge-billing": ("GigForge Billing", "billing"),
            "gigforge-csat": ("GigForge Client Services", "clientservices"),
            "devops": ("GigForge DevOps", "devops"),
            "cybersecurity": ("AI Elevate Security", "security"),
            "security-engineer": ("AI Elevate Security", "security"),
        }
        friendly_name, local = names.get(agent_id, ("GigForge", agent_id))
        return (
            f"{friendly_name} <{local}@gigforge.ai>",
            f"{local}@gigforge.ai",
            "gigforge.ai",
        )

    elif agent_id.startswith("techuni"):
        names = {
            "techuni-ceo": ("TechUni", "ceo"),
            "techuni-sales": ("TechUni Sales", "sales"),
            "techuni-advocate": ("TechUni Client Services", "clientservices"),
            "techuni-support": ("TechUni Support", "support"),
            "techuni-marketing": ("TechUni Marketing", "marketing"),
            "techuni-engineering": ("TechUni Engineering", "engineering"),
            "techuni-pm": ("TechUni Projects", "pm"),
            "techuni-finance": ("TechUni Finance", "finance"),
            "techuni-legal": ("TechUni Legal", "legal"),
        }
        friendly_name, local = names.get(agent_id, ("TechUni", agent_id.replace("techuni-", "")))
        return (
            f"{friendly_name} <{local}@techuni.ai>",
            f"{local}@techuni.ai",
            "techuni.ai",
        )

    else:
        # AI Elevate / other
        return (
            f"AI Elevate <{agent_id}@internal.ai-elevate.ai>",
            f"{agent_id}@internal.ai-elevate.ai",
            "internal.ai-elevate.ai",
        )


def send_email(
    to: str,
    subject: str,
    body: str,
    agent_id: str = "operations",
    cc: str = "braun.brelin@ai-elevate.ai",
    reply_to: str = "",
) -> dict:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Send an email. Automatically uses the correct From/domain for the agent."""

    # Outbound dedup — skip if we already sent to this recipient+subject recently
    if _outbound_dedup_check(to, subject):
        import logging
        logging.getLogger("send_email").warning(
            f"OUTBOUND DEDUP: skipping duplicate send to {to} subject={subject}")
        return {"status": "dedup_skipped", "to": to, "subject": subject}

    from_addr, default_reply_to, mailgun_domain = _resolve_domain(agent_id)
    reply_to = reply_to or default_reply_to

    key = _get_key()
    creds = base64.b64encode(("api:" + key).encode()).decode()

    form = {
        "from": from_addr,
        "to": to,
        "h:Reply-To": reply_to,
        "subject": subject,
        "text": body,
    }
    # Don't CC Braun if Braun is already the recipient
    braun_emails = ['braun.brelin@ai-elevate.ai', 'bbrelin@gmail.com']
    if cc and to.lower() in braun_emails and cc.lower() in braun_emails:
        cc = ''  # Skip CC — Braun is already the recipient
    if cc:
        form["cc"] = cc

    data = urllib.parse.urlencode(form).encode("utf-8")
    url = f"https://api.mailgun.net/v3/{mailgun_domain}/messages"

    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", "Basic " + creds)

    try:
        resp = urllib.request.urlopen(req, timeout=15)
        result = resp.read().decode()
        return {"status": "sent", "domain": mailgun_domain, "response": result[:100]}
    except (EmailError, Exception) as e:
        return {"status": "error", "error": str(e), "domain": mailgun_domain}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Send Email")
    parser.add_argument("--to", required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--body", required=True)
    parser.add_argument("--agent", default="operations")
    parser.add_argument("--cc", default="")
    args = parser.parse_args()

    result = send_email(args.to, args.subject, args.body, args.agent, args.cc)
    print(result)
