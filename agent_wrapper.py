#!/usr/bin/env python3
"""Agent Wrapper — runs workflow actions before and after each agent call.

Instead of forcing the LLM to execute tool calls (which it ignores),
this script wraps the agent call:

  1. PRE: Pull customer context, check directives
  2. AGENT: Run the openclaw agent (it writes the reply)
  3. POST: Update sentiment, notify ops, check for handoff triggers

The email gateway calls this instead of agent-queue directly.

Usage:
  python3 agent_wrapper.py --agent gigforge-sales --message "INBOUND EMAIL..." \
      --sender "sarah@example.com" --subject "Quote request"

Or as a library:
  from agent_wrapper import wrapped_agent_call
  result = wrapped_agent_call(agent_id, message, sender_email, subject)
"""

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from logging_config import get_logger  # Structured JSON logging
from exceptions import AiElevateError  # TODO: Use specific exception types, AgentError, DatabaseError

sys.path.insert(0, "/home/aielevate")

# Sentiment keywords
POSITIVE_KEYWORDS = ["thanks", "great", "love", "perfect", "excellent", "awesome", "lets go ahead",
                     "let's go ahead", "i accept", "go ahead", "approved", "looks good", "sounds good",
                     "lets proceed", "let's proceed", "happy with", "impressed"]
FRUSTRATED_KEYWORDS = ["disappointed", "frustrated", "unacceptable", "not happy", "terrible",
                       "still waiting", "no response", "what is taking", "speak to someone",
                       "talk to a manager", "supervisor", "waste of time", "holdup"]
ACCEPTANCE_KEYWORDS = ["lets go ahead", "let's go ahead", "i accept", "go ahead", "lets proceed",
                       "let's proceed", "approved", "start the project", "when can you start",
                       "ready to move forward", "lets do it", "let's do it"]


def _detect_sentiment(text: str) -> tuple:
    """Detect sentiment from email text. Returns (rating, reason)."""
    lower = text.lower()
    if any(kw in lower for kw in FRUSTRATED_KEYWORDS):
        return "frustrated", "Customer expressed frustration"
    if any(kw in lower for kw in ACCEPTANCE_KEYWORDS):
        return "positive", "Customer accepted/agreed to proceed"
    if any(kw in lower for kw in POSITIVE_KEYWORDS):
        return "positive", "Positive tone in email"
    return "neutral", "Standard inquiry"


def _detect_acceptance(text: str) -> bool:
    """Detect if the customer is accepting a deal."""
    return any(kw in text.lower() for kw in ACCEPTANCE_KEYWORDS)


def _pre_actions(agent_id: str, message: str, sender_email: str, result: dict) -> str:
    """Build enhanced message with customer context and directives. Mutates result['pre_actions']."""
    context_text = ""
    try:
        from customer_context import context_summary
        ctx = context_summary(sender_email)
        if ctx and len(ctx) > 30:
            context_text = f"\n\nCustomer context:\n{ctx}"
            result["pre_actions"].append("customer_context_injected")
    except Exception:
        pass

    directives_text = ""
    try:
        from directives import directives_summary
        d = directives_summary()
        if d:
            directives_text = f"\n\n{d}"
            result["pre_actions"].append("directives_checked")
    except Exception:
        pass

    enhanced = message
    if context_text:
        enhanced += context_text
    if directives_text:
        enhanced += directives_text
    return enhanced


def _run_subprocess(agent_id: str, enhanced_message: str, timeout: int, result: dict) -> None:
    """Run the openclaw agent subprocess. Mutates result with stdout/stderr/returncode/duration."""
    import os
    start = time.time()
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    try:
        proc = subprocess.run(
            ["openclaw", "agent", "--agent", agent_id,
             "--message", enhanced_message,
             "--thinking", "low", "--timeout", str(timeout)],
            capture_output=True, text=True, timeout=timeout + 30, env=env,
        )
        result["stdout"] = proc.stdout or ""
        result["stderr"] = proc.stderr or ""
        result["returncode"] = proc.returncode
        result["duration"] = int(time.time() - start)
    except subprocess.TimeoutExpired:
        result["stdout"] = ""
        result["stderr"] = "timeout"
        result["returncode"] = -1
        result["duration"] = timeout


