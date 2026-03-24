#!/usr/bin/env python3
"""AI Elevate Communications Hub

Integrates: Fuzzy Logic + RAG + NLP + Notification System + Escalation

Every inbound communication (email, chat, inter-agent) flows through this hub:
1. NLP preprocessing (tokenize, extract entities, classify language)
2. Fuzzy logic analysis (sentiment, urgency, intent, satisfaction)
3. RAG context retrieval (find relevant past interactions, knowledge)
4. Priority routing (determine notification channels and escalation tier)
5. Response recommendation (tone, template, context injection)
6. Logging and learning (feed outcomes back to improve)

Usage:
    from comms_hub import process_message
    result = process_message(
        text="I've been waiting 3 days...",
        sender="customer@example.com",
        channel="email",
        org="gigforge",
    )
"""

import json
import os
import re
import sys
import time
from datetime import datetime, UTC
from pathlib import Path
from typing import Any
from exceptions import AiElevateError  # TODO: Use specific exception types

# Import sibling modules
sys.path.insert(0, "/home/aielevate")
from fuzzy_comms import analyze as fuzzy_analyze, classify_priority, should_escalate
from notify import send as notify_send
from sales_marketing import score_lead, track_attribution, trigger_event, update_journey

# ── NLP Module ───────────────────────────────────────────────────────────

class NLP:
    """Lightweight NLP preprocessing — no external dependencies."""

    # Common entity patterns
    EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
    URL_RE = re.compile(r"https?://\S+")
    PHONE_RE = re.compile(r"\b\+?\d[\d\-\(\) ]{7,}\d\b")
    MONEY_RE = re.compile(r"\$[\d,]+(?:\.\d{2})?|\b\d+\s*(?:dollars?|USD|EUR|GBP)\b", re.I)
    DATE_RE = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{1,2}(?:,?\s+\d{4})?\b", re.I)
    ACCOUNT_RE = re.compile(r"\b(?:account|order|ticket|case|ref)\s*#?\s*(\w+)\b", re.I)

    # Language detection (basic — English-centric)
    COMMON_ENGLISH = {"the", "is", "at", "which", "on", "a", "an", "and", "or", "but", "in", "with", "to", "for", "of", "this", "that", "it", "not", "are", "was", "were", "be", "have", "has", "had", "do", "does", "did", "will", "would", "can", "could", "should", "may", "might"}

    @staticmethod
    def extract_entities(text: str) -> dict[str, list[str]]:
        """Extract named entities from text."""
        return {
            "emails": NLP.EMAIL_RE.findall(text),
            "urls": NLP.URL_RE.findall(text),
            "phones": NLP.PHONE_RE.findall(text),
            "money": NLP.MONEY_RE.findall(text),
            "dates": NLP.DATE_RE.findall(text),
            "references": NLP.ACCOUNT_RE.findall(text),
        }

    @staticmethod
    def extract_key_phrases(text: str, max_phrases: int = 5) -> list[str]:
        """Extract key noun phrases using simple heuristics."""
        # Remove common stop words and extract significant bigrams/trigrams
        words = re.findall(r"\b[a-z]{3,}\b", text.lower())
        stop = {"the", "and", "for", "are", "but", "not", "you", "all", "can", "had",
                "her", "was", "one", "our", "out", "has", "have", "been", "from", "this",
                "that", "with", "they", "will", "each", "make", "like", "been", "just",
                "than", "them", "very", "when", "what", "your", "some", "would", "there",
                "their", "about", "which", "could", "other", "into", "more", "also", "after",
                "should", "these", "being", "between"}
        filtered = [w for w in words if w not in stop]

        # Count word frequencies
        freq = {}
        for w in filtered:
            freq[w] = freq.get(w, 0) + 1

        # Extract bigrams
        bigrams = []
        for i in range(len(filtered) - 1):
            bg = f"{filtered[i]} {filtered[i+1]}"
            bigrams.append(bg)

        # Score phrases by frequency and position
        phrases = sorted(freq.items(), key=lambda x: x[1], reverse=True)
        return [p[0] for p in phrases[:max_phrases]]

    @staticmethod
    def summarize(text: str, max_sentences: int = 3) -> str:
        """Extract the most important sentences."""
        sentences = re.split(r'[.!?]+', text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 20]

        if len(sentences) <= max_sentences:
            return ". ".join(sentences) + "."

        # Score sentences by: position, length, keyword density
        scored = []
        key_phrases = set(NLP.extract_key_phrases(text, 10))
        for i, sent in enumerate(sentences):
            words = set(re.findall(r"\b[a-z]{3,}\b", sent.lower()))
            keyword_score = len(words & key_phrases) / max(len(words), 1)
            position_score = 1.0 / (i + 1)  # Earlier sentences weighted more
            length_score = min(len(sent) / 200, 1.0)  # Prefer moderate length
            total = keyword_score * 0.5 + position_score * 0.3 + length_score * 0.2
            scored.append((total, sent))

        scored.sort(reverse=True)
        top = [s[1] for s in scored[:max_sentences]]
        # Return in original order
        return ". ".join(s for s in sentences if s in top) + "."

    @staticmethod
    def detect_language(text: str) -> str:
        """Basic language detection."""
        words = set(re.findall(r"\b[a-z]+\b", text.lower()))
        english_ratio = len(words & NLP.COMMON_ENGLISH) / max(len(words), 1)
        return "en" if english_ratio > 0.15 else "unknown"

    @staticmethod
    def classify_message_type(text: str) -> str:
        """Classify the type of message."""
        lower = text.lower()
        if "?" in text and text.count("?") > text.count("."):
            return "question"
        if any(w in lower for w in ["please fix", "bug", "broken", "error", "crash", "not working"]):
            return "bug_report"
        if any(w in lower for w in ["feature", "would be nice", "suggestion", "could you add", "wish"]):
            return "feature_request"
        if any(w in lower for w in ["cancel", "refund", "unsubscribe", "close my account"]):
            return "cancellation"
        if any(w in lower for w in ["thank", "great", "love", "awesome", "perfect"]):
            return "appreciation"
        if any(w in lower for w in ["how do i", "how to", "help me", "getting started", "tutorial"]):
            return "help_request"
        if any(w in lower for w in ["invoice", "billing", "charge", "payment", "receipt"]):
            return "billing"
        return "general"


