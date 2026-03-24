#!/usr/bin/env python3
"""AI Elevate Sales & Marketing Platform

19 features for GigForge and TechUni:

SALES:
1.  Lead Scoring Model (0-100)
2.  Proposal Auto-Generator
3.  Pipeline Stage Automation
4.  Win/Loss Analysis
5.  Revenue Forecasting
6.  Competitive Intelligence
7.  Referral Tracking
8.  Sales Playbooks

MARKETING:
9.  Content Calendar Automation
10. SEO Content Engine
11. Social Proof Collector
12. Email Drip Campaigns
13. A/B Testing Framework
14. Marketing Attribution
15. Brand Monitoring
16. Event-Triggered Marketing

CROSS-FUNCTIONAL:
17. Sales-Marketing Feedback Loop
18. Customer Journey Mapping
19. Automated Reporting Dashboard
"""

import json
import os
import re
import sys
import csv
import hashlib
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from exceptions import AiElevateError  # TODO: Use specific exception types, DatabaseError, EmailError

sys.path.insert(0, "/home/aielevate")
from notify import send as notify_send

DATA_DIR = Path("/opt/ai-elevate/sales-marketing")
for d in ["leads", "proposals", "pipeline", "winloss", "forecasts", "competitive",
           "referrals", "playbooks", "content-calendar", "seo", "social-proof",
           "drip-campaigns", "ab-tests", "attribution", "brand-mentions",
           "event-triggers", "feedback-loop", "journey", "reports"]:
    (DATA_DIR / d).mkdir(parents=True, exist_ok=True)

MAILGUN_API_KEY = os.environ.get("MAILGUN_API_KEY", "")
MAILGUN_DOMAIN = "mg.ai-elevate.ai"


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


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    entries = []
    with open(path) as f:
        for line in f:
            try:
                entries.append(json.loads(line.strip()))
            except (AiElevateError, Exception) as e:
                pass
    return entries


def _lead_id(email: str) -> str:
    return hashlib.md5(email.lower().strip().encode()).hexdigest()[:12]


# ══════════════════════════════════════════════════════════════════════════
# 1. LEAD SCORING MODEL
# ══════════════════════════════════════════════════════════════════════════

LEAD_WEIGHTS = {
    "budget_signal": 0.25,      # Has budget, mentioned price range
    "technology_fit": 0.20,     # Needs what we offer
    "engagement": 0.20,         # Responded, clicked, visited
    "company_size": 0.15,       # Larger = higher potential
    "urgency": 0.10,            # Has deadline, needs it fast
    "response_time": 0.10,      # How fast they respond
}

def score_lead(email: str, org: str = "gigforge",
               budget: float = 0, budget_signal: str = "",
               skills_match: list[str] = None, company_size: str = "",
               engagement_actions: list[str] = None,
               urgency_level: str = "low",
               response_time_hours: float = 0) -> dict:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Score a lead 0-100. Hot (>70), Warm (40-70), Cold (<40)."""
    lid = _lead_id(email)
    scores = {}

    # Budget signal
    if budget >= 5000:
        scores["budget_signal"] = 100
    elif budget >= 1000:
        scores["budget_signal"] = 70
    elif budget >= 500:
        scores["budget_signal"] = 50
    elif budget > 0:
        scores["budget_signal"] = 30
    elif budget_signal:
        scores["budget_signal"] = 40
    else:
        scores["budget_signal"] = 10

    # Technology fit
    our_skills = {"python", "react", "fastapi", "nextjs", "docker", "ai", "ml", "rag",
                  "llm", "devops", "postgresql", "typescript", "nodejs", "django"}
    if skills_match:
        match_count = sum(1 for s in skills_match if s.lower() in our_skills)
        scores["technology_fit"] = min(match_count * 25, 100)
    else:
        scores["technology_fit"] = 30

    # Engagement
    actions = engagement_actions or []
    scores["engagement"] = min(len(actions) * 20, 100)

    # Company size
    size_scores = {"enterprise": 90, "large": 70, "medium": 50, "small": 30, "startup": 40, "individual": 20}
    scores["company_size"] = size_scores.get(company_size.lower(), 30)

    # Urgency
    urgency_scores = {"critical": 100, "high": 75, "medium": 50, "low": 25}
    scores["urgency"] = urgency_scores.get(urgency_level, 25)

    # Response time
    if response_time_hours <= 1:
        scores["response_time"] = 100
    elif response_time_hours <= 4:
        scores["response_time"] = 80
    elif response_time_hours <= 24:
        scores["response_time"] = 50
    else:
        scores["response_time"] = 20

    # Weighted total
    total = sum(scores.get(k, 0) * v for k, v in LEAD_WEIGHTS.items())
    category = "hot" if total > 70 else "warm" if total > 40 else "cold"

    result = {
        "email": email, "org": org, "score": round(total),
        "category": category, "component_scores": scores,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    _save_json(DATA_DIR / "leads" / f"{lid}.json", result)

    # Auto-route hot leads
    if category == "hot":
        notify_send(f"HOT LEAD — {org.upper()}", f"Lead: {email}\nScore: {total:.0f}/100\nBudget: ${budget}\nSkills: {skills_match}", priority="high", to=["braun"])

    return result


# ══════════════════════════════════════════════════════════════════════════
# 2. PROPOSAL AUTO-GENERATOR
# ══════════════════════════════════════════════════════════════════════════

PROPOSAL_TEMPLATES = {
    "ai_ml": """Dear {client_name},

