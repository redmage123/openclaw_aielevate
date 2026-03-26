#!/usr/bin/env python3
"""Semantic Search — vector embeddings + hybrid retrieval for agent context.

Agents need to find relevant projects, conversations, and knowledge
even when the exact terms don't match. This provides:

1. Vector embeddings (all-MiniLM-L6-v2, 384-dim) for semantic similarity
2. BM25 full-text search for keyword matching
3. Hybrid scoring (0.6 semantic + 0.4 keyword)
4. Auto-indexing of emails, KG entities, milestones, proposals

Every inbound email triggers a search across ALL data sources.
Results are injected into the agent's context before it responds.

Usage:
    from semantic_search import search, index_document, build_index

    # Search across everything
    results = search("Priority Management website rebuild", org="gigforge", top_k=10)

    # Index a new document
    index_document("email", "GF-54 judiciary app migration", metadata={...})
"""

import os
import sys
import json
import hashlib
import logging
import math
import re
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict, Counter

sys.path.insert(0, "/home/aielevate")

log = logging.getLogger("semantic-search")

# ── Configuration ──────────────────────────────────────────────────────

DB_CONFIG = dict(host="127.0.0.1", port=5434, dbname="rag", user="rag", password="rag_vec_2026")
EMBEDDING_DIM = 384
MODEL_NAME = "all-MiniLM-L6-v2"

_embedder = None


def _get_embedder():
    """Lazy-load the sentence transformer model."""
    global _embedder
    if _embedder is None:
        try:
            import os, warnings
            os.environ['HF_HUB_OFFLINE'] = '1'
            os.environ['TOKENIZERS_PARALLELISM'] = 'false'
            warnings.filterwarnings('ignore')
            import logging as _lg
            _lg.getLogger('sentence_transformers').setLevel(_lg.ERROR)
            _lg.getLogger('transformers').setLevel(_lg.ERROR)
            from sentence_transformers import SentenceTransformer
            _embedder = SentenceTransformer(MODEL_NAME, device='cpu')
            log.info(f"Loaded embedding model: {MODEL_NAME}")
        except ImportError:
            log.warning("sentence-transformers not installed — using TF-IDF fallback")
            _embedder = "tfidf_fallback"
    return _embedder


def embed(text):
    """Generate embedding vector for text."""
    embedder = _get_embedder()
    if embedder == "tfidf_fallback":
        return _tfidf_embed(text)
    return embedder.encode(text, normalize_embeddings=True).tolist()


