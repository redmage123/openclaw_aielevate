#!/usr/bin/env python3
"""AI Elevate Knowledge Graph

Lightweight graph database built on SQLite — no external dependencies.
Stores entities (nodes) and relationships (edges) with properties.

Entities: customers, companies, deals, projects, agents, tickets, proposals,
          products, features, content, competitors

Relationships: referred_by, escalated_to, worked_on, purchased, mentioned,
               resolved_by, created_by, assigned_to, related_to, part_of

Usage:
    from knowledge_graph import KG

    kg = KG("gigforge")  # or "techuni"

    # Add entities
    kg.add("customer", "john@acme.com", {"name": "John Smith", "company": "Acme"})
    kg.add("deal", "deal-001", {"title": "RAG Pipeline", "value": 5000})
    kg.add("project", "crm", {"name": "CRM Platform", "status": "active"})

    # Add relationships
    kg.link("customer", "john@acme.com", "deal", "deal-001", "owns")
    kg.link("deal", "deal-001", "agent", "gigforge-pm", "managed_by")
    kg.link("customer", "john@acme.com", "customer", "jane@other.com", "referred_by")

    # Query
    kg.get("customer", "john@acme.com")  # Get entity with all relationships
    kg.neighbors("customer", "john@acme.com")  # All connected entities
    kg.path("customer", "john@acme.com", "agent", "gigforge-pm")  # Find connection path
    kg.search("acme")  # Full-text search across all entities
    kg.query_rel("referred_by")  # All referral relationships
    kg.context("customer", "john@acme.com")  # Rich context for AI prompts
"""

import json
import sqlite3
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from contextlib import contextmanager
from exceptions import AiElevateError  # TODO: Use specific exception types

DB_DIR = Path("/opt/ai-elevate/knowledge-graph")
DB_DIR.mkdir(parents=True, exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    key TEXT NOT NULL,
    properties TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(type, key)
);

CREATE TABLE IF NOT EXISTS relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    src_type TEXT NOT NULL,
    src_key TEXT NOT NULL,
    dst_type TEXT NOT NULL,
    dst_key TEXT NOT NULL,
    rel_type TEXT NOT NULL,
    properties TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    UNIQUE(src_type, src_key, dst_type, dst_key, rel_type)
);

CREATE INDEX IF NOT EXISTS idx_entity_type ON entities(type);
CREATE INDEX IF NOT EXISTS idx_entity_key ON entities(key);
CREATE INDEX IF NOT EXISTS idx_rel_src ON relationships(src_type, src_key);
CREATE INDEX IF NOT EXISTS idx_rel_dst ON relationships(dst_type, dst_key);
CREATE INDEX IF NOT EXISTS idx_rel_type ON relationships(rel_type);

