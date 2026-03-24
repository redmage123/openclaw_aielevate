#!/usr/bin/env python3
"""Customer Context — single-call aggregation of everything we know about a customer.

Stores sentiment, assets, and notes in RAG Postgres (port 5434).
Also syncs writes to KG, RAG collections, and Plane.

Usage:
  from customer_context import get_context, update_sentiment, get_asset_checklist, update_asset
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras
DatabaseError = psycopg2.DatabaseError
from dao import BaseDAO  # TODO: Replace inline DB calls with specific DAOs
from exceptions import AiElevateError  # TODO: Use specific exception types, DatabaseError, KnowledgeGraphError, AgentError

sys.path.insert(0, "/home/aielevate")

DB_CONN = dict(
    host=os.environ.get("DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("DB_PORT", "5434")),
    dbname=os.environ.get("DB_NAME", "rag"),
    user=os.environ.get("DB_USER", "rag"),
    password=os.environ.get("DB_PASSWORD", "rag_vec_2026"),
)


def _get_db():
    conn = psycopg2.connect(**DB_CONN)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Create tables if they don't exist
    cur.execute("""CREATE TABLE IF NOT EXISTS customer_sentiment (
        id SERIAL PRIMARY KEY,
        email TEXT NOT NULL,
        rating TEXT NOT NULL,
        notes TEXT,
        agent TEXT,
        timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS customer_assets (
        email TEXT NOT NULL,
        asset_name TEXT NOT NULL,
        received BOOLEAN DEFAULT FALSE,
        notes TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (email, asset_name)
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS customer_notes (
        id SERIAL PRIMARY KEY,
        email TEXT NOT NULL,
        note TEXT NOT NULL,
        agent TEXT,
        timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sentiment_email ON customer_sentiment(email)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_notes_email ON customer_notes(email)")

    return conn, cur


def _fetch_emails(email: str) -> list:
    """Fetch recent email history for a customer from the local SQLite email index."""
    try:
        import sqlite3
        email_db_path = "/opt/ai-elevate/email-intel/search-index/emails.db"
        if Path(email_db_path).exists():
            edb = sqlite3.connect(email_db_path)
            edb.row_factory = sqlite3.Row
            rows = edb.execute(
                "SELECT direction, timestamp, subject, body, agent FROM emails "
                "WHERE sender=? OR recipients LIKE ? ORDER BY timestamp DESC LIMIT 20",
                (email, f"%{email}%")
            ).fetchall()
            edb.close()
            return [{"direction": r["direction"], "timestamp": str(r["timestamp"])[:19],
                     "subject": r["subject"], "snippet": (r["body"] or "")[:200], "agent": r["agent"]}
                    for r in rows]
    except Exception:
        pass
    return []


def _fetch_proposals(email: str) -> list:
    """Fetch active proposals from the sales pipeline for a customer."""
    try:
        from sales_pipeline import pipeline_status
        return pipeline_status(email).get("proposals", [])
    except Exception:
        return []


def _fetch_previews(email: str) -> list:
    """Fetch active preview deployments for a customer."""
    try:
        from preview_deploy import list_previews
        return [p for p in list_previews() if p.get("customer_email") == email]
    except Exception:
        return []


def _fetch_plane_projects(email: str) -> list:
    """Fetch Plane project tickets referencing a customer email."""
    projects = []
    try:
        from plane_ops import Plane
        for org in ["gigforge", "techuni"]:
            try:
                p = Plane(org)
                for proj in p.projects.keys():
                    try:
                        issues = p.list_issues(proj)
                        if isinstance(issues, dict):
                            issues = issues.get("results", [])
                        if isinstance(issues, list):
                            for i in issues:
                                if email in (i.get("description", "") or ""):
                                    state = i.get("state_detail", {})
                                    projects.append({"org": org, "project": proj,
                                                     "title": i.get("name", ""),
                                                     "state": state.get("name", "unknown") if isinstance(state, dict) else "unknown",
                                                     "id": i.get("id", "")})
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception:
        pass
    return projects


def _fetch_kg_deals(email: str) -> list:
    """Fetch deal records from the knowledge graph for a customer."""
    deals = []
    try:
        from knowledge_graph import KG
        for org in ["gigforge", "techuni"]:
            try:
                results = KG(org).search(email)
                deals.extend(r for r in (results or [])[:5] if isinstance(r, dict))
            except Exception:
                pass
    except Exception:
        pass
    return deals


def _fetch_sentiment(email: str) -> tuple:
    """Fetch sentiment record and history from Postgres. Returns (sentiment_dict, history_list)."""
    try:
        conn, cur = _get_db()
        cur.execute(
            "SELECT rating, notes, agent, timestamp FROM customer_sentiment "
            "WHERE email=%s ORDER BY timestamp DESC LIMIT 10", (email,))
        rows = cur.fetchall()
        conn.close()
        if rows:
            sentiment = {"rating": rows[0]["rating"], "notes": rows[0]["notes"],
                         "as_of": str(rows[0]["timestamp"])[:19]}
            return sentiment, [dict(r) for r in rows]
    except Exception:
        pass
    return None, []


def _fetch_assets(email: str) -> list:
    """Fetch asset checklist from Postgres for a customer."""
    try:
        conn, cur = _get_db()
        cur.execute("SELECT asset_name, received, notes, updated_at FROM customer_assets "
                    "WHERE email=%s ORDER BY asset_name", (email,))
        assets = [dict(r) for r in cur.fetchall()]
        conn.close()
        return assets
    except Exception:
        return []


def _fetch_notes(email: str) -> list:
    """Fetch customer notes from Postgres."""
    try:
        conn, cur = _get_db()
        cur.execute("SELECT note, agent, timestamp FROM customer_notes "
                    "WHERE email=%s ORDER BY timestamp DESC LIMIT 20", (email,))
        notes = [dict(r) for r in cur.fetchall()]
        conn.close()
        return notes
    except Exception:
        return []


def get_context(email: str) -> dict:
    """Pull everything we know about a customer into one dict."""
    sentiment, sentiment_history = _fetch_sentiment(email)
    projects = _fetch_plane_projects(email)
    previews = _fetch_previews(email)

    ctx = {
        "email": email,
        "emails": _fetch_emails(email),
        "deals": _fetch_kg_deals(email),
        "sentiment": sentiment,
        "sentiment_history": sentiment_history,
        "projects": projects,
        "proposals": _fetch_proposals(email),
        "previews": previews,
        "assets": _fetch_assets(email),
        "notes": _fetch_notes(email),
        "active_project": len(projects) > 0 or len(previews) > 0,
        "current_owner": None,
    }

    if ctx["active_project"]:
        ctx["current_owner"] = "gigforge-advocate"
    elif ctx["proposals"]:
        ctx["current_owner"] = "gigforge-sales"

    return ctx


def update_sentiment(email: str, rating: str, notes: str = "", agent: str = "") -> None:
    """Record customer sentiment. Call after every interaction.
    rating: positive, neutral, frustrated, at_risk
    Auto-escalates to CSAT if frustrated or at_risk.
    """
    conn, cur = _get_db()
    cur.execute(
        "INSERT INTO customer_sentiment (email, rating, notes, agent) VALUES (%s, %s, %s, %s)",
        (email, rating, notes, agent)
    )
    conn.close()

    # Auto-escalate if needed
    try:
        from sentiment_escalation import escalate_if_needed
        escalate_if_needed(email, rating, notes, agent)
    except (DatabaseError, Exception) as e:
        pass

    # Sync to KG, RAG, Plane
    _sync_all(email, {"sentiment": rating, "notes": notes, "timestamp": datetime.now(timezone.utc).isoformat()})


def add_note(email: str, note: str, agent: str = "") -> None:
    """Add a freeform note about the customer."""
    conn, cur = _get_db()
    cur.execute(
        "INSERT INTO customer_notes (email, note, agent) VALUES (%s, %s, %s)",
        (email, note, agent)
    )
    conn.close()

    # Sync to KG, RAG, Plane
    _sync_all(email, {"note": note, "timestamp": datetime.now(timezone.utc).isoformat()})


def set_asset_checklist(email: str, assets: list) -> None:
    """Initialize the asset checklist for a customer project."""
    conn, cur = _get_db()
    for asset in assets:
        cur.execute(
            "INSERT INTO customer_assets (email, asset_name, received, notes) VALUES (%s, %s, FALSE, '') ON CONFLICT (email, asset_name) DO NOTHING",
            (email, asset)
        )
    conn.close()


def update_asset(email: str, asset_name: str, received: bool = True, notes: str = "") -> None:
    """Mark an asset as received (or update notes)."""
    conn, cur = _get_db()
    cur.execute(
        "INSERT INTO customer_assets (email, asset_name, received, notes) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (email, asset_name) DO UPDATE SET received=%s, notes=%s, updated_at=NOW()",
        (email, asset_name, received, notes, received, notes)
    )
    conn.close()

    # Sync to KG, RAG, Plane
    _sync_all(email, {"asset_name": asset_name, "received": received, "notes": notes, "timestamp": datetime.now(timezone.utc).isoformat()})


def get_asset_checklist(email: str) -> list:
    """Get asset checklist with status."""
    conn, cur = _get_db()
    cur.execute(
        "SELECT asset_name, received, notes, updated_at FROM customer_assets WHERE email=%s ORDER BY asset_name",
        (email,)
    )
    result = [dict(r) for r in cur.fetchall()]
    conn.close()
    return result


def assets_complete(email: str) -> bool:
    """Check if all tracked assets have been received."""
    checklist = get_asset_checklist(email)
    return len(checklist) > 0 and all(a["received"] for a in checklist)


def context_summary(email: str) -> str:
    """Human-readable summary of customer context for injection into agent prompts."""
    ctx = get_context(email)
    lines = [f"=== Customer Context: {email} ==="]

    if ctx["sentiment"]:
        lines.append(f"Sentiment: {ctx['sentiment']['rating']} ({ctx['sentiment']['notes']}) as of {ctx['sentiment']['as_of']}")

    if ctx["current_owner"]:
        lines.append(f"Current owner: {ctx['current_owner']}")

    if ctx["proposals"]:
        latest = ctx["proposals"][-1]
        lines.append(f"Latest proposal: {latest.get('proposal_id', '?')} — EUR {latest.get('amount', '?')} — status: {latest.get('status', '?')}")

    if ctx["projects"]:
        for p in ctx["projects"]:
            lines.append(f"Active project: [{p['org']}/{p['project']}] {p['title']} — state: {p['state']}")

    if ctx["previews"]:
        for p in ctx["previews"]:
            lines.append(f"Preview: {p.get('url', p.get('direct_url', '?'))} — status: {p.get('status', '?')}")

    if ctx["assets"]:
        received = sum(1 for a in ctx["assets"] if a["received"])
        total = len(ctx["assets"])
        lines.append(f"Assets: {received}/{total} received")
        for a in ctx["assets"]:
            status = "RECEIVED" if a["received"] else "MISSING"
            lines.append(f"  [{status}] {a['asset_name']}" + (f" — {a['notes']}" if a["notes"] else ""))

    if ctx["emails"]:
        lines.append(f"Recent emails: {len(ctx['emails'])} (latest: {ctx['emails'][0]['timestamp']} — {ctx['emails'][0]['subject']})")

    if ctx["notes"]:
        lines.append(f"Notes: {ctx['notes'][0]['note']}")

    return "\n".join(lines)


# --- Cross-system sync ---

def _sync_to_kg(email: str, data: dict, org: str = "gigforge"):
    """Sync customer data to the Knowledge Graph."""
    try:
        from knowledge_graph import KG
        kg = KG(org)
        customer_id = email.replace("@", "_at_").replace(".", "_")
        props = {"email": email, "type": "customer"}
        if "sentiment" in data:
            props["sentiment"] = data["sentiment"]
            props["sentiment_notes"] = data.get("notes", "")
        if "asset_name" in data:
            props[f"asset_{data['asset_name'].replace(' ', '_').lower()}"] = "received" if data.get("received") else "missing"
        if "note" in data:
            props["latest_note"] = data["note"][:200]
        try:
            kg.update("customer", customer_id, props)
        except (DatabaseError, Exception) as e:
            kg.add("customer", customer_id, props)
    except (DatabaseError, Exception) as e:
        pass


def _sync_to_rag(email: str, data: dict, org: str = "gigforge"):
    """Sync customer data to RAG for semantic search."""
    try:
        import urllib.request
        doc_id = f"customer-{email.replace('@', '-at-').replace('.', '-')}"
        text = f"Customer: {email}\n"
        if "sentiment" in data:
            text += f"Sentiment: {data['sentiment']} — {data.get('notes', '')}\n"
        if "asset_name" in data:
            status = "received" if data.get("received") else "missing"
            text += f"Asset {data['asset_name']}: {status}\n"
        if "note" in data:
            text += f"Note: {data['note']}\n"
        payload = json.dumps({
            "documents": [{"id": doc_id, "text": text, "metadata": {"type": "customer", "email": email, "org": org}}]
        }).encode()
        req = urllib.request.Request(
            f"http://localhost:8020/api/v1/collections/{org}-support/documents",
            data=payload, method="POST",
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=5)
    except (AiElevateError, Exception) as e:
        pass


def _sync_to_plane(email: str, data: dict, org: str = "gigforge"):
    """Add a comment to the customer's project ticket in Plane."""
    try:
        from plane_ops import Plane
        p = Plane(org)
        for proj in p.projects.keys():
            try:
                issues = p.list_issues(proj)
                if isinstance(issues, dict):
                    issues = issues.get("results", [])
                if not isinstance(issues, list):
                    continue
                for issue in issues:
                    desc = issue.get("description", "") or ""
                    if email in desc:
                        seq = issue.get("sequence_id")
                        if seq:
                            comment = ""
                            if "sentiment" in data:
                                comment = f"Customer sentiment: {data['sentiment']} — {data.get('notes', '')}"
                            elif "asset_name" in data:
                                status = "RECEIVED" if data.get("received") else "still missing"
                                comment = f"Asset update: {data['asset_name']} — {status}"
                            elif "note" in data:
                                comment = f"Note: {data['note'][:200]}"
                            if comment:
                                p.add_comment(proj, seq, comment)
                            return
            except (DatabaseError, Exception) as e:
                continue
    except (DatabaseError, Exception) as e:
        pass


def _sync_all(email: str, data: dict):
    """Sync customer data to KG, RAG, and Plane."""
    org = "gigforge"
    if "techuni" in email:
        org = "techuni"
    elif "ai-elevate" in email:
        org = "ai-elevate"
    _sync_to_kg(email, data, org)
    _sync_to_rag(email, data, org)
    _sync_to_plane(email, data, org)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Customer Context")
    parser.add_argument("email", nargs="?", help="Customer email")
    parser.add_argument("--summary", action="store_true", help="Print human-readable summary")
    parser.add_argument("--sentiment", help="Update sentiment: positive,neutral,frustrated,at_risk")
    parser.add_argument("--notes", help="Sentiment notes")
    parser.add_argument("--set-assets", nargs="+", help="Initialize asset checklist")
    parser.add_argument("--receive-asset", help="Mark asset as received")
    args = parser.parse_args()

    if not args.email:
        parser.print_help()
    elif args.sentiment:
        update_sentiment(args.email, args.sentiment, args.notes or "")
        print(f"Sentiment updated: {args.sentiment}")
    elif args.set_assets:
        set_asset_checklist(args.email, args.set_assets)
        print(f"Checklist set: {args.set_assets}")
    elif args.receive_asset:
        update_asset(args.email, args.receive_asset)
        print(f"Asset received: {args.receive_asset}")
    elif args.summary:
        print(context_summary(args.email))
    else:
        ctx = get_context(args.email)
        print(json.dumps(ctx, indent=2, default=str))
