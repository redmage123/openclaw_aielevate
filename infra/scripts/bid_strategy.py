#!/usr/bin/env python3
"""Bid Strategy Engine — game-theory-informed proposal optimization.

Scores jobs AND optimizes our proposal strategy for each one.

Factors:
  1. Job fit (skills, budget, complexity) — can we deliver?
  2. Win probability (competition, client quality, timing) — will we win?
  3. Value (budget, follow-on potential, portfolio value) — is it worth winning?
  4. Optimal bid price — game-theory-informed pricing

Usage:
  from bid_strategy import analyze_job, optimal_bid, should_bid

  analysis = analyze_job(job)
  bid = optimal_bid(job)
  decision = should_bid(job)  # True/False with reasoning
"""

import math
from datetime import datetime, timezone


def analyze_job(job: dict) -> dict:
    """Full analysis of a job opportunity."""
    title = (job.get("title", "") or "").lower()
    desc = (job.get("description", "") or "").lower()
    skills = (job.get("skills", "") or "").lower()
    budget_min = job.get("budget_min", 0) or 0
    budget_max = job.get("budget_max", 0) or 0
    text = f"{title} {desc} {skills}"

    analysis = {
        "job_title": job.get("title", ""),
        "platform": job.get("platform", ""),
        "fit_score": 0,         # Can we deliver? (0-100)
        "win_probability": 0,    # Will we win? (0-100)
        "value_score": 0,        # Is it worth winning? (0-100)
        "overall_score": 0,      # Weighted combination
        "recommended_bid": 0,
        "bid_strategy": "",
        "proposal_angle": "",
        "skip_reason": "",
    }

    # === FIT SCORE (0-100) — can we deliver? ===
    strong_skills = ["python", "fastapi", "react", "next.js", "typescript", "ai", "llm",
                     "agent", "automation", "docker", "devops"]
    moderate_skills = ["vue", "node.js", "postgresql", "mongodb", "video", "seo",
                       "flutter", "react native", "shopify", "wordpress"]
    weak_skills = ["java", "c#", ".net", "angular", "ruby", "php", "ios native",
                   "android native", "unity", "unreal"]

    strong_matches = sum(1 for s in strong_skills if s in text)
    moderate_matches = sum(1 for s in moderate_skills if s in text)
    weak_matches = sum(1 for s in weak_skills if s in text)

    if weak_matches > strong_matches + moderate_matches:
        analysis["fit_score"] = 15
        analysis["skip_reason"] = "Primary skills outside our stack"
    else:
        analysis["fit_score"] = min(100, strong_matches * 15 + moderate_matches * 8)

    # === WIN PROBABILITY (0-100) — competitive analysis ===
    win = 50  # Base

    # Budget realism — realistic budgets = serious client
    avg_budget = (budget_min + budget_max) / 2 if budget_max > 0 else budget_min
    if avg_budget >= 3000:
        win += 15  # High budget = fewer competitors willing to deliver quality
    elif avg_budget >= 1000:
        win += 10
    elif avg_budget >= 300:
        win += 0
    elif avg_budget > 0 and avg_budget < 100:
        win -= 20  # Low budget = race to bottom, many competitors

    # Description quality — detailed = serious client
    if len(desc) > 1000:
        win += 10  # Client knows what they want
    elif len(desc) < 100:
        win -= 10  # Vague = tire-kicker

    # Our differentiator: AI-speed delivery
    if any(k in text for k in ["urgent", "asap", "fast", "quickly", "deadline", "rush"]):
        win += 15  # Speed is our advantage

    # AI/agent work — our specialty
    if any(k in text for k in ["ai agent", "llm", "chatbot", "rag", "langchain", "automation"]):
        win += 20  # Few competitors can deliver this well

    analysis["win_probability"] = max(5, min(95, win))

    # === VALUE SCORE (0-100) — is it worth winning? ===
    value = 0

    # Direct revenue
    if avg_budget >= 5000: value += 40
    elif avg_budget >= 2000: value += 30
    elif avg_budget >= 500: value += 20
    elif avg_budget >= 100: value += 10

    # Follow-on potential
    if any(k in text for k in ["ongoing", "retainer", "long-term", "phase 1", "mvp", "v1"]):
        value += 25

    # Portfolio value — case study potential
    if any(k in text for k in ["ai", "blockchain", "fintech", "healthcare", "saas"]):
        value += 15

    # Client quality signal
    if len(desc) > 500 and avg_budget > 1000:
        value += 10  # Serious client = good relationship potential

    # Referral potential
    if any(k in text for k in ["startup", "agency", "enterprise", "company"]):
        value += 10

    analysis["value_score"] = min(100, value)

    # === OVERALL SCORE ===
    # Weighted: 30% fit, 30% win probability, 40% value
    analysis["overall_score"] = (
        analysis["fit_score"] * 0.3 +
        analysis["win_probability"] * 0.3 +
        analysis["value_score"] * 0.4
    )

    # === OPTIMAL BID ===
    analysis["recommended_bid"] = _optimal_bid(avg_budget, analysis["win_probability"], analysis["fit_score"])

    # === STRATEGY ===
    analysis["bid_strategy"] = _bid_strategy(analysis, text, avg_budget)
    analysis["proposal_angle"] = _proposal_angle(text)

    return analysis


