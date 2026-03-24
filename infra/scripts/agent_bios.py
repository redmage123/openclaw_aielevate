#!/usr/bin/env python3
"""Agent bio lookup from Postgres."""
import os
import psycopg2, psycopg2.extras

DB = dict(
    host=os.environ.get("DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("DB_PORT", "5434")),
    dbname=os.environ.get("DB_NAME", "rag"),
    user=os.environ.get("DB_USER", "rag"),
    password=os.environ.get("DB_PASSWORD", ""),
)

def get_bio(agent_id: str) -> dict:
    conn = psycopg2.connect(**DB)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM agent_bios WHERE agent_id=%s", (agent_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else {}

def search_bios(query: str) -> list:
    conn = psycopg2.connect(**DB)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT agent_id, name, role, nationality, age FROM agent_bios WHERE bio ILIKE %s OR name ILIKE %s",
                (f"%{query}%", f"%{query}%"))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def list_all() -> list:
    conn = psycopg2.connect(**DB)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT agent_id, name, role, nationality, age FROM agent_bios ORDER BY agent_id")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        bio = get_bio(sys.argv[1])
        if bio:
            print(f"{bio['name']} ({bio['role']})")
            print(f"Age: {bio['age']} | Nationality: {bio['nationality']} | Citizenship: {bio['citizenship']}")
            print(bio['bio'][:300])
        else:
            print(f"No bio for {sys.argv[1]}")
    else:
        for b in list_all():
            print(f"  {b['agent_id']:30s} {b['name']:20s} {b['role'][:30]}")
