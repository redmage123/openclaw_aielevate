#!/usr/bin/env python3
"""AI Elevate Customer Success Platform

Implements all 14 customer support enhancements:
1.  Auto-Response SLA Timer
2.  Customer Health Score
3.  Knowledge Base Auto-Builder
4.  Win-Back Campaign
5.  Post-Mortem on Tier 3+ Escalations
6.  Sentiment Trend Dashboard
7.  Proactive Check-In System
8.  Multi-Channel Handoff Continuity
9.  VIP Detection
10. Competitor Mention Tracking
11. NPS Survey Automation
12. Support Quality Scoring
13. Predictive Churn Model
14. Escalation Replay Archive
"""

import json
import os
import re
import sys
import time
import csv
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, "/home/aielevate")
from fuzzy_comms import analyze as fuzzy_analyze
from notify import send as notify_send

# ── Configuration ────────────────────────────────────────────────────────

DATA_DIR = Path("/opt/ai-elevate/customer-success")
for d in ["health-scores", "knowledge-base", "check-ins", "handoffs",
           "vip", "competitors", "nps", "quality-scores", "churn-predictions",
           "escalation-replays", "sentiment-trends", "postmortems", "winback"]:
    (DATA_DIR / d).mkdir(parents=True, exist_ok=True)

MAILGUN_API_KEY = open("/opt/ai-elevate/credentials/mailgun-api-key.txt").read().strip()
MAILGUN_DOMAIN = "mg.ai-elevate.ai"


def _customer_id(email: str) -> str:
    return hashlib.md5(email.lower().strip().encode()).hexdigest()[:12]


