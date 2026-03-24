#!/usr/bin/env python3
"""Bid Learning Store — extracts, stores, and retrieves patterns from bid history.

Not just raw data — this stores learned patterns:
  "When we bid on React dashboards at 85% of budget with CryptoAdvisor as portfolio
   proof, we win 67% of the time on Upwork"

Runs pattern extraction after each outcome is recorded.
Agents query patterns before writing proposals.

Usage:
  from bid_learning import extract_patterns, get_patterns_for, record_and_learn

  # Record outcome and extract new patterns
  record_and_learn(bid_id=5, outcome="won", reason="Client loved the CryptoAdvisor demo", revenue=4500)

  # Get relevant patterns for a new job
  patterns = get_patterns_for(platform="upwork", skills=["react", "dashboard"], budget_range=(3000, 8000))
  for p in patterns:
      print(f"{p['pattern']} — confidence: {p['confidence']:.0%}")
"""

import os
import sys
from datetime import datetime, timezone
from collections import defaultdict

import psycopg2
import psycopg2.extras

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
    cur.execute("""CREATE TABLE IF NOT EXISTS bid_patterns (
        id SERIAL PRIMARY KEY,
        pattern_type TEXT NOT NULL,
        pattern TEXT NOT NULL,
        dimension TEXT,
        dimension_value TEXT,
        sample_size INTEGER DEFAULT 0,
        win_count INTEGER DEFAULT 0,
        confidence REAL DEFAULT 0,
        avg_bid_won REAL DEFAULT 0,
        avg_bid_lost REAL DEFAULT 0,
        insight TEXT,
        updated_at TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(pattern_type, dimension, dimension_value)
    )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_patterns_type ON bid_patterns(pattern_type)")
    return conn, cur


def extract_patterns():
    """Extract patterns from bid_history and store in bid_patterns.
    Call after recording an outcome."""
    conn, cur = _get_db()

    # Only analyze resolved bids
    cur.execute("SELECT * FROM bid_history WHERE outcome IN ('won', 'lost') ORDER BY submitted_at")
    bids = [dict(r) for r in cur.fetchall()]

    if len(bids) < 3:
        conn.close()
        return []

    patterns = []

    # === Pattern 1: Win rate by platform ===
    by_platform = defaultdict(lambda: {"won": 0, "lost": 0, "bids_won": [], "bids_lost": []})
    for b in bids:
        p = b["platform"] or "unknown"
        if b["outcome"] == "won":
            by_platform[p]["won"] += 1
            by_platform[p]["bids_won"].append(b["our_bid"] or 0)
        else:
            by_platform[p]["lost"] += 1
            by_platform[p]["bids_lost"].append(b["our_bid"] or 0)

    for platform, data in by_platform.items():
        total = data["won"] + data["lost"]
        if total >= 2:
            rate = data["won"] / total
            avg_won = sum(data["bids_won"]) / len(data["bids_won"]) if data["bids_won"] else 0
            avg_lost = sum(data["bids_lost"]) / len(data["bids_lost"]) if data["bids_lost"] else 0
            insight = f"Win rate on {platform}: {rate:.0%} ({data['won']}/{total})"
            if avg_won > 0 and avg_lost > 0:
                if avg_won > avg_lost:
                    insight += f". Winning bids average ${avg_won:.0f} vs losing ${avg_lost:.0f} — we win at higher prices."
                else:
                    insight += f". Winning bids average ${avg_won:.0f} vs losing ${avg_lost:.0f} — price competitive wins."

            _upsert_pattern(cur, "platform_rate", f"Win rate on {platform}", "platform", platform,
                           total, data["won"], rate, avg_won, avg_lost, insight)
            patterns.append(insight)

    # === Pattern 2: Win rate by strategy ===
    by_strategy = defaultdict(lambda: {"won": 0, "lost": 0})
    for b in bids:
        s = (b["strategy"] or "")[:30]
        if s:
            by_strategy[s]["won" if b["outcome"] == "won" else "lost"] += 1

    for strategy, data in by_strategy.items():
        total = data["won"] + data["lost"]
        if total >= 2:
            rate = data["won"] / total
            insight = f"Strategy '{strategy}': {rate:.0%} win rate ({data['won']}/{total})"
            _upsert_pattern(cur, "strategy_rate", insight, "strategy", strategy,
                           total, data["won"], rate, 0, 0, insight)
            patterns.append(insight)

    # === Pattern 3: Win rate by budget range ===
    budget_tiers = {"under_500": (0, 500), "500_2000": (500, 2000), "2000_5000": (2000, 5000), "over_5000": (5000, 999999)}
    by_budget = defaultdict(lambda: {"won": 0, "lost": 0})
    for b in bids:
        avg = ((b["client_budget_min"] or 0) + (b["client_budget_max"] or 0)) / 2
        for tier, (lo, hi) in budget_tiers.items():
            if lo <= avg < hi:
                by_budget[tier]["won" if b["outcome"] == "won" else "lost"] += 1
                break

    for tier, data in by_budget.items():
        total = data["won"] + data["lost"]
        if total >= 2:
            rate = data["won"] / total
            label = tier.replace("_", "-").replace("under-", "<$").replace("over-", ">$")
            insight = f"Budget {label}: {rate:.0%} win rate ({data['won']}/{total})"
            _upsert_pattern(cur, "budget_rate", insight, "budget_tier", tier,
                           total, data["won"], rate, 0, 0, insight)
            patterns.append(insight)

    # === Pattern 4: Win rate by fit score tier ===
    by_fit = defaultdict(lambda: {"won": 0, "lost": 0})
    for b in bids:
        fit = b["fit_score"] or 0
        tier = "high" if fit >= 60 else "medium" if fit >= 30 else "low"
        by_fit[tier]["won" if b["outcome"] == "won" else "lost"] += 1

    for tier, data in by_fit.items():
        total = data["won"] + data["lost"]
        if total >= 2:
            rate = data["won"] / total
            insight = f"Fit score {tier}: {rate:.0%} win rate ({data['won']}/{total})"
            _upsert_pattern(cur, "fit_rate", insight, "fit_tier", tier,
                           total, data["won"], rate, 0, 0, insight)
            patterns.append(insight)

    # === Pattern 5: Win rate by proposal angle ===
    by_angle = defaultdict(lambda: {"won": 0, "lost": 0})
    for b in bids:
        angle = (b["proposal_angle"] or "")[:40]
        if angle:
            by_angle[angle]["won" if b["outcome"] == "won" else "lost"] += 1

    for angle, data in by_angle.items():
        total = data["won"] + data["lost"]
        if total >= 2:
            rate = data["won"] / total
            insight = f"Angle '{angle}': {rate:.0%} ({data['won']}/{total})"
            _upsert_pattern(cur, "angle_rate", insight, "angle", angle,
                           total, data["won"], rate, 0, 0, insight)
            patterns.append(insight)

    # === Pattern 6: Client score correlation ===
    by_client = defaultdict(lambda: {"won": 0, "lost": 0})
    for b in bids:
        cs = b.get("client_score", 0) or 0
        tier = "premium" if cs >= 70 else "standard" if cs >= 40 else "risky" if cs > 0 else "unknown"
        by_client[tier]["won" if b["outcome"] == "won" else "lost"] += 1

    for tier, data in by_client.items():
        total = data["won"] + data["lost"]
        if total >= 2:
            rate = data["won"] / total
            insight = f"Client tier '{tier}': {rate:.0%} win rate ({data['won']}/{total})"
            _upsert_pattern(cur, "client_rate", insight, "client_tier", tier,
                           total, data["won"], rate, 0, 0, insight)
            patterns.append(insight)

    # === Pattern 7: Competition impact ===
    by_comp = defaultdict(lambda: {"won": 0, "lost": 0})
    for b in bids:
        comp = b.get("competition_count", 0) or 0
        tier = "low" if comp < 10 else "medium" if comp < 25 else "high"
        if comp > 0:
            by_comp[tier]["won" if b["outcome"] == "won" else "lost"] += 1

    for tier, data in by_comp.items():
        total = data["won"] + data["lost"]
        if total >= 2:
            rate = data["won"] / total
            insight = f"Competition {tier}: {rate:.0%} win rate ({data['won']}/{total})"
            _upsert_pattern(cur, "competition_rate", insight, "competition", tier,
                           total, data["won"], rate, 0, 0, insight)
            patterns.append(insight)

    conn.close()
    return patterns


def _upsert_pattern(cur, ptype, pattern, dim, dim_val, sample, won, conf, avg_won, avg_lost, insight):
    cur.execute(
        """INSERT INTO bid_patterns (pattern_type, pattern, dimension, dimension_value,
           sample_size, win_count, confidence, avg_bid_won, avg_bid_lost, insight)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (pattern_type, dimension, dimension_value) DO UPDATE SET
            pattern=%s, sample_size=%s, win_count=%s, confidence=%s,
            avg_bid_won=%s, avg_bid_lost=%s, insight=%s, updated_at=NOW()""",
        (ptype, pattern, dim, dim_val, sample, won, conf, avg_won, avg_lost, insight,
         pattern, sample, won, conf, avg_won, avg_lost, insight)
    )


def get_patterns_for(platform: str = "", skills: list = None, budget_range: tuple = None) -> list:
    """Get relevant learned patterns for a job."""
    conn, cur = _get_db()

    patterns = []

    # Get all patterns sorted by confidence
    cur.execute("SELECT * FROM bid_patterns WHERE sample_size >= 2 ORDER BY confidence DESC")
    all_patterns = [dict(r) for r in cur.fetchall()]

    # Filter by relevance
    for p in all_patterns:
        relevant = False

        if p["dimension"] == "platform" and platform and p["dimension_value"] == platform:
            relevant = True
        elif p["pattern_type"] in ("strategy_rate", "angle_rate", "fit_rate"):
            relevant = True  # Always relevant
        elif p["dimension"] == "budget_tier" and budget_range:
            avg = sum(budget_range) / 2
            tier_map = {"under_500": 250, "500_2000": 1250, "2000_5000": 3500, "over_5000": 7500}
            tier_avg = tier_map.get(p["dimension_value"], 0)
            if abs(avg - tier_avg) < 3000:
                relevant = True
        elif p["dimension"] in ("client_tier", "competition"):
            relevant = True

        if relevant:
            patterns.append(p)

    conn.close()
    return patterns[:10]


def record_and_learn(bid_id: int, outcome: str, reason: str = "", revenue: float = 0) -> list:
    """Record an outcome and extract new patterns."""
    from game_theory_v2 import record_outcome
    record_outcome(bid_id, outcome, reason, revenue)
    return extract_patterns()


def patterns_summary() -> str:
    """Human-readable summary of all learned patterns."""
    conn, cur = _get_db()
    cur.execute("SELECT * FROM bid_patterns WHERE sample_size >= 2 ORDER BY confidence DESC")
    patterns = [dict(r) for r in cur.fetchall()]
    conn.close()

    if not patterns:
        return "No patterns learned yet. Need at least 3 resolved bids."

    lines = ["=== Learned Bid Patterns ===\n"]
    for p in patterns:
        lines.append(f"  [{p['confidence']:.0%}] {p['insight']}")

    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Bid Learning Store")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("patterns")
    sub.add_parser("extract")

    learn = sub.add_parser("learn")
    learn.add_argument("--bid-id", type=int, required=True)
    learn.add_argument("--outcome", required=True)
    learn.add_argument("--reason", default="")
    learn.add_argument("--revenue", type=float, default=0)

    args = parser.parse_args()
    if args.command == "patterns":
        print(patterns_summary())
    elif args.command == "extract":
        p = extract_patterns()
        print(f"Extracted {len(p)} patterns")
        for pat in p:
            print(f"  {pat}")
    elif args.command == "learn":
        p = record_and_learn(args.bid_id, args.outcome, args.reason, args.revenue)
        print(f"Recorded + extracted {len(p)} patterns")
    else:
        parser.print_help()
