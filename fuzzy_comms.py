#!/usr/bin/env python3
"""Fuzzy Logic Communications Module for AI Elevate

Analyzes all communications (customer messages, emails, inter-agent messages)
using fuzzy logic to determine sentiment, urgency, intent, and escalation need.

Instead of binary classification (angry/not angry), this uses fuzzy membership
functions that output degrees (0.0 to 1.0) across multiple dimensions:

  Sentiment:   hostile ←→ negative ←→ neutral ←→ positive ←→ enthusiastic
  Urgency:     routine ←→ moderate ←→ urgent ←→ critical ←→ emergency
  Intent:      inquiry ←→ request ←→ complaint ←→ threat ←→ escalation
  Satisfaction: delighted ←→ satisfied ←→ neutral ←→ dissatisfied ←→ furious
  Complexity:  simple ←→ moderate ←→ complex ←→ requires_expert

Usage:
    from fuzzy_comms import analyze, classify_priority, suggest_response_tone

    result = analyze("I've been waiting 3 days and nobody has responded. This is ridiculous.")
    # result = {
    #   "sentiment": {"negative": 0.85, "hostile": 0.3, "neutral": 0.05},
    #   "urgency": {"urgent": 0.8, "critical": 0.4},
    #   "intent": {"complaint": 0.9, "escalation": 0.5},
    #   "satisfaction": {"dissatisfied": 0.9, "furious": 0.35},
    #   "escalation_score": 0.78,
    #   "recommended_tier": 3,
    #   "response_tone": "empathetic_urgent",
    #   "flags": ["long_wait", "frustration", "no_response_complaint"]
    # }
"""

import re
import math
from typing import Any
from exceptions import AiElevateError  # TODO: Use specific exception types
import argparse

# ── Fuzzy Membership Functions ───────────────────────────────────────────

def _triangular(x: float, a: float, b: float, c: float) -> float:
    """Triangular membership function."""
    if x <= a or x >= c:
        return 0.0
    if x <= b:
        return (x - a) / (b - a) if b != a else 1.0
    return (c - x) / (c - b) if c != b else 1.0


def _trapezoidal(x: float, a: float, b: float, c: float, d: float) -> float:
    """Trapezoidal membership function."""
    if x <= a or x >= d:
        return 0.0
    if a < x <= b:
        return (x - a) / (b - a) if b != a else 1.0
    if b < x <= c:
        return 1.0
    return (d - x) / (d - c) if d != c else 1.0


def _sigmoid(x: float, center: float, steepness: float = 10.0) -> float:
    """Sigmoid membership function."""
    try:
        return 1.0 / (1.0 + math.exp(-steepness * (x - center)))
    except OverflowError:
        return 0.0 if x < center else 1.0


# ── Lexicon Scoring ──────────────────────────────────────────────────────

# Sentiment indicators (word → raw sentiment score, -1.0 to +1.0)
SENTIMENT_LEXICON = {
    # Strongly negative
    "terrible": -0.95, "awful": -0.9, "horrible": -0.9, "unacceptable": -0.85,
    "ridiculous": -0.8, "disgusting": -0.85, "pathetic": -0.85, "incompetent": -0.9,
    "useless": -0.85, "worst": -0.9, "hate": -0.9, "furious": -0.9,
    "outraged": -0.85, "appalling": -0.9, "abysmal": -0.9,
    # Moderately negative
    "frustrated": -0.65, "disappointed": -0.6, "annoyed": -0.6,
    "unhappy": -0.55, "dissatisfied": -0.6, "upset": -0.6,
    "concern": -0.3, "concerned": -0.35, "worried": -0.4,
    "confused": -0.3, "unclear": -0.25, "difficult": -0.35,
    "problem": -0.4, "issue": -0.3, "bug": -0.35, "broken": -0.5,
    "slow": -0.35, "wrong": -0.4, "error": -0.4, "fail": -0.5,
    "failed": -0.5, "crash": -0.55, "down": -0.4,
    # Mildly negative
    "unfortunately": -0.2, "however": -0.1, "but": -0.05,
    "waiting": -0.25, "delay": -0.3, "still": -0.15,
    # Neutral
    "okay": 0.05, "fine": 0.1, "alright": 0.05,
    # Mildly positive
    "thanks": 0.3, "thank": 0.3, "please": 0.1, "appreciate": 0.4,
    "helpful": 0.45, "good": 0.4, "nice": 0.35,
    # Moderately positive
    "great": 0.6, "excellent": 0.7, "wonderful": 0.7, "fantastic": 0.75,
    "amazing": 0.75, "love": 0.7, "perfect": 0.8, "awesome": 0.7,
    "impressed": 0.65, "happy": 0.55, "pleased": 0.55, "satisfied": 0.5,
    # Strongly positive
    "outstanding": 0.85, "exceptional": 0.85, "brilliant": 0.8,
    "incredible": 0.8, "superb": 0.85, "delighted": 0.8,
}

