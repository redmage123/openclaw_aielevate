#!/usr/bin/env python3
"""Procedural Memory - tracks what approaches worked and what didn't across all agents.


log = get_logger("procedural_memory")

Structured "lessons learned" that agents can query before attempting a task.

Usage:
  procedural_memory.py record <org> <agent_id> <task_type> <approach> <outcome> <effectiveness> [--context JSON] [--lessons TEXT] [--tags JSON]
  procedural_memory.py query <task_type> [--tags JSON]
  procedural_memory.py best <task_type>
  procedural_memory.py patterns <task_type>
  procedural_memory.py update-pattern <task_type> <pattern> [--success|--failure]
  procedural_memory.py --report
  procedural_memory.py --analyze   (weekly analysis: discover patterns, generate report)
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from logging_config import get_logger

PROC_DB = "/opt/ai-elevate/data/procedural_memory.db"


def get_db():
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    os.makedirs(os.path.dirname(PROC_DB), exist_ok=True)
    conn = sqlite3.connect(PROC_DB)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS procedures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            org TEXT,
            agent_id TEXT,
            task_type TEXT,
            approach TEXT,
            outcome TEXT,
            effectiveness REAL,
            context TEXT,
            lessons TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            tags TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_type TEXT,
            pattern TEXT,
            success_count INTEGER DEFAULT 0,
            failure_count INTEGER DEFAULT 0,
            confidence REAL DEFAULT 0.5,
            last_used TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return conn


def record(org, agent_id, task_type, approach, outcome, effectiveness,
           context=None, lessons=None, tags=None):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Log a procedure attempt and its outcome."""
    conn = get_db()
    conn.execute(
        "INSERT INTO procedures "
        "(org, agent_id, task_type, approach, outcome, effectiveness, "
        "context, lessons, tags) VALUES (?,?,?,?,?,?,?,?,?)",
        (org, agent_id, task_type, approach, outcome, effectiveness,
         json.dumps(context) if context else None,
         lessons,
         json.dumps(tags) if tags else None))
    conn.commit()
    conn.close()
    log.info("[record] %s/%s: %s -> %s (eff=%s)", extra={"org": org, "agent_id": agent_id, "task_type": task_type})


def query(task_type, tags=None):
    """Find relevant procedures for a task type."""
    conn = get_db()
    if tags:
        # Filter by tags using JSON containment check
        rows = conn.execute(
            "SELECT org, agent_id, approach, outcome, effectiveness, lessons, tags, created_at "
            "FROM procedures WHERE task_type=? ORDER BY effectiveness DESC, created_at DESC",
            (task_type,)).fetchall()
        tag_set = set(tags)
        results = []
        for row in rows:
            row_tags = json.loads(row[6]) if row[6] else []
            if tag_set.intersection(set(row_tags)):
                results.append(row)
    else:
        results = conn.execute(
            "SELECT org, agent_id, approach, outcome, effectiveness, lessons, tags, created_at "
            "FROM procedures WHERE task_type=? ORDER BY effectiveness DESC, created_at DESC",
            (task_type,)).fetchall()
    conn.close()
    return results


def best_approach(task_type):
    """Return the highest-effectiveness approach for a task type."""
    conn = get_db()
    row = conn.execute(
        "SELECT approach, effectiveness, lessons, outcome, agent_id "
        "FROM procedures WHERE task_type=? AND outcome='success' "
        "ORDER BY effectiveness DESC LIMIT 1",
        (task_type,)).fetchone()
    conn.close()
    if row:
        return {
            "approach": row[0],
            "effectiveness": row[1],
            "lessons": row[2],
            "outcome": row[3],
            "agent_id": row[4],
        }
    return None


def patterns_for(task_type):
    """Return learned patterns for a task type."""
    conn = get_db()
    rows = conn.execute(
        "SELECT pattern, success_count, failure_count, confidence, last_used "
        "FROM patterns WHERE task_type=? ORDER BY confidence DESC",
        (task_type,)).fetchall()
    conn.close()
    return [{"pattern": r[0], "success_count": r[1], "failure_count": r[2],
             "confidence": r[3], "last_used": r[4]} for r in rows]


