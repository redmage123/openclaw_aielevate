#!/usr/bin/env python3
"""Email-to-Agent Gateway v2 — clean rewrite.

Receives inbound email via Mailgun webhook, dispatches to OpenClaw agents.
Runs as a FastAPI server on port 8065, bound to 0.0.0.0.

Features:
- Email routing by domain and alias
- Platform notification filtering (Upwork, Fiverr, etc)
- Deduplication by Message-ID
- Auto-ack to sender
- Agent dispatch via agent queue (concurrent, rate-limit safe)
- Auto-reply via Mailgun with correct org domain
- Customer context injection
- Owner directives injection
- Agent identity enforcement
- Proposal approval processing
- Post-interaction audit (via systemd auditor service)
- Email threading (In-Reply-To, References, quoted text)
- Bounce address handling
- Loop detection
"""

import asyncio
import base64 as _b64
import hashlib
import json
import logging
import os
import re
import sys
import time
import urllib.request as _ur
import urllib.parse as _up
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# Imports — all optional, degrade gracefully
# ---------------------------------------------------------------------------
sys.path.insert(0, "/home/aielevate")

def _try_import(module, attr=None):
    try:
        m = __import__(module)
        return getattr(m, attr) if attr else m
    except Exception:
        return None

async_queue_agent_call = _try_import("agent_queue", "async_queue_agent_call")
audit_log_action = _try_import("audit_log", "log_action") or (lambda *a, **k: None)
get_directives = _try_import("directives", "directives_summary")
process_approval = _try_import("proposal_queue", "process_approval_email")
customer_context_summary = _try_import("customer_context", "context_summary")
customer_get_context = _try_import("customer_context", "get_context")
email_format = _try_import("email_templates", "format_email")
email_pick = _try_import("email_templates", "pick_template")
parse_feedback_email = _try_import("feedback_system", "parse_feedback_email")
_process_feedback = _try_import("feedback_system", "process_feedback")
_process_approval = _try_import("proposal_queue", "process_approval_email")

# Email intel
try:
    from email_intel import (add_to_thread, extract_reply, is_blocked,
                             record_metric, get_thread_context, add_label, _append_jsonl)
except Exception:
    add_to_thread = extract_reply = is_blocked = record_metric = None
    get_thread_context = add_label = _append_jsonl = None

try:
    from comms_hub import process_message
except Exception:
    process_message = None

try:
    from sla_tracker import log_received, log_responded
except Exception:
    log_received = log_responded = None

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
app = FastAPI(title="AI Elevate Email Gateway v2")
logger = logging.getLogger("email-gateway")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("/var/log/openclaw/shared/email-gateway.log"),
        logging.StreamHandler(),
    ]
)

MAILGUN_KEY = Path("/opt/ai-elevate/credentials/mailgun-api-key.txt").read_text().strip()
MAILGUN_CREDS = _b64.b64encode(("api:" + MAILGUN_KEY).encode()).decode()

AGENT_DOMAINS = {"gigforge.ai", "techuni.ai", "internal.ai-elevate.ai", "ai-elevate.ai",
                 "mg.gigforge.ai", "mg.techuni.ai", "mg.ai-elevate.ai"}

PLATFORM_SENDERS = {
    "upwork.com", "e.email.upwork.com", "mg.upwork.com",
    "fiverr.com", "email.fiverr.com", "freelancer.com", "email.freelancer.com",
    "contra.com", "toptal.com", "peopleperhour.com",
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "mg.gigforge.ai", "mg.techuni.ai", "mg.ai-elevate.ai",
    "mailer-daemon", "postmaster",
}

GIGFORGE_ALIASES = {
    "ceo": "gigforge", "director": "gigforge", "ops": "gigforge", "operations": "gigforge",
    "alex.reeves": "gigforge", "alex": "gigforge",
    "pm": "gigforge-pm", "sales": "gigforge-sales", "engineer": "gigforge-engineer",
    "devops": "gigforge-devops", "qa": "gigforge-qa", "scout": "gigforge-scout",
    "creative": "gigforge-creative", "social": "gigforge-social",
    "finance": "gigforge-finance", "support": "gigforge-support",
    "info": "gigforge-sales",
    "clientservices": "gigforge-advocate", "advocate": "gigforge-advocate",
    "jordan.reeves": "gigforge-advocate", "jordan": "gigforge-advocate",
    "legal": "gigforge-legal",
    "frontend": "gigforge-dev-frontend", "backend": "gigforge-dev-backend",
    "ai": "gigforge-dev-ai",
}

