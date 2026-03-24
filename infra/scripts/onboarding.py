#!/usr/bin/env python3
"""Automated Onboarding Drip Sequences for AI Elevate.

Tracks new contacts and sends timed drip emails via Mailgun:
  Day 1: Welcome (what we do, how to get started)
  Day 3: Check-in (how is your experience?)
  Day 7: Feature highlight (key features)
  Day 14: Follow-up (anything we can help with?)

Usage:
  python3 onboarding.py --register <email> <org> <first_name>
  python3 onboarding.py --process
  python3 onboarding.py --list
"""

import argparse
import base64
import json
import logging
import os
import sqlite3
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [onboarding] %(levelname)s %(message)s",
)
logger = logging.getLogger("onboarding")

DB_PATH = "/opt/ai-elevate/data/onboarding.db"
MAILGUN_API_KEY = os.environ.get("MAILGUN_API_KEY", "")

ORG_CONFIG = {
    "gigforge": {
        "domain": "gigforge.ai",
        "from_name": "Sarah from GigForge",
        "from_addr": "sarah@gigforge.ai",
        "company": "GigForge",
        "tagline": "We build software products for startups and growing businesses",
        "url": "https://gigforge.ai",
        "features": [
            "Full-stack product development from idea to launch",
            "Dedicated project manager for every engagement",
            "AI-powered development workflows that ship 3x faster",
            "Transparent pricing with milestone-based billing",
        ],
    },
    "techuni": {
        "domain": "techuni.ai",
        "from_name": "Alex from TechUni",
        "from_addr": "alex@techuni.ai",
        "company": "TechUni",
        "tagline": "We help you create and sell professional online courses",
        "url": "https://courses.techuni.ai",
        "features": [
            "AI-assisted course creation that saves you weeks of work",
            "Built-in marketing tools to reach your audience",
            "Analytics dashboard to track student engagement",
            "White-label options for your own brand",
        ],
    },
    "ai-elevate": {
        "domain": "ai-elevate.ai",
        "from_name": "Team at AI Elevate",
        "from_addr": "team@ai-elevate.ai",
        "company": "AI Elevate",
        "tagline": "We bring AI-powered automation to your business",
        "url": "https://ai-elevate.ai",
        "features": [
            "Custom AI agents tailored to your business processes",
            "Multi-channel communication (email, chat, voice)",
            "Enterprise-grade security and compliance",
            "Seamless integration with your existing tools",
        ],
    },
}


def template_day1(name, org):
    c = ORG_CONFIG.get(org, ORG_CONFIG["ai-elevate"])
    fn = c["from_name"].split(" from ")[0] if " from " in c["from_name"] else "The Team"
    subject = "Welcome aboard, " + name
    body = (
        "Hi " + name + ",\n\n"
        "Glad to have you here. I'm reaching out because you recently connected with us at " + c["company"] + ".\n\n"
        "Quick intro: " + c["tagline"] + ". We've been working with teams of all sizes, and we'd love to help you too.\n\n"
        "If you want to get a feel for what we do, check out " + c["url"] + " -- there's a good overview there.\n\n"
        "Got questions? Just hit reply. I read every email personally.\n\n"
        "Talk soon,\n" + fn + "\n" + c["company"]
    )
    return subject, body


def template_day3(name, org):
    c = ORG_CONFIG.get(org, ORG_CONFIG["ai-elevate"])
    fn = c["from_name"].split(" from ")[0] if " from " in c["from_name"] else "The Team"
    subject = "Quick check-in, " + name
    body = (
        "Hey " + name + ",\n\n"
        "Just wanted to follow up from a couple days ago. Have you had a chance to look around?\n\n"
        "I know things get busy, so no pressure at all. But if anything came up -- questions, ideas, concerns -- I'm here.\n\n"
        "Sometimes it helps to start with a specific problem you're trying to solve. If you share what you're working on, I can point you in the right direction.\n\n"
        "Cheers,\n" + fn + "\n" + c["company"]
    )
    return subject, body


def template_day7(name, org):
    c = ORG_CONFIG.get(org, ORG_CONFIG["ai-elevate"])
    fn = c["from_name"].split(" from ")[0] if " from " in c["from_name"] else "The Team"
    features_text = "\n".join("  - " + f for f in c["features"])
    subject = "A few things you might not know about " + c["company"]
    body = (
        "Hi " + name + ",\n\n"
        "Wanted to share a few things that our clients find most valuable:\n\n"
        + features_text + "\n\n"
        "Most people who reach out to us start with just one of these. Once they see results, they usually come back for more.\n\n"
        "If any of that sounds relevant to what you're doing, let me know and I'll send over some specifics.\n\n"
        "Best,\n" + fn + "\n" + c["company"]
    )
    return subject, body


def template_day14(name, org):
    c = ORG_CONFIG.get(org, ORG_CONFIG["ai-elevate"])
    fn = c["from_name"].split(" from ")[0] if " from " in c["from_name"] else "The Team"
    subject = "Still here if you need us, " + name
    body = (
        "Hey " + name + ",\n\n"
        "It's been a couple of weeks since we first connected. Just wanted to let you know we're still here whenever you're ready.\n\n"
        "No hard sell -- I know timing matters. If now's not the right moment, that's completely fine. But if something changes down the road, just drop me a line.\n\n"
        "In the meantime, feel free to check out " + c["url"] + " for updates on what we've been building.\n\n"
        "All the best,\n" + fn + "\n" + c["company"]
    )
    return subject, body


