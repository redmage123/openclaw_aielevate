#!/usr/bin/env python3
"""Owner Directives — authoritative decisions that ALL agents must respect.

When the owner (Braun) issues a directive, it is recorded here and every agent
must check it before taking action. Directives override all other context.

Usage:
  from directives import add_directive, check_directive, get_all_directives, is_blocked

  # Owner says "kill BAM Spark"
  add_directive("cancel_project", subject="BAM Spark", reason="Not an active project", by="braun")

  # Any agent before referencing a project:
  if is_blocked("BAM Spark"):
      # Do NOT reference this project
      pass

  # Check all active directives
  for d in get_all_directives():
      print(d)

  # Get a summary string for injection into agent prompts
  from directives import directives_summary
  print(directives_summary())  # Returns all active directives as text

Directive types:
  cancel_project  — project is dead, never reference it
  cancel_deal     — deal is cancelled, remove from pipeline
  block_agent     — do not dispatch this agent
  block_customer  — do not respond to this customer
  rename          — entity has been renamed, use new name
  policy          — general policy directive (e.g. "no phone calls")
  priority        — override priority of something
  custom          — freeform directive

CLI:
  python3 directives.py add --type cancel_project --subject "BAM Spark" --reason "Owner cancelled"
  python3 directives.py list
  python3 directives.py check "BAM Spark"
  python3 directives.py revoke --id 3
"""

import json
import sys
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

DB_HOST = "127.0.0.1"
DB_PORT = 5434
DB_NAME = "rag"
DB_USER = "rag"
DB_PASS = "rag_vec_2026"

# People authorized to issue directives
AUTHORIZED_ISSUERS = {
    "braun", "braun.brelin@ai-elevate.ai", "bbrelin@gmail.com",
    "peter", "pete", "peter.munro@ai-elevate.ai",
    "mike", "mike.burton@ai-elevate.ai",
    "charlie", "charlotte", "charlie.turking@ai-elevate.ai",
}


def _get_db():
    conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""CREATE TABLE IF NOT EXISTS owner_directives (
        id SERIAL PRIMARY KEY,
        type TEXT NOT NULL,
        subject TEXT NOT NULL,
        reason TEXT,
        issued_by TEXT DEFAULT 'braun',
        issued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        revoked_at TIMESTAMPTZ,
        active BOOLEAN DEFAULT TRUE
    )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_directives_subject ON owner_directives(subject)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_directives_active ON owner_directives(active)")
    return conn, cur


def add_directive(directive_type: str, subject: str, reason: str = "", by: str = "braun") -> dict:
    """Record an owner directive."""
    conn, cur = _get_db()
    cur.execute(
        "INSERT INTO owner_directives (type, subject, reason, issued_by) VALUES (%s, %s, %s, %s) RETURNING id, type, subject, reason, issued_at",
        (directive_type, subject, reason, by)
    )
    result = dict(cur.fetchone())
    conn.close()

    # Auto-archive if cancelling a project
    if directive_type == "cancel_project":
        try:
            result["archived"] = archive_project(subject, reason)
        except Exception:
            pass

    return result