TECHUNI_ALIASES = {
    "ceo": "techuni-ceo", "director": "techuni-ceo", "operations": "techuni-ceo",
    "sales": "techuni-sales", "info": "techuni-sales",
    "marketing": "techuni-marketing", "social": "techuni-social", "seo": "techuni-seo",
    "support": "techuni-support", "cs": "techuni-support",
    "engineering": "techuni-engineering", "engineer": "techuni-engineering",
    "devops": "techuni-devops", "qa": "techuni-qa",
    "pm": "techuni-pm", "scrum": "techuni-scrum-master",
    "finance": "techuni-finance", "billing": "techuni-billing",
    "legal": "techuni-legal",
    "frontend": "techuni-dev-frontend", "backend": "techuni-dev-backend", "ai": "techuni-dev-ai",
    "ux": "techuni-ux-designer", "design": "techuni-ux-designer",
    "clientservices": "techuni-advocate", "advocate": "techuni-advocate",
    "sam.nakamura": "techuni-advocate", "sam": "techuni-advocate",
    "robin.callister": "techuni-ceo", "robin": "techuni-ceo",
    "ellis.kovac": "techuni-sales",
    "cameron.zhao": "techuni-pm",
}


AI_ELEVATE_ALIASES = {
    "ops": "operations", "operations": "operations",
    "security": "cybersecurity", "ciso": "cybersecurity",
    "content": "ai-elevate-content", "writer": "ai-elevate-writer",
    "editor": "ai-elevate-editor", "research": "ai-elevate-researcher",
    "factcheck": "ai-elevate-factchecker", "publisher": "ai-elevate-publisher",
    "reviewer": "ai-elevate-reviewer", "scraper": "ai-elevate-scraper",
    "legal": "ai-elevate-legal", "finance": "ai-elevate-billing",
    "monitor": "ai-elevate-monitor", "feedback": "ai-elevate-feedback",
    "director": "ai-elevate", "ceo": "ai-elevate",
    "kai.sorensen": "operations", "kai": "operations",
    "remy.volkov": "cybersecurity", "remy": "cybersecurity",
    "arin.blackwell": "ai-elevate", "arin": "ai-elevate",
}

# ---------------------------------------------------------------------------
# Agent registry — loaded once at startup from agent AGENTS.md files
# ---------------------------------------------------------------------------
def _load_agents():
    """Load valid agent IDs and names from disk + roles from DB."""
    agents = {}
    names = {}
    agents_dir = Path("/home/aielevate/.openclaw/agents")
    if agents_dir.exists():
        for d in sorted(agents_dir.iterdir()):
            md = d / "agent" / "AGENTS.md"
            if md.exists():
                agents[d.name] = True
                try:
                    text = md.read_text()[:500]
                    m = re.search(r"Your name is (\w+ \w+)", text)
                    name = m.group(1) if m else ""
                    title = ""
                    first_line = text.split("\n")[0]
                    if "\u2014" in first_line:
                        title = first_line.split("\u2014")[-1].strip()
                    elif "--" in first_line:
                        title = first_line.split("--")[-1].strip()
                    if name:
                        names[d.name] = (name, title)
                except Exception:
                    pass

    # Override titles with DB roles (more accurate than AGENTS.md first-line parsing)
    try:
        import psycopg2
        conn = psycopg2.connect(host="127.0.0.1", port=5434, dbname="rag", user="rag", password="rag_vec_2026")
        cur = conn.cursor()
        cur.execute("SELECT agent_id, name, role FROM agent_bios WHERE name != '' AND role != ''")
        for row in cur.fetchall():
            agent_id, db_name, db_role = row
            if agent_id in names:
                # Keep the name from AGENTS.md but use DB role
                names[agent_id] = (names[agent_id][0], db_role)
            elif db_name:
                names[agent_id] = (db_name, db_role)
        conn.close()
    except Exception:
        pass  # Fall back to AGENTS.md parsing if DB unavailable

    return agents, names