# Urgency indicators
URGENCY_LEXICON = {
    "asap": 0.9, "immediately": 0.9, "urgent": 0.85, "emergency": 0.95,
    "critical": 0.9, "now": 0.6, "today": 0.5, "deadline": 0.7,
    "overdue": 0.75, "blocking": 0.8, "blocker": 0.8, "stuck": 0.6,
    "down": 0.7, "outage": 0.9, "crash": 0.75, "broken": 0.65,
    "days": 0.5, "weeks": 0.6, "hours": 0.4, "waiting": 0.45,
    "still": 0.3, "again": 0.35, "keeps": 0.4, "repeatedly": 0.5,
    "whenever": 0.15, "sometimes": 0.1, "occasionally": 0.1,
}

# Intent indicators
THREAT_PHRASES = [
    "cancel", "refund", "leave", "switch to", "competitor",
    "legal action", "lawyer", "sue", "report you", "bad review",
    "never again", "done with", "last straw", "final warning",
    "going to tell everyone", "social media", "bbb",
]

ESCALATION_PHRASES = [
    "speak to manager", "speak to someone", "talk to supervisor",
    "your boss", "someone in charge", "escalate", "management",
    "higher up", "person in charge", "real person", "human",
]

# Time-wait patterns
TIME_WAIT_PATTERNS = [
    (r"\b(\d+)\s*days?\b", "days"),
    (r"\b(\d+)\s*weeks?\b", "weeks"),
    (r"\b(\d+)\s*hours?\b", "hours"),
    (r"\b(\d+)\s*months?\b", "months"),
    (r"since\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)", "weekday"),
    (r"no\s+(response|reply|answer|update)", "no_response"),
]

# Amplifiers and diminishers
AMPLIFIERS = {"very": 1.3, "extremely": 1.5, "absolutely": 1.4, "completely": 1.4,
              "totally": 1.3, "utterly": 1.5, "really": 1.2, "so": 1.2, "incredibly": 1.4}
DIMINISHERS = {"slightly": 0.5, "somewhat": 0.6, "a bit": 0.5, "a little": 0.5,
               "kind of": 0.6, "sort of": 0.6, "mildly": 0.5}
NEGATORS = {"not", "no", "never", "don't", "doesn't", "didn't", "won't", "can't",
            "cannot", "couldn't", "shouldn't", "wouldn't", "isn't", "aren't", "wasn't"}


# ── Core Analysis ────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    """Simple tokenization."""
    return re.findall(r"[a-z']+", text.lower())


def _score_sentiment(text: str) -> float:
    """Calculate raw sentiment score (-1.0 to 1.0)."""
    tokens = _tokenize(text)
    if not tokens:
        return 0.0

    scores = []
    i = 0
    while i < len(tokens):
        word = tokens[i]
        multiplier = 1.0

        # Check for negation (look back 1-2 words)
        negated = False
        if i >= 1 and tokens[i - 1] in NEGATORS:
            negated = True
        if i >= 2 and tokens[i - 2] in NEGATORS:
            negated = True

        # Check for amplifiers/diminishers (look back 1 word)
        if i >= 1:
            prev = tokens[i - 1]
            if prev in AMPLIFIERS:
                multiplier = AMPLIFIERS[prev]
            elif prev in DIMINISHERS:
                multiplier = DIMINISHERS[prev]

        if word in SENTIMENT_LEXICON:
            score = SENTIMENT_LEXICON[word] * multiplier
            if negated:
                score *= -0.75  # Negation partially flips
            scores.append(score)

        i += 1

    if not scores:
        return 0.0

    # Weighted average favoring extreme values
    weights = [abs(s) + 0.1 for s in scores]
    total_weight = sum(weights)
    return sum(s * w for s, w in zip(scores, weights)) / total_weight if total_weight > 0 else 0.0