def _tfidf_embed(text):
    """Fallback: simple hash-based pseudo-embedding when sentence-transformers unavailable."""
    words = text.lower().split()
    vec = [0.0] * EMBEDDING_DIM
    for i, word in enumerate(words):
        h = int(hashlib.md5(word.encode()).hexdigest(), 16)
        idx = h % EMBEDDING_DIM
        vec[idx] += 1.0
    # Normalize
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine_similarity(a, b):
    """Cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a)) or 1.0
    norm_b = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (norm_a * norm_b)


# ── Database Setup ─────────────────────────────────────────────────────

def _get_db():
    import psycopg2
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    return conn


def init_tables():
    """Create semantic search tables if they don't exist."""
    conn = _get_db()
    cur = conn.cursor()

    # Check if pgvector is available
    try:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        has_pgvector = True
    except Exception:
        has_pgvector = False
        conn.rollback()
        conn.autocommit = True

    if has_pgvector:
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS semantic_index (
                id SERIAL PRIMARY KEY,
                doc_type TEXT NOT NULL,
                doc_id TEXT NOT NULL,
                content TEXT NOT NULL,
                embedding vector({EMBEDDING_DIM}),
                metadata JSONB DEFAULT '{{}}',
                org TEXT DEFAULT 'gigforge',
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(doc_type, doc_id)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_semantic_embedding ON semantic_index USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10)")
    else:
        # Fallback without pgvector — store embeddings as JSON
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS semantic_index (
                id SERIAL PRIMARY KEY,
                doc_type TEXT NOT NULL,
                doc_id TEXT NOT NULL,
                content TEXT NOT NULL,
                embedding_json TEXT,
                metadata JSONB DEFAULT '{{}}',
                org TEXT DEFAULT 'gigforge',
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(doc_type, doc_id)
            )
        """)

    # Full-text search index
    cur.execute("CREATE INDEX IF NOT EXISTS idx_semantic_content_fts ON semantic_index USING gin(to_tsvector('english', content))")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_semantic_org ON semantic_index(org)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_semantic_type ON semantic_index(doc_type)")

    conn.close()
    log.info(f"Semantic search tables ready (pgvector={'yes' if has_pgvector else 'no'})")
    return has_pgvector


# ── Indexing ───────────────────────────────────────────────────────────

def index_document(doc_type, content, doc_id=None, metadata=None, org="gigforge"):
    """Index a document for semantic search.

    Args:
        doc_type: "email", "project", "ticket", "proposal", "kg_entity", "milestone"
        content: The text content to index
        doc_id: Unique identifier (auto-generated if not provided)
        metadata: Additional context (sender, subject, etc.)
        org: Organization slug
    """
    if not content or len(content.strip()) < 10:
        return

    if doc_id is None:
        doc_id = hashlib.sha256(f"{doc_type}:{content[:200]}".encode()).hexdigest()[:16]

    vec = embed(content[:2000])  # Limit embedding input
    metadata = metadata or {}

    conn = _get_db()
    cur = conn.cursor()

    try:
        # Check if pgvector table exists
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'semantic_index' AND column_name = 'embedding'")
        has_vector_col = cur.fetchone() is not None

        if has_vector_col:
            cur.execute("""
                INSERT INTO semantic_index (doc_type, doc_id, content, embedding, metadata, org)
                VALUES (%s, %s, %s, %s::vector, %s, %s)
                ON CONFLICT (doc_type, doc_id) DO UPDATE SET
                    content = EXCLUDED.content,
                    embedding = EXCLUDED.embedding,
                    metadata = EXCLUDED.metadata,
                    created_at = NOW()
            """, (doc_type, doc_id, content[:5000], str(vec), json.dumps(metadata), org))
        else:
            cur.execute("""
                INSERT INTO semantic_index (doc_type, doc_id, content, embedding_json, metadata, org)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (doc_type, doc_id) DO UPDATE SET
                    content = EXCLUDED.content,
                    embedding_json = EXCLUDED.embedding_json,
                    metadata = EXCLUDED.metadata,
                    created_at = NOW()
            """, (doc_type, doc_id, content[:5000], json.dumps(vec), json.dumps(metadata), org))

    except Exception as e:
        log.error(f"Index error: {e}")
    finally:
        conn.close()


# ── Search ─────────────────────────────────────────────────────────────

def search(query, org=None, doc_type=None, top_k=10):
    """Hybrid semantic + keyword search.

    Returns list of dicts: {doc_type, doc_id, content, score, metadata}
    """
    conn = _get_db()
    cur = conn.cursor()

    results = []

    # Check which table format we have
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'semantic_index' AND column_name = 'embedding'")
    has_vector_col = cur.fetchone() is not None

    # 1. Vector similarity search
    query_vec = embed(query[:500])

    if has_vector_col:
        try:
            sql = """
                SELECT doc_type, doc_id, content, metadata,
                       1 - (embedding <=> %s::vector) as similarity
                FROM semantic_index
                WHERE 1=1
            """
            params = [str(query_vec)]
            if org:
                sql += " AND org = %s"
                params.append(org)
            if doc_type:
                sql += " AND doc_type = %s"
                params.append(doc_type)
            sql += f" ORDER BY embedding <=> %s::vector LIMIT {top_k * 2}"
            params.append(str(query_vec))

            cur.execute(sql, params)
            for row in cur.fetchall():
                results.append({
                    "doc_type": row[0], "doc_id": row[1],
                    "content": row[2][:500], "metadata": row[3] or {},
                    "vector_score": float(row[4]),
                    "source": "vector",
                })
        except Exception as e:
            log.debug(f"Vector search error: {e}")

    else:
        # Fallback: load all embeddings and compute similarity in Python
        try:
            sql = "SELECT doc_type, doc_id, content, metadata, embedding_json FROM semantic_index WHERE 1=1"
            params = []
            if org:
                sql += " AND org = %s"
                params.append(org)
            if doc_type:
                sql += " AND doc_type = %s"
                params.append(doc_type)

            cur.execute(sql, params)
            for row in cur.fetchall():
                if row[4]:
                    stored_vec = json.loads(row[4])
                    sim = cosine_similarity(query_vec, stored_vec)
                    results.append({
                        "doc_type": row[0], "doc_id": row[1],
                        "content": row[2][:500], "metadata": row[3] or {},
                        "vector_score": sim,
                        "source": "vector",
                    })
        except Exception as e:
            log.debug(f"Fallback vector search error: {e}")

    # 2. BM25 / Full-text search
    try:
        sql = """
            SELECT doc_type, doc_id, content, metadata,
                   ts_rank(to_tsvector('english', content), plainto_tsquery('english', %s)) as rank
            FROM semantic_index
            WHERE to_tsvector('english', content) @@ plainto_tsquery('english', %s)
        """
        params = [query, query]
        if org:
            sql += " AND org = %s"
            params.append(org)
        if doc_type:
            sql += " AND doc_type = %s"
            params.append(doc_type)
        sql += f" ORDER BY rank DESC LIMIT {top_k * 2}"

        cur.execute(sql, params)
        for row in cur.fetchall():
            results.append({
                "doc_type": row[0], "doc_id": row[1],
                "content": row[2][:500], "metadata": row[3] or {},
                "bm25_score": float(row[4]),
                "source": "bm25",
            })
    except Exception as e:
        log.debug(f"BM25 search error: {e}")

    conn.close()

    # 3. Three-tier scoring: exact entity match > BM25 keyword > semantic vector
    merged = {}
    for r in results:
        key = f"{r['doc_type']}:{r['doc_id']}"
        if key not in merged:
            merged[key] = {
                "doc_type": r["doc_type"], "doc_id": r["doc_id"],
                "content": r["content"], "metadata": r["metadata"],
                "vector_score": 0.0, "bm25_score": 0.0, "exact_score": 0.0,
            }
        if "vector_score" in r:
            merged[key]["vector_score"] = max(merged[key]["vector_score"], r["vector_score"])
        if "bm25_score" in r:
            merged[key]["bm25_score"] = max(merged[key]["bm25_score"], r["bm25_score"])

    # Tier 1: Exact entity matching
    query_lower = query.lower().strip()
    query_words = [w for w in query_lower.split() if len(w) > 2]
    # Build multi-word phrases from query
    words = query_lower.split()
    query_phrases = []
    for i in range(len(words)):
        for n in range(2, min(4, len(words) - i + 1)):
            query_phrases.append(" ".join(words[i:i+n]))

    for key, item in merged.items():
        content_lower = item["content"].lower()
        meta = item.get("metadata", {})
        meta_text = " ".join(str(v).lower() for v in meta.values() if v)
        searchable = content_lower + " " + meta_text

        # Full query as exact substring (highest)
        if query_lower in searchable:
            item["exact_score"] = 1.0
        else:
            # Multi-word phrase match
            phrase_hits = sum(1 for p in query_phrases if p in searchable)
            if phrase_hits > 0:
                item["exact_score"] = min(0.8, 0.3 + 0.15 * phrase_hits)
            else:
                # Individual important word match (not stopwords)
                stopwords = {"the", "and", "for", "with", "from", "this", "that", "are", "was"}
                important_words = [w for w in query_words if w not in stopwords]
                word_hits = sum(1 for w in important_words if w in searchable)
                if important_words:
                    item["exact_score"] = 0.1 * (word_hits / len(important_words))

    # Final: exact (0.5) > BM25 keyword (0.3) > semantic (0.2)
    for key in merged:
        item = merged[key]
        item["score"] = (
            0.5 * item["exact_score"] +
            0.3 * item["bm25_score"] +
            0.2 * item["vector_score"]
        )

    ranked = sorted(merged.values(), key=lambda x: x["score"], reverse=True)[:top_k]
    return ranked


# ── Bulk Indexing ──────────────────────────────────────────────────────

def build_index(org="gigforge"):
    """Build/rebuild the semantic index from all data sources."""
    indexed = 0

    conn = _get_db()
    cur = conn.cursor()

    # 1. Index emails
    try:
        cur.execute("SELECT id, sender_email, subject, body_text, created_at FROM email_interactions WHERE subject IS NOT NULL")
        for row in cur.fetchall():
            content = f"{row[2]} {row[3] or ''}"
            index_document("email", content, doc_id=f"email-{row[0]}",
                          metadata={"sender": row[1], "subject": row[2], "date": str(row[4])}, org=org)
            indexed += 1
    except Exception as e:
        log.debug(f"Email indexing: {e}")

    # 2. Index proposals
    try:
        cur.execute("SELECT id, job_title, proposal_text, platform, recommended_bid, status FROM proposal_queue")
        for row in cur.fetchall():
            content = f"{row[1]} {row[2] or ''}"
            index_document("proposal", content, doc_id=f"proposal-{row[0]}",
                          metadata={"title": row[1], "platform": row[3], "bid": row[4], "status": row[5]}, org=org)
            indexed += 1
    except Exception as e:
        log.debug(f"Proposal indexing: {e}")

    # 3. Index milestones
    try:
        cur.execute("SELECT id, project_title, milestone, status, customer_email FROM project_milestones")
        for row in cur.fetchall():
            content = f"{row[1]} {row[2]} {row[3]}"
            index_document("milestone", content, doc_id=f"milestone-{row[0]}",
                          metadata={"project": row[1], "milestone": row[2], "status": row[3], "customer": row[4]}, org=org)
            indexed += 1
    except Exception as e:
        log.debug(f"Milestone indexing: {e}")

    # 4. Index KG entities
    try:
        from knowledge_graph import KG
        kg = KG(org)
        entities = kg.all_entities() if hasattr(kg, 'all_entities') else []
        for ent in entities:
            content = f"{ent.get('type', '')} {ent.get('key', '')} {json.dumps(ent.get('properties', {}))}"
            index_document("kg_entity", content, doc_id=f"kg-{ent.get('key', '')[:50]}",
                          metadata=ent.get("properties", {}), org=org)
            indexed += 1
    except Exception as e:
        log.debug(f"KG indexing: {e}")

    # 5. Index stored encrypted emails
    try:
        from email_nlp_pipeline import get_all_stored_emails
        for email in get_all_stored_emails(limit=200):
            content = f"{email.get('subject', '')} {email.get('body', '')[:1000]}"
            doc_id = hashlib.sha256(f"{email.get('sender','')}{email.get('subject','')}".encode()).hexdigest()[:16]
            index_document("stored_email", content, doc_id=f"stored-{doc_id}",
                          metadata={"sender": email.get("sender"), "subject": email.get("subject"),
                                   "direction": email.get("direction")}, org=org)
            indexed += 1
    except Exception as e:
        log.debug(f"Stored email indexing: {e}")

    conn.close()
    log.info(f"Built semantic index: {indexed} documents")
    return indexed


# ── Agent Context Builder ──────────────────────────────────────────────

def get_context_for_email(sender, subject, body, org="gigforge", max_results=8):
    """Search all data sources for context relevant to an inbound email.

    This is what the agent's workflow activity should call to enrich
    the agent's context before responding.

    Returns formatted string ready to inject into agent prompt.
    """
    # Build query from subject + key phrases from body
    query = f"{subject} {body[:300]}"

    results = search(query, org=org, top_k=max_results)

    if not results:
        return ""

    # Format for agent consumption
    sections = []
    for r in results:
        score = r["score"]
        if score < 0.1:
            continue  # Too low relevance

        meta = r.get("metadata", {})
        doc_type = r["doc_type"]

        if doc_type == "email":
            sections.append(f"[Email] {meta.get('subject', '')} from {meta.get('sender', '')} ({meta.get('date', '')})")
        elif doc_type == "proposal":
            sections.append(f"[Proposal] {meta.get('title', '')} — ${meta.get('bid', '')} ({meta.get('status', '')})")
        elif doc_type == "milestone":
            sections.append(f"[Milestone] {meta.get('project', '')} → {meta.get('milestone', '')}: {meta.get('status', '')}")
        elif doc_type == "kg_entity":
            sections.append(f"[KG] {r['content'][:150]}")
        elif doc_type == "stored_email":
            sections.append(f"[Email Archive] {meta.get('subject', '')} ({meta.get('direction', '')} from {meta.get('sender', '')})")
        else:
            sections.append(f"[{doc_type}] {r['content'][:150]}")

    if sections:
        return "\n\nRelevant context from project history:\n" + "\n".join(f"  • {s}" for s in sections)
    return ""


# ── Init ───────────────────────────────────────────────────────────────

try:
    init_tables()
except Exception as e:
    log.debug(f"Table init deferred: {e}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Semantic Search")
    parser.add_argument("--build", action="store_true", help="Build/rebuild index from all sources")
    parser.add_argument("--search", type=str, help="Search query")
    parser.add_argument("--org", type=str, default="gigforge")
    parser.add_argument("--count", action="store_true", help="Count indexed documents")
    args = parser.parse_args()

    if args.build:
        count = build_index(args.org)
        print(f"Indexed {count} documents")
    elif args.search:
        results = search(args.search, org=args.org)
        for r in results:
            print(f"  [{r['doc_type']}] score={r['score']:.3f} | {r['content'][:80]}")
    elif args.count:
        conn = _get_db()
        cur = conn.cursor()
        cur.execute("SELECT doc_type, COUNT(*) FROM semantic_index GROUP BY doc_type ORDER BY count DESC")
        for row in cur.fetchall():
            print(f"  {row[0]}: {row[1]}")
        cur.execute("SELECT COUNT(*) FROM semantic_index")
        print(f"  TOTAL: {cur.fetchone()[0]}")
        conn.close()