# ── RAG Integration ──────────────────────────────────────────────────────

async def rag_context(text: str, org: str = "", sender: str = "") -> str:
    """Retrieve relevant context from RAG for the communication."""
    try:
        # Import the RAG module if available (CryptoAdvisor has one)
        # For other orgs, use the file-based search
        context_parts = []

        # Search past tickets for similar issues
        ticket_log = f"/opt/ai-elevate/{org}/support/ticket-log.csv"
        if os.path.exists(ticket_log):
            with open(ticket_log) as f:
                lines = f.readlines()[-50:]  # Last 50 tickets
            keywords = set(NLP.extract_key_phrases(text, 5))
            matching = [l.strip() for l in lines if any(k in l.lower() for k in keywords)]
            if matching:
                context_parts.append("PAST TICKETS:\n" + "\n".join(matching[-5:]))

        # Search for sender's history
        if sender:
            sender_history = [l.strip() for l in lines if sender.lower() in l.lower()] if os.path.exists(ticket_log) else []
            if sender_history:
                context_parts.append(f"SENDER HISTORY ({sender}):\n" + "\n".join(sender_history[-3:]))

        # Search knowledge base / FAQ
        for kb_path in [
            f"/opt/ai-elevate/{org}/support/knowledge-base.md",
            f"/opt/ai-elevate/{org}/support/faq.md",
        ]:
            if os.path.exists(kb_path):
                with open(kb_path) as f:
                    kb = f.read()
                keywords = NLP.extract_key_phrases(text, 5)
                for kw in keywords:
                    if kw in kb.lower():
                        # Extract relevant section
                        idx = kb.lower().index(kw)
                        start = max(0, kb.rfind("\n#", 0, idx))
                        end = kb.find("\n#", idx + 1)
                        if end == -1:
                            end = min(idx + 500, len(kb))
                        context_parts.append(f"KNOWLEDGE BASE:\n{kb[start:end].strip()}")
                        break

        return "\n\n".join(context_parts) if context_parts else ""
    except (AiElevateError, Exception) as e:
        return ""


# ── Main Processing Pipeline ────────────────────────────────────────────

