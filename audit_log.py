#!/usr/bin/env python3
"""
Agent Action Log — central audit trail for all agent actions.
Stored in RAG Postgres (port 5434) for querying and operator visibility.

Usage:
    from audit_log import log_action, log_incident, query_actions

    # Log an action
    log_action(agent_id="gigforge-sales", org="gigforge", action_type="email_sent",
               target="customer@example.com", status="success",
               evidence="mailgun-id-abc123", details={"subject": "Re: proposal"})

    # Query recent actions
    actions = query_actions(agent_id="gigforge-sales", hours=24)

    # Log an incident
    log_incident(severity="high", title="Email gateway down",
                 affected_services=["email-gateway"], detected_by="external_evaluator")
"""

import json
import os
import psycopg2
from datetime import datetime, timezone, timedelta
from dao import BaseDAO  # TODO: Replace inline DB calls with specific DAOs
from exceptions import AiElevateError  # TODO: Use specific exception types, DatabaseError
import argparse

DB_CONN = dict(
    host=os.environ.get("DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("DB_PORT", "5434")),
    dbname=os.environ.get("DB_NAME", "rag"),
    user=os.environ.get("DB_USER", "rag"),
    password=os.environ.get("DB_PASSWORD", "rag_vec_2026"),
)


def _conn():
    return psycopg2.connect(**DB_CONN)


def log_action(agent_id, org, action_type, target="", status="success",
               evidence="", details=None, session_id="", duration_ms=0):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Log an agent action to the central audit trail."""
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO agent_actions (agent_id, org, action_type, target, status, evidence, details, session_id, duration_ms)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (agent_id, org, action_type, target, status, evidence,
             json.dumps(details or {}), session_id, duration_ms)
        )
        conn.commit()
        conn.close()
    except (DatabaseError, Exception) as e:
        pass  # Audit logging should never break the caller


def log_incident(severity, title, description="", affected_services=None, detected_by=""):
    """Log an operational incident."""
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO incidents (severity, title, description, affected_services, detected_by)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (severity, title, description, affected_services or [], detected_by)
        )
        incident_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        return incident_id
    except (DatabaseError, Exception) as e:
        return None


def resolve_incident(incident_id, resolution):
    """Mark an incident as resolved."""
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            """UPDATE incidents SET status='resolved', resolved_at=NOW(), resolution=%s WHERE id=%s""",
            (resolution, incident_id)
        )
        conn.commit()
        conn.close()
    except:
        pass


def query_actions(agent_id=None, org=None, action_type=None, hours=24, limit=50):
    """Query recent agent actions."""
    try:
        conn = _conn()
        cur = conn.cursor()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        conditions = ["timestamp > %s"]
        params = [cutoff]
        if agent_id:
            conditions.append("agent_id = %s")
            params.append(agent_id)
        if org:
            conditions.append("org = %s")
            params.append(org)
        if action_type:
            conditions.append("action_type = %s")
            params.append(action_type)
        params.append(limit)
        cur.execute(
            f"SELECT * FROM agent_actions WHERE {' AND '.join(conditions)} ORDER BY timestamp DESC LIMIT %s",
            params
        )
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        conn.close()
        return rows
    except (DatabaseError, Exception) as e:
        return []


def query_incidents(status=None, hours=168):
    """Query incidents."""
    try:
        conn = _conn()
        cur = conn.cursor()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        if status:
            cur.execute(
                "SELECT * FROM incidents WHERE status=%s AND timestamp > %s ORDER BY timestamp DESC",
                (status, cutoff)
            )
        else:
            cur.execute(
                "SELECT * FROM incidents WHERE timestamp > %s ORDER BY timestamp DESC",
                (cutoff,)
            )
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        conn.close()
        return rows
    except:
        return []


def action_summary(hours=24):
    """Get action counts by type and agent for the dashboard."""
    try:
        conn = _conn()
        cur = conn.cursor()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        cur.execute("""
            SELECT agent_id, action_type, status, COUNT(*) as cnt
            FROM agent_actions WHERE timestamp > %s
            GROUP BY agent_id, action_type, status
            ORDER BY cnt DESC
        """, (cutoff,))
        rows = cur.fetchall()

        cur.execute("SELECT COUNT(*) FROM agent_actions WHERE timestamp > %s", (cutoff,))
        total = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM agent_actions WHERE timestamp > %s AND status='failure'", (cutoff,))
        failures = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM incidents WHERE status='open'")
        open_incidents = cur.fetchone()[0]

        conn.close()
        return {
            "total_actions": total,
            "failures": failures,
            "open_incidents": open_incidents,
            "by_agent": [{"agent": r[0], "type": r[1], "status": r[2], "count": r[3]} for r in rows],
        }
    except:
        return {"total_actions": 0, "failures": 0, "open_incidents": 0, "by_agent": []}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit Log")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose output")
    args = parser.parse_args()

    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--summary":
        s = action_summary()
        print(f"Actions (24h): {s['total_actions']}, Failures: {s['failures']}, Open incidents: {s['open_incidents']}")
        for a in s["by_agent"][:10]:
            print(f"  {a['agent']:30s} {a['type']:20s} {a['status']:10s} {a['count']}")
    elif len(sys.argv) > 1 and sys.argv[1] == "--test":
        log_action("test-agent", "gigforge", "test", target="self", status="success", evidence="test-run")
        actions = query_actions(agent_id="test-agent", hours=1)
        print(f"Test: {len(actions)} action(s) found")
    else:
        print("Usage: audit_log.py [--summary|--test]")