def _load_json(path: Path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def _save_json(path: Path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _append_csv(path: Path, row: list, headers: list = None):
    exists = path.exists() and path.stat().st_size > 0
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if not exists and headers:
            w.writerow(headers)
        w.writerow(row)


def _send_email(to: str, subject: str, body: str, from_name: str = "Support",
                from_addr: str = "support", org: str = "gigforge"):
    import urllib.request, urllib.parse, base64
    domain = "mg.ai-elevate.ai" if org in ("gigforge", "techuni") else "mg.ai-elevate.ai"
    data = urllib.parse.urlencode({
        "from": f"{from_name} <{from_addr}@{domain}>",
        "to": to,
        "subject": subject,
        "text": body,
        "h:Reply-To": f"{from_addr}@mg.ai-elevate.ai",
    }).encode("utf-8")
    creds = base64.b64encode(f"api:{MAILGUN_API_KEY}".encode()).decode()
    req = urllib.request.Request(
        f"https://api.mailgun.net/v3/{MAILGUN_DOMAIN}/messages",
        data=data, method="POST"
    )
    req.add_header("Authorization", f"Basic {creds}")
    try:
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception:
        return False


# ── 1. Auto-Response SLA Timer ───────────────────────────────────────────

def auto_acknowledge(customer_email: str, customer_name: str = "", org: str = "gigforge"):
    """Send immediate auto-acknowledgment within 60 seconds of receiving a support request."""
    name = customer_name or customer_email.split("@")[0].replace(".", " ").title()
    org_name = "GigForge" if org == "gigforge" else "TechUni"

    body = f"""Hi {name},

Thanks for reaching out. I've received your message and I'm looking into it right now.

I'll have an update for you within 30 minutes. If this is urgent and you need immediate assistance, just reply to this email with "URGENT" in the subject line and I'll prioritize it.

Best,
Customer Success Team
{org_name}"""

    _send_email(customer_email, f"Got your message — looking into it now", body,
                from_name=f"{org_name} Support", from_addr="support", org=org)

    # Log SLA start
    _append_csv(DATA_DIR / "sla-tracker.csv",
                [datetime.now(timezone.utc).isoformat(), customer_email, "acknowledged", "30min", org],
                ["timestamp", "customer", "status", "sla_target", "org"])


# ── 2. Customer Health Score ─────────────────────────────────────────────

def update_health_score(customer_email: str, org: str = "gigforge",
                         event_type: str = "interaction", sentiment: float = 0.0,
                         resolved: bool = False, response_time_min: int = 0):
    """Update rolling customer health score (0-100)."""
    cid = _customer_id(customer_email)
    path = DATA_DIR / "health-scores" / f"{cid}.json"
    score_data = _load_json(path, {
        "email": customer_email,
        "org": org,
        "score": 75,  # Start at 75 (neutral-positive)
        "history": [],
        "ticket_count_30d": 0,
        "avg_sentiment_30d": 0.0,
        "last_interaction": "",
        "created": datetime.now(timezone.utc).isoformat(),
    })

    now = datetime.now(timezone.utc).isoformat()
    score = score_data["score"]

    # Adjust score based on event
    if event_type == "ticket":
        score -= 3  # Each ticket slightly reduces health
        score_data["ticket_count_30d"] = score_data.get("ticket_count_30d", 0) + 1
    if event_type == "resolution" and resolved:
        score += 5
    if event_type == "interaction":
        if sentiment > 0.3:
            score += int(sentiment * 8)
        elif sentiment < -0.3:
            score -= int(abs(sentiment) * 12)
    if response_time_min > 60:
        score -= 5  # Slow response hurts
    elif response_time_min > 0 and response_time_min < 15:
        score += 2  # Fast response helps

    # Clamp
    score = max(0, min(100, score))
    score_data["score"] = score
    score_data["last_interaction"] = now
    score_data["history"].append({
        "timestamp": now,
        "event": event_type,
        "sentiment": sentiment,
        "score_after": score,
    })
    # Keep last 100 events
    score_data["history"] = score_data["history"][-100:]

    _save_json(path, score_data)

    # Alert if score drops below 40 (at-risk customer)
    if score < 40:
        notify_send(
            f"At-Risk Customer — {org.upper()}",
            f"Customer {customer_email} health score dropped to {score}/100.\n"
            f"Event: {event_type}, Sentiment: {sentiment:.0%}\n"
            f"Tickets in 30d: {score_data['ticket_count_30d']}",
            priority="high", to=["braun"]
        )

    return score


def get_health_score(customer_email: str) -> dict:
    cid = _customer_id(customer_email)
    return _load_json(DATA_DIR / "health-scores" / f"{cid}.json", {"score": 75, "email": customer_email})


# ── 3. Knowledge Base Auto-Builder ───────────────────────────────────────

def log_resolved_ticket(org: str, issue: str, resolution: str, category: str = "general"):
    """Log a resolved ticket. After 3 similar issues, auto-generate FAQ entry."""
    kb_path = DATA_DIR / "knowledge-base" / f"{org}-tickets.jsonl"
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "issue": issue,
        "resolution": resolution,
        "category": category,
    }
    with open(kb_path, "a") as f:
        f.write(json.dumps(entry) + "\n")

    # Check for recurring issues (3+ similar)
    try:
        with open(kb_path) as f:
            tickets = [json.loads(l) for l in f if l.strip()]
    except Exception:
        return

    # Simple keyword matching for similar tickets
    issue_lower = issue.lower()
    keywords = set(re.findall(r"\b\w{4,}\b", issue_lower))
    similar = []
    for t in tickets:
        t_keywords = set(re.findall(r"\b\w{4,}\b", t["issue"].lower()))
        overlap = len(keywords & t_keywords) / max(len(keywords | t_keywords), 1)
        if overlap > 0.4:
            similar.append(t)

    if len(similar) >= 3:
        # Auto-generate FAQ entry
        faq_path = DATA_DIR / "knowledge-base" / f"{org}-faq.md"
        faq_entry = f"\n### {category.title()}: {issue[:80]}\n\n"
        faq_entry += f"**Problem:** {issue}\n\n"
        faq_entry += f"**Solution:** {resolution}\n\n"
        faq_entry += f"*Auto-generated from {len(similar)} similar tickets*\n\n---\n"

        with open(faq_path, "a") as f:
            f.write(faq_entry)

        # Notify engineering about recurring issue
        notify_send(
            f"Recurring Issue Detected — {org.upper()}",
            f"The following issue has occurred {len(similar)} times:\n\n"
            f"Issue: {issue}\nCategory: {category}\n\n"
            f"FAQ entry auto-generated. Consider a permanent fix.",
            priority="medium", to=["braun"]
        )


# ── 4. Win-Back Campaign ────────────────────────────────────────────────

def check_winback_candidates(org: str = "gigforge"):
    """Find inactive/cancelled customers and trigger win-back emails."""
    health_dir = DATA_DIR / "health-scores"
    winback_log = DATA_DIR / "winback" / f"{org}-sent.json"
    sent = _load_json(winback_log, {"sent": []})
    now = datetime.now(timezone.utc)
    org_name = "GigForge" if org == "gigforge" else "TechUni"

    for path in health_dir.glob("*.json"):
        data = _load_json(path)
        if data.get("org") != org:
            continue

        email = data.get("email", "")
        last = data.get("last_interaction", "")
        score = data.get("score", 75)

        if not last or email in sent.get("sent", []):
            continue

        try:
            last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        except Exception:
            continue

        days_inactive = (now - last_dt).days

        if days_inactive >= 30 and score < 50:
            name = email.split("@")[0].replace(".", " ").title()
            body = f"""Hi {name},

It's been a while since we last heard from you, and I wanted to personally check in.

I noticed your experience with {org_name} may not have met your expectations, and that's on us. I'd really like to understand what happened and see if there's anything we can do to make it right.

If you have a few minutes, I'd love to hear what would make {org_name} work better for you. No pressure at all — just genuinely want to help.

Warm regards,
Director of Customer Satisfaction
{org_name}"""

            _send_email(email, f"Checking in — we miss you at {org_name}", body,
                        from_name=f"{org_name} Customer Success", from_addr="csat", org=org)

            sent["sent"].append(email)
            _save_json(winback_log, sent)


# ── 5. Post-Mortem on Tier 3+ Escalations ────────────────────────────────

def create_postmortem(org: str, customer_email: str, issue: str,
                       escalation_path: str, root_cause: str,
                       resolution: str, systemic_fix: str, timeline: str):
    """Create a post-mortem document for a Tier 3+ escalation."""
    pm_dir = DATA_DIR / "postmortems"
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cid = _customer_id(customer_email)
    pm_path = pm_dir / f"{date_str}-{org}-{cid}.md"

    content = f"""# Escalation Post-Mortem

| | |
|---|---|
| **Date** | {date_str} |
| **Org** | {org} |
| **Customer** | {customer_email} |
| **Escalation Path** | {escalation_path} |

## Issue
{issue}

## Timeline
{timeline}

## Root Cause
{root_cause}

## Resolution
{resolution}

## Systemic Fix (to prevent recurrence)
{systemic_fix}

## Lessons Learned
- What went well:
- What went poorly:
- What to change:

---
*Auto-generated by Customer Success Platform*
"""
    with open(pm_path, "w") as f:
        f.write(content)

    return str(pm_path)


# ── 6. Sentiment Trend Dashboard ─────────────────────────────────────────

def record_sentiment(org: str, customer_email: str, sentiment: float, channel: str = "email"):
    """Record a sentiment data point for trend analysis."""
    path = DATA_DIR / "sentiment-trends" / f"{org}-{datetime.now(timezone.utc).strftime('%Y-%m')}.jsonl"
    with open(path, "a") as f:
        f.write(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "customer": customer_email,
            "sentiment": sentiment,
            "channel": channel,
        }) + "\n")


