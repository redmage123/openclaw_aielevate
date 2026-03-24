#!/usr/bin/env python3
"""Memory Decay System — memories lose fidelity over time.


log = get_logger("memory_decay")

Decay levels:
  Full     — original entity (first 7 days)
  Summary  — top 5 properties (7-30 days without access)
  Essence  — one-line summary (30-90 days)
  Archived — just a hash (90+ days)

Usage:
    python3 memory_decay.py --process         # Apply decay rules
    python3 memory_decay.py --import          # Initialize from KG
    python3 memory_decay.py --stats           # Show decay stats
    python3 memory_decay.py --promote <org> <type> <key>  # Restore to full
"""
import argparse
import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from exceptions import AiElevateError  # TODO: Use specific exception types
from logging_config import get_logger

DECAY_DB = "/opt/ai-elevate/data/memory_decay.db"
KG_DIR = "/opt/ai-elevate/knowledge-graph"
ORGS = ["gigforge", "techuni", "ai-elevate"]

# Decay thresholds (days without access)
THRESHOLDS = {"full": 0, "summary": 7, "essence": 30, "archived": 90}

# High-importance entity types
IMPORTANCE_DEFAULTS = {
    "customer": 0.8, "deal": 0.8, "contract": 0.8,
    "person": 0.7, "company": 0.7,
    "project": 0.6, "ticket": 0.5,
    "system": 0.4, "domain": 0.4,
}


