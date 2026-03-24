#!/usr/bin/env python3
"""Post-Delivery Feedback System.

After project delivery:
1. Sales sends thank-you email with feedback form
2. Customer rates experience (1-10) + free-text comments
3. Feedback stored in Postgres
4. Fed back to ALL agents as learning — what went well, what didn't
5. Patterns extracted for organizational self-improvement

The feedback form is a simple email reply — no external forms needed.
Customer replies with their rating and comments, gateway processes it.

Usage:
  from feedback_system import send_feedback_request, process_feedback, get_feedback_summary, get_improvement_actions

  # After delivery
  send_feedback_request(customer_email="sarah@example.com", project_title="Address Book App", sales_agent="gigforge-sales")

  # When customer replies
  process_feedback(customer_email="sarah@example.com", rating=8, comments="Great speed, minor UI issues")

  # Get insights
  summary = get_feedback_summary(months=3)
  actions = get_improvement_actions()
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import psycopg2
import psycopg2.extras
from dao import BaseDAO  # TODO: Replace inline DB calls with specific DAOs
from exceptions import AiElevateError  # TODO: Use specific exception types, AgentError, DatabaseError

sys.path.insert(0, "/home/aielevate")

DB = dict(
    host=os.environ.get("DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("DB_PORT", "5434")),
    dbname=os.environ.get("DB_NAME", "rag"),
    user=os.environ.get("DB_USER", "rag"),
    password=os.environ.get("DB_PASSWORD", ""),
)


def _get_db():
    conn = psycopg2.connect(**DB)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""CREATE TABLE IF NOT EXISTS customer_feedback (
        id SERIAL PRIMARY KEY,
        customer_email TEXT NOT NULL,
        project_title TEXT,
        rating INTEGER,
        comments TEXT,
        what_went_well TEXT,
        what_to_improve TEXT,
        would_recommend BOOLEAN,
        handling_agents TEXT,
        org TEXT DEFAULT 'gigforge',
        submitted_at TIMESTAMPTZ DEFAULT NOW(),
        processed BOOLEAN DEFAULT FALSE,
        improvement_actions TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS improvement_log (
        id SERIAL PRIMARY KEY,
        source_feedback_id INTEGER,
        category TEXT,
        insight TEXT NOT NULL,
        action TEXT,
        assigned_to TEXT,
        status TEXT DEFAULT 'open',
        created_at TIMESTAMPTZ DEFAULT NOW(),
        resolved_at TIMESTAMPTZ
    )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_feedback_email ON customer_feedback(customer_email)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_improvement_status ON improvement_log(status)")
    return conn, cur


def send_feedback_request(customer_email: str, project_title: str, sales_agent: str = "gigforge-sales"):
    """Send post-delivery thank-you email with feedback request."""
    body = f"""Hi,

Thank you for choosing GigForge for your {project_title} project. We hope you're happy with the result.

We'd love to hear about your experience — it helps us get better for every client. Could you take a moment to reply to this email with:

1. **Overall rating** (1-10, where 10 is outstanding):
2. **What went well?**
3. **What could we improve?**
4. **Would you recommend GigForge to a colleague?** (yes/no)
5. **Any other comments?**

Just hit reply and type your answers — no forms or links to click.

If you ever need anything else built, we're here. We also offer retainer packages for ongoing maintenance and new features.

Thank you again for your trust.