def generate_sentiment_report(org: str) -> str:
    """Generate weekly sentiment trend report."""
    path = DATA_DIR / "sentiment-trends" / f"{org}-{datetime.now(timezone.utc).strftime('%Y-%m')}.jsonl"
    if not path.exists():
        return "No sentiment data available."

    entries = []
    with open(path) as f:
        for line in f:
            try:
                entries.append(json.loads(line))
            except Exception:
                pass

    if not entries:
        return "No sentiment data available."

    # Last 7 days
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    recent = [e for e in entries if e["timestamp"] > week_ago]

    if not recent:
        return "No recent sentiment data."

    avg = sum(e["sentiment"] for e in recent) / len(recent)
    negative = sum(1 for e in recent if e["sentiment"] < -0.3)
    positive = sum(1 for e in recent if e["sentiment"] > 0.3)
    neutral = len(recent) - negative - positive

    report = f"""Sentiment Trend Report — {org.upper()} (Last 7 Days)

Total interactions: {len(recent)}
Average sentiment: {avg:.0%}
Positive: {positive} ({positive/len(recent)*100:.0f}%)
Neutral: {neutral} ({neutral/len(recent)*100:.0f}%)
Negative: {negative} ({negative/len(recent)*100:.0f}%)
"""

    if negative / max(len(recent), 1) > 0.3:
        report += "\nWARNING: Negative sentiment exceeds 30% threshold."

    return report