VALID_AGENTS, AGENT_NAMES = _load_agents()
logger.info(f"Loaded {len(VALID_AGENTS)} agents, {len(AGENT_NAMES)} with names")

# ---------------------------------------------------------------------------
# Deduplication — Postgres-backed, survives restarts, 24h TTL
# ---------------------------------------------------------------------------
def _init_dedup_table():
    """Create dedup table if it doesn't exist."""
    try:
        import psycopg2
        conn = psycopg2.connect(host="127.0.0.1", port=5434, dbname="rag", user="rag", password="rag_vec_2026")
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS email_dedup (
            dedup_key TEXT PRIMARY KEY,
            sender TEXT,
            subject TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_dedup_created ON email_dedup(created_at)")
        # Also create email_interactions for audit trail
        cur.execute("""CREATE TABLE IF NOT EXISTS email_interactions (
            id SERIAL PRIMARY KEY,
            message_id TEXT,
            sender_email TEXT,
            recipient TEXT,
            agent_id TEXT,
            subject TEXT,
            direction TEXT DEFAULT 'inbound',
            status TEXT DEFAULT 'received',
            sentiment TEXT,
            actions TEXT,
            workflow_id TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""")
        # Purge entries older than 24h
        cur.execute("DELETE FROM email_dedup WHERE created_at < NOW() - INTERVAL '24 hours'")
        # Add workflow_id column if missing
        try:
            cur.execute("ALTER TABLE email_interactions ADD COLUMN IF NOT EXISTS workflow_id TEXT")
        except Exception:
            pass
        conn.close()
    except Exception as e:
        logger.error(f"Dedup table init error: {e}")

_init_dedup_table()

def _is_duplicate(msg_id: str, sender: str, subject: str) -> bool:
    """Check Postgres for duplicate. Returns True if already processed."""
    key = msg_id or hashlib.md5(f"{sender}:{subject}".encode()).hexdigest()
    try:
        import psycopg2
        conn = psycopg2.connect(host="127.0.0.1", port=5434, dbname="rag", user="rag", password="rag_vec_2026")
        conn.autocommit = True
        cur = conn.cursor()
        # Purge old entries periodically
        cur.execute("DELETE FROM email_dedup WHERE created_at < NOW() - INTERVAL '24 hours'")
        # Add workflow_id column if missing
        try:
            cur.execute("ALTER TABLE email_interactions ADD COLUMN IF NOT EXISTS workflow_id TEXT")
        except Exception:
            pass
        # Check if exists
        cur.execute("SELECT 1 FROM email_dedup WHERE dedup_key = %s", (key,))
        exists = cur.fetchone() is not None
        if not exists and msg_id:
            # Only insert message-id based dedup (prevents Mailgun re-delivery of same message)
            # Do NOT insert sender+subject here — that's done by _mark_responded after reply
            cur.execute("INSERT INTO email_dedup (dedup_key, sender, subject) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                        (key, sender, subject))
        conn.close()
        return exists
    except Exception as e:
        logger.error(f"Dedup check error: {e}")
        return False  # Allow through on error rather than blocking

def _mark_responded(sender: str, subject: str):
    """Mark that we already responded to this sender+subject."""
    key = hashlib.md5(f"resp:{sender.lower()}:{subject.lower().strip()}".encode()).hexdigest()
    try:
        import psycopg2
        conn = psycopg2.connect(host="127.0.0.1", port=5434, dbname="rag", user="rag", password="rag_vec_2026")
        conn.autocommit = True
        conn.cursor().execute(
            "INSERT INTO email_dedup (dedup_key, sender, subject) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (key, sender, subject))
        conn.close()
    except Exception:
        pass

def _already_responded(sender: str, subject: str) -> bool:
    """Check if we already responded to this sender+subject."""
    key = hashlib.md5(f"resp:{sender.lower()}:{subject.lower().strip()}".encode()).hexdigest()
    try:
        import psycopg2
        conn = psycopg2.connect(host="127.0.0.1", port=5434, dbname="rag", user="rag", password="rag_vec_2026")
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM email_dedup WHERE dedup_key = %s", (key,))
        result = cur.fetchone() is not None
        conn.close()
        return result
    except Exception:
        return False

def _log_interaction(message_id, sender_email, recipient, agent_id, subject, direction="inbound", status="received"):
    """Log email interaction for audit trail."""
    try:
        import psycopg2
        conn = psycopg2.connect(host="127.0.0.1", port=5434, dbname="rag", user="rag", password="rag_vec_2026")
        conn.autocommit = True
        conn.cursor().execute(
            "INSERT INTO email_interactions (message_id, sender_email, recipient, agent_id, subject, direction, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (message_id, sender_email, recipient, agent_id, subject, direction, status))
        conn.close()
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _is_platform_notification(sender: str) -> bool:
    s = sender.lower()
    if s.startswith("bounce+") or s.startswith("bounces+"):
        return True
    return any(d in s for d in PLATFORM_SENDERS)

def _extract_email(raw: str) -> str:
    """Extract bare email from 'Name <email>' format."""
    raw = raw.strip()
    if "<" in raw and ">" in raw:
        return raw.split("<")[1].rstrip(">").lower()
    return raw.lower()

def _extract_name(raw: str) -> str:
    """Extract display name or first name from sender."""
    raw = raw.strip()
    if "<" in raw:
        name = raw.split("<")[0].strip().strip('"').strip()
        if name:
            # Use first name from display name
            return name.split()[0]
    # Fallback: parse email address but only use first part
    local = raw.split("@")[0] if "@" in raw else raw
    # Remove common suffixes (test, dev, etc)
    parts = local.replace("-", ".").replace("_", ".").split(".")
    # Take only the first name-like part
    return parts[0].capitalize() if parts else "there"

def _resolve_agent(recipient: str) -> str | None:
    """Resolve recipient email to agent ID."""
    email = _extract_email(recipient)
    local = email.split("@")[0]
    domain = email.split("@")[1] if "@" in email else ""

    # Direct agent match
    if local in VALID_AGENTS:
        return local

    # Domain-specific aliases
    if "gigforge" in domain:
        return GIGFORGE_ALIASES.get(local) or (local if local in VALID_AGENTS else None)
    elif "techuni" in domain:
        return TECHUNI_ALIASES.get(local) or (local if local in VALID_AGENTS else None)
    elif "ai-elevate" in domain:
        return VALID_AGENTS.get(local) and local or GIGFORGE_ALIASES.get(local) or TECHUNI_ALIASES.get(local)

    return None

def _get_org(agent_id: str) -> tuple:
    """Return (org, from_name, from_addr, sending_domain) for an agent."""
    if agent_id.startswith("gigforge") or agent_id in ("operations", "devops", "cybersecurity", "security-engineer"):
        return ("gigforge", "GigForge Team", f"{agent_id}@gigforge.ai", "gigforge.ai")
    elif agent_id.startswith("techuni"):
        return ("techuni", "TechUni Team", f"{agent_id}@techuni.ai", "techuni.ai")
    else:
        return ("ai-elevate", "AI Elevate", f"{agent_id}@internal.ai-elevate.ai", "internal.ai-elevate.ai")

def _send_mailgun(domain: str, form_data: dict):
    """Send email via Mailgun API."""
    data = _up.urlencode(form_data).encode("utf-8")
    req = _ur.Request(f"https://api.mailgun.net/v3/{domain}/messages", data=data, method="POST")
    req.add_header("Authorization", f"Basic {MAILGUN_CREDS}")
    _ur.urlopen(req, timeout=15)

# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------
def _build_system_prompt(agent_id: str, sender: str, sender_name: str, context_extra: str = "") -> str:
    """Build the system prompt for the agent."""
    # Directives
    directives = ""
    if get_directives:
        try:
            d = get_directives()
            if d:
                directives = "\n" + d + "\n"
        except Exception:
            pass

    # Identity
    name, title = AGENT_NAMES.get(agent_id, ("", ""))
    identity = f"YOUR NAME: {name}. YOUR TITLE: {title}. Sign ALL emails as {name}." if name else ""

    return f"""You are an AI agent ({agent_id}) at AI Elevate.
{directives}
{identity}
You handle inbound emails. Your role is defined in your AGENTS.md.

WORKFLOW: Before writing your reply, execute these Bash commands to update internal systems:

bash: python3 -c "import sys; sys.path.insert(0,'/home/aielevate'); from customer_context import update_sentiment; update_sentiment('{sender}', 'SENTIMENT', 'REASON', agent='{agent_id}')"
bash: python3 -c "import sys; sys.path.insert(0,'/home/aielevate'); from ops_notify import ops_notify; ops_notify('status_update', 'DESCRIPTION', agent='{agent_id}', customer_email='{sender}')"

Replace SENTIMENT with positive/neutral/frustrated/at_risk based on email tone.
Replace REASON and DESCRIPTION with brief context.
Execute these BEFORE writing your reply. Use the Bash tool.

After executing workflow commands, output ONLY the email reply text.
Nothing else — no summaries, no markdown, no bullet points about what you did.
Just the plain text email reply as it should appear in the sender's inbox.

The sender's first name is: {sender_name}
Start with "Hi {sender_name}," and end with your sign-off.
Sound like a real human. Use contractions, vary tone, be concise.
Do NOT use robotic phrases. Do NOT offer phone calls or meetings.
Do NOT re-introduce yourself if you have already emailed this person (check context below).
NEVER admit ignorance — search Plane, RAG, and KG first.
{context_extra}"""

# ---------------------------------------------------------------------------
# Main webhook endpoint
# ---------------------------------------------------------------------------
@app.post("/webhook/inbound")
async def inbound_email(
    sender: str = Form(""),
    recipient: str = Form(""),
    subject: str = Form(""),
    body_plain: str = Form("", alias="body-plain"),
    stripped_text: str = Form("", alias="stripped-text"),
    message_id: str = Form("", alias="Message-Id"),
    header_from: str = Form("", alias="from"),
):
    raw_sender = sender
    raw_body = body_plain or stripped_text or ""

    # --- Bounce handling: use From header if envelope is bounce ---
    if sender.lower().startswith("bounce+") and header_from:
        logger.info(f"Bounce detected: envelope={sender[:40]}, From header={header_from[:40]}")
        sender = header_from

    # --- Extract clean email ---
    sender_email = _extract_email(sender)
    sender_name = _extract_name(sender)

    # --- Platform notification filter (MUST be before dedup to avoid poisoning dedup table) ---
    if _is_platform_notification(sender_email):
        logger.info(f"Platform notification from {sender_email} — skipped")
        return {"status": "skipped", "reason": "platform_notification"}

    # --- Dedup ---
    if _is_duplicate(message_id, sender_email, subject):
        logger.info(f"DEDUP: skipping {sender_email} subject={subject}")
        return {"status": "skipped", "reason": "duplicate"}

    # --- Feedback reply processing ---
    if parse_feedback_email and _process_feedback and "how was your" in subject.lower():
        try:
            feedback_data = parse_feedback_email(sender_email, raw_body)
            if feedback_data.get("rating"):
                result = _process_feedback(**feedback_data)
                logger.info(f"Feedback processed: {sender_email} rated {feedback_data.get('rating')}/10")
                return {"status": "feedback_processed", "rating": feedback_data.get("rating")}
        except Exception as e:
            logger.error(f"Feedback processing error: {e}")

    # --- Proposal approval processing ---
    if process_approval and ("APPROVE" in raw_body.upper() or "REJECT" in raw_body.upper()):
        if "proposal" in subject.lower():
            try:
                actions = process_approval(sender_email, raw_body)
                if actions:
                    logger.info(f"Proposal queue: {len(actions)} actions from {sender_email}")
                    return {"status": "proposal_actions", "actions": len(actions)}
            except Exception as e:
                logger.error(f"Proposal queue error: {e}")

    # --- Resolve agent ---
    agent_id = _resolve_agent(recipient)
    if not agent_id:
        logger.warning(f"No agent for recipient: {recipient}")
        return {"status": "no_agent", "recipient": recipient}

    org, from_name, from_addr, sending_domain = _get_org(agent_id)

    # --- Loop detection ---
    sender_domain = sender_email.split("@")[-1] if "@" in sender_email else ""
    if sender_domain in AGENT_DOMAINS and sender_email not in (
        "braun.brelin@ai-elevate.ai", "bbrelin@gmail.com",
        "peter.munro@ai-elevate.ai", "mike.burton@ai-elevate.ai",
        "charlie.turking@ai-elevate.ai",
    ):
        logger.warning(f"LOOP BLOCKED: {sender_email} → {recipient}")
        return {"status": "skipped", "reason": "loop_detection"}

    logger.info(f"Inbound email: {sender_email} → {recipient} (agent={agent_id}) subject='{subject}'")

    # --- SLA tracking ---
    if log_received:
        try:
            log_received(sender=sender_email, recipient=recipient, agent_id=agent_id,
                        subject=subject, org=org, priority="normal")
        except Exception:
            pass

    # --- Extract clean body ---
    message = raw_body
    if extract_reply:
        try:
            message = extract_reply(raw_body) or raw_body
        except Exception:
            pass

    # --- Thread tracking ---
    thread_context = ""
    if add_to_thread:
        try:
            thread_result = add_to_thread(
                sender=sender_email, recipients=[_extract_email(recipient)],
                subject=subject, body=message, direction="inbound", agent_id=agent_id
            )
            thread_id = thread_result.get("thread_id", "")
            if get_thread_context:
                tc = get_thread_context(thread_id)
                if tc and len(tc) > 50:
                    thread_context = f"\n\nConversation history:\n{tc}"
        except Exception:
            pass

    # --- Customer context ---
    customer_ctx = ""
    active_owner = None
    if customer_context_summary:
        try:
            cs = customer_context_summary(sender_email)
            if cs and len(cs) > 30:
                customer_ctx = f"\n\nCustomer context:\n{cs}"
            if customer_get_context:
                full = customer_get_context(sender_email)
                if full and full.get("active_project") and full.get("current_owner"):
                    active_owner = full["current_owner"]
        except Exception:
            pass

    # Smart routing note
    if active_owner and active_owner != agent_id:
        customer_ctx += (f"\n\nNOTE: This customer has an active project owned by {active_owner}. "
                        f"Respond helpfully, then notify {active_owner} via sessions_send.")

    # --- Comms hub analysis ---
    if process_message:
        try:
            process_message(message, sender=sender_email, channel="email", org=org)
        except Exception:
            pass

    # --- Build agent message ---
    full_message = f"INBOUND EMAIL — respond to this email.\n\nFrom: {sender}\nSubject: {subject}\nSender first name: {sender_name}\n\nEmail body:\n{message}"
    if thread_context:
        full_message += thread_context
    if customer_ctx:
        full_message += customer_ctx

    # --- Auto-ack REMOVED: was sending duplicate emails ---
    # The Temporal workflow sends the real reply; no need for a separate ack.
    _log_interaction(message_id, sender_email, recipient, agent_id, subject)

    # --- Read agent AGENTS.md for context ---
    agent_context = ""
    agents_md = Path(f"/home/aielevate/.openclaw/agents/{agent_id}/agent/AGENTS.md")
    if agents_md.exists():
        try:
            agent_context = agents_md.read_text()[:3000]
        except Exception:
            pass

    # --- Build system prompt ---
    system_prompt = _build_system_prompt(agent_id, sender_email, sender_name, customer_ctx)

    # --- Dispatch to agent ---
    try:
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

        if _already_responded(sender_email, subject):
            logger.info(f"DEDUP: already responded to {sender_email}/{subject}")
            return {"status": "skipped", "reason": "already_responded"}

        # Dispatch via Temporal workflow
        try:
            # Try orchestrator first (handles intent classification + workflow routing)
            try:
                from orchestrator_workflow import orchestrate_email
                logger.info(f"Dispatching {agent_id} via orchestrator workflow")
                orch_result = await orchestrate_email(
                    sender_email, recipient, subject, full_message,
                    message_id=message_id, agent_id=agent_id, org=org)
                logger.info(f"Orchestrator started: {orch_result}")
                # NOTE: Do NOT mark responded here — the orchestrator runs async.
                # send_email.py marks responded AFTER the reply is actually sent.
                return {"status": "orchestrated", "workflow": orch_result.get("workflow_id", "")}
            except Exception as orch_err:
                logger.warning(f"Orchestrator unavailable ({orch_err}), falling back to direct")
                # Fallback: direct Temporal workflow (no orchestrator)
                from temporal_workflows import execute_workflow
                logger.info(f"Dispatching {agent_id} via Temporal workflow (fallback)")
                result = await execute_workflow(agent_id, full_message, sender_email, subject, timeout=300)
            stdout = result.get("stdout", "") or ""
            returncode = result.get("returncode", -1)
            suppress_reply = result.get("suppress_reply", False)
            logger.info(f"Temporal: sentiment={result.get('sentiment')}, actions={result.get('actions')}, notified={result.get('notified')}")
            if suppress_reply:
                logger.info(f"Reply suppressed (relay through CS) for {agent_id}")
                return {"status": "relayed", "agent": agent_id}

            # If acceptance detected, start the build workflow
            if result.get("actions") and any("handoff:" in a for a in result.get("actions", [])):
                try:
                    from build_workflow import start_build
                    slug = subject.lower().replace("re: ", "").replace(" ", "-")[:30]
                    slug = "".join(c for c in slug if c.isalnum() or c == "-")
                    build_result = await start_build(sender_email, subject.replace("Re: ", ""), slug or "project", full_message[:1000])
                    logger.info(f"Build workflow started: {build_result}")

                except Exception as build_err:
                    logger.warning(f"Failed to start build workflow: {build_err}")


        except Exception as temporal_err:
            logger.warning(f"Temporal unavailable ({temporal_err}), falling back to direct queue")
            if async_queue_agent_call:
                result = await async_queue_agent_call(agent_id, full_message, timeout=300, thinking="low")
                stdout = result.get("stdout", "") or ""
                returncode = result.get("returncode", -1)
            else:
                logger.error("No dispatch method available")
                return {"status": "error", "reason": "no_dispatch"}


        # --- Process response ---
        response = stdout.strip()

        # Strip tool call markers from output
        # Strip ALL tool call markers (single and multi-line)
        response = re.sub(r'\*\[.*?\]\*', '', response, flags=re.DOTALL).strip()
        # Also strip timing annotations like "(9s) · 9 tools"
        response = re.sub(r'\(\d+s\)\s*·\s*\d+\s*tools?', '', response).strip()
        # Strip lines that are only whitespace or dashes
        response = '\n'.join(line for line in response.split('\n') if line.strip() and line.strip() != '---').strip()

        # --- Output enforcement: fix identity and remove call offers ---
        # Replace wrong names with the correct agent name
        agent_name, agent_title = AGENT_NAMES.get(agent_id, ("", ""))
        if agent_name:
            # Common wrong names from the peer table
            wrong_names = ["Alex Reeves", "Alex", "Jordan Reeves", "Jordan Whitaker", "Jordan", "Sam Carrington",
                          "Sam", "Kai Sorensen", "Kai", "Morgan Dell", "Drew Fontaine"]
            # Remove the correct name from the wrong list
            wrong_names = [n for n in wrong_names if n != agent_name and n != agent_name.split()[0]]
            for wrong in wrong_names:
                # Only replace in sign-off area (last 200 chars)
                tail = response[-200:]
                if wrong in tail:
                    tail = tail.replace(wrong, agent_name)
                    response = response[:-200] + tail

        # Remove call/meeting offers
        call_patterns = [
            r"[Hh]appy to (?:hop on|jump on|set up|schedule) a (?:quick )?call.*?[.!]",
            r"[Ww]e could (?:hop on|jump on) a (?:quick )?call.*?[.!]",
            r"[Ff]eel free to (?:book|schedule|set up) a (?:call|meeting|zoom|video).*?[.!]",
            r"[Hh]appy to (?:chat|talk|discuss) (?:on|over|via) (?:a )?(?:call|phone|zoom|video|teams).*?[.!]",
            r"[Ll]et me know if you.d (?:prefer|like|rather) a (?:quick )?(?:call|chat|meeting).*?[.!]",
        ]
        for pattern in call_patterns:
            response = re.sub(pattern, "", response).strip()


        # Take the last substantial block (the actual reply)
        blocks = [b.strip() for b in response.split('\n\n') if len(b.strip()) > 20]
        if blocks:
            for b in blocks:
                if b[:20].lower().startswith(('hi ', 'hello', 'dear ', 'thanks', 'hey ', 'good ')):
                    response = b
                    break
            else:
                response = blocks[-1]

        logger.info(f"Agent {agent_id} responded ({len(response)} chars) to {sender_email}")

        # --- Check for auth errors — only flag if response IS an error, not contains error text ---
        auth_errors = ["authentication_error", "OAuth token has expired", "Failed to authenticate"]
        _is_auth_error = (
            len(response) < 200 and  # Real replies are longer than 200 chars
            any(err.lower() in response.lower() for err in auth_errors)
        )
        if _is_auth_error:
            logger.error(f"Agent {agent_id} AUTH FAILURE — response not sent")
            return {"status": "auth_error"}

        # --- Send auto-reply ---
        # DISABLED: Agents send their own replies via send_email.py.
        # This _send_mailgun call was creating duplicate emails.
        # The orchestrator path already returns early; this fallback path
        # was also sending, causing 2-3x duplicate replies.
        if False and response and len(response) > 10:
            try:
                # Format with email template — only if response is a real reply
                _skip_template = any(skip in response.lower() for skip in [
                    "already handled", "already replied", "already responded",
                    "no action needed", "no further action", "previous turn",
                ])
                if email_format and email_pick and not _skip_template and len(response) > 50:
                    try:
                        _is_first = True
                        _tpl = email_pick(subject, response, agent_id, _is_first)
                        _an, _at = AGENT_NAMES.get(agent_id, ("GigForge Team", ""))
                        _co = "GigForge" if "gigforge" in agent_id or agent_id in ("operations",) else "TechUni"
                        _fmt = email_format(_tpl, {
                            "customer_name": _extract_name(sender_email),
                            "agent_name": _an or "GigForge Team",
                            "agent_title": _at or "Team",
                            "company": _co,
                            "body": response,
                            "project_title": subject.replace("Re: ", ""),
                        })
                        if _fmt and len(_fmt) > 20:
                            response = _fmt
                    except Exception:
                        pass
                reply_text = response
                # Add quoted previous message
                quoted = f"\n\n---\nOn {datetime.now(timezone.utc).strftime('%b %d, %Y')}, {sender_email} wrote:\n> " + raw_body[:500].replace("\n", "\n> ")
                reply_text += quoted

                _send_mailgun(sending_domain, {
                    "from": f"{from_name} <{from_addr}>",
                    "to": sender_email,
                    "cc": "braun.brelin@ai-elevate.ai",
                    "h:Reply-To": from_addr,
                    "h:In-Reply-To": f"<{message_id}>" if message_id else "",
                    "h:References": f"<{message_id}>" if message_id else "",
                    "subject": f"Re: {subject}",
                    "text": reply_text,
                })
                _mark_responded(sender_email, subject)
                logger.info(f"Auto-reply sent to {sender_email} from {agent_id}")

                # SLA tracking
                if log_responded:
                    try:
                        log_responded(sender=sender_email, agent_id=agent_id, response_length=len(response))
                    except Exception:
                        pass

                # Thread tracking
                if add_to_thread:
                    try:
                        add_to_thread(
                            sender=from_addr, recipients=[sender_email],
                            subject=f"Re: {subject}", body=response,
                            direction="outbound", agent_id=agent_id
                        )
                    except Exception:
                        pass

            except Exception as e:
                logger.error(f"Auto-reply failed: {e}")

        # --- CRM lead registration ---
        try:
            audit_log_action(agent_id=agent_id, org=org, action_type="email_response",
                           target=sender_email, status="success", evidence=f"{len(response)} chars")
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Failed to dispatch to {agent_id}: {e}")

    return {"status": "accepted", "agent": agent_id}

# ---------------------------------------------------------------------------
# Health and utility endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    return {"status": "ok", "agents": len(VALID_AGENTS)}

@app.get("/agents")
async def list_agents():
    return {"agents": sorted(VALID_AGENTS.keys()), "count": len(VALID_AGENTS)}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8065)