Best regards,
GigForge"""

    try:
        from send_email import send_email
        send_email(
            to=customer_email,
            subject=f"Thank you — How was your {project_title} experience?",
            body=body,
            agent_id=sales_agent,
            cc="braun.brelin@ai-elevate.ai",
        )
    except (AgentError, Exception) as e:
        return {"status": "error", "error": str(e)}

    # Create feedback record (pending customer response)
    conn, cur = _get_db()
    cur.execute(
        "INSERT INTO customer_feedback (customer_email, project_title, org) VALUES (%s, %s, %s) RETURNING id",
        (customer_email, project_title, "gigforge" if "gigforge" in sales_agent else "techuni")
    )
    feedback_id = cur.fetchone()["id"]
    conn.close()

    # Notify ops
    try:
        from ops_notify import ops_notify
        ops_notify("status_update",
                  f"Feedback request sent to {customer_email} for {project_title}",
                  agent=sales_agent, customer_email=customer_email)
    except (DatabaseError, Exception) as e:
        pass

    return {"status": "sent", "feedback_id": feedback_id}


def _distribute_feedback(customer_email, project, org, rating, would_recommend,
                         what_went_well, what_to_improve, comments):
    """Notify agents, update sentiment, ops, and KG after feedback."""
    feedback_summary = (
        f"CUSTOMER FEEDBACK RECEIVED\n\n"
        f"Customer: {customer_email}\n"
        f"Project: {project}\n"
        f"Rating: {rating}/10\n"
        f"Would recommend: {'Yes' if would_recommend else 'No'}\n"
        f"What went well: {what_went_well}\n"
        f"What to improve: {what_to_improve}\n"
        f"Comments: {comments}\n\n"
        f"USE THIS FEEDBACK to improve your future interactions. "
        f"If the customer praised something, keep doing it. "
        f"If they criticized something, fix it."
    )
    agents_to_notify = [
        "operations", f"{org}-advocate", f"{org}-pm",
        f"{org}-engineer", f"{org}-sales", f"{org}-csat",
    ]
    for agent in agents_to_notify:
        try:
            subprocess.Popen(
                ["agent-queue", "--agent", agent, "--message", feedback_summary,
                 "--thinking", "low", "--timeout", "120"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except (AgentError, Exception) as e:
            pass
    try:
        from customer_context import update_sentiment
        level = "positive" if rating >= 8 else "neutral" if rating >= 5 else "at_risk"
        update_sentiment(customer_email, level, f"Feedback: {rating}/10", agent="feedback-system")
    except (DatabaseError, Exception) as e:
        pass
    try:
        from ops_notify import ops_notify
        priority = "escalation" if rating and rating <= 4 else "status_update"
        ops_notify(priority, f"Feedback from {customer_email}: {rating}/10 for {project}",
                   agent="feedback-system", customer_email=customer_email)
    except (DatabaseError, Exception) as e:
        pass
    try:
        from knowledge_graph import KG
        kg = KG(org)
        cid = customer_email.replace("@", "_at_").replace(".", "_")
        kg.update("customer", cid, {
            "last_rating": rating, "would_recommend": would_recommend,
            "feedback_date": datetime.now(timezone.utc).isoformat()[:10],
        })
    except (DatabaseError, Exception) as e:
        pass
    return agents_to_notify


def process_feedback(customer_email: str, rating: int = 0, comments: str = "",
                     what_went_well: str = "", what_to_improve: str = "",
                     would_recommend: bool = True, handling_agents: str = ""):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Process customer feedback and distribute to the organization."""
    conn, cur = _get_db()

    # Update the pending feedback record
    cur.execute(
        """UPDATE customer_feedback SET rating=%s, comments=%s, what_went_well=%s,
           what_to_improve=%s, would_recommend=%s, handling_agents=%s, submitted_at=NOW()
        WHERE customer_email=%s AND rating IS NULL
        ORDER BY id DESC LIMIT 1
        RETURNING id, project_title, org""",
        (rating, comments, what_went_well, what_to_improve, would_recommend,
         handling_agents, customer_email)
    )
    row = cur.fetchone()
    if not row:
        # No pending record — create new
        cur.execute(
            """INSERT INTO customer_feedback (customer_email, rating, comments, what_went_well,
               what_to_improve, would_recommend, handling_agents)
            VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id, project_title, org""",
            (customer_email, rating, comments, what_went_well, what_to_improve,
             would_recommend, handling_agents)
        )
        row = cur.fetchone()

    feedback_id = row["id"]
    project = row.get("project_title") or "Unknown project"
    org = row.get("org") or "gigforge"

    # Extract improvement actions from negative feedback
    actions = []
    if rating and rating < 7:
        if what_to_improve:
            actions.append({"category": "improvement", "insight": what_to_improve, "action": "Review and address"})
        if comments:
            actions.append({"category": "feedback", "insight": comments, "action": "Discuss in team review"})

    if rating and rating <= 4:
        actions.append({"category": "escalation", "insight": f"Low rating ({rating}/10) from {customer_email}",
                       "action": "Ops director review and customer follow-up required"})

    # Store improvement actions
    for action in actions:
        cur.execute(
            "INSERT INTO improvement_log (source_feedback_id, category, insight, action, assigned_to) VALUES (%s,%s,%s,%s,%s)",
            (feedback_id, action["category"], action["insight"], action["action"], "operations")
        )

    # Mark as processed
    cur.execute("UPDATE customer_feedback SET processed=TRUE WHERE id=%s", (feedback_id,))
    conn.close()

    # Distribute feedback and update systems
    agents_to_notify = _distribute_feedback(
        customer_email, project, org, rating, would_recommend,
        what_went_well, what_to_improve, comments)

    return {
        "feedback_id": feedback_id,
        "rating": rating,
        "actions_created": len(actions),
        "agents_notified": len(agents_to_notify),
    }