# ── 7. Proactive Check-In System ─────────────────────────────────────────

def schedule_checkin(customer_email: str, org: str, trigger: str, days_from_now: int):
    """Schedule a proactive check-in."""
    path = DATA_DIR / "check-ins" / f"scheduled.jsonl"
    with open(path, "a") as f:
        f.write(json.dumps({
            "customer": customer_email,
            "org": org,
            "trigger": trigger,
            "scheduled_date": (datetime.now(timezone.utc) + timedelta(days=days_from_now)).strftime("%Y-%m-%d"),
            "sent": False,
        }) + "\n")


def process_checkins():
    """Process all due check-ins (run daily via cron)."""
    path = DATA_DIR / "check-ins" / "scheduled.jsonl"
    if not path.exists():
        return

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    remaining = []

    with open(path) as f:
        for line in f:
            try:
                entry = json.loads(line)
            except Exception:
                continue

            if entry.get("sent"):
                continue

            if entry["scheduled_date"] <= today:
                org = entry["org"]
                org_name = "GigForge" if org == "gigforge" else "TechUni"
                email = entry["customer"]
                name = email.split("@")[0].replace(".", " ").title()
                trigger = entry["trigger"]

                bodies = {
                    "onboarding_7d": f"Hi {name},\n\nIt's been a week since you joined {org_name} and I wanted to check in. How's everything going so far? Are you finding what you need?\n\nIf there's anything at all I can help with — questions about features, getting set up, or anything else — just hit reply. I'm here.\n\nBest,\nCustomer Success\n{org_name}",
                    "onboarding_30d": f"Hi {name},\n\nYou've been with {org_name} for about a month now and I'd love to hear how things are going. Are you getting the value you expected?\n\nWe recently shipped some updates that might interest you, and I'm happy to walk you through anything.\n\nCheers,\nCustomer Success\n{org_name}",
                    "post_resolution": f"Hi {name},\n\nI'm just following up on the issue we resolved recently. I wanted to make sure everything is still working well on your end.\n\nIf anything comes up, don't hesitate to reach out.\n\nBest,\nCustomer Success\n{org_name}",
                    "quarterly": f"Hi {name},\n\nJust a quick check-in from the {org_name} team. We value your business and I wanted to see if there's anything we can do to make your experience even better.\n\nAny feedback, feature requests, or questions — I'm all ears.\n\nWarm regards,\nCustomer Success\n{org_name}",
                }

                body = bodies.get(trigger, bodies["quarterly"])
                _send_email(email, f"Quick check-in from {org_name}", body,
                            from_name=f"{org_name} Customer Success", from_addr="success", org=org)
                entry["sent"] = True

            remaining.append(entry)

    with open(path, "w") as f:
        for entry in remaining:
            f.write(json.dumps(entry) + "\n")


# ── 8. Multi-Channel Handoff Continuity ──────────────────────────────────

def log_interaction(customer_email: str, channel: str, direction: str,
                     content: str, agent_id: str = "", org: str = "gigforge"):
    """Log every interaction for cross-channel continuity."""
    cid = _customer_id(customer_email)
    path = DATA_DIR / "handoffs" / f"{cid}.jsonl"
    with open(path, "a") as f:
        f.write(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "channel": channel,
            "direction": direction,
            "agent": agent_id,
            "content": content[:500],
            "org": org,
        }) + "\n")