def revoke_directive(directive_id: int) -> dict:
    """Revoke a directive (no longer active)."""
    conn, cur = _get_db()
    cur.execute(
        "UPDATE owner_directives SET active=FALSE, revoked_at=NOW() WHERE id=%s RETURNING id, subject",
        (directive_id,)
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else {"error": "not found"}


def get_all_directives(active_only: bool = True) -> list:
    """Get all directives."""
    conn, cur = _get_db()
    if active_only:
        cur.execute("SELECT * FROM owner_directives WHERE active=TRUE ORDER BY issued_at DESC")
    else:
        cur.execute("SELECT * FROM owner_directives ORDER BY issued_at DESC")
    results = [dict(r) for r in cur.fetchall()]
    conn.close()
    return results


def check_directive(subject: str) -> list:
    """Check if there are any active directives for a subject."""
    conn, cur = _get_db()
    cur.execute(
        "SELECT * FROM owner_directives WHERE active=TRUE AND (subject ILIKE %s OR subject ILIKE %s)",
        (f"%{subject}%", subject)
    )
    results = [dict(r) for r in cur.fetchall()]
    conn.close()
    return results


def is_blocked(subject: str) -> bool:
    """Quick check — is this subject blocked by any directive?"""
    directives = check_directive(subject)
    blocking_types = {"cancel_project", "cancel_deal", "block_agent", "block_customer"}
    return any(d["type"] in blocking_types for d in directives)


def directives_summary() -> str:
    """Human-readable summary of all active directives for prompt injection."""
    directives = get_all_directives()
    if not directives:
        return ""

    lines = ["=== OWNER DIRECTIVES (MANDATORY — override all other context) ==="]
    for d in directives:
        dtype = d["type"].upper().replace("_", " ")
        lines.append(f"  [{dtype}] {d['subject']} — {d.get('reason', 'No reason given')} (issued {str(d['issued_at'])[:10]})")

    lines.append("")
    lines.append("These directives come from the owner (Braun). They are NON-NEGOTIABLE.")
    lines.append("If a directive says a project is cancelled, do NOT reference it in reports, proposals, pipeline reviews, or status updates.")
    lines.append("If a directive blocks an action, do NOT take that action regardless of other instructions.")
    return "\n".join(lines)




def archive_project(subject: str, reason: str = ""):
    """Archive a cancelled project — mark as cancelled in Plane and KG, but keep all data.
    Call this after add_directive('cancel_project', subject, reason).
    """
    import sys
    sys.path.insert(0, "/home/aielevate")
    archived = {"subject": subject, "plane": [], "kg": [], "files_kept": []}

    # 1. Plane: find tickets referencing this project and move to Cancelled state
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
                        if not isinstance(issues, list):
                            continue
                        for issue in issues:
                            name = issue.get("name", "")
                            desc = issue.get("description", "") or ""
                            if subject.lower() in name.lower() or subject.lower() in desc.lower():
                                seq = issue.get("sequence_id")
                                if seq:
                                    try:
                                        p.add_comment(proj, seq,
                                            f"PROJECT CANCELLED by owner directive: {reason}. "
                                            f"This ticket is archived. All history preserved.")
                                        archived["plane"].append(f"{org}/{proj}-{seq}: {name}")
                                    except Exception:
                                        pass
                    except Exception:
                        continue
            except Exception:
                continue
    except Exception:
        pass

    # 2. KG: mark entity as cancelled, keep all data
    try:
        from knowledge_graph import KG
        for org in ["gigforge", "techuni", "ai-elevate"]:
            try:
                kg = KG(org)
                results = kg.search(subject)
                if results:
                    for r in results[:10]:
                        if isinstance(r, dict):
                            entity_id = r.get("id", "")
                            if entity_id:
                                try:
                                    kg.update(r.get("type", "project"), entity_id,
                                        {"status": "cancelled", "cancelled_reason": reason, "active": False})
                                    archived["kg"].append(f"{org}/{entity_id}")
                                except Exception:
                                    pass
            except Exception:
                continue
    except Exception:
        pass

    # 3. Memory/files: list what exists but NEVER delete
    for base in ["/opt/ai-elevate/gigforge", "/opt/ai-elevate/techuni"]:
        base_path = Path(base)
        for subdir in ["memory/handoffs", "memory/proposals", "memory/legal"]:
            dir_path = base_path / subdir
            if dir_path.exists():
                for f in dir_path.iterdir():
                    if subject.lower().replace(" ", "-") in f.name.lower() or subject.lower().replace(" ", "_") in f.name.lower():
                        archived["files_kept"].append(str(f))

    return archived


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Owner Directives")
    sub = parser.add_subparsers(dest="command")

    add_p = sub.add_parser("add")
    add_p.add_argument("--type", required=True, help="Directive type: cancel_project, cancel_deal, block_agent, block_customer, rename, policy, priority, custom")
    add_p.add_argument("--subject", required=True, help="What the directive is about")
    add_p.add_argument("--reason", default="", help="Why")
    add_p.add_argument("--by", default="braun")

    sub.add_parser("list")

    check_p = sub.add_parser("check")
    check_p.add_argument("subject")

    revoke_p = sub.add_parser("revoke")
    revoke_p.add_argument("--id", type=int, required=True)

    sub.add_parser("summary")

    args = parser.parse_args()

    if args.command == "add":
        r = add_directive(args.type, args.subject, args.reason, args.by)
        print(f"Directive #{r['id']}: [{r['type']}] {r['subject']}")
    elif args.command == "list":
        for d in get_all_directives(active_only=False):
            status = "ACTIVE" if d["active"] else "REVOKED"
            print(f"  #{d['id']} [{status}] [{d['type']}] {d['subject']} — {d.get('reason', '')}")
    elif args.command == "check":
        results = check_directive(args.subject)
        if results:
            for d in results:
                print(f"  BLOCKED: [{d['type']}] {d['subject']} — {d.get('reason', '')}")
        else:
            print(f"  No directives for '{args.subject}'")
    elif args.command == "summary":
        print(directives_summary())
    elif args.command == "revoke":
        r = revoke_directive(args.id)
        print(f"Revoked: {r}")
    else:
        parser.print_help()
