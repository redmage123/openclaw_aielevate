#!/usr/bin/env python3
"""Unified Communications Pipeline — ALL channels, ALL directions.

Every message (inbound, outbound, agent-to-agent) passes through this pipeline:

INBOUND:  Store → NLP classify → Route → Deliver to agent
OUTBOUND: Scrub → Store → NLP train → Deliver to customer
AGENT-TO-AGENT: Store → NLP train → Deliver

Channels: email, telegram, whatsapp, slack, discord, sessions_send, webhook

This replaces the separate:
- email_nlp_pipeline.py (email-specific)
- nlp_email_scrubber.py (email-specific)
- comms_hub.py process_message (partial)
And unifies them into one entry point.
"""

import sys
import json
import logging
import threading
import time
import re
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, "/home/aielevate")

log = logging.getLogger("unified-comms")

# ── Imports with graceful fallback ───────────────────────────────────

def _safe_import(module, func):
    try:
        mod = __import__(module)
        return getattr(mod, func)
    except Exception as _e:

        import logging; logging.getLogger('unified_comms.py').debug(f'Suppressed: {_e}')

        return None

# NLP components
_classify = _safe_import("email_nlp_pipeline", "classify_email")
_store = _safe_import("email_nlp_pipeline", "store_email")
_queue_train = _safe_import("email_nlp_pipeline", "queue_training")
_scrub = _safe_import("nlp_email_scrubber", "scrub_email")
_process_attachments = _safe_import("attachment_processor", "process_attachments")

# Comms hub components
_comms_process = _safe_import("comms_hub", "process_message")
_fuzzy_analyze = _safe_import("fuzzy_comms", "analyze")


# ── Unified Pipeline ─────────────────────────────────────────────────

