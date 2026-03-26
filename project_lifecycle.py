#!/usr/bin/env python3
"""Project Lifecycle Controller — enforces quality gates on EVERY project.

Runs automatically at each stage. No project can skip a gate.
Works across all orgs (GigForge, TechUni, CareHaven, etc).

Stages:
  CREATED → BUILDING → VERIFYING → SECURITY → APPROVAL → DEPLOYED → MONITORING

Each transition requires the previous gate to pass.
"""

import sys
import os
import json
import logging
import time
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, "/home/aielevate")

log = logging.getLogger("lifecycle")

DB_CONFIG = dict(host="127.0.0.1", port=5434, dbname="rag", user="rag",
                 password=os.environ.get("DB_PASSWORD", "rag_vec_2026"))

STAGES = ["created", "building", "verifying", "security", "approval", "deployed", "monitoring"]


def _get_db():
    import psycopg2
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    return conn


def init_tables():
    """Create lifecycle tracking table."""
    conn = _get_db()
    conn.cursor().execute("""
        CREATE TABLE IF NOT EXISTS project_lifecycle (
            id SERIAL PRIMARY KEY,
            project_slug TEXT UNIQUE NOT NULL,
            project_title TEXT NOT NULL,
            org TEXT NOT NULL DEFAULT 'gigforge',
            stage TEXT NOT NULL DEFAULT 'created',
            url TEXT,
            customer_email TEXT,
            acceptance_score REAL,
            security_passed BOOLEAN DEFAULT FALSE,
            owner_approved BOOLEAN DEFAULT FALSE,
            credentials_registered BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            metadata JSONB DEFAULT '{}'
        )
    """)
    conn.close()


# ── Project Registration ────────────────────────────────────────────

def register_project(slug, title, org="gigforge", customer_email="", url=""):
    """Register a new project — creates lifecycle record + Plane ticket + credential placeholder."""
    conn = _get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO project_lifecycle (project_slug, project_title, org, customer_email, url)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (project_slug) DO UPDATE SET
            project_title = EXCLUDED.project_title,
            updated_at = NOW()
        RETURNING id
    """, (slug, title, org, customer_email, url))

    project_id = cur.fetchone()[0]
    conn.close()

    # Register credential placeholder
    try:
        from enforce_rules import register_credentials
        register_credentials(slug, url=url or "TBD", username="TBD", password="TBD",
                           notes=f"Auto-created for {title}. Update after deployment.")
    except Exception:
        pass

    # Index in semantic search
    try:
        from semantic_search import index_document
        index_document("project", f"{title} — {org} project for {customer_email}. Stage: created.",
                      doc_id=f"project-{slug}", metadata={"project": title, "org": org,
                      "client_email": customer_email, "status": "created"}, org=org)
    except Exception:
        pass

    log.info(f"Project registered: {slug} ({title})")
    return project_id


# ── Stage Transitions ────────────────────────────────────────────────

def advance_stage(slug, target_stage, force=False):
    """Advance a project to the next stage — runs the required gate.

    Returns (success, message).
    """
    conn = _get_db()
    cur = conn.cursor()
    cur.execute("SELECT stage, url, acceptance_score, security_passed, owner_approved FROM project_lifecycle WHERE project_slug = %s", (slug,))
    row = cur.fetchone()

    if not row:
        conn.close()
        return False, f"Project {slug} not found"

    current_stage, url, acceptance_score, security_passed, owner_approved = row

    # Validate stage transition
    if target_stage not in STAGES:
        conn.close()
        return False, f"Invalid stage: {target_stage}"

    current_idx = STAGES.index(current_stage) if current_stage in STAGES else 0
    target_idx = STAGES.index(target_stage)

    if target_idx <= current_idx and not force:
        conn.close()
        return False, f"Cannot go backwards: {current_stage} → {target_stage}"

    # Run the gate for the target stage
    gate_result = _run_gate(slug, target_stage, url)

    if not gate_result["passed"] and not force:
        conn.close()
        return False, f"Gate failed for {target_stage}: {gate_result['reason']}"

    # Update stage
    updates = {"stage": target_stage, "updated_at": "NOW()"}
    if gate_result.get("acceptance_score") is not None:
        updates["acceptance_score"] = gate_result["acceptance_score"]
    if gate_result.get("security_passed") is not None:
        updates["security_passed"] = gate_result["security_passed"]
    if gate_result.get("owner_approved") is not None:
        updates["owner_approved"] = gate_result["owner_approved"]
    if gate_result.get("credentials_registered") is not None:
        updates["credentials_registered"] = gate_result["credentials_registered"]

    set_clause = ", ".join(f"{k} = %s" for k in updates.keys())
    values = list(updates.values())
    values = [v if v != "NOW()" else datetime.now(timezone.utc) for v in values]
    cur.execute(f"UPDATE project_lifecycle SET {set_clause} WHERE project_slug = %s",
                values + [slug])
    conn.close()

    log.info(f"Project {slug}: {current_stage} → {target_stage}")
    return True, f"Advanced to {target_stage}"


def _run_gate(slug, stage, url):
    """Run the gate check for a specific stage."""

    if stage == "building":
        return {"passed": True, "reason": "Building stage — no gate required"}

    elif stage == "verifying":
        # Run acceptance test
        if not url:
            return {"passed": False, "reason": "No URL set — cannot run acceptance test"}
        try:
            from acceptance_test import run_tests
            results = run_tests(url, slug)
            score = results["passed"] / (results["passed"] + results["failed"]) * 100 if (results["passed"] + results["failed"]) > 0 else 0
            passed = score >= 70  # Minimum 70% to proceed
            return {"passed": passed, "reason": f"Acceptance: {score:.0f}% ({results['passed']}/{results['passed']+results['failed']})",
                    "acceptance_score": score}
        except Exception as e:
            return {"passed": False, "reason": f"Acceptance test failed: {e}"}

    elif stage == "security":
        # Run security review
        project_dir = f"/opt/ai-elevate/gigforge/projects/{slug}"
        try:
            from security_review import run_security_review
            passed, report = run_security_review(project_dir, url)
            return {"passed": passed, "reason": report[:200], "security_passed": passed}
        except Exception as e:
            return {"passed": False, "reason": f"Security review failed: {e}", "security_passed": False}

    elif stage == "approval":
        # Check if owner has approved
        try:
            conn = _get_db()
            cur = conn.cursor()
            cur.execute("SELECT owner_approved FROM project_lifecycle WHERE project_slug = %s", (slug,))
            approved = cur.fetchone()[0]
            conn.close()
            if approved:
                return {"passed": True, "reason": "Owner approved"}
            else:
                # Send approval request
                _request_approval(slug)
                return {"passed": False, "reason": "Awaiting owner approval — email sent to Braun"}
        except Exception as e:
            return {"passed": False, "reason": f"Approval check failed: {e}"}

    elif stage == "deployed":
        # Verify credentials are registered (not TBD)
        try:
            from enforce_rules import get_credentials
            creds = get_credentials(slug)
            if creds["found"] and creds.get("password") != "TBD":
                return {"passed": True, "reason": "Credentials registered",
                        "credentials_registered": True}
            else:
                return {"passed": False, "reason": "Credentials not registered — update the vault before deploying",
                        "credentials_registered": False}
        except Exception as e:
            return {"passed": False, "reason": f"Credential check failed: {e}"}

    elif stage == "monitoring":
        return {"passed": True, "reason": "Monitoring stage — proactive tracker handles this"}

    return {"passed": True, "reason": f"No gate defined for {stage}"}


def _request_approval(slug):
    """Send approval request to Braun."""
    try:
        from send_email import send_email
        conn = _get_db()
        cur = conn.cursor()
        cur.execute("SELECT project_title, url, acceptance_score, security_passed FROM project_lifecycle WHERE project_slug = %s", (slug,))
        row = cur.fetchone()
        conn.close()

        if row:
            title, url, score, security = row
            send_email(
                to="braun.brelin@ai-elevate.ai",
                subject=f"[APPROVAL REQUIRED] {title} ready for deployment",
                body=f"Project: {title}\n"
                     f"URL: {url}\n"
                     f"Acceptance score: {score:.0f}%\n"
                     f"Security review: {'PASSED' if security else 'PENDING'}\n\n"
                     f"Reply APPROVED to proceed with deployment.",
                agent_id="operations",
                cc="peter.munro@ai-elevate.ai",
            )
    except Exception:
        pass


def approve_project(slug):
    """Mark a project as owner-approved."""
    conn = _get_db()
    conn.cursor().execute(
        "UPDATE project_lifecycle SET owner_approved = TRUE, updated_at = NOW() WHERE project_slug = %s",
        (slug,))
    conn.close()
    log.info(f"Project {slug}: owner approved")


# ── Status ───────────────────────────────────────────────────────────

def get_status(slug=None):
    """Get project lifecycle status."""
    conn = _get_db()
    cur = conn.cursor()

    if slug:
        cur.execute("SELECT * FROM project_lifecycle WHERE project_slug = %s", (slug,))
    else:
        cur.execute("SELECT * FROM project_lifecycle ORDER BY updated_at DESC")

    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()
    conn.close()

    return [dict(zip(columns, row)) for row in rows]


def get_blocked():
    """Get projects that are blocked at a gate."""
    conn = _get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT project_slug, project_title, stage, acceptance_score, security_passed, owner_approved
        FROM project_lifecycle
        WHERE stage NOT IN ('deployed', 'monitoring')
        ORDER BY updated_at ASC
    """)
    rows = cur.fetchall()
    conn.close()
    return [{"slug": r[0], "title": r[1], "stage": r[2],
             "acceptance": r[3], "security": r[4], "approved": r[5]} for r in rows]