def update_pattern(task_type, pattern, success=True):
    """Update pattern confidence based on new evidence."""
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    existing = conn.execute(
        "SELECT id, success_count, failure_count FROM patterns "
        "WHERE task_type=? AND pattern=?",
        (task_type, pattern)).fetchone()
    if existing:
        pid, sc, fc = existing
        if success:
            sc += 1
        else:
            fc += 1
        total = sc + fc
        confidence = sc / total if total > 0 else 0.5
        conn.execute(
            "UPDATE patterns SET success_count=?, failure_count=?, confidence=?, last_used=? "
            "WHERE id=?",
            (sc, fc, confidence, now, pid))
    else:
        sc = 1 if success else 0
        fc = 0 if success else 1
        confidence = 1.0 if success else 0.0
        conn.execute(
            "INSERT INTO patterns (task_type, pattern, success_count, failure_count, confidence, last_used) "
            "VALUES (?,?,?,?,?,?)",
            (task_type, pattern, sc, fc, confidence, now))
    conn.commit()
    conn.close()
    status = "success" if success else "failure"
    log.info("[pattern] %s: '%s' updated (%s, confidence=%s)", extra={"task_type": task_type, "pattern": pattern, "status": status})


def cmd_report(args):
    """Show effectiveness stats per task type."""
    conn = get_db()
    rows = conn.execute("""
        SELECT task_type,
            count(*) as total,
            count(CASE WHEN outcome='success' THEN 1 END) as successes,
            count(CASE WHEN outcome='failure' THEN 1 END) as failures,
            count(CASE WHEN outcome='partial' THEN 1 END) as partials,
            avg(effectiveness) as avg_eff,
            max(effectiveness) as max_eff
        FROM procedures GROUP BY task_type ORDER BY avg_eff DESC
    """).fetchall()
    if not rows:
        log.info("[report] No procedures recorded yet.")
        return
    log.info("=== Procedural Memory Report ===\n")
    for tt, total, succ, fail, part, avg_e, max_e in rows:
        sr = (succ / total * 100) if total > 0 else 0
        log.info("  %s:", extra={"tt": tt})
        log.info("    Total: %s | Success: %s | Failure: %s | Partial: %s", extra={"total": total, "succ": succ, "fail": fail})
        log.info("    Success rate: {sr:.0f}% | Avg effectiveness: {avg_e:.2f} | Max: {max_e:.2f}")

    # Show top patterns
    patterns = conn.execute(
        "SELECT task_type, pattern, confidence, success_count, failure_count "
        "FROM patterns WHERE confidence >= 0.6 ORDER BY confidence DESC LIMIT 10"
    ).fetchall()
    if patterns:
        log.info("\n--- High-Confidence Patterns ---")
        for tt, pat, conf, sc, fc in patterns:
            log.info("  [%s] %s (confidence=%s, %sS/%sF)", extra={"tt": tt, "pat": pat, "sc": sc})

    # Per-agent stats
    agents = conn.execute("""
        SELECT agent_id, count(*) as total,
            avg(effectiveness) as avg_eff,
            count(CASE WHEN outcome='success' THEN 1 END) as successes
        FROM procedures GROUP BY agent_id ORDER BY avg_eff DESC
    """).fetchall()
    if agents:
        log.info("\n--- Agent Performance ---")
        for aid, total, avg_e, succ in agents:
            sr = (succ / total * 100) if total > 0 else 0
            log.info("  %s: %s procedures, %s% success, avg_eff=%s", extra={"aid": aid, "total": total})
    conn.close()


def cmd_analyze(args):
    """Weekly analysis: discover new patterns from procedure data."""
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()

    # Find approaches that succeeded 3+ times for the same task_type
    candidates = conn.execute("""
        SELECT task_type, approach, count(*) as cnt, avg(effectiveness) as avg_eff
        FROM procedures
        WHERE outcome='success' AND effectiveness >= 0.7
        GROUP BY task_type, approach
        HAVING cnt >= 3
        ORDER BY avg_eff DESC
    """).fetchall()

    new_patterns = 0
    for tt, approach, cnt, avg_eff in candidates:
        # Check if pattern already exists
        existing = conn.execute(
            "SELECT id FROM patterns WHERE task_type=? AND pattern LIKE ?",
            (tt, f"%{approach[:50]}%")).fetchone()
        if not existing:
            pattern = f"When doing '{tt}', use approach: {approach} (avg effectiveness: {avg_eff:.2f})"
            conn.execute(
                "INSERT INTO patterns (task_type, pattern, success_count, failure_count, confidence, last_used) "
                "VALUES (?,?,?,0,?,?)",
                (tt, pattern, cnt, min(avg_eff, 1.0), now))
            new_patterns += 1
            log.info("  New pattern: [%s] %s", extra={"tt": tt, "pattern": pattern})

    # Also identify failure patterns (approaches that failed 3+ times)
    fail_candidates = conn.execute("""
        SELECT task_type, approach, count(*) as cnt
        FROM procedures
        WHERE outcome='failure'
        GROUP BY task_type, approach
        HAVING cnt >= 3
    """).fetchall()

    for tt, approach, cnt in fail_candidates:
        existing = conn.execute(
            "SELECT id FROM patterns WHERE task_type=? AND pattern LIKE ?",
            (tt, f"%AVOID%{approach[:30]}%")).fetchone()
        if not existing:
            pattern = f"AVOID for '{tt}': approach '{approach}' failed {cnt} times"
            conn.execute(
                "INSERT INTO patterns (task_type, pattern, success_count, failure_count, confidence, last_used) "
                "VALUES (?,?,0,?,?,?)",
                (tt, pattern, cnt, 0.1, now))
            new_patterns += 1
            log.info("  Anti-pattern: [%s] %s", extra={"tt": tt, "pattern": pattern})

    conn.commit()
    conn.close()

    log.info("\n[analyze] Discovered %s new patterns.", extra={"new_patterns": new_patterns})
    # Also generate the report
    cmd_report(args)