Thank you for sharing your project requirements. I've reviewed them carefully and I'm confident we're an excellent fit.

PROJECT UNDERSTANDING
{project_summary}

OUR APPROACH
We'll deliver this using our proven AI/ML pipeline:
- Phase 1: Architecture design and data ingestion ({timeline_phase1})
- Phase 2: Model development and RAG pipeline ({timeline_phase2})
- Phase 3: Integration, testing, and deployment ({timeline_phase3})

RELEVANT EXPERIENCE
{portfolio_matches}

INVESTMENT
{pricing}

TIMELINE
{timeline_total}

We're ready to start {start_date}. Happy to jump on a quick call to discuss details.

Best regards,
{sender_name}
{org_name}""",

    "fullstack": """Dear {client_name},

Thanks for reaching out about your project. I've gone through the requirements and here's how we'd approach it.

PROJECT SCOPE
{project_summary}

TECH STACK
{tech_stack}

DELIVERY PLAN
{delivery_plan}

PORTFOLIO
{portfolio_matches}

PRICING
{pricing}

We can have a working prototype within {prototype_timeline}. Let me know if you'd like to discuss.

Best,
{sender_name}
{org_name}""",

    "devops": """Dear {client_name},

I've reviewed your infrastructure requirements and put together a plan.

CURRENT STATE ASSESSMENT
{project_summary}

PROPOSED ARCHITECTURE
{tech_stack}

IMPLEMENTATION PHASES
{delivery_plan}

INVESTMENT
{pricing}

We specialize in exactly this kind of work. Ready to start immediately.

Best regards,
{sender_name}
{org_name}""",
}

def generate_proposal(lead_email: str, org: str, template_type: str,
                       project_summary: str, budget: float,
                       skills: list[str] = None, timeline_weeks: int = 4) -> str:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Auto-generate a tailored proposal."""
    template = PROPOSAL_TEMPLATES.get(template_type, PROPOSAL_TEMPLATES["fullstack"])
    org_name = "GigForge" if org == "gigforge" else "TechUni"
    client_name = lead_email.split("@")[0].replace(".", " ").title()

    proposal = template.format(
        client_name=client_name,
        project_summary=project_summary,
        tech_stack=", ".join(skills or ["React", "FastAPI", "PostgreSQL", "Docker"]),
        delivery_plan=f"Sprint-based delivery over {timeline_weeks} weeks with weekly demos",
        portfolio_matches="See our portfolio at gigforge.ai for similar projects",
        pricing=f"${budget:,.0f} (fixed price, milestone-based payments)",
        timeline_total=f"{timeline_weeks} weeks from kickoff to deployment",
        timeline_phase1=f"Week 1-{max(1,timeline_weeks//4)}",
        timeline_phase2=f"Week {max(2,timeline_weeks//4+1)}-{max(3,timeline_weeks*3//4)}",
        timeline_phase3=f"Week {max(3,timeline_weeks*3//4+1)}-{timeline_weeks}",
        prototype_timeline=f"{max(1,timeline_weeks//3)} weeks",
        start_date="this Monday",
        sender_name="Sales Team",
        org_name=org_name,
    )

    # Save proposal
    lid = _lead_id(lead_email)
    path = DATA_DIR / "proposals" / f"{lid}-{datetime.now(timezone.utc).strftime('%Y%m%d')}.md"
    with open(path, "w") as f:
        f.write(proposal)

    return proposal


