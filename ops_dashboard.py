#!/usr/bin/env python3
"""GigForge Ops Dashboard — full operations center."""
import asyncio
import hashlib
import hmac
import json
import os
import sys
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from dao import BaseDAO  # TODO: Replace inline DB calls with specific DAOs
from exceptions import AiElevateError  # TODO: Use specific exception types, AgentError, DatabaseError
AgentError = Exception  # placeholder until agent_dispatch exports AgentError
DatabaseError = Exception  # placeholder until psycopg2.DatabaseError is imported directly

from starlette.middleware.sessions import SessionMiddleware

sys.path.insert(0, "/home/aielevate")

app = FastAPI(title="GigForge Ops Dashboard")
_TEMPLATE_DIR = Path(__file__).parent / "templates"

STATUS_MAP = {1: "RUNNING", 2: "COMPLETED", 3: "FAILED", 4: "CANCELLED", 7: "TIMED_OUT"}
DB_CONN = dict(
    host=os.environ.get("DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("DB_PORT", "5434")),
    dbname=os.environ.get("DB_NAME", "rag"),
    user=os.environ.get("DB_USER", "rag"),
    password=os.environ.get("DB_PASSWORD", "rag_vec_2026"),
)

# ============================================================================
# Auth
# ============================================================================

_SESSION_SECRET = os.environ.get("SESSION_SECRET", "1dfaafdffc23963ee7aff18ce0c1c128ac41f3f7ccbe789cb7628134dce07897")

_USERS = {
    "braun": {
        "salt": "746ec994f0e5da1614edee6f1d9d74f0",
        "hash": "5303ddb57eb07d37c34f60bdde95737bdce8854270b8033bc14aa837e5dc2476",
        "name": "Braun Brelin",
    },
    "pete": {
        "salt": "702f5926f49526dc26e2d9476654bf55",
        "hash": "faf809b70b048c961593fee4a0ff041cfd71175843cb63276ea8acd34455edc8",
        "name": "Pete Munro",
    },
    "team": {
        "salt": "5fc5581c3ebd5427a23450e3ea1ccfcf",
        "hash": "69854b804d48451022b2ee031ab68aa4666cb0679dc23cdf737ca0fdb0f67c5c",
        "name": "GigForge Team",
    },
}


def _verify_pw(username: str, password: str) -> bool:
    user = _USERS.get(username.lower())
    if not user:
        return False
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), user["salt"].encode(), 100000).hex()
    return hmac.compare_digest(h, user["hash"])


def mask_email(email: str) -> str:
    """Mask email for display: jo***@example.com"""
    if not email or "@" not in email:
        return email or ""
    local, domain = email.split("@", 1)
    masked = (local[:2] + "***") if len(local) > 2 else "***"
    return f"{masked}@{domain}"


_LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>GigForge Ops — Sign In</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; display: flex; justify-content: center; align-items: center; min-height: 100vh; }}
        .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 40px; width: 360px; }}
        h1 {{ color: #f8fafc; font-size: 20px; margin-bottom: 6px; }}
        .sub {{ color: #64748b; font-size: 12px; margin-bottom: 28px; }}
        label {{ display: block; color: #94a3b8; font-size: 12px; text-transform: uppercase; letter-spacing: .5px; margin-bottom: 4px; }}
        input {{ width: 100%; background: #0f172a; border: 1px solid #334155; color: #f1f5f9; padding: 10px 12px; border-radius: 6px; font-size: 14px; margin-bottom: 16px; outline: none; }}
        input:focus {{ border-color: #60a5fa; }}
        button {{ width: 100%; background: #3b82f6; color: #fff; border: none; padding: 10px; border-radius: 6px; font-size: 14px; cursor: pointer; font-weight: 600; }}
        button:hover {{ background: #2563eb; }}
        .error {{ background: rgba(220,38,38,.15); border: 1px solid rgba(220,38,38,.4); color: #f87171; font-size: 13px; padding: 8px 12px; border-radius: 6px; margin-bottom: 16px; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>GigForge Ops</h1>
        <p class="sub">Authorised personnel only</p>
        {error_block}
        <form method="POST" action="/login">
            <label>Username</label>
            <input type="text" name="username" autocomplete="username" autofocus required>
            <label>Password</label>
            <input type="password" name="password" autocomplete="current-password" required>
            <button type="submit">Sign In</button>
        </form>
    </div>
</body>
</html>"""


@app.middleware("http")
async def _auth_gate(request: Request, call_next):
    public = {"/login", "/login/"}
    if request.url.path in public:
        return await call_next(request)
    if not request.session.get("user"):
        return RedirectResponse(url="/login", status_code=302)
    return await call_next(request)


# SessionMiddleware must be added AFTER the auth middleware decorator so it runs
# first (outermost) and populates request.session before _auth_gate runs.
app.add_middleware(
    SessionMiddleware,
    secret_key=_SESSION_SECRET,
    session_cookie="gfops_session",
    https_only=False,
    max_age=86400,  # 24h
)


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return _LOGIN_HTML.format(error_block="")


@app.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if _verify_pw(username, password):
        request.session["user"] = _USERS[username.lower()]["name"]
        return RedirectResponse(url="/", status_code=302)
    err = '<div class="error">Invalid username or password.</div>'
    return HTMLResponse(_LOGIN_HTML.format(error_block=err), status_code=401)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)


def _db():
    import psycopg2
    conn = psycopg2.connect(**DB_CONN)
    conn.autocommit = True
    return conn


# ============================================================================
# API Endpoints
# ============================================================================

@app.get("/api/workflows")
async def get_workflows():
    from temporalio.client import Client
    client = await Client.connect("localhost:7233")
    workflows = []
    async for wf in client.list_workflows(""):
        h = client.get_workflow_handle(wf.id)
        try:
            desc = await h.describe()
            workflows.append({
                "id": wf.id,
                "type": wf.workflow_type,
                "status": STATUS_MAP.get(desc.status, str(desc.status)),
                "started": desc.start_time.isoformat() if desc.start_time else "",
                "closed": desc.close_time.isoformat() if desc.close_time else "",
            })
        except (AiElevateError, Exception):
            pass
    return {"workflows": sorted(workflows, key=lambda w: w["started"], reverse=True)[:50]}


@app.get("/api/health")
async def get_health():
    try:
        from agent_dispatch import circuit_status, check_gateway
        circuit = circuit_status()
        gateway = check_gateway()
    except (AgentError, Exception):
        circuit = {"status": "unknown"}
        gateway = False

    services = {}
    for svc in ["email-gateway", "temporal-worker", "build-worker", "project-worker", "orchestrator-worker", "ops-dashboard"]:
        result = subprocess.run(["systemctl", "is-active", svc], capture_output=True, text=True, timeout=5)
        services[svc] = result.stdout.strip()

    checkpoints = []
    cp_dir = Path("/opt/ai-elevate/workflow-checkpoints")
    if cp_dir.exists():
        for f in cp_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                done = sum(1 for s in data.get("steps", {}).values() if s.get("status") in ("completed", "skipped"))
                total = len(data.get("steps", {}))
                checkpoints.append({"project": f.stem, "progress": f"{done}/{total}", "updated": data.get("updated_at", "")})
            except (DatabaseError, Exception):
                pass

    return {
        "circuit_breaker": circuit,
        "gateway": "up" if gateway else "down",
        "services": services,
        "checkpoints": checkpoints,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/projects")
async def get_projects():
    """All projects with milestone progress."""
    conn = _db()
    cur = conn.cursor()
    projects = []

    cur.execute("""
        SELECT customer_email, project_title,
               COUNT(*) as total,
               SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) as done
        FROM project_milestones
        GROUP BY customer_email, project_title
        ORDER BY MAX(created_at) DESC
    """)
    for row in cur.fetchall():
        projects.append({
            "customer_id": row[0],
            "customer": mask_email(row[0]),
            "customer_display": mask_email(row[0]),
            "title": row[1],
            "total": row[2],
            "done": row[3],
            "progress": round(row[3] / row[2] * 100) if row[2] > 0 else 0,
        })

    conn.close()
    return {"projects": projects}


@app.get("/api/customers")
async def get_customers():
    """Customer pipeline — leads, active, delivered."""
    conn = _db()
    cur = conn.cursor()

    cur.execute("SELECT email, rating, notes, agent FROM customer_sentiment ORDER BY timestamp DESC LIMIT 30")
    cols = [d[0] for d in cur.description]
    customers = []
    for r in cur.fetchall():
        d = dict(zip(cols, r))
        d["email_display"] = mask_email(d.pop("email", "") or "")
        customers.append(d)

    cur.execute("SELECT email, note, agent, timestamp FROM customer_notes ORDER BY timestamp DESC LIMIT 30")
    cols = [d[0] for d in cur.description]
    notes = []
    for r in cur.fetchall():
        d = dict(zip(cols, r))
        if d.get("timestamp"):
            d["timestamp"] = d["timestamp"].isoformat()
        d["email_display"] = mask_email(d.pop("email", "") or "")
        notes.append(d)

    conn.close()
    return {"customers": customers, "notes": notes}


@app.get("/api/emails")
async def get_emails():
    """Recent email activity."""
    conn = _db()
    cur = conn.cursor()
    cur.execute("""
        SELECT sender_email, agent_id, subject, direction, status, created_at
        FROM email_interactions ORDER BY created_at DESC LIMIT 30
    """)
    cols = [d[0] for d in cur.description]
    emails = []
    for r in cur.fetchall():
        d = dict(zip(cols, r))
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat()
        d["sender_display"] = mask_email(d.pop("sender_email", "") or "")
        emails.append(d)
    conn.close()
    return {"emails": emails}


@app.get("/api/sites")
async def get_sites():
    """Deployed sites with live status check."""
    sites = [
        {"name": "GigForge Website", "url": "https://gigforge.ai", "port": 4091},
        {"name": "TechUni Website", "url": "https://techuni.ai", "port": 4090},
        {"name": "Sudačka Mreža", "url": "https://sudacka-demo.gigforge.ai", "port": 4092},
        {"name": "Contact Manager", "url": "https://contacts-demo.gigforge.ai", "port": 4098},
        {"name": "SaaS Billing API", "url": "https://billing-demo.gigforge.ai", "port": 4100},
        {"name": "DevOps Starter Kit", "url": "https://devops-demo.gigforge.ai", "port": 4101},
        {"name": "CRM Platform", "url": "https://crm-demo.gigforge.ai", "port": 3090},
        {"name": "Ops Dashboard", "url": "https://dashboard.gigforge.ai", "port": 8070},
    ]
    for s in sites:
        try:
            r = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"http://localhost:{s['port']}/"],
                capture_output=True, text=True, timeout=5)
            s["status"] = "up" if r.stdout.strip() in ("200", "301", "302", "304") else "down"
            s["code"] = r.stdout.strip()
        except (AgentError, Exception):
            s["status"] = "down"
            s["code"] = "000"
    return {"sites": sites}


@app.get("/api/agents")
async def get_agents():
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT agent_id, name, role FROM agent_bios WHERE name != '' ORDER BY agent_id")
    agents = [{"id": r[0], "name": r[1], "role": r[2]} for r in cur.fetchall()]
    conn.close()

    result = subprocess.run(["pgrep", "-fa", "openclaw-agent"], capture_output=True, text=True, timeout=5)
    running = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()] if result.returncode == 0 else []
    return {"agents": agents, "running": running}


@app.get("/api/proposals-legacy")
async def get_proposals_legacy():
    """Legacy file-based proposals (kept for reference)."""
    import re as _re
    proposals = []
    proposals_dir = Path("/opt/ai-elevate/gigforge/memory/proposals")
    if proposals_dir.exists():
        for f in sorted(proposals_dir.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True)[:30]:
            parts = f.stem.split("-")
            if len(parts) > 3 and parts[0].isdigit():
                client_name = " ".join(p.capitalize() for p in parts[3:])
                date_str = "-".join(parts[:3])
            else:
                client_name = f.stem
                date_str = ""
            practice, value, status = "other", None, "pending"
            try:
                content = f.read_text(errors="replace")[:4000]
                cl = content.lower()
                for p_key, p_terms in [
                    ("ai-automation", ["ai-automation", "rag", "llm", "ai agent", "automation"]),
                    ("programming", ["programming", "full-stack", "backend", "frontend", "react", "node.js", "api"]),
                    ("video", ["video", "motion graphics", "editing"]),
                    ("marketing", ["marketing", "seo", "ads", "social media"]),
                    ("devops", ["devops", "docker", "ci/cd", "kubernetes"]),
                ]:
                    if any(t in cl or t in f.name.lower() for t in p_terms):
                        practice = p_key
                        break
                m = _re.search(r'\$\s*([\d,]+)', content) or _re.search(r'EUR?\s*([\d,]+)', content, _re.IGNORECASE)
                if m:
                    try:
                        value = int(m.group(1).replace(",", ""))
                    except (AgentError, Exception):
                        pass
                if any(w in cl for w in ["accepted", "won", "confirmed"]):
                    status = "accepted"
                elif any(w in cl for w in ["rejected", "declined", "lost"]):
                    status = "rejected"
                elif any(w in cl for w in ["negotiating", "counter-offer"]):
                    status = "negotiating"
                elif any(w in cl for w in ["submitted", "ready to submit", "proposal sent"]):
                    status = "sent"
            except (AgentError, Exception):
                pass
            proposals.append({"file": f.name, "client": client_name, "practice": practice,
                               "value": value, "status": status, "date": date_str})
    return {"proposals": proposals}


@app.get("/api/approvals")
async def get_approvals():
    conn = _db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, customer_email, project_title, price_eur, status, created_at FROM proposal_queue WHERE status='pending' ORDER BY created_at DESC")
        cols = [d[0] for d in cur.description]
        proposals = []
        for r in cur.fetchall():
            d = dict(zip(cols, r))
            if d.get("created_at"):
                d["created_at"] = d["created_at"].isoformat()
            d["customer_display"] = mask_email(d.pop("customer_email", "") or "")
            proposals.append(d)
    except (DatabaseError, Exception):
        proposals = []
    conn.close()
    return {"approvals": proposals}


@app.post("/api/approvals/{item_id}/approve")
async def approve_item(item_id: int):
    conn = _db()
    conn.cursor().execute("UPDATE proposal_queue SET status='approved', approved_by='braun' WHERE id=%s", (item_id,))
    conn.close()
    return {"status": "approved", "id": item_id}


@app.post("/api/approvals/{item_id}/reject")
async def reject_item(item_id: int):
    conn = _db()
    conn.cursor().execute("UPDATE proposal_queue SET status='rejected' WHERE id=%s", (item_id,))
    conn.close()
    return {"status": "rejected", "id": item_id}


@app.post("/api/actions/restart/{service}")
async def restart_service(service: str):
    allowed = ["email-gateway", "temporal-worker", "build-worker", "project-worker", "orchestrator-worker"]
    if service not in allowed:
        return JSONResponse({"error": "Not allowed"}, status_code=403)
    subprocess.run(["sudo", "systemctl", "restart", service], timeout=10)
    return {"status": "restarted", "service": service}


@app.post("/api/actions/scout")
async def run_scout():
    subprocess.Popen(
        ["/opt/ai-elevate/cron/job-scout-pipeline.sh"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"status": "started"}


@app.get("/api/kpis")
async def get_kpis():
    """KPI summary bar — live business metrics from DB and process checks."""
    import psycopg2
    conn = psycopg2.connect(**DB_CONN)
    cur = conn.cursor()

    try:
        cur.execute("SELECT COUNT(*) FROM proposal_queue WHERE status='pending'")
        pending_approvals = int(cur.fetchone()[0])
    except Exception:
        pending_approvals = 0

    try:
        cur.execute("SELECT COUNT(*) FROM proposal_queue WHERE status='submitted'")
        submitted = int(cur.fetchone()[0])
    except Exception:
        submitted = 0

    try:
        cur.execute("SELECT COALESCE(SUM(CAST(NULLIF(regexp_replace(recommended_bid, '[^0-9.]', '', 'g'), '') AS NUMERIC)), 0) FROM proposal_queue WHERE status='submitted'")
        pipeline_value = int(cur.fetchone()[0])
    except Exception:
        pipeline_value = 0

    try:
        cur.execute("SELECT COUNT(*) FROM email_interactions WHERE created_at::date = CURRENT_DATE")
        emails_today = int(cur.fetchone()[0])
    except Exception:
        emails_today = 0

    services_down = 0
    critical_processes = {
        "email-gateway": "email-gateway",
        "temporal-server": "temporal-server",
        "temporal-workflows": "temporal_workflows",
        "orchestrator-worker": "orchestrator_worker",
        "support-chain-worker": "support_chain_worker",
    }
    down_list = []
    for name, grep_pattern in critical_processes.items():
        try:
            r = subprocess.run(["pgrep", "-f", grep_pattern], capture_output=True, text=True, timeout=3)
            if r.returncode != 0:
                services_down += 1
                down_list.append(name)
        except Exception:
            services_down += 1
            down_list.append(name)

    try:
        r = subprocess.run(["pgrep", "-fc", "openclaw"], capture_output=True, text=True, timeout=3)
        active_agents = int(r.stdout.strip()) if r.returncode == 0 else 0
    except Exception:
        active_agents = 0

    conn.close()
    return {
        "pending_approvals": pending_approvals,
        "active_projects": submitted,
        "open_proposals": pipeline_value,
        "services_down": services_down,
        "emails_today": emails_today,
        "down_list": down_list,
        "active_agents": active_agents,
    }


@app.get("/api/alerts")
async def get_alerts():
    """Critical alerts panel — actionable issues requiring attention."""
    alerts = []
    conn = _db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM proposal_queue WHERE status='pending'")
        n = int(cur.fetchone()[0])
        if n > 0:
            alerts.append({"severity": "critical", "icon": "🔔",
                           "message": f"{n} proposal{'s' if n > 1 else ''} awaiting your approval",
                           "action": "/approvals", "action_label": "Go to Approvals"})
    except (DatabaseError, Exception):
        pass
    down_svcs = []
    for svc, pattern in [("email-gateway", "email-gateway"), ("temporal-server", "temporal-server"),
                          ("temporal-workflows", "temporal_workflows"), ("orchestrator-worker", "orchestrator_worker"),
                          ("support-chain-worker", "support_chain_worker")]:
        try:
            r = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, timeout=3)
            if r.returncode != 0:
                down_svcs.append(svc)
        except Exception:
            down_svcs.append(svc)
    if down_svcs:
        alerts.append({"severity": "critical", "icon": "🔴",
                       "message": f"Service(s) down: {', '.join(down_svcs)}",
                       "action": None, "action_label": None})
    try:
        cur.execute("""SELECT customer_email, project_title, COUNT(*) as total,
            SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) as done
            FROM project_milestones GROUP BY customer_email, project_title
            HAVING SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END)::float / NULLIF(COUNT(*),0) < 0.3
            AND MIN(created_at) < NOW() - INTERVAL '7 days'""")
        for row in cur.fetchall():
            alerts.append({"severity": "warning", "icon": "⚠️",
                           "message": f"Stalled project: {row[1]} — {mask_email(row[0])} ({row[3]}/{row[2]} milestones done)",
                           "action": None, "action_label": None})
    except (DatabaseError, Exception):
        pass
    try:
        cur.execute("SELECT COUNT(*) FROM customer_sentiment WHERE rating='frustrated' AND timestamp > NOW() - INTERVAL '24 hours'")
        n = int(cur.fetchone()[0])
        if n > 0:
            alerts.append({"severity": "warning", "icon": "😟",
                           "message": f"{n} frustrated customer(s) flagged in the last 24h",
                           "action": None, "action_label": None})
    except (DatabaseError, Exception):
        pass
    conn.close()
    return {"alerts": alerts, "all_clear": len(alerts) == 0}


@app.get("/api/emails/by-customer")
async def get_emails_by_customer():
    """Email activity grouped by customer/sender."""
    conn = _db()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT sender_email, COUNT(*) as total, MAX(created_at) as last_contact,
                SUM(CASE WHEN direction='inbound' THEN 1 ELSE 0 END) as inbound_count,
                SUM(CASE WHEN direction='outbound' THEN 1 ELSE 0 END) as outbound_count,
                (SELECT subject FROM email_interactions ei2 WHERE ei2.sender_email = ei.sender_email
                 ORDER BY created_at DESC LIMIT 1) as last_subject
            FROM email_interactions ei GROUP BY sender_email ORDER BY MAX(created_at) DESC LIMIT 25""")
        cols = [d[0] for d in cur.description]
        customers = []
        for r in cur.fetchall():
            d = dict(zip(cols, r))
            if d.get("last_contact"):
                d["last_contact"] = d["last_contact"].isoformat()
            d["sender_display"] = mask_email(d.pop("sender_email", "") or "")
            customers.append(d)
    except (DatabaseError, Exception):
        customers = []
    conn.close()
    return {"customers": customers}


@app.get("/api/workflows/{workflow_id}")
async def get_workflow_detail(workflow_id: str):
    """Get activity history for a specific workflow."""
    from temporalio.client import Client
    client = await Client.connect("localhost:7233")
    h = client.get_workflow_handle(workflow_id)
    desc = await h.describe()

    activities = []
    async for e in h.fetch_history_events():
        if hasattr(e, "activity_task_scheduled_event_attributes"):
            a = e.activity_task_scheduled_event_attributes
            if a and a.activity_type and a.activity_type.name:
                activities.append({"type": "scheduled", "name": a.activity_type.name, "time": str(e.event_time)})
        if hasattr(e, "activity_task_completed_event_attributes"):
            activities.append({"type": "completed", "time": str(e.event_time)})
        if hasattr(e, "activity_task_failed_event_attributes"):
            a = e.activity_task_failed_event_attributes
            msg = a.failure.message[:200] if a and a.failure and a.failure.message else ""
            activities.append({"type": "failed", "error": msg, "time": str(e.event_time)})

    return {
        "id": workflow_id,
        "status": STATUS_MAP.get(desc.status, str(desc.status)),
        "started": desc.start_time.isoformat() if desc.start_time else "",
        "closed": desc.close_time.isoformat() if desc.close_time else "",
        "activities": activities,
    }


@app.get("/api/projects/{customer_email}")
async def get_project_detail(customer_email: str):
    """Get milestones for a specific customer project."""
    conn = _db()
    cur = conn.cursor()
    cur.execute(
        "SELECT milestone, status, completed_at FROM project_milestones WHERE customer_email=%s ORDER BY id",
        (customer_email,))
    milestones = [{"name": r[0], "status": r[1], "completed": r[2].isoformat() if r[2] else None} for r in cur.fetchall()]

    cur.execute(
        "SELECT note, agent, timestamp FROM customer_notes WHERE email=%s ORDER BY timestamp DESC LIMIT 10",
        (customer_email,))
    notes = [{"note": r[0], "agent": r[1], "time": r[2].isoformat() if r[2] else ""} for r in cur.fetchall()]

    conn.close()
    return {"customer": mask_email(customer_email), "milestones": milestones, "notes": notes}


# ============================================================================
# HTML Pages
# ============================================================================

async def get_proposals():
    """Get proposal queue for dashboard."""
    try:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(**DB_CONN)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT id, job_title, job_url, job_budget, platform, status, "
            "recommended_bid, drafted_by, created_at, cover_letter, milestones, screening_answers, bid_type, due_date "
            "FROM proposal_queue ORDER BY created_at DESC LIMIT 30"
        )
        rows = cur.fetchall()
        conn.close()
        for r in rows:
            if r.get("created_at"):
                r["created_at"] = r["created_at"].isoformat()
        return rows
    except Exception as e:
        return [{"error": str(e)}]


@app.get("/api/proposals")
async def api_proposals():
    """Proposals queue API."""
    return await get_proposals()


@app.post("/api/proposals/{proposal_id}/approve")
async def approve_proposal(proposal_id: str):
    """Approve a proposal."""
    try:
        import sys
        sys.path.insert(0, "/home/aielevate")
        from proposal_approval_system import update_status, send_approved_proposal
        import psycopg2, psycopg2.extras
        conn = psycopg2.connect(**DB_CONN)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM proposal_queue WHERE id = %s", (proposal_id,))
        p = cur.fetchone()
        conn.close()
        if not p:
            return {"error": "not found"}
        update_status(proposal_id, "approved")
        send_approved_proposal(p)
        return {"status": "approved", "title": p["job_title"]}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/proposals/{proposal_id}/reject")
async def reject_proposal(proposal_id: str):
    """Reject a proposal."""
    try:
        from proposal_approval_system import update_status
        update_status(proposal_id, "rejected")
        return {"status": "rejected"}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/proposals/{proposal_id}/submitted")
async def mark_submitted(proposal_id: str):
    """Mark a proposal as submitted on the platform."""
    try:
        from proposal_approval_system import update_status
        update_status(proposal_id, "submitted")
        return {"status": "submitted"}
    except Exception as e:
        return {"error": str(e)}


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return (_TEMPLATE_DIR / "dashboard.html").read_text()


@app.get("/api/orgs")
async def get_orgs():
    """List all organizations."""
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT substring(agent_id from '^([^-]+)') as org FROM agent_bios WHERE agent_id LIKE '%-%' ORDER BY org")
    orgs = []
    for (org_slug,) in cur.fetchall():
        cur.execute("SELECT count(*) FROM agent_bios WHERE agent_id LIKE %s", (f"{org_slug}-%",))
        count = cur.fetchone()[0]
        cur.execute("SELECT name, role FROM agent_bios WHERE agent_id = %s", (org_slug,))
        root = cur.fetchone()
        orgs.append({
            "slug": org_slug,
            "director": root[0] if root else "",
            "director_role": root[1] if root else "",
            "agent_count": count,
        })
    conn.close()
    return {"orgs": orgs}


@app.post("/api/orgs/build")
async def build_new_org(request: Request):
    """Start building a new organization."""
    data = await request.json()
    name = data.get("name", "")
    domain = data.get("domain", "")
    description = data.get("description", "")
    org_type = data.get("org_type", "commercial")
    if not name:
        return JSONResponse({"error": "Name required"}, status_code=400)
    try:
        from org_builder import build_organization
        result = await build_organization(name, domain=domain, description=description, org_type=org_type)
        return result
    except (AiElevateError, Exception) as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/orgs/{slug}")
async def get_org_detail(slug: str):
    """Get details of a specific org."""
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT agent_id, name, role FROM agent_bios WHERE agent_id LIKE %s OR agent_id = %s ORDER BY agent_id", (f"{slug}-%", slug))
    agents = [{"id": r[0], "name": r[1], "role": r[2]} for r in cur.fetchall()]
    conn.close()

    org_dir = Path(f"/opt/ai-elevate/{slug}")
    files = {}
    for fname in ["org-design.json", "team-roster.txt", "email-aliases.json", "dns-requirements.md", "ORG_SUMMARY.md"]:
        f = org_dir / fname
        files[fname] = f.exists()

    return {"slug": slug, "agents": agents, "files": files}


@app.get("/build", response_class=HTMLResponse)
async def build_page():
    return (_TEMPLATE_DIR / "build.html").read_text()


@app.get("/approvals", response_class=HTMLResponse)
async def approvals_page():
    return (_TEMPLATE_DIR / "approvals.html").read_text()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8071)