def get_customer_history(customer_email: str, limit: int = 20) -> list[dict]:
    """Get full interaction history across all channels."""
    cid = _customer_id(customer_email)
    path = DATA_DIR / "handoffs" / f"{cid}.jsonl"
    if not path.exists():
        return []
    entries = []
    with open(path) as f:
        for line in f:
            try:
                entries.append(json.loads(line))
            except Exception:
                pass
    return entries[-limit:]


# ── 9. VIP Detection ────────────────────────────────────────────────────

def check_vip_status(customer_email: str, mrr: float = 0, tenure_months: int = 0,
                      org: str = "gigforge") -> dict:
    """Determine if customer qualifies for VIP treatment."""
    cid = _customer_id(customer_email)
    health = get_health_score(customer_email)

    vip_score = 0
    reasons = []

    if mrr >= 500:
        vip_score += 40
        reasons.append(f"high MRR (${mrr})")
    elif mrr >= 200:
        vip_score += 20
        reasons.append(f"moderate MRR (${mrr})")

    if tenure_months >= 12:
        vip_score += 30
        reasons.append(f"long tenure ({tenure_months}mo)")
    elif tenure_months >= 6:
        vip_score += 15
        reasons.append(f"established ({tenure_months}mo)")

    if health.get("score", 75) >= 80:
        vip_score += 20
        reasons.append("excellent health score")

    is_vip = vip_score >= 50
    tier = "platinum" if vip_score >= 80 else "gold" if vip_score >= 50 else "standard"

    result = {
        "email": customer_email,
        "is_vip": is_vip,
        "tier": tier,
        "vip_score": vip_score,
        "reasons": reasons,
        "sla": {"response_min": 5, "resolution_hr": 2} if is_vip else {"response_min": 30, "resolution_hr": 24},
    }

    _save_json(DATA_DIR / "vip" / f"{cid}.json", result)
    return result


# ── 10. Competitor Mention Tracking ──────────────────────────────────────

COMPETITORS = {
    "gigforge": ["upwork", "fiverr", "toptal", "freelancer", "contra", "devsquad", "turing.com", "andela"],
    "techuni": ["teachable", "thinkific", "kajabi", "udemy", "coursera", "skillshare", "podia", "learnworlds"],
}


def track_competitor_mentions(text: str, customer_email: str, org: str = "gigforge"):
    """Detect and log competitor mentions in customer communications."""
    lower = text.lower()
    mentioned = []

    for comp in COMPETITORS.get(org, []):
        if comp in lower:
            mentioned.append(comp)

    if mentioned:
        _append_csv(
            DATA_DIR / "competitors" / f"{org}-mentions.csv",
            [datetime.now(timezone.utc).isoformat(), customer_email, "|".join(mentioned), text[:200]],
            ["timestamp", "customer", "competitors", "context"]
        )

    return mentioned


# ── 11. NPS Survey Automation ────────────────────────────────────────────

def send_nps_survey(customer_email: str, org: str = "gigforge"):
    """Send NPS survey email."""
    org_name = "GigForge" if org == "gigforge" else "TechUni"
    name = customer_email.split("@")[0].replace(".", " ").title()

    body = f"""Hi {name},

I have one quick question for you — it'll take 10 seconds:

On a scale of 0-10, how likely are you to recommend {org_name} to a friend or colleague?

Just reply with your number (0-10) and optionally a sentence about why. That's it.

Your feedback directly shapes what we build next.

Thanks,
Customer Success
{org_name}"""

    _send_email(customer_email, f"One quick question about {org_name}", body,
                from_name=f"{org_name}", from_addr="feedback", org=org)

    _append_csv(DATA_DIR / "nps" / f"{org}-surveys.csv",
                [datetime.now(timezone.utc).isoformat(), customer_email, "sent", ""],
                ["timestamp", "customer", "status", "score"])


def record_nps_response(customer_email: str, score: int, comment: str = "", org: str = "gigforge"):
    """Record an NPS survey response."""
    _append_csv(DATA_DIR / "nps" / f"{org}-surveys.csv",
                [datetime.now(timezone.utc).isoformat(), customer_email, "responded", str(score)],
                ["timestamp", "customer", "status", "score"])

    # Classify
    category = "promoter" if score >= 9 else "passive" if score >= 7 else "detractor"

    if category == "detractor":
        notify_send(
            f"NPS Detractor Alert — {org.upper()}",
            f"Customer {customer_email} scored {score}/10 (detractor).\nComment: {comment or 'none'}",
            priority="high", to=["braun"]
        )


