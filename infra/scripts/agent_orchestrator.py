#!/usr/bin/env python3
"""Agent Orchestrator — per-agent entry point that manages the full interaction lifecycle.

The gateway calls this via REST. The orchestrator:
  1. Pre-actions: context, directives, history
  2. Calls the LLM (openclaw agent)
  3. Post-actions: sentiment, ops, notify relevant agents
  4. Returns the reply

Every interaction notifies ALL relevant agents:
  - CS/Advocate (unless they're the one handling it) — customer relationship
  - Ops — oversight
  - PM — if there's an active project
  - Sales — if pre-contract or follow-up context

Runs as a FastAPI service on port 8068.

Usage:
  POST http://localhost:8068/handle
  {
    "agent_id": "gigforge-sales",
    "message": "INBOUND EMAIL...",
    "sender_email": "sarah@example.com",
    "subject": "Quote request"
  }
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel

sys.path.insert(0, "/home/aielevate")

app = FastAPI(title="Agent Orchestrator")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [orchestrator] %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("/var/log/openclaw/shared/orchestrator.log"),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger("orchestrator")

# Sentiment detection
POSITIVE = ["thanks", "great", "love", "perfect", "excellent", "awesome", "lets go ahead",
            "let's go ahead", "i accept", "go ahead", "approved", "looks good", "sounds good",
            "lets proceed", "let's proceed", "happy with", "impressed", "wonderful"]
FRUSTRATED = ["disappointed", "frustrated", "unacceptable", "not happy", "terrible",
              "still waiting", "no response", "what is taking", "speak to someone",
              "talk to a manager", "supervisor", "waste of time", "holdup", "awful",
              "ridiculous", "fed up", "done with"]
ACCEPTANCE = ["lets go ahead", "let's go ahead", "i accept", "go ahead", "lets proceed",
              "let's proceed", "approved", "start the project", "when can you start",
              "ready to move forward", "lets do it", "let's do it"]

# Agent notification map — who needs to know about interactions
NOTIFY_MAP = {
    "gigforge-sales": ["gigforge-advocate", "operations"],
    "gigforge-advocate": ["operations", "gigforge-pm"],
    "gigforge-support": ["gigforge-advocate", "operations", "gigforge-csat"],
    "gigforge-csat": ["operations", "gigforge-advocate"],
    "gigforge-engineer": ["gigforge-pm", "operations"],
    "gigforge-pm": ["operations", "gigforge-advocate"],
    "gigforge-billing": ["operations", "gigforge-advocate"],
    "gigforge": ["operations"],
    "operations": ["gigforge-advocate"],
    "techuni-sales": ["techuni-advocate", "operations"],
    "techuni-advocate": ["operations", "techuni-pm"],
    "techuni-support": ["techuni-advocate", "operations", "techuni-csat"],
    "techuni-csat": ["operations", "techuni-advocate"],
    "techuni-ceo": ["operations"],
}


class InteractionRequest(BaseModel):
    agent_id: str
    message: str
    sender_email: str = ""
    subject: str = ""
    timeout: int = 300


class InteractionResponse(BaseModel):
    stdout: str = ""
    returncode: int = 0
    duration: int = 0
    pre_actions: list = []
    post_actions: list = []
    sentiment: str = ""
    notifications_sent: list = []


def _detect_sentiment(text: str) -> tuple:
    lower = text.lower()
    if any(kw in lower for kw in FRUSTRATED):
        return "frustrated", "Customer expressed frustration"
    if any(kw in lower for kw in ACCEPTANCE):
        return "positive", "Customer accepted/agreed to proceed"
    if any(kw in lower for kw in POSITIVE):
        return "positive", "Positive tone in email"
    return "neutral", "Standard inquiry"


def _detect_acceptance(text: str) -> bool:
    return any(kw in text.lower() for kw in ACCEPTANCE)


def _notify_agent(agent_id: str, message: str):
    """Dispatch a notification to an agent via the queue."""
    subprocess.Popen(
        ["agent-queue", "--agent", agent_id, "--message", message,
         "--thinking", "low", "--timeout", "180"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


@app.post("/handle", response_model=InteractionResponse)
async def handle_interaction(req: InteractionRequest):
    """Full orchestration of an agent interaction."""
    result = InteractionResponse()
    agent_id = req.agent_id
    sender = req.sender_email
    subject = req.subject
    message = req.message

    log.info(f"Orchestrating: {agent_id} for {sender} re: {subject[:40]}")

    # === PRE-ACTIONS ===

    # 1. Customer context
    enhanced_message = message
    try:
        from customer_context import context_summary
        ctx = context_summary(sender)
        if ctx and len(ctx) > 30:
            enhanced_message += f"\n\nCustomer context:\n{ctx}"
            result.pre_actions.append("context_injected")
    except Exception:
        pass

    # 2. Directives
    try:
        from directives import directives_summary
        d = directives_summary()
        if d:
            enhanced_message += f"\n\n{d}"
            result.pre_actions.append("directives_checked")
    except Exception:
        pass

    # === CALL THE AGENT ===
    start = time.time()
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    loop = asyncio.get_event_loop()
    try:
        def _run_agent():
            return subprocess.run(
                ["openclaw", "agent", "--agent", agent_id,
                 "--message", enhanced_message,
                 "--thinking", "low", "--timeout", str(req.timeout)],
                capture_output=True, text=True, timeout=req.timeout + 30, env=env,
            )
        proc = await loop.run_in_executor(None, _run_agent)
        result.stdout = proc.stdout or ""
        result.returncode = proc.returncode
    except subprocess.TimeoutExpired:
        result.stdout = ""
        result.returncode = -1
    result.duration = int(time.time() - start)

    log.info(f"Agent {agent_id} responded: {len(result.stdout)} chars in {result.duration}s")

    if not sender:
        return result

    # === POST-ACTIONS ===

    # 1. Update sentiment
    rating, reason = _detect_sentiment(message)
    result.sentiment = rating
    try:
        from customer_context import update_sentiment
        update_sentiment(sender, rating, reason, agent=agent_id)
        result.post_actions.append(f"sentiment:{rating}")
    except Exception as e:
        result.post_actions.append(f"sentiment_error:{e}")

    # 2. Notify ops
    try:
        from ops_notify import ops_notify
        event_type = "new_project" if _detect_acceptance(message) else "status_update"
        if rating in ("frustrated", "at_risk"):
            event_type = "escalation"
        desc = f"{agent_id} handled email from {sender}: {subject[:40]}"
        ops_notify(event_type, desc, agent=agent_id, customer_email=sender)
        result.post_actions.append(f"ops:{event_type}")
    except Exception as e:
        result.post_actions.append(f"ops_error:{e}")

    # 3. Notify all relevant agents
    agents_to_notify = NOTIFY_MAP.get(agent_id, ["operations"])
    # Remove self from notification list
    agents_to_notify = [a for a in agents_to_notify if a != agent_id]

    notification = (
        f"INTERACTION NOTIFICATION: {agent_id} just handled an email from {sender}.\n"
        f"Subject: {subject}\n"
        f"Sentiment: {rating} ({reason})\n"
        f"Summary: Customer {'accepted a deal' if _detect_acceptance(message) else 'sent an inquiry/update'}.\n"
        f"Email excerpt: {message[:300]}"
    )

    for notify_agent in agents_to_notify:
        try:
            _notify_agent(notify_agent, notification)
            result.notifications_sent.append(notify_agent)
        except Exception:
            pass

    log.info(f"Post-actions: {result.post_actions}, Notified: {result.notifications_sent}")

    # 4. Update ALL data stores — Plane, KG, customer context DB
    # Plane: add comment to customer's project ticket
    try:
        from plane_ops import Plane
        org = "gigforge" if "gigforge" in agent_id or agent_id in ("operations", "devops") else "techuni"
        p = Plane(org)
        for proj in p.projects.keys():
            try:
                issues = p.list_issues(proj)
                if isinstance(issues, dict): issues = issues.get("results", [])
                if isinstance(issues, list):
                    for issue in issues:
                        if sender in str(issue.get("description", "") or ""):
                            seq = issue.get("sequence_id")
                            if seq:
                                p.add_comment(proj, seq,
                                    f"Email from {sender} handled by {agent_id}. Sentiment: {rating}. Subject: {subject[:50]}")
                                result.post_actions.append(f"plane_comment:{proj}-{seq}")
                            break
            except Exception:
                continue
    except Exception as e:
        result.post_actions.append(f"plane_error:{e}")

    # KG: update customer entity
    try:
        from knowledge_graph import KG
        org = "gigforge" if "gigforge" in agent_id or agent_id in ("operations", "devops") else "techuni"
        kg = KG(org)
        customer_id = sender.replace("@", "_at_").replace(".", "_")
        try:
            kg.update("customer", customer_id, {
                "email": sender,
                "last_interaction": datetime.now(timezone.utc).isoformat()[:19],
                "last_agent": agent_id,
                "sentiment": rating,
            })
        except Exception:
            kg.add("customer", customer_id, {
                "email": sender,
                "type": "customer",
                "last_interaction": datetime.now(timezone.utc).isoformat()[:19],
                "last_agent": agent_id,
                "sentiment": rating,
            })
        result.post_actions.append("kg_updated")
    except Exception as e:
        result.post_actions.append(f"kg_error:{e}")

    # Customer notes: log the interaction
    try:
        from customer_context import add_note
        add_note(sender, f"Email handled by {agent_id}. Subject: {subject[:50]}. Sentiment: {rating}.", agent=agent_id)
        result.post_actions.append("note_added")
    except Exception:
        pass

    # 5. Acceptance handoff — dispatch advocate if sales got an acceptance
    if _detect_acceptance(message) and "sales" in agent_id:
        try:
            advocate = "gigforge-advocate" if "gigforge" in agent_id else "techuni-advocate"
            _notify_agent(advocate,
                f"HANDOFF FROM SALES: Customer {sender} accepted a quote.\n"
                f"Subject: {subject}\n"
                f"Introduce yourself and take over the project relationship.\n"
                f"CC braun.brelin@ai-elevate.ai on all emails.")
            result.post_actions.append(f"advocate_handoff:{advocate}")
        except Exception:
            pass

    # 5. Frustration escalation — dispatch CSAT
    if rating in ("frustrated", "at_risk"):
        csat = "gigforge-csat" if "gigforge" in agent_id else "techuni-csat"
        if csat != agent_id:  # Don't escalate to yourself
            try:
                _notify_agent(csat,
                    f"ESCALATION: Customer {sender} sentiment is {rating}.\n"
                    f"Subject: {subject}\n"
                    f"Reason: {reason}\n"
                    f"Email excerpt: {message[:500]}\n"
                    f"Take action — contact the customer, resolve the issue.")
                result.post_actions.append(f"csat_escalation:{csat}")
            except Exception:
                pass

    return result


@app.get("/health")
async def health():
    return {"status": "ok", "service": "agent-orchestrator"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8068)