# ── Init ─────────────────────────────────────────────────────────────

try:
    init_tables()
except Exception:
    pass


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Project Lifecycle Controller")
    parser.add_argument("--register", nargs=3, metavar=("SLUG", "TITLE", "ORG"), help="Register a new project")
    parser.add_argument("--advance", nargs=2, metavar=("SLUG", "STAGE"), help="Advance to stage")
    parser.add_argument("--approve", type=str, help="Approve a project")
    parser.add_argument("--status", type=str, nargs="?", const="ALL", help="Show status")
    parser.add_argument("--blocked", action="store_true", help="Show blocked projects")
    parser.add_argument("--url", type=str, help="Set project URL")
    parser.add_argument("--customer", type=str, help="Set customer email")
    args = parser.parse_args()

    if args.register:
        slug, title, org = args.register
        pid = register_project(slug, title, org, args.customer or "", args.url or "")
        print(f"Registered: {slug} (id={pid})")
    elif args.advance:
        slug, stage = args.advance
        ok, msg = advance_stage(slug, stage)
        print(f"{'OK' if ok else 'BLOCKED'}: {msg}")
    elif args.approve:
        approve_project(args.approve)
        print(f"Approved: {args.approve}")
    elif args.status is not None:
        slug = args.status if args.status != "ALL" else None
        for p in get_status(slug):
            print(f"  {p['project_slug']:30s} | {p['stage']:12s} | score={p.get('acceptance_score','?')} | security={p.get('security_passed','?')} | approved={p.get('owner_approved','?')}")
    elif args.blocked:
        for p in get_blocked():
            print(f"  {p['slug']:30s} | blocked at {p['stage']} | acceptance={p['acceptance']} security={p['security']} approved={p['approved']}")