# ══════════════════════════════════════════════════════════════════════════
# 3. PIPELINE STAGE AUTOMATION
# ══════════════════════════════════════════════════════════════════════════

PIPELINE_STAGES = ["lead", "contacted", "proposal_sent", "negotiation", "won", "lost", "stale"]
STAGE_AUTO_ACTIONS = {
    "proposal_sent": {"follow_up_days": 3, "next_action": "Send follow-up email"},
    "contacted": {"follow_up_days": 7, "next_action": "Second follow-up or mark stale"},
    "negotiation": {"follow_up_days": 5, "next_action": "Check decision status"},
}

def update_pipeline(deal_id: str, org: str, stage: str, value: float = 0,
                     contact_email: str = "", notes: str = ""):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Update a deal's pipeline stage with auto-scheduling."""
    path = DATA_DIR / "pipeline" / f"{deal_id}.json"
    deal = _load_json(path, {
        "id": deal_id, "org": org, "contact": contact_email,
        "value": value, "stage": "lead", "history": [], "created": datetime.now(timezone.utc).isoformat(),
    })

    deal["stage"] = stage
    deal["value"] = value or deal.get("value", 0)
    deal["updated"] = datetime.now(timezone.utc).isoformat()
    deal["history"].append({"stage": stage, "timestamp": deal["updated"], "notes": notes})

    # Auto-schedule follow-up
    auto = STAGE_AUTO_ACTIONS.get(stage)
    if auto:
        follow_up = (datetime.now(timezone.utc) + timedelta(days=auto["follow_up_days"])).isoformat()
        deal["next_follow_up"] = follow_up
        deal["next_action"] = auto["next_action"]

    _save_json(path, deal)
    return deal


def check_stale_deals(org: str = "gigforge") -> list[dict]:
    """Find deals that haven't been updated in 14+ days."""
    stale = []
    for path in (DATA_DIR / "pipeline").glob("*.json"):
        deal = _load_json(path)
        if deal.get("org") != org or deal.get("stage") in ("won", "lost", "stale"):
            continue
        updated = deal.get("updated", deal.get("created", ""))
        if not updated:
            continue
        try:
            dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - dt).days >= 14:
                stale.append(deal)
        except (DatabaseError, Exception) as e:
            pass
    return stale


# ══════════════════════════════════════════════════════════════════════════
# 4. WIN/LOSS ANALYSIS
# ══════════════════════════════════════════════════════════════════════════

def record_outcome(deal_id: str, outcome: str, reason: str, competitor: str = "",
                    lessons: str = "", org: str = "gigforge"):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Record win/loss outcome for analysis."""
    _append_jsonl(DATA_DIR / "winloss" / f"{org}-outcomes.jsonl", {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "deal_id": deal_id,
        "outcome": outcome,
        "reason": reason,
        "competitor": competitor,
        "lessons": lessons,
    })

    # Update pipeline
    deal_path = DATA_DIR / "pipeline" / f"{deal_id}.json"
    if deal_path.exists():
        deal = _load_json(deal_path)
        deal["stage"] = "won" if outcome == "won" else "lost"
        deal["outcome_reason"] = reason
        _save_json(deal_path, deal)


def generate_winloss_report(org: str) -> str:
    """Monthly win/loss analysis report."""
    entries = _read_jsonl(DATA_DIR / "winloss" / f"{org}-outcomes.jsonl")
    if not entries:
        return "No win/loss data yet."

    month_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    recent = [e for e in entries if e["timestamp"] > month_ago]

    wins = [e for e in recent if e["outcome"] == "won"]
    losses = [e for e in recent if e["outcome"] == "lost"]

    report = f"""Win/Loss Report — {org.upper()} (Last 30 Days)

Wins: {len(wins)} | Losses: {len(losses)} | Win Rate: {len(wins)/max(len(recent),1)*100:.0f}%

