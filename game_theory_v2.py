#!/usr/bin/env python3
"""Game Theory Engine v2 — learning bid strategy with historical tracking,
competition density, and client reputation scoring.

Builds on bid_strategy.py with:
1. Historical win rate tracking — learns from actual outcomes
2. Competition density — scrapes proposal count per job
3. Client reputation scoring — hire rate, reviews, payment history
4. Bayesian updating — adjusts win probability based on our track record

Storage: Postgres (bid_history, client_scores tables)

Usage:
  from game_theory_v2 import (
      analyze_job_v2, record_outcome, get_win_rate,
      scrape_competition, score_client, get_learning_stats
  )
"""

import json
import math
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import psycopg2
import psycopg2.extras
from dao import BaseDAO  # TODO: Replace inline DB calls with specific DAOs
from exceptions import AiElevateError  # TODO: Use specific exception types, DatabaseError

sys.path.insert(0, "/home/aielevate")

DB = dict(
    host=os.environ.get("DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("DB_PORT", "5434")),
    dbname=os.environ.get("DB_NAME", "rag"),
    user=os.environ.get("DB_USER", "rag"),
    password=os.environ.get("DB_PASSWORD", ""),
)


def _get_db():
    conn = psycopg2.connect(**DB)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""CREATE TABLE IF NOT EXISTS bid_history (
        id SERIAL PRIMARY KEY,
        job_id TEXT,
        platform TEXT,
        job_title TEXT,
        our_bid REAL,
        client_budget_min REAL,
        client_budget_max REAL,
        fit_score REAL,
        win_probability REAL,
        value_score REAL,
        overall_score REAL,
        strategy TEXT,
        proposal_angle TEXT,
        competition_count INTEGER,
        client_score REAL,
        outcome TEXT DEFAULT 'pending',
        outcome_reason TEXT,
        submitted_at TIMESTAMPTZ DEFAULT NOW(),
        resolved_at TIMESTAMPTZ,
        revenue REAL DEFAULT 0
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS client_scores (
        id SERIAL PRIMARY KEY,
        platform TEXT,
        client_id TEXT,
        client_name TEXT,
        hire_rate REAL,
        total_spent REAL,
        avg_review REAL,
        jobs_posted INTEGER,
        payment_verified BOOLEAN,
        country TEXT,
        member_since TEXT,
        score REAL,
        scraped_at TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(platform, client_id)
    )""")

    cur.execute("CREATE INDEX IF NOT EXISTS idx_bid_outcome ON bid_history(outcome)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bid_platform ON bid_history(platform)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_client_platform ON client_scores(platform, client_id)")

    return conn, cur


# === Historical Win Rate Tracking ===

def record_bid(job: dict, analysis: dict, competition_count: int = 0, client_score: float = 0) -> int:
    """Record a bid we submitted for tracking."""
    conn, cur = _get_db()
    cur.execute(
        """INSERT INTO bid_history (job_id, platform, job_title, our_bid, client_budget_min, client_budget_max,
           fit_score, win_probability, value_score, overall_score, strategy, proposal_angle,
           competition_count, client_score)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (job.get("id"), job.get("platform"), job.get("title"),
         analysis.get("recommended_bid", 0),
         job.get("budget_min", 0), job.get("budget_max", 0),
         analysis.get("fit_score", 0), analysis.get("win_probability", 0),
         analysis.get("value_score", 0), analysis.get("overall_score", 0),
         analysis.get("bid_strategy", ""), analysis.get("proposal_angle", ""),
         competition_count, client_score)
    )
    bid_id = cur.fetchone()["id"]
    conn.close()
    return bid_id


def record_outcome(bid_id: int, outcome: str, reason: str = "", revenue: float = 0):
    """Record whether we won or lost a bid.
    outcome: 'won', 'lost', 'withdrawn', 'expired', 'no_response'
    """
    conn, cur = _get_db()
    cur.execute(
        "UPDATE bid_history SET outcome=%s, outcome_reason=%s, revenue=%s, resolved_at=NOW() WHERE id=%s",
        (outcome, reason, revenue, bid_id)
    )
    conn.close()


def get_win_rate(platform: str = None, months: int = 6) -> dict:
    """Get historical win rate with breakdowns."""
    conn, cur = _get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=months * 30)).isoformat()

    where = "WHERE submitted_at > %s AND outcome != 'pending'"
    params = [cutoff]
    if platform:
        where += " AND platform = %s"
        params.append(platform)

    cur.execute("SELECT outcome, COUNT(*) as cnt FROM bid_history " + where + " GROUP BY outcome", params)
    outcomes = {r["outcome"]: r["cnt"] for r in cur.fetchall()}

    total = sum(outcomes.values())
    won = outcomes.get("won", 0)

    # Win rate by strategy
    cur.execute(
        "SELECT strategy, outcome, COUNT(*) as cnt FROM bid_history " + where + " GROUP BY strategy, outcome",
        params)
    strategy_stats = {}
    for r in cur.fetchall():
        s = r["strategy"][:30] if r["strategy"] else "unknown"
        if s not in strategy_stats:
            strategy_stats[s] = {"won": 0, "total": 0}
        strategy_stats[s]["total"] += r["cnt"]
        if r["outcome"] == "won":
            strategy_stats[s]["won"] += r["cnt"]

    # Win rate by score range
    cur.execute(
        "SELECT CASE WHEN overall_score >= 70 THEN 'high' WHEN overall_score >= 40 THEN 'medium' ELSE 'low' END as tier,"
        " outcome, COUNT(*) as cnt FROM bid_history " + where + " GROUP BY tier, outcome",
        params)
    tier_stats = {}
    for r in cur.fetchall():
        t = r["tier"]
        if t not in tier_stats:
            tier_stats[t] = {"won": 0, "total": 0}
        tier_stats[t]["total"] += r["cnt"]
        if r["outcome"] == "won":
            tier_stats[t]["won"] += r["cnt"]

    # Average bid for wins vs losses
    cur.execute("SELECT outcome, AVG(our_bid) as avg_bid FROM bid_history " + where + " AND our_bid > 0 GROUP BY outcome", params)
    avg_bids = {r["outcome"]: r["avg_bid"] for r in cur.fetchall()}

    conn.close()

    return {
        "total_bids": total,
        "won": won,
        "win_rate": won / total if total > 0 else 0,
        "outcomes": outcomes,
        "by_strategy": {k: {"win_rate": v["won"] / v["total"] if v["total"] > 0 else 0, **v} for k, v in strategy_stats.items()},
        "by_tier": {k: {"win_rate": v["won"] / v["total"] if v["total"] > 0 else 0, **v} for k, v in tier_stats.items()},
        "avg_bid_won": avg_bids.get("won", 0),
        "avg_bid_lost": avg_bids.get("lost", 0),
        "total_revenue": sum(r.get("revenue", 0) for r in []),  # Will be from query
    }