CREATE VIRTUAL TABLE IF NOT EXISTS entity_fts USING fts5(
    type, key, properties,
    content='entities', content_rowid='id',
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS entity_ai AFTER INSERT ON entities BEGIN
    INSERT INTO entity_fts(rowid, type, key, properties)
    VALUES (new.id, new.type, new.key, new.properties);
END;
CREATE TRIGGER IF NOT EXISTS entity_ad AFTER DELETE ON entities BEGIN
    INSERT INTO entity_fts(entity_fts, rowid, type, key, properties)
    VALUES ('delete', old.id, old.type, old.key, old.properties);
END;
CREATE TRIGGER IF NOT EXISTS entity_au AFTER UPDATE ON entities BEGIN
    INSERT INTO entity_fts(entity_fts, rowid, type, key, properties)
    VALUES ('delete', old.id, old.type, old.key, old.properties);
    INSERT INTO entity_fts(rowid, type, key, properties)
    VALUES (new.id, new.type, new.key, new.properties);
END;
"""


class KG:
    """Knowledge Graph for an organization."""

    def __init__(self, org: str):
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        self.org = org
        self.db_path = DB_DIR / f"{org}.db"
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    # ── Entities ─────────────────────────────────────────────────────────

    def add(self, entity_type: str, key: str, properties: dict = None) -> dict:
        """Add or update an entity."""
        now = time.time()
        props = json.dumps(properties or {})
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO entities (type, key, properties, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(type, key) DO UPDATE SET
                   properties = json_patch(properties, excluded.properties),
                   updated_at = excluded.updated_at""",
                (entity_type, key, props, now, now)
            )
        return {"type": entity_type, "key": key, "properties": properties}

    def get(self, entity_type: str, key: str) -> Optional[dict]:
        """Get an entity with all its relationships."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM entities WHERE type=? AND key=?",
                (entity_type, key)
            ).fetchone()
            if not row:
                return None

            entity = {
                "type": row["type"],
                "key": row["key"],
                "properties": json.loads(row["properties"]),
                "created_at": row["created_at"],
            }

            # Get outgoing relationships
            rels_out = conn.execute(
                "SELECT * FROM relationships WHERE src_type=? AND src_key=?",
                (entity_type, key)
            ).fetchall()
            entity["relationships_out"] = [
                {"rel": r["rel_type"], "target_type": r["dst_type"],
                 "target_key": r["dst_key"], "properties": json.loads(r["properties"])}
                for r in rels_out
            ]

            # Get incoming relationships
            rels_in = conn.execute(
                "SELECT * FROM relationships WHERE dst_type=? AND dst_key=?",
                (entity_type, key)
            ).fetchall()
            entity["relationships_in"] = [
                {"rel": r["rel_type"], "source_type": r["src_type"],
                 "source_key": r["src_key"], "properties": json.loads(r["properties"])}
                for r in rels_in
            ]

            return entity

    def remove(self, entity_type: str, key: str):
        """Remove an entity and all its relationships."""
        with self._conn() as conn:
            conn.execute("DELETE FROM entities WHERE type=? AND key=?", (entity_type, key))
            conn.execute("DELETE FROM relationships WHERE (src_type=? AND src_key=?) OR (dst_type=? AND dst_key=?)",
                        (entity_type, key, entity_type, key))

    def list_entities(self, entity_type: str = "", limit: int = 100) -> list[dict]:
        """List entities, optionally filtered by type."""
        with self._conn() as conn:
            if entity_type:
                rows = conn.execute("SELECT * FROM entities WHERE type=? ORDER BY updated_at DESC LIMIT ?",
                                   (entity_type, limit)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM entities ORDER BY updated_at DESC LIMIT ?",
                                   (limit,)).fetchall()
        return [{"type": r["type"], "key": r["key"], "properties": json.loads(r["properties"])} for r in rows]

    # ── Relationships ────────────────────────────────────────────────────

    def link(self, src_type: str, src_key: str, dst_type: str, dst_key: str,
             rel_type: str, properties: dict = None):
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        """Create a relationship between two entities."""
        now = time.time()
        props = json.dumps(properties or {})
        with self._conn() as conn:
            # Auto-create entities if they don't exist
            conn.execute(
                "INSERT OR IGNORE INTO entities (type, key, properties, created_at, updated_at) VALUES (?,?,'{}',?,?)",
                (src_type, src_key, now, now))
            conn.execute(
                "INSERT OR IGNORE INTO entities (type, key, properties, created_at, updated_at) VALUES (?,?,'{}',?,?)",
                (dst_type, dst_key, now, now))
            conn.execute(
                """INSERT OR REPLACE INTO relationships (src_type, src_key, dst_type, dst_key, rel_type, properties, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (src_type, src_key, dst_type, dst_key, rel_type, props, now))

    def unlink(self, src_type: str, src_key: str, dst_type: str, dst_key: str, rel_type: str):
        """Remove a relationship."""
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM relationships WHERE src_type=? AND src_key=? AND dst_type=? AND dst_key=? AND rel_type=?",
                (src_type, src_key, dst_type, dst_key, rel_type))

    def neighbors(self, entity_type: str, key: str, rel_type: str = "", depth: int = 1) -> list[dict]:
        """Get all connected entities (both directions)."""
        with self._conn() as conn:
            conditions = "(src_type=? AND src_key=?) OR (dst_type=? AND dst_key=?)"
            params = [entity_type, key, entity_type, key]
            if rel_type:
                conditions = f"({conditions}) AND rel_type=?"
                params.append(rel_type)

            rows = conn.execute("SELECT * FROM relationships WHERE " + conditions, params).fetchall()

        results = []
        for r in rows:
            if r["src_type"] == entity_type and r["src_key"] == key:
                results.append({"direction": "out", "rel": r["rel_type"],
                               "type": r["dst_type"], "key": r["dst_key"],
                               "properties": json.loads(r["properties"])})
            else:
                results.append({"direction": "in", "rel": r["rel_type"],
                               "type": r["src_type"], "key": r["src_key"],
                               "properties": json.loads(r["properties"])})

        # Depth > 1: recursively get neighbors of neighbors
        if depth > 1:
            seen = {(entity_type, key)}
            for n in list(results):
                nkey = (n["type"], n["key"])
                if nkey not in seen:
                    seen.add(nkey)
                    deeper = self.neighbors(n["type"], n["key"], rel_type, depth - 1)
                    for d in deeper:
                        dkey = (d["type"], d["key"])
                        if dkey not in seen:
                            d["depth"] = 2
                            results.append(d)
                            seen.add(dkey)
        return results

    def query_rel(self, rel_type: str, limit: int = 50) -> list[dict]:
        """Get all relationships of a given type."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM relationships WHERE rel_type=? ORDER BY created_at DESC LIMIT ?",
                (rel_type, limit)).fetchall()
        return [{"source": f"{r['src_type']}:{r['src_key']}",
                 "target": f"{r['dst_type']}:{r['dst_key']}",
                 "rel": r["rel_type"],
                 "properties": json.loads(r["properties"])} for r in rows]

    # ── Search ───────────────────────────────────────────────────────────

    def search(self, query: str, entity_type: str = "", limit: int = 20) -> list[dict]:
        """Full-text search across all entities."""
        fts_query = " OR ".join(f'"{w}"' for w in query.split() if w.strip())
        if not fts_query:
            return []
        with self._conn() as conn:
            if entity_type:
                rows = conn.execute(
                    """SELECT e.* FROM entity_fts f JOIN entities e ON e.id = f.rowid
                       WHERE f.entity_fts MATCH ? AND e.type = ? LIMIT ?""",
                    (fts_query, entity_type, limit)).fetchall()
            else:
                rows = conn.execute(
                    """SELECT e.* FROM entity_fts f JOIN entities e ON e.id = f.rowid
                       WHERE f.entity_fts MATCH ? LIMIT ?""",
                    (fts_query, limit)).fetchall()
        return [{"type": r["type"], "key": r["key"], "properties": json.loads(r["properties"])} for r in rows]

    # ── Context Builder (for AI prompts) ─────────────────────────────────

    def context(self, entity_type: str, key: str, max_depth: int = 2) -> str:
        """Build rich context string for an entity — suitable for injecting into AI prompts."""
        entity = self.get(entity_type, key)
        if not entity:
            return f"No data found for {entity_type}:{key}"

        lines = [f"=== {entity_type.upper()}: {key} ==="]
        props = entity["properties"]
        for k, v in props.items():
            lines.append(f"  {k}: {v}")

        if entity["relationships_out"]:
            lines.append("\nConnections (outgoing):")
            for r in entity["relationships_out"]:
                target = self.get(r["target_type"], r["target_key"])
                target_props = target["properties"] if target else {}
                name = target_props.get("name", target_props.get("title", r["target_key"]))
                lines.append(f"  → {r['rel']} → {r['target_type']}:{name}")

        if entity["relationships_in"]:
            lines.append("\nConnections (incoming):")
            for r in entity["relationships_in"]:
                source = self.get(r["source_type"], r["source_key"])
                source_props = source["properties"] if source else {}
                name = source_props.get("name", source_props.get("title", r["source_key"]))
                lines.append(f"  ← {r['rel']} ← {r['source_type']}:{name}")

        # Second-degree connections
        if max_depth >= 2:
            second = self.neighbors(entity_type, key, depth=2)
            deep = [n for n in second if n.get("depth") == 2]
            if deep:
                lines.append(f"\nExtended network ({len(deep)} entities at depth 2):")
                for n in deep[:10]:
                    lines.append(f"  {n['type']}:{n['key']} ({n['rel']})")

        return "\n".join(lines)

    # ── Stats ────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Get graph statistics."""
        with self._conn() as conn:
            entity_count = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
            rel_count = conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0]
            by_type = conn.execute("SELECT type, COUNT(*) as c FROM entities GROUP BY type ORDER BY c DESC").fetchall()
            by_rel = conn.execute("SELECT rel_type, COUNT(*) as c FROM relationships GROUP BY rel_type ORDER BY c DESC").fetchall()
        return {
            "org": self.org,
            "entities": entity_count,
            "relationships": rel_count,
            "entity_types": {r["type"]: r["c"] for r in by_type},
            "relationship_types": {r["rel_type"]: r["c"] for r in by_rel},
        }

    # ── Bulk Import ──────────────────────────────────────────────────────

    def import_from_crm(self):
        """Import entities from CRM data, ticket logs, proposals, etc."""
        import csv
        org = self.org

        # Import from ticket logs
        ticket_path = f"/opt/ai-elevate/{org}/support/ticket-log.csv"
        if os.path.exists(ticket_path):
            with open(ticket_path) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    customer = row.get("customer", "").strip()
                    if customer and "@" in customer:
                        self.add("customer", customer, {"source": "support_ticket"})
                        self.add("ticket", f"ticket-{row.get('timestamp','')[:10]}", {
                            "issue": row.get("issue_summary", ""),
                            "status": row.get("status", ""),
                            "tier": row.get("tier", "1"),
                        })
                        self.link("customer", customer, "ticket",
                                 f"ticket-{row.get('timestamp','')[:10]}", "filed")

        # Import from proposals
        proposals_dir = Path(f"/opt/ai-elevate/{org}/memory/proposals")
        if proposals_dir.exists():
            for pf in proposals_dir.glob("*.md"):
                name = pf.stem
                self.add("proposal", name, {"file": str(pf), "date": name[:10]})

        # Import agents as entities
        agents_dir = Path(f"/home/aielevate/.openclaw/agents")
        for agent_dir in agents_dir.iterdir():
            agent_id = agent_dir.name
            if agent_id.startswith(org) or (org == "gigforge" and not agent_id.startswith("techuni")):
                self.add("agent", agent_id, {"org": org})

        # Import projects
        projects_dir = Path(f"/opt/ai-elevate/{org}/projects") if org == "gigforge" else None
        if projects_dir and projects_dir.exists():
            for proj in projects_dir.iterdir():
                if proj.is_dir():
                    self.add("project", proj.name, {"path": str(proj), "org": org})

    def import_from_health_scores(self):
        """Import customer health data into the graph."""
        health_dir = Path("/opt/ai-elevate/customer-success/health-scores")
        if not health_dir.exists():
            return
        for hf in health_dir.glob("*.json"):
            try:
                with open(hf) as f:
                    data = json.load(f)
                if data.get("org") != self.org:
                    continue
                email = data.get("email", "")
                if email:
                    self.add("customer", email, {
                        "health_score": data.get("score", 75),
                        "ticket_count_30d": data.get("ticket_count_30d", 0),
                    })
            except (AiElevateError, Exception) as e:
                pass