Top Win Reasons:
"""
    win_reasons = {}
    for w in wins:
        r = w.get("reason", "unknown")
        win_reasons[r] = win_reasons.get(r, 0) + 1
    for r, c in sorted(win_reasons.items(), key=lambda x: -x[1]):
        report += f"  - {r} ({c}x)\n"

    report += "\nTop Loss Reasons:\n"
    loss_reasons = {}
    for l in losses:
        r = l.get("reason", "unknown")
        loss_reasons[r] = loss_reasons.get(r, 0) + 1
    for r, c in sorted(loss_reasons.items(), key=lambda x: -x[1]):
        report += f"  - {r} ({c}x)\n"

    competitors = {}
    for l in losses:
        comp = l.get("competitor", "")
        if comp:
            competitors[comp] = competitors.get(comp, 0) + 1
    if competitors:
        report += "\nLost to Competitors:\n"
        for comp, c in sorted(competitors.items(), key=lambda x: -x[1]):
            report += f"  - {comp} ({c}x)\n"

    return report


# ══════════════════════════════════════════════════════════════════════════
# 5. REVENUE FORECASTING
# ══════════════════════════════════════════════════════════════════════════

STAGE_PROBABILITIES = {"lead": 0.1, "contacted": 0.2, "proposal_sent": 0.4,
                        "negotiation": 0.7, "won": 1.0, "lost": 0.0, "stale": 0.05}

def generate_forecast(org: str = "gigforge") -> dict:
    """Weighted pipeline value forecast."""
    deals = []
    for path in (DATA_DIR / "pipeline").glob("*.json"):
        deal = _load_json(path)
        if deal.get("org") == org and deal.get("stage") not in ("won", "lost"):
            deals.append(deal)

    total_pipeline = sum(d.get("value", 0) for d in deals)
    weighted = sum(d.get("value", 0) * STAGE_PROBABILITIES.get(d.get("stage", "lead"), 0.1) for d in deals)
    by_stage = {}
    for d in deals:
        stage = d.get("stage", "lead")
        by_stage[stage] = by_stage.get(stage, {"count": 0, "value": 0})
        by_stage[stage]["count"] += 1
        by_stage[stage]["value"] += d.get("value", 0)

    forecast = {
        "org": org,
        "total_pipeline": total_pipeline,
        "weighted_forecast": round(weighted),
        "deal_count": len(deals),
        "by_stage": by_stage,
        "generated": datetime.now(timezone.utc).isoformat(),
    }

    _save_json(DATA_DIR / "forecasts" / f"{org}-latest.json", forecast)
    return forecast


# ══════════════════════════════════════════════════════════════════════════
# 6. COMPETITIVE INTELLIGENCE
# ══════════════════════════════════════════════════════════════════════════

def log_competitor_pricing(competitor: str, service: str, price: float,
                            source: str = "", org: str = "gigforge"):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Track competitor pricing."""
    _append_jsonl(DATA_DIR / "competitive" / f"{org}-pricing.jsonl", {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "competitor": competitor, "service": service,
        "price": price, "source": source,
    })


def get_competitive_landscape(org: str) -> list[dict]:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    return _read_jsonl(DATA_DIR / "competitive" / f"{org}-pricing.jsonl")


# ══════════════════════════════════════════════════════════════════════════
# 7. REFERRAL TRACKING
# ══════════════════════════════════════════════════════════════════════════

def log_referral(referrer_email: str, referred_email: str, org: str = "gigforge"):
    """Track customer referrals."""
    _append_jsonl(DATA_DIR / "referrals" / f"{org}-referrals.jsonl", {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "referrer": referrer_email, "referred": referred_email, "converted": False,
    })


def mark_referral_converted(referred_email: str, org: str = "gigforge"):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    path = DATA_DIR / "referrals" / f"{org}-referrals.jsonl"
    entries = _read_jsonl(path)
    with open(path, "w") as f:
        for e in entries:
            if e.get("referred") == referred_email:
                e["converted"] = True
            f.write(json.dumps(e) + "\n")


# ══════════════════════════════════════════════════════════════════════════
# 8. SALES PLAYBOOKS
# ══════════════════════════════════════════════════════════════════════════