DRIP_STEPS = {
    "registered": {"days": 1, "template": template_day1, "next": "day1_sent"},
    "day1_sent":  {"days": 3, "template": template_day3, "next": "day3_sent"},
    "day3_sent":  {"days": 7, "template": template_day7, "next": "day7_sent"},
    "day7_sent":  {"days": 14, "template": template_day14, "next": "day14_sent"},
    "day14_sent": None,
}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS contacts (
        email TEXT PRIMARY KEY,
        org TEXT NOT NULL,
        first_name TEXT NOT NULL,
        first_contact_date TEXT NOT NULL,
        last_step TEXT NOT NULL DEFAULT 'registered',
        next_step_date TEXT NOT NULL
    )""")
    conn.commit()
    return conn


def register(email, org, first_name):
    """Register a new contact. Returns True if newly added."""
    db = get_db()
    try:
        now = datetime.now(timezone.utc)
        next_date = now + timedelta(days=1)
        db.execute(
            "INSERT OR IGNORE INTO contacts (email, org, first_name, first_contact_date, last_step, next_step_date) VALUES (?, ?, ?, ?, ?, ?)",
            (email.lower().strip(), org.lower().strip(), first_name.strip(),
             now.isoformat(), "registered", next_date.isoformat()),
        )
        if db.total_changes > 0:
            db.commit()
            logger.info("Registered new contact: %s (%s) -- %s", email, org, first_name)
            return True
        else:
            logger.info("Contact already exists: %s", email)
            return False
    finally:
        db.close()


def send_email(to, subject, body, org):
    """Send an email via Mailgun using the org's sending domain."""
    c = ORG_CONFIG.get(org, ORG_CONFIG["ai-elevate"])
    domain = c["domain"]
    params = {
        "from": c["from_name"] + " <" + c["from_addr"] + ">",
        "to": to,
        "subject": subject,
        "text": body,
        "h:Reply-To": c["from_addr"],
    }
    try:
        data = urllib.parse.urlencode(params).encode("utf-8")
        creds = base64.b64encode(("api:" + MAILGUN_API_KEY).encode()).decode()
        req = urllib.request.Request(
            "https://api.mailgun.net/v3/" + domain + "/messages",
            data=data, method="POST",
        )
        req.add_header("Authorization", "Basic " + creds)
        urllib.request.urlopen(req, timeout=15)
        logger.info("Sent drip email to %s via %s: %s", to, domain, subject)
        return True
    except Exception as e:
        logger.error("Failed to send to %s via %s: %s", to, domain, e)
        return False


def process():
    """Check all contacts and send drip emails that are due."""
    db = get_db()
    now = datetime.now(timezone.utc)
    rows = db.execute(
        "SELECT * FROM contacts WHERE last_step != 'day14_sent' AND next_step_date <= ?",
        (now.isoformat(),),
    ).fetchall()

    sent = 0
    skipped = 0

    for row in rows:
        step_config = DRIP_STEPS.get(row["last_step"])
        if step_config is None:
            continue
        template_fn = step_config["template"]
        next_step = step_config["next"]
        subject, body = template_fn(row["first_name"], row["org"])

        if send_email(row["email"], subject, body, row["org"]):
            next_config = DRIP_STEPS.get(next_step)
            if next_config:
                next_date = now + timedelta(days=next_config["days"])
            else:
                next_date = now
            db.execute(
                "UPDATE contacts SET last_step = ?, next_step_date = ? WHERE email = ?",
                (next_step, next_date.isoformat(), row["email"]),
            )
            db.commit()
            sent += 1
            logger.info("Advanced %s to step %s", row["email"], next_step)
        else:
            skipped += 1

    logger.info("Processed drip queue: %d sent, %d skipped, %d due", sent, skipped, len(rows))
    return sent, skipped


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Automated Onboarding Sequences")
    parser.add_argument("--register", nargs=3, metavar=("EMAIL", "ORG", "FIRST_NAME"),
                        help="Register a new contact for drip sequence")
    parser.add_argument("--process", action="store_true",
                        help="Process drip queue and send due emails")
    parser.add_argument("--list", action="store_true",
                        help="List all contacts and their status")
    args = parser.parse_args()

    if args.register:
        email, org, first_name = args.register
        if org not in ORG_CONFIG:
            print("Unknown org: " + org + ". Valid: " + ", ".join(ORG_CONFIG.keys()))
            sys.exit(1)
        added = register(email, org, first_name)
        print(("Registered" if added else "Already exists") + ": " + email)
    elif args.process:
        sent, skipped = process()
        print("Drip process complete: %d sent, %d failed" % (sent, skipped))
    elif args.list:
        db = get_db()
        rows = db.execute("SELECT * FROM contacts ORDER BY first_contact_date DESC").fetchall()
        if not rows:
            print("No contacts registered.")
        else:
            for r in rows:
                print("  %-40s %-12s %-15s step=%-12s next=%s" % (
                    r["email"], r["org"], r["first_name"], r["last_step"], r["next_step_date"][:10]))
        db.close()
    else:
        parser.print_help()