def _optimal_bid(client_budget: float, win_prob: float, fit: float) -> float:
    """Game-theory optimal bid price.

    In a first-price sealed-bid auction with N competitors:
    Optimal bid = value * (N-1)/N

    We estimate N from the budget level:
    - High budget (>$5K): ~5-10 serious bidders
    - Medium ($1-5K): ~15-30 bidders
    - Low (<$1K): ~50+ bidders

    We bid slightly below our value but above rock-bottom.
    """
    if client_budget <= 0:
        return 0

    # Estimate competition
    if client_budget >= 5000:
        n_competitors = 8
    elif client_budget >= 2000:
        n_competitors = 15
    elif client_budget >= 500:
        n_competitors = 25
    else:
        n_competitors = 40

    # Our value delivery (what we think it's worth)
    our_value = client_budget * 0.9  # We can deliver at 90% of budget

    # Nash equilibrium bid: shade down based on competition
    # bid = value * (N-1)/N
    shade_factor = (n_competitors - 1) / n_competitors
    optimal = our_value * shade_factor

    # Floor: never bid below 70% of budget (signals quality)
    floor = client_budget * 0.7

    # If we're a strong fit, bid closer to budget (confidence signal)
    if fit > 70:
        optimal = max(optimal, client_budget * 0.85)

    return max(floor, min(optimal, client_budget * 0.95))


def _bid_strategy(analysis: dict, text: str, budget: float) -> str:
    """Recommend a bidding strategy."""
    if analysis["overall_score"] < 25:
        return "SKIP — low fit and value. Not worth the proposal time."

    if analysis["win_probability"] > 70 and analysis["fit_score"] > 60:
        return "STRONG BID — we are a top contender. Bid at 85-90% of budget. Lead with specific portfolio match."

    if analysis["win_probability"] > 50 and analysis["value_score"] > 60:
        return "VALUE BID — good opportunity. Bid at 80% of budget. Emphasize speed and AI expertise."

    if "urgent" in text or "asap" in text:
        return "SPEED BID — client needs it fast. Bid at 90-95% of budget. Lead with 'we deliver in hours, not weeks.'"

    if analysis["fit_score"] > 70 and analysis["win_probability"] < 40:
        return "DIFFERENTIATE — strong fit but crowded field. Lead with a unique angle (AI-powered delivery, same-day turnaround)."

    return "STANDARD BID — moderate opportunity. Bid at 75-80% of budget. Keep proposal concise."


def _proposal_angle(text: str) -> str:
    """Recommend the proposal angle based on job content."""
    if any(k in text for k in ["ai", "llm", "agent", "chatbot"]):
        return "Lead with AI expertise — we build AI agents daily, not as a side project."

    if any(k in text for k in ["urgent", "asap", "fast", "deadline"]):
        return "Lead with speed — 'We deliver in hours, not weeks. Your timeline is in your hands.'"

    if any(k in text for k in ["startup", "mvp", "prototype"]):
        return "Lead with startup empathy — 'We've built MVPs that became real products. Let's scope this tight.'"

    if any(k in text for k in ["enterprise", "saas", "platform"]):
        return "Lead with scale — 'We architect for growth from day one. Docker, CI/CD, monitoring built in.'"

    if any(k in text for k in ["design", "ui", "ux", "beautiful", "modern"]):
        return "Lead with portfolio — show our best-looking projects. Visual proof beats words."

    return "Lead with competence — specific tech stack match, relevant portfolio piece, clear timeline."


def should_bid(job: dict) -> dict:
    """Quick decision: should we bid on this job?"""
    analysis = analyze_job(job)

    if analysis["overall_score"] < 25:
        return {"bid": False, "reason": analysis.get("skip_reason") or "Score too low", "score": analysis["overall_score"]}

    if analysis["fit_score"] < 20:
        return {"bid": False, "reason": "Skills don't match our stack", "score": analysis["overall_score"]}

    return {
        "bid": True,
        "score": analysis["overall_score"],
        "recommended_bid": analysis["recommended_bid"],
        "strategy": analysis["bid_strategy"],
        "angle": analysis["proposal_angle"],
    }