PLAYBOOKS = {
    "cold_outreach": {
        "name": "Cold Outreach",
        "steps": [
            "Research the prospect — check their website, LinkedIn, recent posts",
            "Personalize the opening — reference something specific about their business",
            "Lead with value — what problem can you solve for them?",
            "Include a relevant portfolio piece as social proof",
            "Clear CTA — suggest a specific time for a 15-min call",
            "Follow up Day 3 if no response (different angle)",
            "Follow up Day 7 (final, no-pressure close)",
        ],
    },
    "follow_up": {
        "name": "Follow-Up Sequence",
        "steps": [
            "Day 1: Reference previous conversation + new value add",
            "Day 3: Share relevant case study or blog post",
            "Day 7: Direct ask — 'Is this still a priority?'",
            "Day 14: Break-up email — 'Closing the loop on this'",
        ],
    },
    "objection_handling": {
        "name": "Objection Handling",
        "responses": {
            "too expensive": "I understand budget is important. Let me break down the ROI — most clients see the project pay for itself within {timeframe}. We can also phase the delivery to spread the investment.",
            "need to think about it": "Absolutely, take your time. To help with your decision, would it be useful if I sent over a case study from a similar project? What specific concerns would you like addressed?",
            "already have a provider": "That makes sense. Many of our clients came to us when they needed specialized AI/ML expertise that their current provider couldn't offer. Would it be worth a quick conversation about where we might complement what you already have?",
            "timeline too long": "I hear you on the timeline. We can accelerate by running workstreams in parallel. If we start this week, we could have a working prototype in {prototype_days} days.",
            "not sure about quality": "Totally valid concern. Here are three live projects we've built: [portfolio links]. We also offer a paid proof-of-concept — small scope, low risk — so you can evaluate our work before committing to the full project.",
        },
    },
    "closing": {
        "name": "Closing Techniques",
        "steps": [
            "Summary close: Recap their needs, your solution, the investment, the timeline",
            "Assumptive close: 'Shall I send over the contract for Monday start?'",
            "Urgency close: 'We have capacity now but are booking up for next month'",
            "Trial close: 'If we could start with a small pilot, would that work?'",
            "Risk-reversal: 'If you're not happy with the first milestone, you can walk away'",
        ],
    },
}

def get_playbook(name: str) -> dict:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    return PLAYBOOKS.get(name, {})

def list_playbooks() -> list[str]:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    return list(PLAYBOOKS.keys())


# ══════════════════════════════════════════════════════════════════════════
# 9. CONTENT CALENDAR AUTOMATION
# ══════════════════════════════════════════════════════════════════════════

def generate_content_calendar(org: str, week_start: str = "") -> dict:
    """Generate a weekly content calendar."""
    if not week_start:
        today = datetime.now(timezone.utc)
        monday = today - timedelta(days=today.weekday())
        week_start = monday.strftime("%Y-%m-%d")

    org_name = "GigForge" if org == "gigforge" else "TechUni"

    calendar = {
        "org": org, "week_start": week_start,
        "posts": [
            {"day": "Monday", "platform": "LinkedIn", "type": "thought_leadership",
             "topic": f"AI industry insight from {org_name}", "status": "planned"},
            {"day": "Tuesday", "platform": "Twitter", "type": "product_update",
             "topic": "Feature highlight or project showcase", "status": "planned"},
            {"day": "Wednesday", "platform": "LinkedIn", "type": "case_study",
             "topic": "Client success story or project demo", "status": "planned"},
            {"day": "Thursday", "platform": "Reddit", "type": "educational",
             "topic": "Technical tutorial or industry analysis", "status": "planned"},
            {"day": "Friday", "platform": "Twitter+Bluesky", "type": "engagement",
             "topic": "Week recap, team highlight, or community question", "status": "planned"},
        ],
    }

    _save_json(DATA_DIR / "content-calendar" / f"{org}-{week_start}.json", calendar)
    return calendar


# ══════════════════════════════════════════════════════════════════════════
# 10. SEO CONTENT ENGINE
# ══════════════════════════════════════════════════════════════════════════

SEO_KEYWORDS = {
    "gigforge": [
        "ai development agency", "rag pipeline development", "llm integration services",
        "react developer for hire", "fastapi development", "ai agent development",
        "custom ai chatbot", "devops consulting", "saas development agency",
    ],
    "techuni": [
        "ai course creator", "automated course builder", "lms with ai",
        "course creation platform", "ai powered elearning", "online course software",
        "docker lab environments", "corporate training platform",
    ],
    "cryptoadvisor": [
        "crypto portfolio tracker", "ai crypto analysis", "defi dashboard",
        "crypto tax calculator", "whale tracking tool", "dca calculator crypto",
    ],
}

def get_seo_keywords(org: str) -> list[str]:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    return SEO_KEYWORDS.get(org, [])

def log_seo_content(org: str, keyword: str, title: str, url: str = ""):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    _append_jsonl(DATA_DIR / "seo" / f"{org}-content.jsonl", {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "keyword": keyword, "title": title, "url": url,
    })


# ══════════════════════════════════════════════════════════════════════════
# 11. SOCIAL PROOF COLLECTOR
# ══════════════════════════════════════════════════════════════════════════

