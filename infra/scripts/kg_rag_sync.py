#!/usr/bin/env python3
"""
KG → RAG Sync — persists knowledge graph entities to the RAG database.

The KG (SQLite + FTS5) is the fast in-memory-like store queried first for low latency.
The RAG DB (ChromaDB via the RAG service) is the persistent semantic search layer.

This script:
1. Reads all entities and relationships from each org's KG
2. Formats them as searchable documents
3. Ingests into the RAG service's "knowledge-graph" collection
4. Tracks what's been synced to avoid duplicates (via updated_at timestamps)

Usage:
    python3 kg_rag_sync.py --sync        # Sync all orgs
    python3 kg_rag_sync.py --sync gigforge  # Sync one org
    python3 kg_rag_sync.py --status      # Show sync status
"""

import argparse
import json
import os
import sqlite3
import ssl
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

KG_DIR = "/opt/ai-elevate/knowledge-graph"
STATE_FILE = "/opt/ai-elevate/data/kg-rag-sync-state.json"
RAG_URL = "http://localhost:8020/api/v1"

# RAG API key
RAG_API_KEY = os.environ.get("RAG_API_KEY", "")

ORGS = {
    "gigforge": f"{KG_DIR}/gigforge.db",
    "techuni": f"{KG_DIR}/techuni.db",
    "ai-elevate": f"{KG_DIR}/ai-elevate.db",
}


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except:
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def ensure_collection(org_slug):
    """Ensure the knowledge-graph collection exists for an org."""
    data = json.dumps({
        "org_slug": org_slug,
        "slug": "knowledge-graph",
        "name": f"{org_slug} Knowledge Graph",
        "description": "Synced from KG SQLite store",
    }).encode()
    req = urllib.request.Request(f"{RAG_URL}/collections", data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {RAG_API_KEY}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return True
    except:
        return True  # 409 conflict means it already exists


def rag_ingest_batch(org_slug, documents):
    """Batch ingest documents into the RAG service."""
    data = json.dumps({
        "org_slug": org_slug,
        "collection_slug": "knowledge-graph",
        "documents": documents,
    }).encode()

    req = urllib.request.Request(f"{RAG_URL}/ingest", data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {RAG_API_KEY}")

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}


def rag_ingest(org_slug, title, content, source_type="markdown"):
    """Ingest a document into the RAG service."""
    data = json.dumps({
        "org_slug": org_slug,
        "collection_slug": "knowledge-graph",
        "documents": [{
            "title": title,
            "content": content,
            "source_type": source_type,
            "metadata": {"source": "kg-sync"},
        }],
    }).encode()

    req = urllib.request.Request(f"{RAG_URL}/ingest", data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {RAG_API_KEY}")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}


def get_kg_entities(db_path):
    """Read all entities and relationships from a KG SQLite database."""
    if not os.path.exists(db_path):
        return [], []

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    entities = conn.execute(
        "SELECT type, key, properties, created_at, updated_at FROM entities ORDER BY updated_at DESC"
    ).fetchall()

    relationships = conn.execute(
        "SELECT src_type, src_key, dst_type, dst_key, rel_type, properties, created_at FROM relationships"
    ).fetchall()

    conn.close()
    return entities, relationships


def format_entity_for_rag(entity, relationships_for_entity):
    """Format a KG entity as a searchable RAG document."""
    etype = entity["type"]
    ekey = entity["key"]
    props = json.loads(entity["properties"]) if entity["properties"] else {}

    lines = [
        f"Entity Type: {etype}",
        f"Key: {ekey}",
    ]

    # Add properties
    for k, v in props.items():
        if isinstance(v, (str, int, float, bool)):
            lines.append(f"{k}: {v}")
        elif isinstance(v, list):
            lines.append(f"{k}: {', '.join(str(x) for x in v)}")

    # Add relationships
    if relationships_for_entity:
        lines.append("")
        lines.append("Relationships:")
        for rel in relationships_for_entity:
            src = f"{rel['src_type']}:{rel['src_key']}"
            dst = f"{rel['dst_type']}:{rel['dst_key']}"
            lines.append(f"  {src} --[{rel['rel_type']}]--> {dst}")

    return "\n".join(lines)


def sync_org(org_slug):
    """Sync one org's KG to RAG."""
    db_path = ORGS.get(org_slug)
    if not db_path:
        print(f"  Unknown org: {org_slug}")
        return 0

    entities, relationships = get_kg_entities(db_path)
    if not entities:
        print(f"  {org_slug}: no entities")
        return 0

    state = load_state()
    org_state = state.get(org_slug, {})
    last_sync = org_state.get("last_sync", "")

    # Build relationship lookup
    rel_lookup = {}
    for rel in relationships:
        key = f"{rel['src_type']}:{rel['src_key']}"
        if key not in rel_lookup:
            rel_lookup[key] = []
        rel_lookup[key].append(dict(rel))
        # Also index by destination
        dkey = f"{rel['dst_type']}:{rel['dst_key']}"
        if dkey not in rel_lookup:
            rel_lookup[dkey] = []
        rel_lookup[dkey].append(dict(rel))

    # Ensure collection exists
    ensure_collection(org_slug)

    synced = 0
    errors = 0

    # Batch all entities into documents for a single ingest call
    docs_to_ingest = []
    for entity in entities:
        updated = entity["updated_at"] or entity["created_at"] or ""
        updated_str = str(updated) if updated else ""
        if last_sync and updated_str and updated_str <= last_sync:
            continue

        etype = entity["type"]
        ekey = entity["key"]
        entity_key = f"{etype}:{ekey}"
        rels = rel_lookup.get(entity_key, [])
        doc_content = format_entity_for_rag(entity, rels)
        docs_to_ingest.append({
            "title": f"[KG] {etype}: {ekey}",
            "content": doc_content,
            "source_type": "markdown",
            "metadata": {"source": "kg-sync", "entity_type": etype, "entity_key": ekey},
        })

    if docs_to_ingest:
        # Batch ingest in chunks of 50
        for i in range(0, len(docs_to_ingest), 50):
            batch = docs_to_ingest[i:i+50]
            result = rag_ingest_batch(org_slug, batch)
            if "error" in result:
                errors += len(batch)
                print(f"  RAG batch error: {result['error']}")
            else:
                synced += result.get("ingested", 0)

    # Update state
    now = datetime.now(timezone.utc).isoformat()
    state[org_slug] = {
        "last_sync": now,
        "entities_synced": synced,
        "errors": errors,
        "total_entities": len(entities),
        "total_relationships": len(relationships),
    }
    save_state(state)

    print(f"  {org_slug}: synced {synced} entities ({errors} errors), {len(entities)} total, {len(relationships)} relationships")
    return synced


def show_status():
    """Show sync status for all orgs."""
    state = load_state()
    for org in ORGS:
        s = state.get(org, {})
        print(f"  {org}:")
        print(f"    Last sync: {s.get('last_sync', 'never')}")
        print(f"    Entities synced: {s.get('entities_synced', 0)}")
        print(f"    Total entities: {s.get('total_entities', 0)}")
        print(f"    Relationships: {s.get('total_relationships', 0)}")
        print(f"    Errors: {s.get('errors', 0)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KG → RAG Sync")
    parser.add_argument("--sync", nargs="?", const="all", help="Sync KG to RAG (optionally specify org)")
    parser.add_argument("--status", action="store_true", help="Show sync status")
    parser.add_argument("--full", action="store_true", help="Full re-sync (ignore last sync timestamp)")
    args = parser.parse_args()

    if args.status:
        show_status()
    elif args.sync:
        if args.full:
            # Reset state to force full sync
            save_state({})

        if args.sync == "all":
            print("Syncing all orgs...")
            for org in ORGS:
                sync_org(org)
        else:
            sync_org(args.sync)
    else:
        parser.print_help()
