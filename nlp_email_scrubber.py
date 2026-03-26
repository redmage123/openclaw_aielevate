#!/usr/bin/env python3
"""NLP-based email scrubber — uses the pipeline to detect and remove:
1. Internal metadata (Trigger lines, workflow references)
2. Call/meeting suggestions
3. AI-sounding language

Uses sentence-level classification instead of regex patterns.
Every sentence gets scored — problematic ones are removed.
"""

import re
import logging

log = logging.getLogger("email-scrubber")

# Sentence classifiers — each returns True if the sentence should be removed

def _is_metadata(sentence):
    """Detect internal agent metadata that shouldn't be in customer emails."""
    s = sentence.strip().lower()
    # Trigger/workflow/internal markers
    if s.startswith("trigger:"):
        return True
    if s.startswith("workflow:"):
        return True
    if s.startswith("[internal]"):
        return True
    if s.startswith("context:") and any(w in s for w in ["directive", "dispatch", "session", "inbound"]):
        return True
    # Agent metadata patterns
    if re.match(r'^(note|debug|system|agent):', s):
        return True
    # Ticket/directive references at end of email (not inline mentions)
    if re.match(r'^trigger\b', s) and any(w in s for w in ["directive", "owner", "gf-", "cc-", "inbound", "reply from"]):
        return True
    return False


def _is_call_suggestion(sentence):
    """Detect any suggestion of synchronous communication."""
    s = sentence.strip().lower()

    # Direct mentions of calls/meetings/video
    call_words = ["call", "screen share", "screensh", "zoom", "teams", "video chat",
                  "google meet", "meeting", "walk through", "walk you through", "conversation", "discuss this",
                  "hop on", "jump on", "in person", "face to face", "demo call"]

    has_call_word = any(w in s for w in call_words)
    # Also catch reach-out patterns that imply future sync contact
    if "reach out" in s and ("to you" in s or "directly" in s or "set" in s):
        has_call_word = True
    if not has_call_word:
        return False

    # Offering/suggesting patterns
    offer_patterns = [
        "happy to", "would you like", "shall we", "we could", "let me know if",
        "feel free to", "we can", "i can", "i'd love to", "i'll",
        "would be great to", "let's", "we should", "if you'd prefer",
        "if you'd like", "just say the word", "say the word",
        "set up a", "schedule a", "book a", "arrange a",
        "reach out", "be in touch", "get in touch", "set that up", "to set up",
        "suggest scheduling", "suggest setting up", "suggest hopping on",
        "love to have a", "advisory conversation", "advisory chat",
        "reach out to you", "reach out directly",
        "in person",
    ]

    return any(p in s for p in offer_patterns)


def _is_ai_language(sentence):
    """Detect overtly AI-sounding phrases."""
    s = sentence.strip().lower()
    ai_phrases = [
        "leverage", "utilize", "cutting-edge", "game-changing",
        "revolutionize", "synergy", "paradigm shift", "best-in-class",
        "holistic approach", "deep dive", "move the needle",
        "circle back", "low-hanging fruit", "at the end of the day",
        "it goes without saying", "needless to say",
    ]
    return any(p in s for p in ai_phrases)


def scrub_email(body):
    """Scrub an email body using NLP sentence classification.

    Splits into sentences, classifies each, removes problematic ones.
    Returns cleaned body text.
    """
    if not body:
        return body

    # Split into lines first (preserves paragraph structure)
    lines = body.split("\n")
    clean_lines = []

    for line in lines:
        stripped = line.strip()

        # Empty lines preserved (paragraph breaks)
        if not stripped:
            clean_lines.append(line)
            continue

        # Check full line first (metadata is usually a complete line)
        if _is_metadata(stripped):
            log.info(f"Scrubbed metadata: {stripped[:60]}")
            continue

        # Split line into sentences and check each
        # Simple sentence splitting — period/exclamation/question followed by space+capital
        sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', stripped)
        kept_sentences = []

        for sent in sentences:
            if _is_metadata(sent):
                log.info(f"Scrubbed metadata (sentence): {sent[:60]}")
                continue
            if _is_call_suggestion(sent):
                log.info(f"Scrubbed call suggestion: {sent[:60]}")
                continue
            if _is_ai_language(sent):
                log.info(f"Scrubbed AI language: {sent[:60]}")
                continue
            kept_sentences.append(sent)

        if kept_sentences:
            clean_lines.append(" ".join(kept_sentences))
        # If all sentences in a line were removed, skip the line entirely

    # Reassemble and clean up
    result = "\n".join(clean_lines)
    # Remove triple+ blank lines
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()