def request_testimonial(customer_email: str, org: str = "gigforge",
                         project: str = "", nps_score: int = 0):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Auto-request testimonial from happy customers (NPS 9-10)."""
    if nps_score < 9:
        return False

    org_name = "GigForge" if org == "gigforge" else "TechUni"
    name = customer_email.split("@")[0].replace(".", " ").title()

    body = f"""Hi {name},

I'm really glad to hear you've had a great experience with {org_name}. Your feedback means a lot to us.

Would you be open to sharing a quick testimonial about your experience? Just 2-3 sentences about what you liked or what results you've seen. It would really help other people considering our services.

No pressure at all — and if you'd prefer, I can draft something based on our conversations for you to approve.

Thanks so much,
{org_name} Team"""

    import urllib.request, urllib.parse, base64
    data = urllib.parse.urlencode({
        "from": f"{org_name} <success@team.{org}.ai>",
        "h:Reply-To": f"success@team.{org}.ai",
        "to": customer_email,
        "subject": f"Would you share a quick testimonial?",
        "text": body,
    }).encode("utf-8")
    creds = base64.b64encode(f"api:{MAILGUN_API_KEY}".encode()).decode()
    req = urllib.request.Request(f"https://api.mailgun.net/v3/{MAILGUN_DOMAIN}/messages", data=data, method="POST")
    req.add_header("Authorization", f"Basic {creds}")
    try:
        urllib.request.urlopen(req, timeout=15)
        return True
    except (EmailError, Exception) as e:
        return False


# ══════════════════════════════════════════════════════════════════════════
# 12. EMAIL DRIP CAMPAIGNS
# ══════════════════════════════════════════════════════════════════════════

DRIP_SEQUENCES = {
    "welcome": {
        "name": "Welcome Series",
        "emails": [
            {"day": 0, "subject": "Welcome to {org_name}!", "template": "welcome_1"},
            {"day": 2, "subject": "Getting started — 3 things to try first", "template": "welcome_2"},
            {"day": 5, "subject": "How {customer_name} at {company} uses {org_name}", "template": "welcome_3"},
            {"day": 10, "subject": "Your {org_name} progress update", "template": "welcome_4"},
            {"day": 14, "subject": "Ready for more? Upgrade to Pro", "template": "welcome_5"},
        ],
    },
    "re_engagement": {
        "name": "Re-Engagement Series",
        "emails": [
            {"day": 0, "subject": "We miss you at {org_name}", "template": "reengage_1"},
            {"day": 3, "subject": "What's new at {org_name} (you'll want to see this)", "template": "reengage_2"},
            {"day": 7, "subject": "Last chance: Special offer inside", "template": "reengage_3"},
        ],
    },
    "upgrade_nudge": {
        "name": "Upgrade Nudge Series",
        "emails": [
            {"day": 0, "subject": "You're outgrowing the free plan", "template": "upgrade_1"},
            {"day": 5, "subject": "Pro features your team would love", "template": "upgrade_2"},
            {"day": 12, "subject": "Limited: 20% off Pro for your first 3 months", "template": "upgrade_3"},
        ],
    },
}

def enroll_drip(customer_email: str, sequence: str, org: str = "gigforge"):
    """Enroll a customer in a drip campaign."""
    _append_jsonl(DATA_DIR / "drip-campaigns" / f"{org}-active.jsonl", {
        "customer": customer_email, "sequence": sequence, "org": org,
        "enrolled": datetime.now(timezone.utc).isoformat(),
        "current_step": 0, "completed": False,
    })

def get_drip_sequences() -> dict:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    return DRIP_SEQUENCES


# ══════════════════════════════════════════════════════════════════════════
# 13. A/B TESTING FRAMEWORK
# ══════════════════════════════════════════════════════════════════════════

def create_ab_test(name: str, variant_a: str, variant_b: str, metric: str = "conversion",
                    org: str = "gigforge") -> str:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Create an A/B test."""
    test_id = hashlib.md5(f"{name}{time.time()}".encode()).hexdigest()[:8]
    test = {
        "id": test_id, "name": name, "org": org, "metric": metric,
        "variant_a": {"content": variant_a, "views": 0, "conversions": 0},
        "variant_b": {"content": variant_b, "views": 0, "conversions": 0},
        "status": "active",
        "created": datetime.now(timezone.utc).isoformat(),
    }
    _save_json(DATA_DIR / "ab-tests" / f"{test_id}.json", test)
    return test_id

