#!/usr/bin/env python3
"""
Agent Performance Tracker — monitors response times, quality, and compliance.

Usage:
    python3 agent_performance.py --collect    # Collect metrics from logs
    python3 agent_performance.py --report     # Generate performance report
"""

import json
import os
import sqlite3
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

DB_PATH = "/opt/ai-elevate/data/agent_performance.db"

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS metrics (
        id INTEGER PRIMARY KEY,
        timestamp TEXT,
        agent_id TEXT,
        metric_type TEXT,
        value REAL,
        details TEXT
    )""")
    conn.commit()
    return conn

def collect_metrics():
    """Collect metrics from various sources."""
    conn = init_db()
    now = datetime.now(timezone.utc).isoformat()
    
    # Collect from SLA tracker
    try:
        sla_db = sqlite3.connect("/opt/ai-elevate/data/sla.db")
        rows = sla_db.execute("""
            SELECT agent_id, AVG(response_time_seconds), COUNT(*), 
                   SUM(CASE WHEN sla_met = 1 THEN 1 ELSE 0 END)
            FROM sla_events 
            WHERE received_at > datetime('now', '-7 days')
            GROUP BY agent_id
        """).fetchall()
        for agent_id, avg_time, total, met in rows:
            conn.execute("INSERT INTO metrics (timestamp, agent_id, metric_type, value, details) VALUES (?,?,?,?,?)",
                (now, agent_id, "avg_response_time", avg_time or 0, f"total={total},met={met}"))
            if total > 0:
                conn.execute("INSERT INTO metrics (timestamp, agent_id, metric_type, value, details) VALUES (?,?,?,?,?)",
                    (now, agent_id, "sla_compliance", (met or 0)/total*100, f"met={met},total={total}"))
        sla_db.close()
    except Exception as e:
        print(f"SLA metrics error: {e}")
    
    # Collect from email gateway response logs
    try:
        log_dir = Path("/opt/ai-elevate/email-gateway/logs")
        if log_dir.exists():
            total_responses = 0
            for log_file in log_dir.glob("responses-*.jsonl"):
                with open(log_file) as f:
                    for line in f:
                        try:
                            entry = json.loads(line)
                            agent = entry.get("agent", "")
                            length = entry.get("response_length", 0)
                            conn.execute("INSERT INTO metrics (timestamp, agent_id, metric_type, value, details) VALUES (?,?,?,?,?)",
                                (now, agent, "response_length", length, ""))
                            total_responses += 1
                        except:
                            pass
            print(f"Collected {total_responses} response metrics")
    except Exception as e:
        print(f"Response log error: {e}")
    
    conn.commit()
    conn.close()
    print("Metrics collection complete")

def generate_report():
    """Generate performance report."""
    conn = init_db()
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    
    print("=" * 60)
    print("AGENT PERFORMANCE REPORT")
    print(f"Period: Last 7 days")
    print("=" * 60)
    
    # SLA compliance
    rows = conn.execute("""
        SELECT agent_id, AVG(value) FROM metrics 
        WHERE metric_type = 'sla_compliance' AND timestamp > ?
        GROUP BY agent_id ORDER BY AVG(value)
    """, (week_ago,)).fetchall()
    
    if rows:
        print("\nSLA Compliance:")
        for agent, compliance in rows:
            print(f"  {agent:30s} {compliance:.0f}%")
    
    # Response times
    rows = conn.execute("""
        SELECT agent_id, AVG(value) FROM metrics
        WHERE metric_type = 'avg_response_time' AND timestamp > ?
        GROUP BY agent_id ORDER BY AVG(value) DESC
    """, (week_ago,)).fetchall()
    
    if rows:
        print("\nAvg Response Time:")
        for agent, avg_time in rows:
            print(f"  {agent:30s} {avg_time:.0f}s")
    
    conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--collect", action="store_true")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    
    if args.collect:
        collect_metrics()
    elif args.report:
        generate_report()
    else:
        parser.print_help()
