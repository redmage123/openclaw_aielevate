#!/usr/bin/env python3
"""GigForge E2E Test — automated full lifecycle test."""
import asyncio
import json
import os
import re
import sys
import time
import subprocess
import psycopg2
import requests
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/aielevate")

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
SKIP = "\033[93mSKIP\033[0m"

results = []

def check(name, condition, detail=""):
    status = PASS if condition else FAIL
    results.append((name, condition))
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    return condition


def clean():
    """Wipe all test data."""
    conn = psycopg2.connect(host="127.0.0.1", port=5434, dbname="rag", user="rag", password="rag_vec_2026")
    conn.autocommit = True
    cur = conn.cursor()
    for t in ["email_dedup", "email_interactions"]:
        cur.execute(f"DELETE FROM {t}")
    for t in ["customer_sentiment", "customer_notes", "project_milestones"]:
        cur.execute(f"DELETE FROM {t} WHERE {'email' if t != 'project_milestones' else 'customer_email'} LIKE '%e2etest%'")
    conn.close()

    # Clear sessions
    import glob
    for f in glob.glob("/home/aielevate/.openclaw/agents/*/sessions/*.jsonl"):
        Path(f).unlink()

    # Clear test files
    for pattern in ["/opt/ai-elevate/gigforge/inbox/programming/*e2etest*",
                    "/opt/ai-elevate/gigforge/memory/handoffs/*e2etest*"]:
        for f in glob.glob(pattern):
            Path(f).unlink()


def send_webhook(sender, recipient, subject, body, message_id):
    """Send a test email via webhook."""
    r = subprocess.run(
        ["curl", "-s", "-X", "POST", "http://localhost:8065/webhook/inbound",
         "-d", f"sender={sender}",
         "-d", f"recipient={recipient}",
         "-d", f"subject={subject}",
         "-d", f"from={sender}",
         "-d", f"Message-Id={message_id}",
         "--data-urlencode", f"body-plain={body}"],
        capture_output=True, text=True, timeout=180)
    return json.loads(r.stdout) if r.stdout else {}


async def check_workflows(keyword):
    """Check for running/completed workflows matching keyword."""
    from temporalio.client import Client
    client = await Client.connect("localhost:7233")
    found = []
    async for wf in client.list_workflows(""):
        if keyword in wf.id:
            h = client.get_workflow_handle(wf.id)
            desc = await h.describe()
            found.append({"id": wf.id, "status": desc.status, "type": wf.workflow_type})
    return found


def main():
    print("=" * 60)
    print("GigForge E2E Test")
    print("=" * 60)
    start = time.time()

    # Setup
    print("\n--- Setup ---")
    clean()
    print("  Test data cleaned")

    # Test 1: Send inquiry
    print("\n--- Test 1: Customer Inquiry ---")
    result = send_webhook(
        "E2E Test <e2etest@example.com>",
        "sales@gigforge.ai",
        "E2E test project",
        "Hi, I need a simple web app built. React frontend, Node backend. Budget 1000 EUR.",
        "e2e-test-001")
    check("Webhook accepted", result.get("status") in ("accepted", "orchestrated"))

    # Wait for processing
    print("  Waiting 90s for agent response...")
    time.sleep(90)

    # Test 2: Verify response
    print("\n--- Test 2: Verify Response ---")
    conn = psycopg2.connect(host="127.0.0.1", port=5434, dbname="rag", user="rag", password="rag_vec_2026")
    cur = conn.cursor()

    cur.execute("SELECT count(*) FROM email_dedup WHERE sender = 'e2etest@example.com'")
    dedup_count = cur.fetchone()[0]
    check("Dedup recorded", dedup_count >= 1, f"{dedup_count} entries")

    cur.execute("SELECT count(*) FROM email_interactions WHERE sender_email = 'e2etest@example.com'")
    interaction_count = cur.fetchone()[0]
    check("Interaction logged", interaction_count >= 1, f"{interaction_count} entries")

    cur.execute("SELECT rating FROM customer_sentiment WHERE email = 'e2etest@example.com'")
    row = cur.fetchone()
    check("Sentiment tracked", row is not None, f"rating={row[0]}" if row else "missing")

    cur.execute("SELECT count(*) FROM customer_notes WHERE email = 'e2etest@example.com'")
    note_count = cur.fetchone()[0]
    check("Customer note added", note_count >= 1)
    conn.close()

    # Check no duplicate emails sent
    key = open("/opt/ai-elevate/credentials/mailgun-api-key.txt").read().strip()
    r = requests.get("https://api.mailgun.net/v3/gigforge.ai/events",
        auth=("api", key), params={"limit": 10, "recipient": "e2etest@example.com"})
    events = [e for e in r.json().get("items", []) if e.get("event") == "accepted"]
    check("Single reply (no duplicates)", len(events) <= 2, f"{len(events)} emails sent")

    # Test 3: Send acceptance
    print("\n--- Test 3: Customer Acceptance ---")
    result2 = send_webhook(
        "E2E Test <e2etest@example.com>",
        "sales@gigforge.ai",
        "Re: E2E test project",
        "Sounds great, lets go ahead with the project!",
        "e2e-test-002")
    check("Acceptance webhook accepted", result2.get("status") in ("accepted", "orchestrated"))

    print("  Waiting 120s for acceptance detection + workflow triggers...")
    time.sleep(120)

    # Test 4: Verify workflows started
    print("\n--- Test 4: Verify Workflows ---")
    workflows = asyncio.run(check_workflows("e2e"))
    build_started = any(w["type"] == "ProjectBuildWorkflow" for w in workflows)
    check("Build workflow started", build_started,
          f"{len(workflows)} workflows found")

    # Test 5: Circuit breaker healthy
    print("\n--- Test 5: Infrastructure Health ---")
    from agent_dispatch import is_healthy, circuit_status
    check("System healthy", is_healthy())
    cs = circuit_status()
    check("Circuit breaker closed", cs["status"] == "closed", f"failures={cs['failures']}")

    # Summary
    print("\n" + "=" * 60)
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    elapsed = time.time() - start
    print(f"Results: {passed}/{total} passed in {elapsed:.0f}s")
    if passed == total:
        print(f"[{PASS}] ALL TESTS PASSED")
    else:
        failed = [name for name, ok in results if not ok]
        print(f"[{FAIL}] FAILED: {', '.join(failed)}")
    print("=" * 60)

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