def record_ab_event(test_id: str, variant: str, event: str = "view"):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    path = DATA_DIR / "ab-tests" / f"{test_id}.json"
    test = _load_json(path)
    if not test:
        return
    v = test.get(f"variant_{variant}", {})
    if event == "view":
        v["views"] = v.get("views", 0) + 1
    elif event == "conversion":
        v["conversions"] = v.get("conversions", 0) + 1
    test[f"variant_{variant}"] = v
    _save_json(path, test)


# ══════════════════════════════════════════════════════════════════════════
# 14. MARKETING ATTRIBUTION
# ══════════════════════════════════════════════════════════════════════════

def track_attribution(lead_email: str, channel: str, campaign: str = "",
                       org: str = "gigforge"):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Track lead source attribution."""
    _append_jsonl(DATA_DIR / "attribution" / f"{org}-sources.jsonl", {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "lead": lead_email, "channel": channel, "campaign": campaign,
    })

def get_attribution_report(org: str) -> dict:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    entries = _read_jsonl(DATA_DIR / "attribution" / f"{org}-sources.jsonl")
    by_channel = {}
    for e in entries:
        ch = e.get("channel", "unknown")
        by_channel[ch] = by_channel.get(ch, 0) + 1
    return {"org": org, "total_leads": len(entries), "by_channel": by_channel}


# ══════════════════════════════════════════════════════════════════════════
# 15. BRAND MONITORING
# ══════════════════════════════════════════════════════════════════════════

def log_brand_mention(platform: str, content: str, sentiment: float,
                       url: str = "", org: str = "gigforge"):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Log a brand mention from social/review monitoring."""
    _append_jsonl(DATA_DIR / "brand-mentions" / f"{org}-mentions.jsonl", {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "platform": platform, "content": content[:500],
        "sentiment": sentiment, "url": url,
    })

    if sentiment < -0.5:
        notify_send(f"Negative Brand Mention — {org.upper()}",
                     f"Platform: {platform}\nContent: {content[:200]}\nURL: {url}",
                     priority="high", to=["braun"])


# ══════════════════════════════════════════════════════════════════════════
# 16. EVENT-TRIGGERED MARKETING
# ══════════════════════════════════════════════════════════════════════════

def trigger_event(customer_email: str, event: str, org: str = "gigforge", data: dict = None):
    """Process a marketing trigger event."""
    triggers = {
        "signup_no_convert_7d": lambda: enroll_drip(customer_email, "upgrade_nudge", org),
        "usage_milestone": lambda: None,  # Send congrats + upsell
        "inactive_14d": lambda: enroll_drip(customer_email, "re_engagement", org),
        "first_purchase": lambda: enroll_drip(customer_email, "welcome", org),
    }

    action = triggers.get(event)
    if action:
        action()

    _append_jsonl(DATA_DIR / "event-triggers" / f"{org}-events.jsonl", {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "customer": customer_email, "event": event, "data": data or {},
    })


# ══════════════════════════════════════════════════════════════════════════
# 17. SALES-MARKETING FEEDBACK LOOP
# ══════════════════════════════════════════════════════════════════════════

def log_content_effectiveness(content_id: str, content_type: str,
                                helped_close: bool, deal_id: str = "",
                                feedback: str = "", org: str = "gigforge"):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Sales reports which marketing content helped close deals."""
    _append_jsonl(DATA_DIR / "feedback-loop" / f"{org}-feedback.jsonl", {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "content_id": content_id, "content_type": content_type,
        "helped_close": helped_close, "deal_id": deal_id, "feedback": feedback,
    })


# ══════════════════════════════════════════════════════════════════════════
# 18. CUSTOMER JOURNEY MAPPING
# ══════════════════════════════════════════════════════════════════════════

JOURNEY_STAGES = ["awareness", "consideration", "trial", "purchase", "onboarding",
                   "active_use", "expansion", "advocacy", "churned"]

def update_journey(customer_email: str, stage: str, touchpoint: str = "",
                    org: str = "gigforge"):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Track customer through their journey."""
    lid = _lead_id(customer_email)
    path = DATA_DIR / "journey" / f"{lid}.json"
    journey = _load_json(path, {
        "customer": customer_email, "org": org,
        "current_stage": "awareness", "touchpoints": [],
    })

    journey["current_stage"] = stage
    journey["touchpoints"].append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "stage": stage, "touchpoint": touchpoint,
    })
    journey["touchpoints"] = journey["touchpoints"][-50:]

    _save_json(path, journey)