def get_decay_db():
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    os.makedirs(os.path.dirname(DECAY_DB), exist_ok=True)
    conn = sqlite3.connect(DECAY_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS decay_state (
        org TEXT, entity_type TEXT, entity_key TEXT,
        importance REAL DEFAULT 0.5,
        last_accessed TEXT,
        access_count INTEGER DEFAULT 0,
        decay_level TEXT DEFAULT 'full',
        summary TEXT, essence TEXT, content_hash TEXT,
        PRIMARY KEY(org, entity_type, entity_key)
    )""")
    conn.commit()
    return conn


def get_kg_conn(org):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    db = os.path.join(KG_DIR, f"{org}.db")
    if not os.path.exists(db):
        return None
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def import_from_kg():
    """Initialize decay state from existing KG entities."""
    decay = get_decay_db()
    now = datetime.now(timezone.utc).isoformat()
    total = 0
    for org in ORGS:
        kg = get_kg_conn(org)
        if not kg:
            continue
        entities = kg.execute("SELECT type, key, properties FROM entities").fetchall()
        # Count relationships per entity
        rels = {}
        for r in kg.execute("SELECT src_type, src_key FROM relationships").fetchall():
            k = f"{r['src_type']}:{r['src_key']}"
            rels[k] = rels.get(k, 0) + 1
        for r in kg.execute("SELECT dst_type, dst_key FROM relationships").fetchall():
            k = f"{r['dst_type']}:{r['dst_key']}"
            rels[k] = rels.get(k, 0) + 1

        for e in entities:
            importance = IMPORTANCE_DEFAULTS.get(e["type"], 0.5)
            rel_count = rels.get(f"{e['type']}:{e['key']}", 0)
            importance = min(importance + rel_count * 0.1, 1.0)
            props = e["properties"] or "{}"
            content_hash = hashlib.sha256(props.encode()).hexdigest()

            decay.execute("""INSERT OR IGNORE INTO decay_state
                (org, entity_type, entity_key, importance, last_accessed, access_count, decay_level, content_hash)
                VALUES (?, ?, ?, ?, ?, 1, 'full', ?)""",
                (org, e["type"], e["key"], importance, now, content_hash))
            total += 1
        kg.close()
    decay.commit()
    decay.close()
    log.info("Imported %s entities", extra={"total": total})


def compress_to_summary(properties_json):
    """Keep top 5 most important properties."""
    try:
        props = json.loads(properties_json)
        if not isinstance(props, dict):
            return properties_json
        # Priority keys
        priority = ["name", "title", "email", "status", "role", "company", "value", "priority"]
        kept = {}
        for k in priority:
            if k in props:
                kept[k] = props[k]
        for k, v in props.items():
            if len(kept) >= 5:
                break
            if k not in kept:
                kept[k] = v
        return json.dumps(kept)
    except:
        return properties_json


def compress_to_essence(entity_type, entity_key, properties_json):
    """One-line summary."""
    try:
        props = json.loads(properties_json)
        best_prop = ""
        for k in ["name", "title", "status", "email", "role"]:
            if k in props:
                best_prop = f"{k}={props[k]}"
                break
        if not best_prop and props:
            k, v = next(iter(props.items()))
            best_prop = f"{k}={v}"
        return f"{entity_type}: {entity_key} -- {best_prop}"
    except:
        return f"{entity_type}: {entity_key}"


def process_decay():
    """Apply decay rules to all entities."""
    decay = get_decay_db()
    now = datetime.now(timezone.utc)
    stats = {"full": 0, "summary": 0, "essence": 0, "archived": 0, "promoted": 0}

    rows = decay.execute("SELECT * FROM decay_state").fetchall()
    for row in rows:
        last_accessed = row["last_accessed"] or now.isoformat()
        try:
            la = datetime.fromisoformat(last_accessed.replace("Z", "+00:00"))
        except:
            la = now
        days_since = (now - la).days
        importance = row["importance"] or 0.5
        current_level = row["decay_level"] or "full"

        # Importance slows decay: multiply days threshold by (1 + importance)
        adjusted_days = days_since / (1 + importance)

        if adjusted_days >= THRESHOLDS["archived"] and current_level != "archived":
            new_level = "archived"
        elif adjusted_days >= THRESHOLDS["essence"] and current_level not in ("archived", "essence"):
            new_level = "essence"
        elif adjusted_days >= THRESHOLDS["summary"] and current_level == "full":
            new_level = "summary"
        else:
            new_level = current_level

        if new_level != current_level:
            # Compress
            kg = get_kg_conn(row["org"])
            if kg:
                entity = kg.execute("SELECT properties FROM entities WHERE type = ? AND key = ?",
                    (row["entity_type"], row["entity_key"])).fetchone()
                kg.close()
                if entity:
                    props = entity["properties"] or "{}"
                    summary = compress_to_summary(props) if new_level in ("summary", "essence", "archived") else None
                    essence = compress_to_essence(row["entity_type"], row["entity_key"], props) if new_level in ("essence", "archived") else None
                    content_hash = hashlib.sha256(props.encode()).hexdigest() if new_level == "archived" else row["content_hash"]

                    decay.execute("""UPDATE decay_state SET decay_level = ?, summary = ?, essence = ?, content_hash = ?
                        WHERE org = ? AND entity_type = ? AND entity_key = ?""",
                        (new_level, summary, essence, content_hash,
                         row["org"], row["entity_type"], row["entity_key"]))

        stats[new_level] = stats.get(new_level, 0) + 1

    decay.commit()
    decay.close()
    print(f"Decay processed: {sum(stats.values())} entities — " +
          ", ".join(f"{k}: {v}" for k, v in stats.items() if v))


def promote(org, entity_type, entity_key):
    """Promote a decayed entity back to full resolution."""
    decay = get_decay_db()
    now = datetime.now(timezone.utc).isoformat()
    decay.execute("""UPDATE decay_state SET decay_level = 'full', last_accessed = ?,
        access_count = access_count + 1 WHERE org = ? AND entity_type = ? AND entity_key = ?""",
        (now, org, entity_type, entity_key))
    decay.commit()
    decay.close()
    log.info("Promoted %s/%s:%s to full", extra={"org": org, "entity_type": entity_type, "entity_key": entity_key})


def show_stats():
    """Show decay statistics."""
    decay = get_decay_db()
    print("=" * 50)
    log.info("MEMORY DECAY — STATISTICS")
    print("=" * 50)
    for org in ORGS:
        rows = decay.execute(
            "SELECT decay_level, COUNT(*) as cnt FROM decay_state WHERE org = ? GROUP BY decay_level",
            (org,)).fetchall()
        if rows:
            parts = [f"{r['decay_level']}: {r['cnt']}" for r in rows]
            total = sum(r["cnt"] for r in rows)
            log.info("  {org:15s} {total:4d} total — {', '.join(parts)}")
    decay.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Memory Decay System")
    parser.add_argument("--process", action="store_true", help="Apply decay rules")
    parser.add_argument("--import", dest="do_import", action="store_true", help="Initialize from KG")
    parser.add_argument("--stats", action="store_true", help="Show stats")
    parser.add_argument("--promote", nargs=3, metavar=("ORG", "TYPE", "KEY"), help="Promote to full")
    args = parser.parse_args()

    if args.do_import:
        import_from_kg()
    elif args.process:
        process_decay()
    elif args.stats:
        show_stats()
    elif args.promote:
        promote(*args.promote)
    else:
        parser.print_help()