def _score_urgency(text: str) -> float:
    """Calculate urgency score (0.0 to 1.0)."""
    tokens = _tokenize(text)
    lower = text.lower()

    scores = []
    for word in tokens:
        if word in URGENCY_LEXICON:
            scores.append(URGENCY_LEXICON[word])

    # Check for time-wait patterns (increases urgency)
    for pattern, ptype in TIME_WAIT_PATTERNS:
        match = re.search(pattern, lower)
        if match:
            if ptype == "days":
                days = int(match.group(1))
                scores.append(min(0.3 + days * 0.15, 0.95))
            elif ptype == "weeks":
                scores.append(min(0.5 + int(match.group(1)) * 0.2, 0.95))
            elif ptype == "hours":
                hours = int(match.group(1))
                scores.append(0.3 if hours < 4 else 0.5 if hours < 12 else 0.7)
            elif ptype == "months":
                scores.append(0.9)
            elif ptype == "no_response":
                scores.append(0.65)
            elif ptype == "weekday":
                scores.append(0.5)

    # Exclamation marks increase urgency
    excl_count = text.count("!")
    if excl_count >= 3:
        scores.append(0.6)
    elif excl_count >= 1:
        scores.append(0.3)

    # ALL CAPS words increase urgency
    caps_words = len(re.findall(r"\b[A-Z]{3,}\b", text))
    if caps_words >= 3:
        scores.append(0.7)
    elif caps_words >= 1:
        scores.append(0.4)

    if not scores:
        return 0.1

    return min(max(scores) * 0.7 + sum(scores) / len(scores) * 0.3, 1.0)


def _detect_intent(text: str) -> dict[str, float]:
    """Detect communication intent with fuzzy scores."""
    lower = text.lower()
    intents = {
        "inquiry": 0.0,
        "request": 0.0,
        "complaint": 0.0,
        "threat": 0.0,
        "escalation": 0.0,
        "praise": 0.0,
        "feedback": 0.0,
    }

    # Question marks → inquiry
    q_count = text.count("?")
    intents["inquiry"] = min(q_count * 0.3, 0.9) if q_count > 0 else 0.1

    # Request indicators
    if any(w in lower for w in ["please", "could you", "can you", "would you", "i need", "i want", "help me"]):
        intents["request"] = 0.7

    # Complaint scoring based on sentiment
    sent = _score_sentiment(text)
    if sent < -0.3:
        intents["complaint"] = min(abs(sent) * 1.2, 1.0)

    # Threat detection
    threat_count = sum(1 for phrase in THREAT_PHRASES if phrase in lower)
    intents["threat"] = min(threat_count * 0.35, 1.0)

    # Escalation request
    esc_count = sum(1 for phrase in ESCALATION_PHRASES if phrase in lower)
    intents["escalation"] = min(esc_count * 0.5, 1.0)

    # Praise
    if sent > 0.4:
        intents["praise"] = min(sent * 1.1, 1.0)

    # Feedback (moderate sentiment either way + substantive content)
    if -0.5 < sent < 0.5 and len(text) > 100:
        intents["feedback"] = 0.5

    return intents


def _assess_satisfaction(sentiment: float, urgency: float, intents: dict) -> dict[str, float]:
    """Fuzzy satisfaction membership."""
    return {
        "delighted": _trapezoidal(sentiment, 0.6, 0.8, 1.0, 1.1),
        "satisfied": _triangular(sentiment, 0.2, 0.5, 0.8),
        "neutral": _triangular(sentiment, -0.2, 0.0, 0.2),
        "dissatisfied": _triangular(sentiment, -0.7, -0.4, -0.1),
        "furious": _trapezoidal(sentiment, -1.1, -1.0, -0.7, -0.5) + intents.get("threat", 0) * 0.3,
    }


