#!/usr/bin/env python3
"""CRM Auto-Population from Email

Automatically registers leads from inbound email contacts.
Uses a local SQLite database at /opt/ai-elevate/data/leads.db.
Also attempts to push contacts to the CRM API at http://localhost:8070.

Usage:
  python3 /home/aielevate/crm_auto_lead.py --add <email> <name> <org> <source> <subject>
  python3 /home/aielevate/crm_auto_lead.py --list
  python3 /home/aielevate/crm_auto_lead.py --report
"""

import argparse
import json
import os
import sqlite3
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = "/opt/ai-elevate/data/leads.db"
CRM_URL = "http://localhost:8070"

# Internal team emails — never register as leads
INTERNAL_TEAM = {
    "braun.brelin@ai-elevate.ai",
    "peter.munro@ai-elevate.ai",
    "mike.burton@ai-elevate.ai",
    "charlie.turking@ai-elevate.ai",
}


def get_db():
    """Get SQLite connection, creating the table if needed."""
    db_dir = Path(DB_PATH).parent
    db_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            org TEXT DEFAULT '',
            source TEXT DEFAULT 'email',
            subject TEXT DEFAULT '',
            first_contact TEXT NOT NULL,
            last_contact TEXT NOT NULL,
            contact_count INTEGER DEFAULT 1,
            status TEXT DEFAULT 'new',
            notes TEXT DEFAULT ''
        )
    """)
    conn.commit()
    return conn


def is_internal(email):
    """Check if email belongs to internal team."""
    return email.lower().strip() in INTERNAL_TEAM


def add_lead(email, name, org="", source="email", subject=""):
    """Add or update a lead. Deduplicates by email."""
    if is_internal(email):
        print(f"Skipping internal team member: {email}")
        return False

    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()

    # Check for existing lead
    existing = conn.execute("SELECT * FROM leads WHERE email = ?", (email.lower().strip(),)).fetchone()

    if existing:
        # Update last contact and increment count
        conn.execute("""
            UPDATE leads
            SET last_contact = ?, contact_count = contact_count + 1,
                subject = CASE WHEN subject = '' THEN ? ELSE subject END,
                org = CASE WHEN org = '' THEN ? ELSE org END
            WHERE email = ?
        """, (now, subject, org, email.lower().strip()))
        conn.commit()
        print(f"Updated existing lead: {email} (contact #{existing['contact_count'] + 1})")
        conn.close()
        return False  # Not a new lead
    else:
        conn.execute("""
            INSERT INTO leads (email, name, org, source, subject, first_contact, last_contact)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (email.lower().strip(), name, org, source, subject, now, now))
        conn.commit()
        print(f"New lead registered: {name} <{email}> (org: {org}, source: {source})")
        conn.close()

        # Try to push to CRM API
        try_crm_push(email, name, org, source)
        return True


def try_crm_push(email, name, org, source):
    """Attempt to create a contact in the CRM API. Best-effort."""
    try:
        # Split name into first/last
        parts = name.strip().split(None, 1)
        first_name = parts[0] if parts else name
        last_name = parts[1] if len(parts) > 1 else ""

        data = json.dumps({
            "first_name": first_name,
            "last_name": last_name or "(unknown)",
            "email": email,
            "source": source,
            "status": "lead",
            "custom_fields": {"org": org},
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{CRM_URL}/contacts",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=5)
        if resp.status in (200, 201):
            print(f"  -> Also pushed to CRM API")
    except Exception as e:
        # CRM push is best-effort; SQLite is the primary store
        pass


def list_leads():
    """List all leads."""
    conn = get_db()
    leads = conn.execute(
        "SELECT * FROM leads ORDER BY last_contact DESC"
    ).fetchall()
    conn.close()

    if not leads:
        print("No leads found.")
        return

    print(f"{'Email':<35} {'Name':<20} {'Org':<15} {'Source':<10} {'Status':<8} {'Count':<6} {'First Contact':<20}")
    print("-" * 120)
    for lead in leads:
        first = lead["first_contact"][:10] if lead["first_contact"] else ""
        print(
            f"{lead['email']:<35} {lead['name']:<20} {lead['org']:<15} "
            f"{lead['source']:<10} {lead['status']:<8} {lead['contact_count']:<6} {first:<20}"
        )
    print(f"\nTotal: {len(leads)} leads")


def weekly_report():
    """Generate weekly lead summary."""
    conn = get_db()
    now = datetime.now(timezone.utc)
    week_ago = (now - timedelta(days=7)).isoformat()

    # New leads this week
    new_leads = conn.execute(
        "SELECT * FROM leads WHERE first_contact >= ? ORDER BY first_contact DESC",
        (week_ago,)
    ).fetchall()

    # All leads
    total = conn.execute("SELECT COUNT(*) as c FROM leads").fetchone()["c"]

    # Leads by source
    by_source = conn.execute(
        "SELECT source, COUNT(*) as c FROM leads GROUP BY source ORDER BY c DESC"
    ).fetchall()

    # Leads by org
    by_org = conn.execute(
        "SELECT org, COUNT(*) as c FROM leads WHERE org != '' GROUP BY org ORDER BY c DESC"
    ).fetchall()

    # Leads by status
    by_status = conn.execute(
        "SELECT status, COUNT(*) as c FROM leads GROUP BY status ORDER BY c DESC"
    ).fetchall()

    conn.close()

    print("=" * 60)
    print(f"WEEKLY LEAD REPORT — {now.strftime('%Y-%m-%d')}")
    print("=" * 60)
    print(f"\nNew leads this week: {len(new_leads)}")
    print(f"Total leads: {total}")

    if new_leads:
        print(f"\nNew leads:")
        for lead in new_leads:
            print(f"  - {lead['name']} <{lead['email']}> (org: {lead['org']}, source: {lead['source']})")
            if lead["subject"]:
                print(f"    Subject: {lead['subject']}")

    if by_source:
        print(f"\nLeads by source:")
        for row in by_source:
            print(f"  {row['source']}: {row['c']}")

    if by_org:
        print(f"\nLeads by organization:")
        for row in by_org:
            print(f"  {row['org']}: {row['c']}")

    if by_status:
        print(f"\nLeads by status:")
        for row in by_status:
            print(f"  {row['status']}: {row['c']}")

    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CRM Auto-Lead Population")
    parser.add_argument("--add", nargs=5, metavar=("EMAIL", "NAME", "ORG", "SOURCE", "SUBJECT"),
                        help="Add a new lead")
    parser.add_argument("--list", action="store_true", help="List all leads")
    parser.add_argument("--report", action="store_true", help="Weekly lead summary")

    args = parser.parse_args()

    if args.add:
        email, name, org, source, subject = args.add
        add_lead(email, name, org, source, subject)
    elif args.list:
        list_leads()
    elif args.report:
        weekly_report()
    else:
        parser.print_help()
