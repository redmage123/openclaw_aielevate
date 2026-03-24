#!/usr/bin/env python3
from exceptions import AiElevateError  # TODO: Use specific exception types
"""

log = get_logger("external_evaluator")

External Evaluator — monitors the agent ecosystem from OUTSIDE.
NEVER uses claude, openclaw, or any agent infrastructure.
Runs independently. Alerts via direct Mailgun API.
"""
import json
import os
import sqlite3
import time
import urllib.request
import urllib.parse
import base64
import hashlib
import ssl
import argparse
import sys
sys.path.insert(0, "/home/aielevate")
try:
    from audit_log import log_action, log_incident
except:
    def log_action(*a, **k): pass
    def log_incident(*a, **k): return None
from datetime import datetime, timezone
from pathlib import Path
from logging_config import get_logger

DB_PATH = "/opt/ai-elevate/data/evaluator.db"
MG_KEY = Path("/opt/ai-elevate/credentials/mailgun-api-key.txt").read_text().strip()
ALERT_TO = "braun.brelin@ai-elevate.ai"

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS evaluations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, check_name TEXT, status TEXT,
        details TEXT, response_time_ms INTEGER
    )""")
    conn.commit()
    return conn


def alert_direct(subject, body):
    """Send alert bypassing ALL agent infrastructure."""
    try:
        data = urllib.parse.urlencode({
            "from": "External Monitor <monitor@internal.ai-elevate.ai>",
            "to": ALERT_TO,
            "subject": f"[EXTERNAL MONITOR] {subject}",
            "text": body,
        }).encode()
        creds = base64.b64encode(f"api:{MG_KEY}".encode()).decode()
        req = urllib.request.Request(
            "https://api.mailgun.net/v3/internal.ai-elevate.ai/messages",
            data=data, method="POST")
        req.add_header("Authorization", f"Basic {creds}")
        urllib.request.urlopen(req, timeout=15)
    except:
        pass


def check_endpoint(name, url, use_ssl=False):
    """Check if an HTTP endpoint responds."""
    start = time.time()
    try:
        if use_ssl:
            resp = urllib.request.urlopen(url, timeout=10, context=_ctx)
        else:
            resp = urllib.request.urlopen(url, timeout=10)
        ms = int((time.time() - start) * 1000)
        code = resp.getcode()
        return {"name": name, "status": "ok" if 200 <= code < 400 else "degraded",
                "details": f"HTTP {code} in {ms}ms", "ms": ms}
    except Exception as e:
        ms = int((time.time() - start) * 1000)
        return {"name": name, "status": "down", "details": str(e)[:200], "ms": ms}


def check_token():
    """Check Claude OAuth token expiry."""
    creds_file = "/home/aielevate/.claude/.credentials.json"
    try:
        with open(creds_file) as f:
            d = json.load(f)
        expires = d.get("claudeAiOauth", {}).get("expiresAt", 0)
        now_ms = int(time.time() * 1000)
        remaining_min = (expires - now_ms) // 60000
        if remaining_min < 0:
            return {"name": "claude-token", "status": "down",
                    "details": f"Token EXPIRED {abs(remaining_min)} min ago", "ms": 0}
        elif remaining_min < 60:
            return {"name": "claude-token", "status": "degraded",
                    "details": f"Token expires in {remaining_min} min", "ms": 0}
        else:
            return {"name": "claude-token", "status": "ok",
                    "details": f"Token valid for {remaining_min} min", "ms": 0}
    except Exception as e:
        return {"name": "claude-token", "status": "down", "details": str(e)[:100], "ms": 0}


def check_config_integrity():
    """Check if openclaw.json has been modified unexpectedly."""
    config_file = "/home/aielevate/.openclaw/openclaw.json"
    hash_file = "/opt/ai-elevate/data/config_hash.txt"
    try:
        with open(config_file, "rb") as f:
            current_hash = hashlib.sha256(f.read()).hexdigest()
        if os.path.exists(hash_file):
            with open(hash_file) as f:
                stored_hash = f.read().strip()
            if current_hash != stored_hash:
                with open(hash_file, "w") as f:
                    f.write(current_hash)
                return {"name": "config-integrity", "status": "degraded",
                        "details": "openclaw.json modified since last check", "ms": 0}
        else:
            with open(hash_file, "w") as f:
                f.write(current_hash)
        return {"name": "config-integrity", "status": "ok",
                "details": f"Hash: {current_hash[:16]}...", "ms": 0}
    except Exception as e:
        return {"name": "config-integrity", "status": "down", "details": str(e)[:100], "ms": 0}


def check_cron_health():
    """Check if key crons have run recently."""
    checks = {
        "infra-healthcheck": "/var/log/openclaw-infra-health.log",
        "uptime-monitor": "/var/log/openclaw-uptime.log",
        "plane-triage": "/var/log/openclaw-plane-triage.log",
        "kg-rag-sync": "/var/log/openclaw-kg-rag-sync.log",
    }
    stale = []
    for name, logfile in checks.items():
        try:
            mtime = os.path.getmtime(logfile)
            age_hours = (time.time() - mtime) / 3600
            if age_hours > 2:
                stale.append(f"{name}: {age_hours:.0f}h old")
        except:
            stale.append(f"{name}: log missing")
    if stale:
        return {"name": "cron-health", "status": "degraded",
                "details": "; ".join(stale), "ms": 0}
    return {"name": "cron-health", "status": "ok", "details": "All crons recent", "ms": 0}


def run_all_checks():
    """Run all health checks."""
    results = []

    # Service endpoints
    results.append(check_endpoint("gateway", "http://localhost:18789/health"))
    # Email gateway has no root endpoint — 404/405 means it's running
    r = check_endpoint("email-gateway", "http://localhost:8065/")
    if r["status"] == "down" and ("404" in r["details"] or "405" in r["details"]):
        r["status"] = "ok"
        r["details"] = "Running (no root endpoint)"
    results.append(r)
    results.append(check_endpoint("strapi-cms", "http://localhost:1337/_health"))
    results.append(check_endpoint("voice-platform", "http://localhost:8067/health"))
    results.append(check_endpoint("webhook-router", "http://localhost:8066/health"))
    results.append(check_endpoint("plane-gigforge", "http://localhost:8801"))
    results.append(check_endpoint("plane-techuni", "http://localhost:8802"))
    results.append(check_endpoint("rag-service", "http://localhost:8020/api/v1/health"))
    results.append(check_endpoint("crm", "http://localhost:8070/health"))
    results.append(check_endpoint("course-creator", "https://localhost:3000", use_ssl=True))
    results.append(check_endpoint("gigforge-website", "http://localhost:4091"))
    results.append(check_endpoint("techuni-website", "http://localhost:4090"))

    # System checks
    results.append(check_token())
    results.append(check_config_integrity())
    results.append(check_cron_health())

    return results


def process_results(results):
    """Store results and alert on failures."""
    conn = init_db()
    now = datetime.now(timezone.utc).isoformat()
    failures = []

    for r in results:
        conn.execute(
            "INSERT INTO evaluations (timestamp, check_name, status, details, response_time_ms) VALUES (?,?,?,?,?)",
            (now, r["name"], r["status"], r["details"], r["ms"]))
        if r["status"] == "down":
            failures.append(f"{r['name']}: {r['details']}")
    conn.commit()
    conn.close()

    if failures:
        log_incident(severity="high", title=f"{len(failures)} service(s) DOWN",
                    description="\n"
.join(f"- {f}" for f in failures), detected_by="external_evaluator")
        alert_direct(
            f"{len(failures)} service(s) DOWN",
            "External evaluator detected failures:\n\n" +
            "\n".join(f"- {f}" for f in failures) +
            f"\n\nTimestamp: {now}")
        log.info("ALERT: {len(failures)} failures")
    else:
        log.info("OK: {len(results)} checks passed")


def report():
    """Generate health report."""
    conn = init_db()
    print("=" * 60)
    log.info("EXTERNAL EVALUATOR — HEALTH REPORT")
    print("=" * 60)

    # Latest status per check
    rows = conn.execute("""
        SELECT check_name, status, details, response_time_ms, timestamp
        FROM evaluations
        WHERE id IN (SELECT MAX(id) FROM evaluations GROUP BY check_name)
        ORDER BY check_name
    """).fetchall()

    for name, status, details, ms, ts in rows:
        icon = "OK" if status == "ok" else "WARN" if status == "degraded" else "FAIL"
        log.info("  [%s] %s %s %sms", extra={"ms": ms})

    # Failure count last 24h
    count = conn.execute("""
        SELECT COUNT(*) FROM evaluations
        WHERE status = 'down' AND timestamp > datetime('now', '-1 day')
    """).fetchone()[0]
    log.info("\nFailures in last 24h: %s", extra={"count": count})
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="External Evaluator")
    parser.add_argument("--check", action="store_true", help="Run all checks")
    parser.add_argument("--report", action="store_true", help="Show health report")
    args = parser.parse_args()

    if args.check:
        results = run_all_checks()
        process_results(results)
    elif args.report:
        report()
    else:
        parser.print_help()