def _calculate_escalation_score(sentiment: float, urgency: float,
                                 intents: dict, satisfaction: dict) -> float:
    """Calculate composite escalation score (0.0 to 1.0)."""
    # Weighted combination
    score = (
        abs(min(sentiment, 0)) * 0.25 +         # Negative sentiment contributes
        urgency * 0.2 +                            # Urgency contributes
        intents.get("complaint", 0) * 0.15 +      # Complaint intent
        intents.get("threat", 0) * 0.25 +          # Threats heavily weighted
        intents.get("escalation", 0) * 0.15 +     # Explicit escalation request
        satisfaction.get("furious", 0) * 0.2       # Fury level
    )

    # Boost if multiple signals converge
    high_signals = sum(1 for v in [
        abs(min(sentiment, 0)) > 0.5,
        urgency > 0.6,
        intents.get("threat", 0) > 0.3,
        intents.get("escalation", 0) > 0.3,
    ] if v)
    if high_signals >= 3:
        score = min(score * 1.3, 1.0)

    return min(score, 1.0)


def _recommend_tier(escalation_score: float, intents: dict) -> int:
    """Recommend support tier based on escalation score."""
    # Explicit escalation request → at least Tier 3
    if intents.get("escalation", 0) > 0.5:
        return max(3, _score_to_tier(escalation_score))
    # Explicit threat → at least Tier 3
    if intents.get("threat", 0) > 0.5:
        return max(3, _score_to_tier(escalation_score))
    return _score_to_tier(escalation_score)


def _score_to_tier(score: float) -> int:
    if score >= 0.85:
        return 4
    if score >= 0.6:
        return 3
    if score >= 0.35:
        return 2
    return 1


def _recommend_tone(sentiment: float, urgency: float, satisfaction: dict) -> str:
    """Recommend response tone."""
    if satisfaction.get("furious", 0) > 0.5 or sentiment < -0.7:
        return "empathetic_deescalation"
    if satisfaction.get("dissatisfied", 0) > 0.5:
        return "empathetic_urgent"
    if urgency > 0.7:
        return "professional_urgent"
    if sentiment > 0.5:
        return "warm_appreciative"
    if satisfaction.get("neutral", 0) > 0.5:
        return "professional_helpful"
    return "professional_empathetic"


def _detect_flags(text: str, sentiment: float, urgency: float) -> list[str]:
    """Detect specific flags for routing."""
    lower = text.lower()
    flags = []

    if re.search(r"\b\d+\s*days?\b", lower) and urgency > 0.4:
        flags.append("long_wait")
    if re.search(r"no\s+(response|reply|answer)", lower):
        flags.append("no_response_complaint")
    if sentiment < -0.6:
        flags.append("high_frustration")
    if any(p in lower for p in THREAT_PHRASES[:5]):
        flags.append("churn_risk")
    if any(p in lower for p in ESCALATION_PHRASES):
        flags.append("escalation_requested")
    if text.count("!") >= 3 or len(re.findall(r"\b[A-Z]{3,}\b", text)) >= 3:
        flags.append("emotional_intensity")
    if "refund" in lower:
        flags.append("refund_request")
    if any(w in lower for w in ["legal", "lawyer", "sue", "court"]):
        flags.append("legal_threat")
    if any(w in lower for w in ["data", "breach", "hack", "leaked", "exposed"]):
        flags.append("security_concern")
    if re.search(r"(multiple|several|many)\s+(times?|issues?|problems?)", lower):
        flags.append("recurring_issue")

    return flags


# ── Public API ───────────────────────────────────────────────────────────