def parse_feedback_email(sender: str, body: str) -> dict:
    """Parse a customer's feedback reply email into structured data."""
    import re
    data = {"customer_email": sender}

    # Look for rating
    rating_match = re.search(r'(?:rating|score)[:\s]*(\d+)', body, re.I)
    if not rating_match:
        rating_match = re.search(r'(\d+)\s*/\s*10', body)
    if not rating_match:
        # Look for standalone number 1-10
        rating_match = re.search(r'\b([1-9]|10)\b', body)
    if rating_match:
        data["rating"] = min(10, max(1, int(rating_match.group(1))))

    # Look for well/improve sections
    well_match = re.search(r'(?:went well|liked|great|good)[:\s]*(.+?)(?:\n\n|\n\d|$)', body, re.I | re.DOTALL)
    if well_match:
        data["what_went_well"] = well_match.group(1).strip()[:500]

    improve_match = re.search(r'(?:improve|better|wish|issue|problem)[:\s]*(.+?)(?:\n\n|\n\d|$)', body, re.I | re.DOTALL)
    if improve_match:
        data["what_to_improve"] = improve_match.group(1).strip()[:500]

    # Recommend
    data["would_recommend"] = "yes" in body.lower() and "recommend" in body.lower()

    data["comments"] = body[:1000]
    return data


def get_feedback_summary(org: str = "", months: int = 3) -> dict:
    """Get feedback summary for the organization."""
    conn, cur = _get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=months * 30)).isoformat()

    where = "WHERE submitted_at > %s AND rating IS NOT NULL"
    params = [cutoff]
    if org:
        where += " AND org = %s"
        params.append(org)

    cur.execute("SELECT COUNT(*) as cnt, AVG(rating) as avg, MIN(rating) as min, MAX(rating) as max FROM customer_feedback " + where, params)
    stats = dict(cur.fetchone())

    cur.execute("SELECT rating, COUNT(*) as cnt FROM customer_feedback " + where + " GROUP BY rating ORDER BY rating", params)
    distribution = {r["rating"]: r["cnt"] for r in cur.fetchall()}

    cur.execute("SELECT COUNT(*) as cnt FROM customer_feedback " + where + " AND would_recommend=TRUE", params)
    recommend = cur.fetchone()["cnt"]

    cur.execute("SELECT what_to_improve FROM customer_feedback " + where + " AND what_to_improve IS NOT NULL AND what_to_improve != ''", params)
    improvements = [r["what_to_improve"] for r in cur.fetchall()]

    conn.close()

    return {
        "total_responses": stats["cnt"] or 0,
        "average_rating": round(stats["avg"] or 0, 1),
        "min_rating": stats["min"],
        "max_rating": stats["max"],
        "distribution": distribution,
        "would_recommend_count": recommend,
        "recommend_rate": recommend / stats["cnt"] if stats["cnt"] else 0,
        "improvements_mentioned": improvements[:10],
        "months": months,
    }


def get_improvement_actions(status: str = "open") -> list:
    """Get open improvement actions from feedback."""
    conn, cur = _get_db()
    cur.execute("SELECT * FROM improvement_log WHERE status=%s ORDER BY created_at DESC", (status,))
    actions = [dict(r) for r in cur.fetchall()]
    conn.close()
    return actions


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Feedback System")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("summary")
    sub.add_parser("actions")

    send = sub.add_parser("send")
    send.add_argument("--email", required=True)
    send.add_argument("--project", required=True)

    proc = sub.add_parser("process")
    proc.add_argument("--email", required=True)
    proc.add_argument("--rating", type=int, required=True)
    proc.add_argument("--comments", default="")

    args = parser.parse_args()

    if args.command == "summary":
        s = get_feedback_summary()
        print(f"Feedback: {s['total_responses']} responses, avg {s['average_rating']}/10, {s['recommend_rate']:.0%} recommend")
    elif args.command == "actions":
        for a in get_improvement_actions():
            print(f"  [{a['category']}] {a['insight'][:60]} → {a['action']}")
    elif args.command == "send":
        r = send_feedback_request(args.email, args.project)
        print(r)
    elif args.command == "process":
        r = process_feedback(args.email, args.rating, args.comments)
        print(r)
    else:
        parser.print_help()