def process_inbound(
    text: str,
    sender: str,
    recipient: str = "",
    subject: str = "",
    channel: str = "email",
    agent_id: str = "",
    org: str = "gigforge",
    attachments: list = None,
    cc: str = "",
    metadata: dict = None,
) -> dict:
    """Process ANY inbound message through the full NLP pipeline.

    Works for: email, telegram, whatsapp, slack, discord, webhook, sessions_send.

    Returns:
        dict with: intent, confidence, entities, sentiment, routing, attachment_text
    """
    result = {
        "channel": channel,
        "sender": sender,
        "recipient": recipient,
        "subject": subject,
        "agent_id": agent_id,
        "org": org,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # 1. Extract attachment text if present
    attachment_text = ""
    if attachments and _process_attachments:
        try:
            att_result = _process_attachments(attachments)
            attachment_text = att_result.get("text", "")
            result["attachments"] = {
                "count": att_result.get("count", 0),
                "filenames": att_result.get("filenames", []),
                "text_length": len(attachment_text),
            }
        except Exception as e:
            log.debug(f"Attachment processing: {e}")

    full_text = f"{subject}\n\n{text}"
    if attachment_text:
        full_text += f"\n\n{attachment_text}"

    # 2. NLP classification (intent, entities, topics)
    if _classify:
        try:
            classification = _classify(sender, subject, text + " " + attachment_text)
            result["intent"] = classification.get("intent", "general")
            result["confidence"] = classification.get("confidence", 0)
            result["topics"] = classification.get("topics", {})
            result["entities"] = classification.get("entities", {})
            result["method"] = classification.get("method", "unknown")
        except Exception as e:
            log.debug(f"Classification: {e}")
            result["intent"] = "general"
            result["confidence"] = 0.5

    # 3. Comms hub analysis (fuzzy sentiment, urgency, escalation)
    if _comms_process:
        try:
            comms = _comms_process(text, sender=sender, channel=channel, org=org, agent_id=agent_id)
            result["sentiment"] = comms.get("fuzzy", {}).get("raw_scores", {}).get("sentiment", 0.5)
            result["urgency"] = comms.get("fuzzy", {}).get("raw_scores", {}).get("urgency", 0)
            result["routing"] = comms.get("routing", {})
            result["flags"] = comms.get("flags", [])
        except Exception as e:
            log.debug(f"Comms hub: {e}")

    # 4. Store compressed + encrypted
    if _store:
        try:
            _store(sender, recipient, subject, text + ("\n\n" + attachment_text if attachment_text else ""),
                   direction="inbound", agent_id=agent_id)
        except Exception as e:
            log.debug(f"Storage: {e}")

    # 5. Queue for incremental NLP training
    if _queue_train:
        try:
            _queue_train(sender, subject, text + " " + attachment_text)
        except Exception as e:
            log.debug(f"Training queue: {e}")

    return result


def process_outbound(
    text: str,
    sender_agent: str,
    recipient: str,
    subject: str = "",
    channel: str = "email",
    org: str = "gigforge",
    metadata: dict = None,
) -> str:
    """Process ANY outbound message — scrub, store, train.

    Works for: email, telegram, whatsapp, slack, discord, sessions_send.

    Returns:
        Scrubbed text ready to send.
    """
    # 1. Scrub (remove metadata, call suggestions, AI language)
    if _scrub:
        try:
            text = _scrub(text)
        except Exception as e:
            log.debug(f"Scrub: {e}")

    # 2. Store compressed + encrypted
    if _store:
        try:
            _store(recipient, sender_agent, subject, text, direction="outbound", agent_id=sender_agent)
        except Exception as e:
            log.debug(f"Storage: {e}")

    # 3. Queue for training
    if _queue_train:
        try:
            _queue_train(recipient, subject, text)
        except Exception as e:
            log.debug(f"Training: {e}")

    return text


def process_agent_message(
    text: str,
    from_agent: str,
    to_agent: str,
    org: str = "gigforge",
    metadata: dict = None,
) -> str:
    """Process agent-to-agent communication — store, train, no scrubbing.

    Internal messages don't need scrubbing but DO need:
    - Storage (for audit trail and training data)
    - NLP training (agent comms help the model understand internal patterns)
    """
    # Store
    if _store:
        try:
            _store(from_agent, to_agent, f"[agent] {from_agent} → {to_agent}", text,
                   direction="internal", agent_id=from_agent)
        except Exception as e:
            log.debug(f"Storage: {e}")

    # Train
    if _queue_train:
        try:
            _queue_train(from_agent, f"agent-comms-{from_agent}", text)
        except Exception as e:
            log.debug(f"Training: {e}")

    return text


# ── Channel-specific wrappers ────────────────────────────────────────

def process_telegram_inbound(message: dict) -> dict:
    """Process inbound Telegram message."""
    text = message.get("text", "")
    sender = message.get("from", {}).get("username", str(message.get("from", {}).get("id", "")))
    chat_id = str(message.get("chat", {}).get("id", ""))
    return process_inbound(text, sender=sender, recipient=chat_id, channel="telegram")


def process_telegram_outbound(text: str, agent_id: str, chat_id: str) -> str:
    """Scrub and store outbound Telegram message."""
    return process_outbound(text, sender_agent=agent_id, recipient=chat_id, channel="telegram")


def process_whatsapp_inbound(message: dict) -> dict:
    """Process inbound WhatsApp message."""
    text = message.get("body", message.get("text", ""))
    sender = message.get("from", "")
    return process_inbound(text, sender=sender, channel="whatsapp")


def process_whatsapp_outbound(text: str, agent_id: str, recipient: str) -> str:
    """Scrub and store outbound WhatsApp message."""
    return process_outbound(text, sender_agent=agent_id, recipient=recipient, channel="whatsapp")


def process_slack_inbound(event: dict) -> dict:
    """Process inbound Slack message."""
    text = event.get("text", "")
    sender = event.get("user", "")
    channel_id = event.get("channel", "")
    return process_inbound(text, sender=sender, recipient=channel_id, channel="slack")


def process_slack_outbound(text: str, agent_id: str, channel_id: str) -> str:
    """Scrub and store outbound Slack message."""
    return process_outbound(text, sender_agent=agent_id, recipient=channel_id, channel="slack")


def process_discord_inbound(message: dict) -> dict:
    """Process inbound Discord message."""
    text = message.get("content", "")
    sender = message.get("author", {}).get("username", "")
    channel_id = str(message.get("channel_id", ""))
    return process_inbound(text, sender=sender, recipient=channel_id, channel="discord")


def process_discord_outbound(text: str, agent_id: str, channel_id: str) -> str:
    """Scrub and store outbound Discord message."""
    return process_outbound(text, sender_agent=agent_id, recipient=channel_id, channel="discord")


def process_sessions_send(text: str, from_agent: str, to_agent: str) -> str:
    """Process agent-to-agent sessions_send message."""
    return process_agent_message(text, from_agent=from_agent, to_agent=to_agent)