# ── Cross-Org Graph ──────────────────────────────────────────────────────

class CrossOrgKG:
    """Query across both org graphs."""

    def __init__(self):
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        self.gf = KG("gigforge")
        self.tu = KG("techuni")

    def search_all(self, query: str) -> dict:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        return {
            "gigforge": self.gf.search(query),
            "techuni": self.tu.search(query),
        }

    def find_connections(self, entity_type: str, key: str) -> dict:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        return {
            "gigforge": self.gf.neighbors(entity_type, key, depth=2),
            "techuni": self.tu.neighbors(entity_type, key, depth=2),
        }

    def stats(self) -> dict:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        return {
            "gigforge": self.gf.stats(),
            "techuni": self.tu.stats(),
        }


# ── CLI ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Knowledge Graph")
    parser.add_argument("command", choices=["add", "get", "link", "search", "neighbors",
                                             "context", "stats", "import", "query-rel"])
    parser.add_argument("--org", default="gigforge")
    parser.add_argument("--type", default="")
    parser.add_argument("--key", default="")
    parser.add_argument("--dst-type", default="")
    parser.add_argument("--dst-key", default="")
    parser.add_argument("--rel", default="")
    parser.add_argument("--props", default="{}")
    parser.add_argument("--query", "-q", default="")
    parser.add_argument("--depth", type=int, default=1)
    args = parser.parse_args()

    kg = KG(args.org)

    if args.command == "add":
        print(json.dumps(kg.add(args.type, args.key, json.loads(args.props)), indent=2))
    elif args.command == "get":
        print(json.dumps(kg.get(args.type, args.key), indent=2))
    elif args.command == "link":
        kg.link(args.type, args.key, args.dst_type, args.dst_key, args.rel, json.loads(args.props))
        print(f"Linked {args.type}:{args.key} --{args.rel}--> {args.dst_type}:{args.dst_key}")
    elif args.command == "search":
        print(json.dumps(kg.search(args.query, args.type), indent=2))
    elif args.command == "neighbors":
        print(json.dumps(kg.neighbors(args.type, args.key, args.rel, args.depth), indent=2))
    elif args.command == "context":
        print(kg.context(args.type, args.key))
    elif args.command == "stats":
        print(json.dumps(kg.stats(), indent=2))
    elif args.command == "import":
        kg.import_from_crm()
        kg.import_from_health_scores()
        print(json.dumps(kg.stats(), indent=2))
    elif args.command == "query-rel":
        print(json.dumps(kg.query_rel(args.rel), indent=2))