# === Competition Density ===

def scrape_competition(job_url: str, platform: str = "upwork") -> dict:
    """Scrape the number of proposals already submitted on a job."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36")
            page.goto(job_url, timeout=20000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

            result = {"url": job_url, "platform": platform}

            if platform == "upwork":
                # Look for proposal count
                el = page.query_selector('[data-test="proposals-tier"], .proposals-tier, .cfe-ui-job-proposals')
                if el:
                    text = el.inner_text()
                    nums = re.findall(r'(\d+)', text)
                    if nums:
                        result["proposals"] = int(nums[0])

                # Look for client info
                client_el = page.query_selector('[data-test="client-info"], .client-info')
                if client_el:
                    client_text = client_el.inner_text()
                    result["client_info_raw"] = client_text[:300]

                    # Extract hire rate
                    hire_match = re.search(r'(\d+)%\s*hire\s*rate', client_text, re.I)
                    if hire_match:
                        result["hire_rate"] = int(hire_match.group(1))

                    # Extract total spent
                    spent_match = re.search(r'\$(\d[\d,]*)\s*(?:total\s*)?spent', client_text, re.I)
                    if spent_match:
                        result["total_spent"] = float(spent_match.group(1).replace(",", ""))

                    # Extract rating
                    rating_match = re.search(r'([\d.]+)\s*(?:of\s*5|stars?|rating)', client_text, re.I)
                    if rating_match:
                        result["avg_review"] = float(rating_match.group(1))

                    # Payment verified
                    result["payment_verified"] = "payment verified" in client_text.lower()

            elif platform == "freelancer":
                # Freelancer shows bid count
                bid_el = page.query_selector('.BidsCount, .bid-count, [data-bid-count]')
                if bid_el:
                    text = bid_el.inner_text()
                    nums = re.findall(r'(\d+)', text)
                    if nums:
                        result["proposals"] = int(nums[0])

            browser.close()
            return result

    except (DatabaseError, Exception) as e:
        return {"url": job_url, "error": str(e)}


# === Client Reputation Scoring ===

def score_client(client_info: dict) -> float:
    """Score a client 0-100 based on their platform reputation."""
    score = 50  # Base

    hire_rate = client_info.get("hire_rate", 0)
    if hire_rate >= 80:
        score += 20
    elif hire_rate >= 50:
        score += 10
    elif hire_rate > 0 and hire_rate < 20:
        score -= 15  # Barely hires anyone

    total_spent = client_info.get("total_spent", 0)
    if total_spent >= 50000:
        score += 20
    elif total_spent >= 10000:
        score += 15
    elif total_spent >= 1000:
        score += 5
    elif total_spent == 0:
        score -= 10  # New client, higher risk

    avg_review = client_info.get("avg_review", 0)
    if avg_review >= 4.8:
        score += 10
    elif avg_review >= 4.0:
        score += 5
    elif avg_review > 0 and avg_review < 3.0:
        score -= 15  # Bad reviews

    if client_info.get("payment_verified"):
        score += 5

    jobs = client_info.get("jobs_posted", 0)
    if jobs > 20:
        score += 5  # Experienced client
    elif jobs == 1:
        score -= 5  # First-time poster

    return max(0, min(100, score))


def save_client_score(platform: str, client_id: str, client_info: dict):
    """Save client reputation to database."""
    conn, cur = _get_db()
    s = score_client(client_info)
    cur.execute(
        """INSERT INTO client_scores (platform, client_id, client_name, hire_rate, total_spent,
           avg_review, jobs_posted, payment_verified, country, score)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (platform, client_id) DO UPDATE SET
            hire_rate=%s, total_spent=%s, avg_review=%s, jobs_posted=%s,
            payment_verified=%s, score=%s, scraped_at=NOW()""",
        (platform, client_id, client_info.get("client_name", ""),
         client_info.get("hire_rate", 0), client_info.get("total_spent", 0),
         client_info.get("avg_review", 0), client_info.get("jobs_posted", 0),
         client_info.get("payment_verified", False), client_info.get("country", ""),
         s,
         client_info.get("hire_rate", 0), client_info.get("total_spent", 0),
         client_info.get("avg_review", 0), client_info.get("jobs_posted", 0),
         client_info.get("payment_verified", False), s)
    )
    conn.close()
    return s


# === Enhanced Analysis ===

def analyze_job_v2(job: dict) -> dict:
    """Enhanced job analysis with historical learning and competition data."""
    from bid_strategy import analyze_job as base_analyze

    analysis = base_analyze(job)

    # Enhance with historical win rate
    win_data = get_win_rate(platform=job.get("platform"))
    if win_data["total_bids"] > 5:
        # Bayesian update: adjust win probability based on our track record
        prior = analysis["win_probability"] / 100
        historical = win_data["win_rate"]
        # Weight historical data more as we get more bids
        weight = min(win_data["total_bids"] / 50, 0.7)  # Max 70% weight to history
        posterior = prior * (1 - weight) + historical * weight
        analysis["win_probability"] = posterior * 100
        analysis["historical_win_rate"] = historical
        analysis["historical_bids"] = win_data["total_bids"]

        # Adjust bid based on what won vs lost historically
        if win_data["avg_bid_won"] > 0 and win_data["avg_bid_lost"] > 0:
            # If our winning bids were higher than losing bids, we're underpricing
            if win_data["avg_bid_won"] > win_data["avg_bid_lost"] * 1.1:
                analysis["recommended_bid"] *= 1.1  # Bid higher
                analysis["bid_adjustment"] = "UP — our wins are at higher prices"
            # If winning bids were lower, we might be overpricing
            elif win_data["avg_bid_won"] < win_data["avg_bid_lost"] * 0.9:
                analysis["recommended_bid"] *= 0.95
                analysis["bid_adjustment"] = "DOWN — our wins are at lower prices"

    # Strategy performance
    if win_data.get("by_strategy"):
        best_strategy = max(win_data["by_strategy"].items(),
                          key=lambda x: x[1]["win_rate"] if x[1]["total"] >= 3 else 0,
                          default=("none", {"win_rate": 0}))
        if best_strategy[1].get("win_rate", 0) > 0:
            analysis["best_historical_strategy"] = f"{best_strategy[0]} ({best_strategy[1]['win_rate']:.0%} win rate)"

    # Competition count (if available)
    competition = job.get("proposals", 0) or job.get("competition_count", 0)
    if competition > 0:
        analysis["competition_count"] = competition
        # Adjust strategy based on competition
        if competition > 30:
            analysis["bid_strategy"] = "HIGH COMPETITION — differentiate hard or skip. " + analysis["bid_strategy"]
        elif competition < 5:
            analysis["bid_strategy"] = "LOW COMPETITION — strong position, bid confidently. " + analysis["bid_strategy"]

    # Client score (if available)
    client_score = job.get("client_score", 0)
    if client_score > 0:
        analysis["client_score"] = client_score
        if client_score < 30:
            analysis["bid_strategy"] = "RISKY CLIENT — consider skipping. " + analysis["bid_strategy"]
        elif client_score > 80:
            analysis["bid_strategy"] = "PREMIUM CLIENT — invest in this proposal. " + analysis["bid_strategy"]

    # Recalculate overall with new data
    analysis["overall_score"] = (
        analysis["fit_score"] * 0.25 +
        analysis["win_probability"] * 0.25 +
        analysis["value_score"] * 0.35 +
        (client_score * 0.15 if client_score > 0 else analysis["value_score"] * 0.15)
    )

    return analysis


# === Learning Stats ===

def get_learning_stats() -> dict:
    """Get overall learning statistics."""
    conn, cur = _get_db()

    cur.execute("SELECT COUNT(*) as total, SUM(CASE WHEN outcome='won' THEN 1 ELSE 0 END) as won, SUM(revenue) as rev FROM bid_history WHERE outcome != 'pending'")
    overall = dict(cur.fetchone())

    cur.execute("SELECT platform, COUNT(*) as total, SUM(CASE WHEN outcome='won' THEN 1 ELSE 0 END) as won FROM bid_history WHERE outcome != 'pending' GROUP BY platform")
    by_platform = {r["platform"]: {"total": r["total"], "won": r["won"], "rate": r["won"]/r["total"] if r["total"] > 0 else 0} for r in cur.fetchall()}

    cur.execute("SELECT COUNT(*) as clients FROM client_scores")
    clients = cur.fetchone()["clients"]

    conn.close()

    return {
        "total_bids": overall["total"] or 0,
        "total_won": overall["won"] or 0,
        "win_rate": (overall["won"] or 0) / (overall["total"] or 1),
        "total_revenue": overall["rev"] or 0,
        "by_platform": by_platform,
        "clients_scored": clients,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Game Theory Engine v2")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("stats")
    sub.add_parser("win-rate")

    rec = sub.add_parser("record-outcome")
    rec.add_argument("--bid-id", type=int, required=True)
    rec.add_argument("--outcome", required=True, choices=["won", "lost", "withdrawn", "expired", "no_response"])
    rec.add_argument("--reason", default="")
    rec.add_argument("--revenue", type=float, default=0)

    args = parser.parse_args()

    if args.command == "stats":
        s = get_learning_stats()
        print(f"Total bids: {s['total_bids']}, Won: {s['total_won']}, Rate: {s['win_rate']:.0%}")
        print(f"Revenue: EUR {s['total_revenue']:.2f}")
        print(f"Clients scored: {s['clients_scored']}")
        for p, d in s["by_platform"].items():
            print(f"  {p}: {d['total']} bids, {d['won']} won ({d['rate']:.0%})")
    elif args.command == "win-rate":
        wr = get_win_rate()
        print(f"Win rate: {wr['win_rate']:.0%} ({wr['won']}/{wr['total_bids']})")
        print(f"Avg winning bid: ${wr['avg_bid_won']:.0f}")
        print(f"Avg losing bid: ${wr['avg_bid_lost']:.0f}")
        for s, d in wr["by_strategy"].items():
            print(f"  {s}: {d['win_rate']:.0%} ({d['won']}/{d['total']})")
    elif args.command == "record-outcome":
        record_outcome(args.bid_id, args.outcome, args.reason, args.revenue)
        print(f"Recorded: bid #{args.bid_id} = {args.outcome}")
    else:
        parser.print_help()