def get_journey_report(org: str) -> dict:
    """Report on where customers are in their journey."""
    by_stage = {s: 0 for s in JOURNEY_STAGES}
    for path in (DATA_DIR / "journey").glob("*.json"):
        data = _load_json(path)
        if data.get("org") == org:
            stage = data.get("current_stage", "awareness")
            by_stage[stage] = by_stage.get(stage, 0) + 1
    return {"org": org, "by_stage": by_stage, "total": sum(by_stage.values())}


# ══════════════════════════════════════════════════════════════════════════
# 19. AUTOMATED REPORTING DASHBOARD
# ══════════════════════════════════════════════════════════════════════════

def generate_weekly_report(org: str) -> str:
    """Comprehensive weekly sales & marketing report."""
    forecast = generate_forecast(org)
    attribution = get_attribution_report(org)
    journey = get_journey_report(org)
    winloss = generate_winloss_report(org)
    stale = check_stale_deals(org)

    report = f"""═══════════════════════════════════════════
WEEKLY SALES & MARKETING REPORT — {org.upper()}
{datetime.now(timezone.utc).strftime('%B %d, %Y')}
═══════════════════════════════════════════

PIPELINE
  Total deals: {forecast.get('deal_count', 0)}
  Pipeline value: ${forecast.get('total_pipeline', 0):,.0f}
  Weighted forecast: ${forecast.get('weighted_forecast', 0):,.0f}
  Stale deals (14+ days): {len(stale)}

LEAD SOURCES
  Total leads tracked: {attribution.get('total_leads', 0)}
"""
    for ch, count in sorted(attribution.get("by_channel", {}).items(), key=lambda x: -x[1]):
        report += f"  - {ch}: {count}\n"

    report += f"""
CUSTOMER JOURNEY
"""
    for stage, count in journey.get("by_stage", {}).items():
        if count > 0:
            report += f"  {stage}: {count}\n"

    report += f"""
WIN/LOSS
{winloss}

STALE DEALS REQUIRING ACTION
"""
    for d in stale[:5]:
        report += f"  - {d.get('contact', 'unknown')} — ${d.get('value', 0):,.0f} — stage: {d.get('stage', '?')}\n"

    report += "\n═══════════════════════════════════════════\n"
    return report


# ── Cron Jobs ────────────────────────────────────────────────────────────

def daily_cron():
    """Daily sales & marketing tasks."""
    for org in ["gigforge", "techuni"]:
        stale = check_stale_deals(org)
        if stale:
            notify_send(f"{len(stale)} Stale Deals — {org.upper()}",
                         "\n".join(f"- {d.get('contact','?')}: ${d.get('value',0):,.0f}" for d in stale[:5]),
                         priority="medium", to=["braun"])

def weekly_cron():
    """Weekly sales & marketing report."""
    for org in ["gigforge", "techuni"]:
        report = generate_weekly_report(org)
        notify_send(f"Weekly Sales Report — {org.upper()}", report,
                     priority="medium", to=["braun", "peter"])
        _save_json(DATA_DIR / "reports" / f"{org}-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json",
                    {"report": report, "generated": datetime.now(timezone.utc).isoformat()})


# ── CLI ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Sales & Marketing Platform")
    parser.add_argument("command", choices=["daily", "weekly", "forecast", "winloss",
                                             "calendar", "playbook", "report", "keywords"])
    parser.add_argument("--org", default="gigforge")
    parser.add_argument("--name", default="")
    args = parser.parse_args()

    if args.command == "daily":
        daily_cron(); print("Done")
    elif args.command == "weekly":
        weekly_cron(); print("Done")
    elif args.command == "forecast":
        print(json.dumps(generate_forecast(args.org), indent=2))
    elif args.command == "winloss":
        print(generate_winloss_report(args.org))
    elif args.command == "calendar":
        print(json.dumps(generate_content_calendar(args.org), indent=2))
    elif args.command == "playbook":
        if args.name:
            print(json.dumps(get_playbook(args.name), indent=2))
        else:
            print("Available:", ", ".join(list_playbooks()))
    elif args.command == "report":
        print(generate_weekly_report(args.org))
    elif args.command == "keywords":
        print("\n".join(get_seo_keywords(args.org)))
