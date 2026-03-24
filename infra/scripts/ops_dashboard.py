#!/usr/bin/env python3
"""GigForge Ops Dashboard — full operations center."""
import asyncio
import json
import os
import sys
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

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
        except Exception:
            pass
    return {"workflows": sorted(workflows, key=lambda w: w["started"], reverse=True)[:50]}


@app.get("/api/health")
async def get_health():
    try:
        from agent_dispatch import circuit_status, check_gateway
        circuit = circuit_status()
        gateway = check_gateway()
    except Exception:
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
            except Exception:
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
            "customer": row[0],
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

    cur.execute("SELECT email, rating, notes, agent FROM customer_sentiment ORDER BY updated_at DESC LIMIT 30")
    cols = [d[0] for d in cur.description]
    customers = [dict(zip(cols, r)) for r in cur.fetchall()]

    cur.execute("SELECT email, note, agent, timestamp FROM customer_notes ORDER BY timestamp DESC LIMIT 30")
    cols = [d[0] for d in cur.description]
    notes = [dict(zip(cols, r)) for r in cur.fetchall()]
    for n in notes:
        if n.get("timestamp"):
            n["timestamp"] = n["timestamp"].isoformat()

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
        except Exception:
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


@app.get("/api/proposals")
async def get_proposals():
    """Proposals with parsed client name, practice, value, and status."""
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
                    except Exception:
                        pass
                if any(w in cl for w in ["accepted", "won", "confirmed"]):
                    status = "accepted"
                elif any(w in cl for w in ["rejected", "declined", "lost"]):
                    status = "rejected"
                elif any(w in cl for w in ["negotiating", "counter-offer"]):
                    status = "negotiating"
                elif any(w in cl for w in ["submitted", "ready to submit", "proposal sent"]):
                    status = "sent"
            except Exception:
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
            proposals.append(d)
    except Exception:
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
    """KPI summary bar — headline business metrics."""
    conn = _db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM proposal_queue WHERE status='pending'")
        pending_approvals = int(cur.fetchone()[0])
    except Exception:
        pending_approvals = 0
    try:
        cur.execute("""SELECT COUNT(*) FROM (
            SELECT customer_email FROM project_milestones
            GROUP BY customer_email
            HAVING SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) < COUNT(*)) sub""")
        active_projects = int(cur.fetchone()[0])
    except Exception:
        active_projects = 0
    proposals_dir = Path("/opt/ai-elevate/gigforge/memory/proposals")
    this_month = datetime.now().strftime("%Y-%m")
    open_proposals = sum(1 for f in proposals_dir.glob("*.md") if f.name.startswith(this_month)) if proposals_dir.exists() else 0
    services_down = 0
    for svc in ["email-gateway", "temporal-worker", "build-worker", "project-worker", "orchestrator-worker"]:
        try:
            r = subprocess.run(["systemctl", "is-active", svc], capture_output=True, text=True, timeout=3)
            if r.stdout.strip() != "active":
                services_down += 1
        except Exception:
            services_down += 1
    try:
        cur.execute("SELECT COUNT(*) FROM email_interactions WHERE created_at::date = CURRENT_DATE")
        emails_today = int(cur.fetchone()[0])
    except Exception:
        emails_today = 0
    conn.close()
    return {"pending_approvals": pending_approvals, "active_projects": active_projects,
            "open_proposals": open_proposals, "services_down": services_down, "emails_today": emails_today}


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
    except Exception:
        pass
    down_svcs = []
    for svc in ["email-gateway", "temporal-worker", "build-worker", "project-worker", "orchestrator-worker"]:
        try:
            r = subprocess.run(["systemctl", "is-active", svc], capture_output=True, text=True, timeout=3)
            if r.stdout.strip() != "active":
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
                           "message": f"Stalled project: {row[1]} ({row[3]}/{row[2]} milestones done)",
                           "action": None, "action_label": None})
    except Exception:
        pass
    try:
        cur.execute("SELECT COUNT(*) FROM customer_sentiment WHERE rating='frustrated' AND updated_at > NOW() - INTERVAL '24 hours'")
        n = int(cur.fetchone()[0])
        if n > 0:
            alerts.append({"severity": "warning", "icon": "😟",
                           "message": f"{n} frustrated customer(s) flagged in the last 24h",
                           "action": None, "action_label": None})
    except Exception:
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
            customers.append(d)
    except Exception:
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
    return {"customer": customer_email, "milestones": milestones, "notes": notes}

# ============================================================================
# HTML Pages
# ============================================================================

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
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/orgs/{slug}")
async def get_org_detail(slug: str):
    """Get details of a specific org."""
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT agent_id, name, role FROM agent_bios WHERE agent_id LIKE %s OR agent_id = %s ORDER BY agent_id", (f"{slug}-%", slug))
    agents = [{"id": r[0], "name": r[1], "role": r[2]} for r in cur.fetchall()]
    conn.close()

    # Check for org files
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