def main():
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    parser = argparse.ArgumentParser(description="Procedural Memory System")
    sub = parser.add_subparsers(dest="command")

    # record subcommand
    rec = sub.add_parser("record", help="Log a procedure")
    rec.add_argument("org")
    rec.add_argument("agent_id")
    rec.add_argument("task_type")
    rec.add_argument("approach")
    rec.add_argument("outcome", choices=["success", "failure", "partial"])
    rec.add_argument("effectiveness", type=float)
    rec.add_argument("--context", default=None, help="JSON context string")
    rec.add_argument("--lessons", default=None)
    rec.add_argument("--tags", default=None, help="JSON array of tags")

    # query subcommand
    q = sub.add_parser("query", help="Find procedures for a task type")
    q.add_argument("task_type")
    q.add_argument("--tags", default=None, help="JSON array of tags to filter by")

    # best subcommand
    b = sub.add_parser("best", help="Best approach for a task type")
    b.add_argument("task_type")

    # patterns subcommand
    p = sub.add_parser("patterns", help="Show patterns for a task type")
    p.add_argument("task_type")

    # update-pattern subcommand
    up = sub.add_parser("update-pattern", help="Update a pattern")
    up.add_argument("task_type")
    up.add_argument("pattern")
    up.add_argument("--success", action="store_true", default=True)
    up.add_argument("--failure", action="store_true")

    # report and analyze flags
    parser.add_argument("--report", action="store_true", help="Show stats report")
    parser.add_argument("--analyze", action="store_true", help="Weekly analysis")

    args = parser.parse_args()

    if args.report:
        cmd_report(args)
    elif args.analyze:
        cmd_analyze(args)
    elif args.command == "record":
        ctx = json.loads(args.context) if args.context else None
        tgs = json.loads(args.tags) if args.tags else None
        record(args.org, args.agent_id, args.task_type, args.approach,
               args.outcome, args.effectiveness, ctx, args.lessons, tgs)
    elif args.command == "query":
        tgs = json.loads(args.tags) if args.tags else None
        results = query(args.task_type, tgs)
        if not results:
            log.info("No procedures found for task type '{args.task_type}'")
        else:
            for r in results:
                org, aid, approach, outcome, eff, lessons, tags, created = r
                log.info("  [%s] eff=%s by %s@%s: %s", extra={"outcome": outcome, "aid": aid, "org": org})
                if lessons:
                    log.info("    Lessons: %s", extra={"lessons": lessons})
    elif args.command == "best":
        result = best_approach(args.task_type)
        if result:
            log.info("Best approach for '{args.task_type}':")
            log.info("  Approach: {result['approach']}")
            log.info("  Effectiveness: {result['effectiveness']:.2f}")
            log.info("  Agent: {result['agent_id']}")
            if result['lessons']:
                log.info("  Lessons: {result['lessons']}")
        else:
            log.info("No successful procedures found for '{args.task_type}'")
    elif args.command == "patterns":
        pats = patterns_for(args.task_type)
        if not pats:
            log.info("No patterns found for '{args.task_type}'")
        else:
            for p in pats:
                log.info("  [{p['confidence']:.2f}] {p['pattern']} ({p['success_count']}S/{p['failure_count']}F)")
    elif args.command == "update-pattern":
        success = not args.failure
        update_pattern(args.task_type, args.pattern, success)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