def process_message(text: str, sender: str = "", channel: str = "email",
                    org: str = "gigforge", current_tier: int = 1,
                    agent_id: str = "") -> dict[str, Any]:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Process an inbound communication through the full pipeline.

    Returns comprehensive analysis + routing decision + response recommendation.
    """
    timestamp = datetime.now(UTC).isoformat()

    # Step 1: NLP preprocessing
    entities = NLP.extract_entities(text)
    key_phrases = NLP.extract_key_phrases(text)
    summary = NLP.summarize(text)
    message_type = NLP.classify_message_type(text)
    language = NLP.detect_language(text)

    # Step 2: Fuzzy logic analysis
    fuzzy = fuzzy_analyze(text)

    # Step 3: Escalation decision
    should_esc, rec_tier, esc_reason = should_escalate(text, current_tier)

    # Step 4: Priority classification
    priority = classify_priority(text)

    # Step 5: Build result
    result = {
        "timestamp": timestamp,
        "channel": channel,
        "sender": sender,
        "org": org,
        "language": language,

        # NLP
        "nlp": {
            "message_type": message_type,
            "summary": summary,
            "key_phrases": key_phrases,
            "entities": {k: v for k, v in entities.items() if v},
        },

        # Fuzzy analysis
        "fuzzy": fuzzy,

        # Routing
        "routing": {
            "priority": priority,
            "current_tier": current_tier,
            "recommended_tier": rec_tier,
            "should_escalate": should_esc,
            "escalation_reason": esc_reason,
            "response_tone": fuzzy["response_tone"],
            "notification_channels": _get_channels(priority),
        },

        # Flags
        "flags": fuzzy["flags"],
    }

    # Step 5.5: Sales & Marketing integration
    # Auto-score leads from inbound messages
    if result["nlp"]["message_type"] in ("inquiry", "help_request", "general"):
        lead_data = score_lead(sender, org=org, 
                               engagement_actions=["inbound_email"],
                               urgency_level="medium" if fuzzy["raw_scores"]["urgency"] > 0.5 else "low")
        result["lead_score"] = lead_data.get("score", 0)
        result["lead_category"] = lead_data.get("category", "cold")
        track_attribution(sender, channel=channel, org=org)
        update_journey(sender, "consideration", touchpoint=f"inbound_{channel}", org=org)

    # Track competitor mentions from customer messages
    comps = track_competitor_mentions(text, sender, org=org) if callable(globals().get('track_competitor_mentions')) else []
    if comps:
        result["competitors_mentioned"] = comps

    # Step 6: Auto-escalate if needed
    if should_esc and rec_tier >= 3:
        _auto_escalate(result, text, org)

    # Step 7: Log
    _log_communication(result, text)

    return result


def _get_channels(priority: str) -> list[str]:
    """Get notification channels for priority level."""
    channels = {
        "critical": ["telegram", "email", "ntfy"],
        "high": ["telegram", "email", "ntfy"],
        "medium": ["email"],
        "low": ["email_batch"],
    }
    return channels.get(priority, ["email"])


def _auto_escalate(result: dict, text: str, org: str):
    """Automatically escalate and notify."""
    tier = result["routing"]["recommended_tier"]
    reason = result["routing"]["escalation_reason"]
    sender = result.get("sender", "unknown")
    summary = result["nlp"]["summary"]

    if tier >= 4:
        # Executive escalation
        notify_send(
            f"EXECUTIVE ESCALATION — {org.upper()}",
            f"Customer: {sender}\n"
            f"Issue: {summary}\n"
            f"Escalation: {reason}\n"
            f"Sentiment: {result['fuzzy']['raw_scores']['sentiment']:.0%} negative\n"
            f"Urgency: {result['fuzzy']['raw_scores']['urgency']:.0%}\n"
            f"Flags: {', '.join(result['flags'])}",
            priority="critical",
            to="all",
        )
    elif tier >= 3:
        # Management escalation
        notify_send(
            f"Customer Escalation — {org.upper()} (Tier 3)",
            f"Customer: {sender}\n"
            f"Issue: {summary}\n"
            f"Reason: {reason}\n"
            f"Recommended tone: {result['routing']['response_tone']}",
            priority="high",
            to=["braun", "peter"],
        )


def _log_communication(result: dict, text: str):
    """Log the processed communication."""
    log_dir = Path("/opt/ai-elevate/notifications/comms-log")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"comms-{datetime.now(UTC).strftime('%Y-%m-%d')}.jsonl"

    entry = {
        "timestamp": result["timestamp"],
        "sender": result["sender"],
        "channel": result["channel"],
        "org": result["org"],
        "type": result["nlp"]["message_type"],
        "sentiment": result["fuzzy"]["raw_scores"]["sentiment"],
        "urgency": result["fuzzy"]["raw_scores"]["urgency"],
        "escalation_score": result["fuzzy"]["escalation_score"],
        "tier": result["routing"]["recommended_tier"],
        "priority": result["routing"]["priority"],
        "flags": result["flags"],
        "summary": result["nlp"]["summary"][:200],
    }
    with open(log_file, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ── CLI ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Communications Hub")
    parser.add_argument("text", nargs="?", help="Message text (or pipe via stdin)")
    parser.add_argument("--sender", default="unknown")
    parser.add_argument("--channel", default="email", choices=["email", "chat", "telegram", "webhook", "internal"])
    parser.add_argument("--org", default="gigforge", choices=["gigforge", "techuni", "ai-elevate"])
    parser.add_argument("--tier", type=int, default=1, help="Current support tier")
    args = parser.parse_args()

    text = args.text or sys.stdin.read().strip()
    if not text:
        print("Usage: python3 comms_hub.py 'message' --sender email --org gigforge")
        sys.exit(1)

    result = process_message(text, sender=args.sender, channel=args.channel,
                             org=args.org, current_tier=args.tier)
    print(json.dumps(result, indent=2))