# ── 12. Support Quality Scoring ──────────────────────────────────────────

def score_support_response(agent_id: str, response_text: str, customer_message: str,
                             response_time_min: int, resolved: bool) -> dict:
    """Grade a support agent's response on multiple dimensions."""
    # Analyze the response
    analysis = fuzzy_analyze(response_text)

    scores = {}

    # Empathy (does the response acknowledge the customer's feelings?)
    empathy_words = ["understand", "sorry", "appreciate", "frustrat", "apolog", "hear you",
                     "must be", "i can see", "that sounds"]
    empathy_count = sum(1 for w in empathy_words if w in response_text.lower())
    scores["empathy"] = min(empathy_count * 25, 100)

    # Speed
    if response_time_min <= 5:
        scores["speed"] = 100
    elif response_time_min <= 15:
        scores["speed"] = 80
    elif response_time_min <= 30:
        scores["speed"] = 60
    elif response_time_min <= 60:
        scores["speed"] = 40
    else:
        scores["speed"] = 20

    # Resolution
    scores["resolution"] = 100 if resolved else 30

    # Tone (should be positive/professional even when customer is angry)
    response_sentiment = analysis["raw_scores"]["sentiment"]
    scores["tone"] = max(0, min(100, int((response_sentiment + 1) * 50)))

    # Completeness (longer, more detailed responses score higher)
    word_count = len(response_text.split())
    scores["completeness"] = min(word_count * 2, 100)

    # Personalization (does it use specific details from the customer's message?)
    customer_keywords = set(re.findall(r"\b\w{5,}\b", customer_message.lower()))
    response_keywords = set(re.findall(r"\b\w{5,}\b", response_text.lower()))
    overlap = len(customer_keywords & response_keywords)
    scores["personalization"] = min(overlap * 15, 100)

    # Overall weighted score
    weights = {"empathy": 0.25, "speed": 0.15, "resolution": 0.2, "tone": 0.15,
               "completeness": 0.1, "personalization": 0.15}
    overall = sum(scores[k] * weights[k] for k in weights)
    scores["overall"] = round(overall)

    result = {"agent": agent_id, "scores": scores, "timestamp": datetime.now(timezone.utc).isoformat()}

    _append_csv(
        DATA_DIR / "quality-scores" / "scores.csv",
        [result["timestamp"], agent_id, scores["overall"], scores["empathy"], scores["speed"],
         scores["resolution"], scores["tone"], scores["completeness"], scores["personalization"]],
        ["timestamp", "agent", "overall", "empathy", "speed", "resolution", "tone", "completeness", "personalization"]
    )

    return result


# ── 13. Predictive Churn Model ───────────────────────────────────────────

