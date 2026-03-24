#!/usr/bin/env python3
"""Agent Response Quality Audit


log = get_logger("response_audit")

Samples recent agent email responses and sends them to PM agents for review.
GigForge responses go to gigforge-pm, TechUni responses go to techuni-pm.

Usage:
  python3 /home/aielevate/response_audit.py --audit
"""

import argparse
import json
import os
import random
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from exceptions import AiElevateError  # TODO: Use specific exception types, AgentError
from logging_config import get_logger

LOG_DIR = Path("/opt/ai-elevate/email-gateway/logs")
SAMPLE_SIZE = 5


def load_responses_past_week():
    """Load all agent email responses from the past 7 days."""
    responses = []
    now = datetime.now(timezone.utc)
    for days_ago in range(7):
        date = (now - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        resp_file = LOG_DIR / f"responses-{date}.jsonl"
        inbound_file = LOG_DIR / f"inbound-{date}.jsonl"

        # Load inbound logs for context (subject, sender)
        inbound_by_key = {}
        if inbound_file.exists():
            with open(inbound_file) as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        key = (entry.get("from", ""), entry.get("subject", ""), entry.get("agent", ""))
                        inbound_by_key[key] = entry
                    except (json.JSONDecodeError, KeyError):
                        continue

        if resp_file.exists():
            with open(resp_file) as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        # Skip entries with no real response
                        if entry.get("response_length", 0) < 20:
                            continue
                        if entry.get("exit_code", 1) != 0:
                            continue
                        # Enrich with inbound context
                        key = (entry.get("from", ""), entry.get("subject", ""), entry.get("agent", ""))
                        if key in inbound_by_key:
                            entry["body_length"] = inbound_by_key[key].get("body_length", 0)
                        entry["_date"] = date
                        responses.append(entry)
                    except (json.JSONDecodeError, KeyError):
                        continue

    return responses


def classify_org(agent_id):
    """Classify agent as gigforge, techuni, or other."""
    if not agent_id:
        return "other"
    if "gigforge" in agent_id:
        return "gigforge"
    if "techuni" in agent_id:
        return "techuni"
    return "other"


def run_audit():
    """Sample responses and send to PM agents for review."""
    responses = load_responses_past_week()

    if not responses:
        log.info("No agent email responses found in the past week.")
        return

    # Sample up to SAMPLE_SIZE responses
    sample = random.sample(responses, min(SAMPLE_SIZE, len(responses)))

    # Group by org
    gigforge_samples = []
    techuni_samples = []

    for i, resp in enumerate(sample, 1):
        org = classify_org(resp.get("agent", ""))
        entry_text = (
            f"  {i}. Agent: {resp.get('agent', 'unknown')}\n"
            f"     From: {resp.get('from', 'unknown')}\n"
            f"     Subject: {resp.get('subject', '(no subject)')}\n"
            f"     Date: {resp.get('timestamp', resp.get('_date', 'unknown'))}\n"
            f"     Response length: {resp.get('response_length', 0)} chars\n"
        )
        if org == "techuni":
            techuni_samples.append(entry_text)
        else:
            # Route gigforge + other to gigforge-pm
            gigforge_samples.append(entry_text)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Send to gigforge-pm
    if gigforge_samples:
        msg = (
            f"WEEKLY RESPONSE AUDIT ({timestamp}): Please review these "
            f"{len(gigforge_samples)} agent email responses for quality. "
            f"Flag any that sound robotic, make false promises, use wrong names, "
            f"or are inappropriate.\n\n"
            + "\n".join(gigforge_samples)
            + "\n\nPlease check the email gateway logs at "
            "/opt/ai-elevate/email-gateway/logs/ for full response details. "
            "Look at both the inbound and responses JSONL files for the dates listed. "
            "Report any issues found."
        )
        log.info("Sending {len(gigforge_samples)} samples to gigforge-pm...")
        try:
            env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
            result = subprocess.run(
                [
                    "openclaw", "agent",
                    "--agent", "gigforge-pm",
                    "--message", msg,
                    "--thinking", "low",
                    "--timeout", "300",
                ],
                capture_output=True, text=True, timeout=360, env=env,
            )
            log.info("gigforge-pm response ({len(result.stdout)} chars): {result.stdout[:500]}")
            if result.returncode != 0:
                print(f"gigforge-pm stderr: {result.stderr[:300]}", file=sys.stderr)
        except (AgentError, Exception) as e:
            print(f"Failed to send to gigforge-pm: {e}", file=sys.stderr)

    # Send to techuni-pm
    if techuni_samples:
        msg = (
            f"WEEKLY RESPONSE AUDIT ({timestamp}): Please review these "
            f"{len(techuni_samples)} agent email responses for quality. "
            f"Flag any that sound robotic, make false promises, use wrong names, "
            f"or are inappropriate.\n\n"
            + "\n".join(techuni_samples)
            + "\n\nPlease check the email gateway logs at "
            "/opt/ai-elevate/email-gateway/logs/ for full response details. "
            "Look at both the inbound and responses JSONL files for the dates listed. "
            "Report any issues found."
        )
        log.info("Sending {len(techuni_samples)} samples to techuni-pm...")
        try:
            env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
            result = subprocess.run(
                [
                    "openclaw", "agent",
                    "--agent", "techuni-pm",
                    "--message", msg,
                    "--thinking", "low",
                    "--timeout", "300",
                ],
                capture_output=True, text=True, timeout=360, env=env,
            )
            log.info("techuni-pm response ({len(result.stdout)} chars): {result.stdout[:500]}")
            if result.returncode != 0:
                print(f"techuni-pm stderr: {result.stderr[:300]}", file=sys.stderr)
        except (AgentError, Exception) as e:
            print(f"Failed to send to techuni-pm: {e}", file=sys.stderr)

    if not gigforge_samples and not techuni_samples:
        log.info("No samples to audit after classification.")
        return

    log.info("\nAudit complete. {len(gigforge_samples)} to gigforge-pm, {len(techuni_samples)} to techuni-pm.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agent Response Quality Audit")
    parser.add_argument("--audit", action="store_true", help="Run the weekly response audit")
    args = parser.parse_args()

    if args.audit:
        run_audit()
    else:
        parser.print_help()