def analyze(text: str) -> dict[str, Any]:
    """Analyze a communication using fuzzy logic.

    Returns a comprehensive analysis with fuzzy membership scores,
    escalation recommendation, and response tone suggestion.
    """
    sentiment_score = _score_sentiment(text)
    urgency_score = _score_urgency(text)
    intents = _detect_intent(text)
    satisfaction = _assess_satisfaction(sentiment_score, urgency_score, intents)
    escalation_score = _calculate_escalation_score(sentiment_score, urgency_score, intents, satisfaction)
    tier = _recommend_tier(escalation_score, intents)
    tone = _recommend_tone(sentiment_score, urgency_score, satisfaction)
    flags = _detect_flags(text, sentiment_score, urgency_score)

    # Build fuzzy sentiment memberships
    sentiment_fuzzy = {
        "hostile": _trapezoidal(sentiment_score, -1.1, -1.0, -0.8, -0.6),
        "negative": _triangular(sentiment_score, -0.8, -0.5, -0.2),
        "neutral": _triangular(sentiment_score, -0.3, 0.0, 0.3),
        "positive": _triangular(sentiment_score, 0.2, 0.5, 0.8),
        "enthusiastic": _trapezoidal(sentiment_score, 0.6, 0.8, 1.0, 1.1),
    }

    urgency_fuzzy = {
        "routine": _trapezoidal(urgency_score, -0.1, 0.0, 0.15, 0.3),
        "moderate": _triangular(urgency_score, 0.2, 0.35, 0.5),
        "urgent": _triangular(urgency_score, 0.4, 0.6, 0.8),
        "critical": _triangular(urgency_score, 0.7, 0.85, 0.95),
        "emergency": _trapezoidal(urgency_score, 0.85, 0.95, 1.0, 1.1),
    }

    return {
        "raw_scores": {
            "sentiment": round(sentiment_score, 3),
            "urgency": round(urgency_score, 3),
        },
        "sentiment": {k: round(v, 3) for k, v in sentiment_fuzzy.items() if v > 0.01},
        "urgency": {k: round(v, 3) for k, v in urgency_fuzzy.items() if v > 0.01},
        "intent": {k: round(v, 3) for k, v in intents.items() if v > 0.05},
        "satisfaction": {k: round(v, 3) for k, v in satisfaction.items() if v > 0.01},
        "escalation_score": round(escalation_score, 3),
        "recommended_tier": tier,
        "response_tone": tone,
        "flags": flags,
    }


def classify_priority(text: str) -> str:
    """Quick classification into notification priority level."""
    result = analyze(text)
    score = result["escalation_score"]
    if score >= 0.8 or "legal_threat" in result["flags"] or "security_concern" in result["flags"]:
        return "critical"
    if score >= 0.5 or "churn_risk" in result["flags"]:
        return "high"
    if score >= 0.25:
        return "medium"
    return "low"


def suggest_response_tone(text: str) -> str:
    """Get recommended response tone for a message."""
    return analyze(text)["response_tone"]


def should_escalate(text: str, current_tier: int = 1) -> tuple[bool, int, str]:
    """Determine if a message should trigger escalation.

    Returns (should_escalate, recommended_tier, reason)
    """
    result = analyze(text)
    rec_tier = result["recommended_tier"]

    if rec_tier > current_tier:
        reasons = []
        if "churn_risk" in result["flags"]:
            reasons.append("customer threatening to leave")
        if "escalation_requested" in result["flags"]:
            reasons.append("customer requested escalation")
        if "legal_threat" in result["flags"]:
            reasons.append("legal threat detected")
        if "refund_request" in result["flags"]:
            reasons.append("refund requested")
        if result["escalation_score"] > 0.7:
            reasons.append(f"high distress score ({result['escalation_score']:.0%})")
        if "long_wait" in result["flags"]:
            reasons.append("extended wait time")

        return True, rec_tier, "; ".join(reasons) or "elevated distress signals"

    return False, current_tier, ""


# ── CLI ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fuzzy Comms")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose output")
    args = parser.parse_args()

    import sys
    import json

    if len(sys.argv) > 1:
        text = " ".join(sys.argv[1:])
    else:
        text = sys.stdin.read().strip()

    if not text:
        print("Usage: python3 fuzzy_comms.py 'message text'")
        sys.exit(1)

    result = analyze(text)
    print(json.dumps(result, indent=2))