def predict_churn(customer_email: str, org: str = "gigforge") -> dict:
    """Predict churn probability based on health score, sentiment, and behavior."""
    health = get_health_score(customer_email)
    cid = _customer_id(customer_email)
    history = get_customer_history(customer_email)

    score = health.get("score", 75)
    ticket_count = health.get("ticket_count_30d", 0)

    # Calculate churn signals
    signals = {}

    # Low health score
    signals["health_risk"] = max(0, (50 - score) / 50) if score < 50 else 0

    # High ticket frequency
    signals["ticket_frequency"] = min(ticket_count / 5, 1.0)

    # Negative sentiment trend
    recent_sentiments = [h.get("sentiment", 0) for h in health.get("history", [])[-10:]]
    if recent_sentiments:
        avg_sent = sum(recent_sentiments) / len(recent_sentiments)
        signals["sentiment_trend"] = max(0, -avg_sent)
    else:
        signals["sentiment_trend"] = 0

    # Inactivity
    last = health.get("last_interaction", "")
    if last:
        try:
            last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
            days_since = (datetime.now(timezone.utc) - last_dt).days
            signals["inactivity"] = min(days_since / 30, 1.0)
        except Exception:
            signals["inactivity"] = 0
    else:
        signals["inactivity"] = 0.5

    # Competitor mentions
    comp_path = DATA_DIR / "competitors" / f"{org}-mentions.csv"
    comp_mentions = 0
    if comp_path.exists():
        with open(comp_path) as f:
            for row in csv.reader(f):
                if len(row) > 1 and customer_email in row[1]:
                    comp_mentions += 1
    signals["competitor_interest"] = min(comp_mentions / 3, 1.0)

    # Weighted churn probability
    weights = {"health_risk": 0.3, "ticket_frequency": 0.15, "sentiment_trend": 0.2,
               "inactivity": 0.2, "competitor_interest": 0.15}
    churn_prob = sum(signals[k] * weights[k] for k in weights)

    result = {
        "customer": customer_email,
        "churn_probability": round(churn_prob, 3),
        "risk_level": "critical" if churn_prob > 0.7 else "high" if churn_prob > 0.5 else "medium" if churn_prob > 0.3 else "low",
        "signals": {k: round(v, 3) for k, v in signals.items()},
        "health_score": score,
    }

    _save_json(DATA_DIR / "churn-predictions" / f"{cid}.json", result)

    if churn_prob > 0.7:
        notify_send(
            f"CHURN RISK: Critical — {org.upper()}",
            f"Customer {customer_email} has {churn_prob:.0%} churn probability.\n"
            f"Health: {score}/100\nSignals: {json.dumps(signals, indent=2)}",
            priority="high", to=["braun"]
        )

    return result


# ── 14. Escalation Replay Archive ────────────────────────────────────────

def archive_escalation(org: str, customer_email: str, issue: str,
                         interactions: list[dict], outcome: str, csat_score: int = 0):
    """Archive a complete escalation as a replayable case study."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cid = _customer_id(customer_email)
    path = DATA_DIR / "escalation-replays" / f"{date_str}-{org}-{cid}.json"

    replay = {
        "date": date_str,
        "org": org,
        "customer": customer_email,
        "issue": issue,
        "interactions": interactions,
        "outcome": outcome,
        "csat_score": csat_score,
        "lessons": [],
    }

    _save_json(path, replay)
    return str(path)


# ── Cron Jobs ────────────────────────────────────────────────────────────

def daily_cron():
    """Run daily customer success tasks."""
    # Process scheduled check-ins
    process_checkins()

    # Check for win-back candidates
    for org in ["gigforge", "techuni"]:
        check_winback_candidates(org)

    # Generate sentiment reports
    for org in ["gigforge", "techuni"]:
        report = generate_sentiment_report(org)
        if "WARNING" in report:
            notify_send(f"Sentiment Alert — {org.upper()}", report, priority="medium", to=["braun"])


def weekly_cron():
    """Run weekly customer success tasks."""
    # Send sentiment trend reports
    for org in ["gigforge", "techuni"]:
        report = generate_sentiment_report(org)
        notify_send(f"Weekly Sentiment Report — {org.upper()}", report, priority="low", to=["braun", "peter"])

    # Run churn predictions on all customers
    for path in (DATA_DIR / "health-scores").glob("*.json"):
        data = _load_json(path)
        if data.get("email"):
            predict_churn(data["email"], data.get("org", "gigforge"))


# ── CLI ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Customer Success Platform")
    parser.add_argument("command", choices=["daily", "weekly", "health", "churn", "sentiment", "checkins"])
    parser.add_argument("--email", default="")
    parser.add_argument("--org", default="gigforge")
    args = parser.parse_args()

    if args.command == "daily":
        daily_cron()
        print("Daily tasks complete")
    elif args.command == "weekly":
        weekly_cron()
        print("Weekly tasks complete")
    elif args.command == "health":
        print(json.dumps(get_health_score(args.email), indent=2))
    elif args.command == "churn":
        print(json.dumps(predict_churn(args.email, args.org), indent=2))
    elif args.command == "sentiment":
        print(generate_sentiment_report(args.org))
    elif args.command == "checkins":
        process_checkins()
        print("Check-ins processed")
