#!/usr/bin/env python3
"""Billing Pipeline — milestone invoices through approval queue, delivered by email.

Flow:
  1. PM marks milestone complete → billing agent creates invoice
  2. Invoice goes to approval queue (same as proposals)
  3. Braun approves → billing agent sends invoice email to customer
  4. Customer clicks Stripe link → pays → webhook fires → next phase unlocked
  5. Overdue → automated reminders at 7, 14, 30 days

All customer communication is plain email. No portals, no dashboards.

Usage:
  from billing_pipeline import create_milestone_invoice, send_invoice_email, check_overdue

  # Create invoice (goes to approval queue)
  create_milestone_invoice(
      customer_email="sarah@example.com",
      project_title="Address Book App",
      milestone="Deposit (30%)",
      amount_eur=1350,
      org="gigforge",
  )

  # After approval, send invoice email
  send_invoice_email(invoice_id=1)

  # Check for overdue invoices
  check_overdue()
"""

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import psycopg2
import psycopg2.extras
from dao import BaseDAO  # TODO: Replace inline DB calls with specific DAOs
from exceptions import AiElevateError  # TODO: Use specific exception types, DatabaseError

sys.path.insert(0, "/home/aielevate")

DB = dict(
    host=os.environ.get("DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("DB_PORT", "5434")),
    dbname=os.environ.get("DB_NAME", "rag"),
    user=os.environ.get("DB_USER", "rag"),
    password=os.environ.get("DB_PASSWORD", "rag_vec_2026"),
)


def _get_db():
    conn = psycopg2.connect(**DB)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""CREATE TABLE IF NOT EXISTS invoices (
        id SERIAL PRIMARY KEY,
        customer_email TEXT NOT NULL,
        project_title TEXT NOT NULL,
        milestone TEXT NOT NULL,
        amount_eur REAL NOT NULL,
        currency TEXT DEFAULT 'eur',
        org TEXT DEFAULT 'gigforge',
        stripe_link TEXT,
        status TEXT DEFAULT 'draft',
        approved_by TEXT,
        sent_at TIMESTAMPTZ,
        paid_at TIMESTAMPTZ,
        due_date TIMESTAMPTZ,
        reminder_count INTEGER DEFAULT 0,
        last_reminder TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        notes TEXT
    )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_invoices_customer ON invoices(customer_email)")
    return conn, cur


def create_milestone_invoice(
    customer_email: str,
    project_title: str,
    milestone: str,
    amount_eur: float,
    org: str = "gigforge",
    notes: str = "",
) -> dict:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Create an invoice and queue it for approval."""
    conn, cur = _get_db()

    # Create Stripe payment link
    stripe_link = ""
    try:
        from stripe_payments import create_payment_link
        result = create_payment_link(
            org=org,
            description=f"{project_title} — {milestone}",
            amount_eur=amount_eur,
            customer_email=customer_email,
        )
        stripe_link = result.get("url", "")
    except (AiElevateError, Exception) as e:
        stripe_link = f"FAILED: {e}"

    # Create invoice record
    due_date = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
    cur.execute(
        """INSERT INTO invoices (customer_email, project_title, milestone, amount_eur, org, stripe_link, due_date, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, customer_email, project_title, milestone, amount_eur, stripe_link""",
        (customer_email, project_title, milestone, amount_eur, org, stripe_link, due_date, notes)
    )
    invoice = dict(cur.fetchone())
    conn.close()

    # Queue for approval (reuse proposal_queue)
    try:
        from proposal_queue import queue_proposal
        queue_proposal(
            platform="invoice",
            job_title=f"INVOICE: {project_title} — {milestone}",
            job_url=stripe_link,
            job_budget=f"EUR {amount_eur:.2f}",
            proposal_text=f"Invoice #{invoice['id']} for {customer_email}\n\n"
                         f"Project: {project_title}\n"
                         f"Milestone: {milestone}\n"
                         f"Amount: EUR {amount_eur:.2f}\n"
                         f"Payment link: {stripe_link}\n\n"
                         f"When approved, an email will be sent to the customer with the payment link.",
            recommended_bid=f"EUR {amount_eur:.2f}",
            org=org,
            drafted_by="gigforge-billing",
            notes=f"invoice_id:{invoice['id']}",
        )
    except (AiElevateError, Exception) as e:
        pass

    # Notify ops
    try:
        from ops_notify import ops_notify
        ops_notify("status_update",
                  f"Invoice #{invoice['id']} created: {project_title} — {milestone} — EUR {amount_eur:.2f}",
                  agent="billing-pipeline",
                  customer_email=customer_email)
    except (DatabaseError, Exception) as e:
        pass

    return invoice


def send_invoice_email(invoice_id: int) -> dict:
    """Send invoice email to customer. Called after approval."""
    conn, cur = _get_db()
    cur.execute("SELECT * FROM invoices WHERE id=%s", (invoice_id,))
    inv = cur.fetchone()
    if not inv:
        conn.close()
        return {"error": "Invoice not found"}

    inv = dict(inv)

    body = f"""Hi,

Here is your invoice for the {inv['milestone']} on your {inv['project_title']} project.

Amount: EUR {inv['amount_eur']:.2f}
Due: Within 14 days
Payment link: {inv['stripe_link']}

Click the link above to pay securely via Stripe (card accepted).

If you have any questions about this invoice, just reply to this email.

Best regards,
GigForge Billing"""

    try:
        from send_email import send_email
        result = send_email(
            to=inv['customer_email'],
            subject=f"Invoice: {inv['project_title']} — {inv['milestone']} (EUR {inv['amount_eur']:.2f})",
            body=body,
            agent_id="gigforge-billing",
        )

        cur.execute(
            "UPDATE invoices SET status='sent', sent_at=NOW() WHERE id=%s",
            (invoice_id,)
        )
        conn.close()
        return {"status": "sent", "invoice_id": invoice_id, "email_result": result}

    except (DatabaseError, Exception) as e:
        conn.close()
        return {"status": "error", "error": str(e)}


def mark_paid(invoice_id: int) -> dict:
    """Mark invoice as paid (called by Stripe webhook handler)."""
    conn, cur = _get_db()
    cur.execute(
        "UPDATE invoices SET status='paid', paid_at=NOW() WHERE id=%s RETURNING id, customer_email, project_title, milestone",
        (invoice_id,)
    )
    row = cur.fetchone()
    conn.close()
    if row:
        row = dict(row)
        try:
            from ops_notify import ops_notify
            ops_notify("payment_received",
                      f"Invoice #{row['id']} paid: {row['project_title']} — {row['milestone']}",
                      customer_email=row['customer_email'])
        except (DatabaseError, Exception) as e:
            pass
        return row
    return {"error": "not found"}


def check_overdue() -> list:
    """Check for overdue invoices and send reminders."""
    conn, cur = _get_db()
    now = datetime.now(timezone.utc)

    cur.execute(
        "SELECT * FROM invoices WHERE status='sent' AND due_date < %s ORDER BY due_date",
        (now.isoformat(),)
    )
    overdue = [dict(r) for r in cur.fetchall()]
    actions = []

    for inv in overdue:
        days_overdue = (now - inv['due_date']).days if inv['due_date'] else 0
        last_reminder = inv.get('last_reminder')
        reminder_count = inv.get('reminder_count', 0)

        # Reminder schedule: 7 days, 14 days, 30 days
        should_remind = False
        if reminder_count == 0 and days_overdue >= 7:
            should_remind = True
        elif reminder_count == 1 and days_overdue >= 14:
            should_remind = True
        elif reminder_count == 2 and days_overdue >= 30:
            should_remind = True

        if should_remind:
            # Send reminder
            if days_overdue >= 30:
                tone = "This invoice is now 30 days overdue. Please arrange payment at your earliest convenience to avoid any disruption to your project."
                # Also escalate to ops
                try:
                    from ops_notify import ops_notify
                    ops_notify("payment_overdue",
                              f"Invoice #{inv['id']} is {days_overdue} days overdue: {inv['project_title']} EUR {inv['amount_eur']:.2f}",
                              customer_email=inv['customer_email'])
                except (AiElevateError, Exception) as e:
                    pass
            elif days_overdue >= 14:
                tone = "Just a friendly follow-up — this invoice is now two weeks past due. If there is any issue with the payment, please let me know."
            else:
                tone = "Just a quick reminder that this invoice is still outstanding. No rush, but wanted to make sure it did not get buried in your inbox."

            body = f"""Hi,

{tone}

Invoice: {inv['project_title']} — {inv['milestone']}
Amount: EUR {inv['amount_eur']:.2f}
Payment link: {inv['stripe_link']}

Best regards,
GigForge Billing"""

            try:
                from send_email import send_email
                send_email(
                    to=inv['customer_email'],
                    subject=f"Reminder: Invoice for {inv['project_title']} (EUR {inv['amount_eur']:.2f})",
                    body=body,
                    agent_id="gigforge-billing",
                )
                cur.execute(
                    "UPDATE invoices SET reminder_count=%s, last_reminder=NOW() WHERE id=%s",
                    (reminder_count + 1, inv['id'])
                )
                actions.append({"invoice_id": inv['id'], "days_overdue": days_overdue, "reminder": reminder_count + 1})
            except (DatabaseError, Exception) as e:
                pass

    conn.close()
    return actions


def get_outstanding(org: str = "") -> list:
    """Get all unpaid invoices."""
    conn, cur = _get_db()
    if org:
        cur.execute("SELECT * FROM invoices WHERE status IN ('draft','sent') AND org=%s ORDER BY created_at", (org,))
    else:
        cur.execute("SELECT * FROM invoices WHERE status IN ('draft','sent') ORDER BY created_at")
    result = [dict(r) for r in cur.fetchall()]
    conn.close()
    return result


def get_revenue(org: str = "", months: int = 3) -> dict:
    """Get revenue summary from paid invoices."""
    conn, cur = _get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=months * 30)).isoformat()
    if org:
        cur.execute("SELECT SUM(amount_eur) as total, COUNT(*) as count FROM invoices WHERE status='paid' AND org=%s AND paid_at > %s", (org, cutoff))
    else:
        cur.execute("SELECT SUM(amount_eur) as total, COUNT(*) as count FROM invoices WHERE status='paid' AND paid_at > %s", (cutoff,))
    row = cur.fetchone()
    conn.close()
    return {"total_eur": row['total'] or 0, "count": row['count'] or 0, "months": months}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Billing Pipeline")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("outstanding")
    sub.add_parser("overdue")
    sub.add_parser("revenue")

    cr = sub.add_parser("create")
    cr.add_argument("--email", required=True)
    cr.add_argument("--project", required=True)
    cr.add_argument("--milestone", required=True)
    cr.add_argument("--amount", type=float, required=True)
    cr.add_argument("--org", default="gigforge")

    snd = sub.add_parser("send")
    snd.add_argument("--id", type=int, required=True)

    args = parser.parse_args()

    if args.command == "outstanding":
        for inv in get_outstanding():
            print(f"  #{inv['id']} [{inv['status']}] {inv['customer_email']} | {inv['project_title']} — {inv['milestone']} | EUR {inv['amount_eur']:.2f}")
    elif args.command == "overdue":
        actions = check_overdue()
        print(f"Sent {len(actions)} reminders")
        for a in actions:
            print(f"  Invoice #{a['invoice_id']}: {a['days_overdue']} days overdue, reminder #{a['reminder']}")
    elif args.command == "revenue":
        r = get_revenue()
        print(f"Revenue (last {r['months']} months): EUR {r['total_eur']:.2f} from {r['count']} invoices")
    elif args.command == "create":
        inv = create_milestone_invoice(args.email, args.project, args.milestone, args.amount, args.org)
        print(f"Created invoice #{inv['id']}: {inv['stripe_link']}")
    elif args.command == "send":
        r = send_invoice_email(args.id)
        print(r)
    else:
        parser.print_help()