def _post_actions(agent_id: str, message: str, sender_email: str, subject: str, result: dict) -> None:
    """Run post-call actions: sentiment update, ops notify, handoff triggers. Mutates result['post_actions']."""
    try:
        rating, reason = _detect_sentiment(message)
        from customer_context import update_sentiment
        update_sentiment(sender_email, rating, reason, agent=agent_id)
        result["post_actions"].append(f"sentiment:{rating}")
    except Exception as e:
        result["post_actions"].append(f"sentiment_failed:{e}")

    try:
        from ops_notify import ops_notify
        desc = f"{agent_id} responded to {sender_email} re: {subject[:50]}"
        event_type = "status_update"
        if _detect_acceptance(message):
            event_type = "new_project"
            desc = f"Customer {sender_email} accepted — {subject[:50]}"
        ops_notify(event_type, desc, agent=agent_id, customer_email=sender_email)
        result["post_actions"].append(f"ops:{event_type}")
    except Exception as e:
        result["post_actions"].append(f"ops_failed:{e}")

    if _detect_acceptance(message) and "sales" in agent_id:
        try:
            subprocess.Popen(
                ["agent-queue", "--agent", "gigforge-advocate",
                 "--message", f"HANDOFF FROM SALES: Customer {sender_email} accepted a quote. "
                 f"Subject: {subject}. Introduce yourself and take over the project. "
                 f"CC braun.brelin@ai-elevate.ai on all emails.",
                 "--thinking", "low", "--timeout", "300"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            result["post_actions"].append("advocate_handoff_dispatched")
        except Exception as e:
            result["post_actions"].append(f"advocate_handoff_failed:{e}")

    if _detect_sentiment(message)[0] in ("frustrated", "at_risk"):
        try:
            from customer_context import context_summary as cs
            ctx = cs(sender_email) or ""
            subprocess.Popen(
                ["agent-queue", "--agent", "gigforge-csat",
                 "--message", f"ESCALATION: Customer {sender_email} is frustrated. "
                 f"Subject: {subject}. Context: {ctx[:500]}. Take action.",
                 "--thinking", "low", "--timeout", "300"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            result["post_actions"].append("csat_escalation_dispatched")
        except Exception:
            pass


def wrapped_agent_call(agent_id: str, message: str, sender_email: str = "",
                       subject: str = "", timeout: int = 300) -> dict:
    """Run the agent with pre/post workflow actions.

    Pre-actions: injects customer context and active directives into the message.
    Post-actions: updates sentiment, notifies ops, triggers handoffs on acceptance/frustration.

    Returns a dict with stdout, stderr, returncode, duration, pre_actions, post_actions.
    """
    result = {"agent_id": agent_id, "sender": sender_email, "pre_actions": [], "post_actions": []}

    enhanced_message = _pre_actions(agent_id, message, sender_email, result)
    _run_subprocess(agent_id, enhanced_message, timeout, result)

    if sender_email:
        _post_actions(agent_id, message, sender_email, subject, result)

    return result


# Async version for the email gateway
async def async_wrapped_agent_call(agent_id: str, message: str, sender_email: str = "",
                                    subject: str = "", timeout: int = 300) -> dict:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Async version — runs in executor."""
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: wrapped_agent_call(agent_id, message, sender_email, subject, timeout)
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agent Wrapper")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--message", required=True)
    parser.add_argument("--sender", default="")
    parser.add_argument("--subject", default="")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    result = wrapped_agent_call(args.agent, args.message, args.sender, args.subject, args.timeout)

    if result["stdout"]:
        print(result["stdout"])

    # Log actions
    import logging
    # logging.basicConfig removed — using get_logger() from logging_config
    log = get_logger("agent-wrapper")
    log.info(f"Agent: {args.agent}, Pre: {result['pre_actions']}, Post: {result['post_actions']}, Duration: {result.get('duration', 0)}s")

    sys.exit(result["returncode"] if result["returncode"] >= 0 else 1)
