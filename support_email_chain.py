#!/usr/bin/env python3
"""Support Email Chain Workflow

Implements a structured email chain for customer support requests:
1. Instant ACK with ticket ID (no LLM, < 2 seconds)
2. Agent investigation response
3. Status updates as work progresses
4. Root cause analysis from engineering/QA

Integrates with:
- Plane (ticket creation and tracking)
- send_email.py (outbound email with dedup)
- OpenClaw agents (investigation, RCA)
- Temporal (durable workflow execution)

Usage:
    from support_email_chain import start_support_chain

    await start_support_chain(
        sender_email="customer@example.com",
        subject="Login page broken",
        body="I can't log in since this morning...",
        agent_id="gigforge",
        org="gigforge",
    )
"""

import json
import re
import sys
import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

sys.path.insert(0, "/home/aielevate")

from temporalio import workflow, activity
from temporalio.common import RetryPolicy

from logging_config import get_logger
from exceptions import AiElevateError

log = get_logger("support-chain")

RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(seconds=60),
    maximum_attempts=3,
)


@dataclass
class SupportChainInput:
    sender_email: str
    subject: str
    body: str
    agent_id: str
    org: str
    message_id: str = ""
    sender_name: str = ""


@dataclass
class SupportChainResult:
    ticket_id: str = ""
    ticket_seq: int = 0
    ack_sent: bool = False
    investigation_sent: bool = False
    status_updates: int = 0
    rca_sent: bool = False
    resolved: bool = False


# ── Activity: Create Plane Ticket ──────────────────────────────────────────

@activity.defn
async def create_support_ticket(input: SupportChainInput) -> dict:
    """Create a ticket in Plane and return the ticket ID and sequence number."""
    try:
        from plane_ops import Plane
        p = Plane("gigforge")

        # Determine priority from keywords
        priority = "medium"
        body_lower = input.body.lower()
        if any(w in body_lower for w in ["urgent", "critical", "down", "broken", "crash", "emergency"]):
            priority = "urgent"
        elif any(w in body_lower for w in ["important", "asap", "blocking"]):
            priority = "high"

        # Clean subject for ticket title
        clean_subject = re.sub(r'^(re:\s*)+', '', input.subject, flags=re.IGNORECASE).strip()

        issue = p.create_bug(
            app=input.org.replace("gigforge", "GigForge").replace("techuni", "TechUni"),
            title=clean_subject,
            description=f"**Reported by:** {input.sender_email}\n\n{input.body[:2000]}",
            priority=priority,
            labels=["customer-reported", "email-inbound"],
            reporter=input.agent_id,
        )

        ticket_id = issue.get("id", "")
        seq = issue.get("sequence_id", 0)
        log.info(f"Ticket created: {ticket_id} (#{seq}) for {input.sender_email}")

        return {"ticket_id": ticket_id, "sequence_id": seq, "priority": priority}

    except Exception as e:
        log.error(f"Failed to create ticket: {e}")
        # Return a fallback — don't block the chain
        import hashlib
        fallback_id = hashlib.sha256(
            f"{input.sender_email}{input.subject}".encode()
        ).hexdigest()[:8].upper()
        return {"ticket_id": f"FALLBACK-{fallback_id}", "sequence_id": 0, "priority": "medium"}


# ── Activity: Send ACK Email ──────────────────────────────────────────────

@activity.defn
async def send_ack_email(input: SupportChainInput, ticket_info: dict) -> bool:
    """Send immediate acknowledgement with ticket ID."""
    from send_email import send_email

    seq = ticket_info.get("sequence_id", 0)
    priority = ticket_info.get("priority", "medium")
    ticket_ref = f"GF-{seq}" if seq else ticket_info.get("ticket_id", "N/A")[:12]

    # Determine the right agent name
    agent_names = {
        "gigforge": "Alex Reeves",
        "gigforge-support": "Taylor Brooks",
        "gigforge-advocate": "Jordan Whitaker",
        "gigforge-engineer": "Chris Novak",
        "gigforge-devops": "Casey Muller",
        "operations": "Kai Sorensen",
    }
    agent_name = agent_names.get(input.agent_id, "GigForge Support")

    name = (input.sender_name or input.sender_email.split("@")[0].replace(".", " ").title()).split()[0]

    priority_line = ""
    if priority in ("urgent", "high"):
        priority_line = f"\nPriority: {priority.upper()} — this has been flagged for immediate attention.\n"

    body = f"""Hi {name},

Thank you for reaching out. Your request has been received and logged.

Ticket: {ticket_ref}
Subject: {input.subject}
{priority_line}
Our team is reviewing this now. You'll receive updates as we investigate and work towards a resolution. Please reference {ticket_ref} in any follow-up emails.

Best regards,
{agent_name}
GigForge"""

    result = send_email(
        to=input.sender_email,
        subject=f"[{ticket_ref}] Re: {input.subject}",
        body=body,
        agent_id=input.agent_id,
        cc="braun.brelin@ai-elevate.ai",
    )
    log.info(f"ACK sent to {input.sender_email}: {ticket_ref} ({result.get('status')})")
    return result.get("status") == "sent"


# ── Activity: Agent Investigation ─────────────────────────────────────────

@activity.defn
async def run_investigation(input: SupportChainInput, ticket_info: dict) -> dict:
    """Dispatch the agent to investigate and respond."""
    try:
        from temporal_workflows import execute_workflow

        ticket_ref = f"GF-{ticket_info.get('sequence_id', 0)}" if ticket_info.get("sequence_id") else "N/A"

        enhanced_message = (
            f"SUPPORT TICKET: {ticket_ref}\n"
            f"From: {input.sender_email}\n"
            f"Subject: {input.subject}\n\n"
            f"{input.body}\n\n"
            f"INSTRUCTIONS: Investigate this issue. After your analysis, send a status update "
            f"email to {input.sender_email} with your findings. Include the ticket reference "
            f"{ticket_ref} in the subject line. Be specific about what you found and what "
            f"steps you're taking. Do NOT send an acknowledgement — that was already sent."
        )

        result = await execute_workflow(
            input.agent_id, enhanced_message, input.sender_email,
            input.subject, timeout=300)

        return {
            "stdout": (result.get("stdout") or "")[:1000],
            "sentiment": result.get("sentiment", ""),
            "actions": result.get("actions", []),
        }
    except Exception as e:
        log.error(f"Investigation failed: {e}")
        return {"stdout": "", "error": str(e)[:200]}


# ── Activity: Send Status Update ──────────────────────────────────────────

@activity.defn
async def send_status_update(
    input: SupportChainInput, ticket_info: dict, status: str, details: str
) -> bool:
    """Send a status update email in the chain."""
    from send_email import send_email

    seq = ticket_info.get("sequence_id", 0)
    ticket_ref = f"GF-{seq}" if seq else ticket_info.get("ticket_id", "N/A")[:12]
    name = (input.sender_name or input.sender_email.split("@")[0].replace(".", " ").title()).split()[0]

    body = f"""Hi {name},

Status update on your ticket {ticket_ref}:

Status: {status}

{details}

We'll keep you posted on further progress. Reply to this email or reference {ticket_ref} if you have questions.

Best regards,
GigForge Engineering"""

    result = send_email(
        to=input.sender_email,
        subject=f"[{ticket_ref}] {status} — {input.subject}",
        body=body,
        agent_id="gigforge-engineer",
        cc="braun.brelin@ai-elevate.ai",
    )
    log.info(f"Status update sent: {ticket_ref} → {status} ({result.get('status')})")
    return result.get("status") == "sent"


# ── Activity: Root Cause Analysis ─────────────────────────────────────────

@activity.defn
async def run_rca(input: SupportChainInput, ticket_info: dict, investigation: dict) -> dict:
    """Have engineering/QA produce a root cause analysis."""
    try:
        from temporal_workflows import execute_workflow

        ticket_ref = f"GF-{ticket_info.get('sequence_id', 0)}" if ticket_info.get("sequence_id") else "N/A"

        rca_prompt = (
            f"SUPPORT TICKET: {ticket_ref}\n"
            f"Customer: {input.sender_email}\n"
            f"Issue: {input.subject}\n"
            f"Description: {input.body[:1000]}\n\n"
            f"Investigation findings:\n{investigation.get('stdout', 'No findings available')[:1000]}\n\n"
            f"TASK: Write a root cause analysis (RCA) for this issue. Include:\n"
            f"1. Root Cause — what exactly went wrong and why\n"
            f"2. Impact — what was affected\n"
            f"3. Resolution — what was done to fix it\n"
            f"4. Prevention — what we're doing to prevent recurrence\n\n"
            f"Then send the RCA to {input.sender_email} via send_email with subject "
            f"'[{ticket_ref}] Root Cause Analysis — {input.subject}'. "
            f"Sign off as the engineering team."
        )

        # Route RCA to the right specialist
        rca_agent = "gigforge-engineer"
        body_lower = input.body.lower()
        subject_lower = input.subject.lower()
        findings_lower = investigation.get("stdout", "").lower()
        combined = body_lower + " " + subject_lower + " " + findings_lower

        # Infrastructure issues → DevOps (Casey Muller)
        infra_keywords = [
            "server", "deploy", "docker", "container", "nginx", "ssl", "cert",
            "dns", "domain", "port", "firewall", "ufw", "ssh", "cpu", "memory",
            "disk", "load", "timeout", "502", "503", "504", "crash", "restart",
            "uptime", "downtime", "outage", "infra", "infrastructure", "devops",
            "pipeline", "ci/cd", "build fail", "health check", "network",
            "database down", "connection refused", "cloudflare", "cdn",
            "scaling", "kubernetes", "k8s", "monitoring", "alert",
        ]
        if any(kw in combined for kw in infra_keywords):
            rca_agent = "gigforge-devops"
        # QA/testing issues → QA (Riley Svensson)
        elif "qa" in input.agent_id or any(kw in combined for kw in ["test", "regression", "flaky", "assertion"]):
            rca_agent = "gigforge-qa"

        result = await execute_workflow(
            rca_agent, rca_prompt, input.sender_email,
            input.subject, timeout=300)

        return {
            "stdout": (result.get("stdout") or "")[:1000],
            "agent": rca_agent,
        }
    except Exception as e:
        log.error(f"RCA failed: {e}")
        return {"stdout": "", "error": str(e)[:200]}


# ── Activity: Verify Fix ───────────────────────────────────────────────────

@activity.defn
async def verify_fix(input: SupportChainInput, ticket_info: dict, investigation: dict) -> dict:
    """Have QA or DevOps verify the fix before we tell the customer it's resolved.

    Routes to the appropriate verifier:
    - Infrastructure issues → gigforge-devops
    - Code/logic issues → gigforge-qa
    """
    try:
        from temporal_workflows import execute_workflow

        ticket_ref = f"GF-{ticket_info.get('sequence_id', 0)}" if ticket_info.get("sequence_id") else "N/A"

        body_lower = input.body.lower()
        findings_lower = investigation.get("stdout", "").lower()
        combined = body_lower + " " + findings_lower

        # Route verification to the right team
        infra_keywords = [
            "server", "deploy", "docker", "container", "nginx", "ssl", "dns",
            "port", "firewall", "ssh", "cpu", "memory", "disk", "load",
            "timeout", "502", "503", "504", "crash", "restart", "infra",
            "infrastructure", "devops", "pipeline", "health check", "network",
            "database down", "connection refused", "cloudflare", "scaling",
        ]
        if any(kw in combined for kw in infra_keywords):
            verify_agent = "gigforge-devops"
        else:
            verify_agent = "gigforge-qa"

        verify_prompt = (
            f"SUPPORT TICKET: {ticket_ref}\n"
            f"Customer: {input.sender_email}\n"
            f"Issue: {input.subject}\n"
            f"Description: {input.body[:800]}\n\n"
            f"Investigation findings:\n{investigation.get('stdout', 'N/A')[:800]}\n\n"
            f"TASK: VERIFY that this fix actually works. You must:\n"
            f"1. Check that the reported issue is no longer reproducible\n"
            f"2. Run any relevant health checks or tests\n"
            f"3. Confirm no regressions were introduced\n\n"
            f"Reply with VERIFIED if the fix is confirmed working, or FAILED with details "
            f"if the issue persists. Do NOT send any email to the customer — "
            f"just report your verification result here."
        )

        result = await execute_workflow(
            verify_agent, verify_prompt, input.sender_email,
            input.subject, timeout=300)

        stdout = (result.get("stdout") or "").strip()
        verified = "verified" in stdout.lower() and "failed" not in stdout.lower()

        return {
            "verified": verified,
            "agent": verify_agent,
            "details": stdout[:500],
        }
    except Exception as e:
        log.error(f"Verification failed: {e}")
        # If verification itself fails, don't block — flag it
        return {"verified": False, "agent": "unknown", "error": str(e)[:200]}


# ── Activity: Update Plane Ticket ─────────────────────────────────────────

@activity.defn
async def update_ticket_state(ticket_info: dict, state: str, comment: str = "") -> bool:
    """Update the Plane ticket state and optionally add a comment."""
    try:
        from plane_ops import Plane
        p = Plane("gigforge")
        ticket_id = ticket_info.get("ticket_id", "")
        if not ticket_id or ticket_id.startswith("FALLBACK"):
            return False

        p.set_state(project="BUG", issue_id=ticket_id, state=state)
        if comment:
            p.add_comment(project="BUG", issue_id=ticket_id, body=comment)
        log.info(f"Ticket {ticket_id} → {state}")
        return True
    except Exception as e:
        log.error(f"Failed to update ticket: {e}")
        return False


# ── Workflow: Support Email Chain ─────────────────────────────────────────

@workflow.defn
class SupportEmailChainWorkflow:
    """Structured support email chain: ACK → Investigation → Status → RCA."""

    @workflow.run
    async def run(self, input: SupportChainInput) -> SupportChainResult:
        result = SupportChainResult()
        ts = timedelta(seconds=60)
        tl = timedelta(seconds=420)

        # Step 1: Create ticket in Plane
        ticket_info = await workflow.execute_activity(
            create_support_ticket, input,
            start_to_close_timeout=ts, retry_policy=RETRY)
        result.ticket_id = ticket_info.get("ticket_id", "")
        result.ticket_seq = ticket_info.get("sequence_id", 0)

        # Step 2: Send instant ACK (< 2 seconds, no LLM)
        result.ack_sent = await workflow.execute_activity(
            send_ack_email, args=[input, ticket_info],
            start_to_close_timeout=ts, retry_policy=RETRY)

        # Step 3: Update ticket to In Progress
        await workflow.execute_activity(
            update_ticket_state, args=[ticket_info, "In Progress",
                f"Investigation started. Customer notified with ticket reference."],
            start_to_close_timeout=ts, retry_policy=RETRY)

        # Step 4: Agent investigation (LLM — sends its own email)
        investigation = await workflow.execute_activity(
            run_investigation, args=[input, ticket_info],
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.investigation_sent = bool(investigation.get("stdout"))

        # Step 5: Verify the fix (QA or DevOps — NO email to customer yet)
        verification = await workflow.execute_activity(
            verify_fix, args=[input, ticket_info, investigation],
            start_to_close_timeout=tl, retry_policy=RETRY)

        if verification.get("verified"):
            # Step 6a: Fix verified — send status update to customer
            await workflow.execute_activity(
                send_status_update,
                args=[input, ticket_info, "Fix Verified",
                      f"The fix has been verified by our {verification.get('agent', 'team').replace('gigforge-', '')} team. "
                      f"A detailed root cause analysis will follow shortly."],
                start_to_close_timeout=ts, retry_policy=RETRY)
            result.status_updates += 1

            # Step 7: Update ticket to In Review
            await workflow.execute_activity(
                update_ticket_state, args=[ticket_info, "In Review",
                    f"Fix verified by {verification.get('agent', 'team')}. Proceeding to RCA."],
                start_to_close_timeout=ts, retry_policy=RETRY)

            # Step 8: Root Cause Analysis (engineer/devops/QA — sends email)
            rca = await workflow.execute_activity(
                run_rca, args=[input, ticket_info, investigation],
                start_to_close_timeout=tl, retry_policy=RETRY)
            result.rca_sent = bool(rca.get("stdout")) and not rca.get("error")

            # Step 9: Mark ticket as Done
            if result.rca_sent:
                await workflow.execute_activity(
                    update_ticket_state, args=[ticket_info, "Done",
                        "Fix verified. RCA sent to customer. Ticket resolved."],
                    start_to_close_timeout=ts, retry_policy=RETRY)
                result.resolved = True
        else:
            # Step 6b: Verification failed — notify customer it's still being worked on
            fail_details = verification.get("details", "")[:200] or "Further investigation needed."
            await workflow.execute_activity(
                send_status_update,
                args=[input, ticket_info, "Still Investigating",
                      f"Our team has identified the issue but the fix needs further work. "
                      f"We're continuing to investigate and will update you once the fix is confirmed."],
                start_to_close_timeout=ts, retry_policy=RETRY)
            result.status_updates += 1

            await workflow.execute_activity(
                update_ticket_state, args=[ticket_info, "In Progress",
                    f"Verification FAILED: {fail_details}. Reopened for further investigation."],
                start_to_close_timeout=ts, retry_policy=RETRY)

        log.info(f"Support chain complete: ticket={result.ticket_id} "
                 f"ack={result.ack_sent} rca={result.rca_sent} resolved={result.resolved}")
        return result


# ── Client function ───────────────────────────────────────────────────────

async def start_support_chain(
    sender_email: str,
    subject: str,
    body: str,
    agent_id: str = "gigforge",
    org: str = "gigforge",
    message_id: str = "",
    sender_name: str = "",
) -> dict:
    """Start the support email chain workflow via Temporal."""
    from temporalio.client import Client

    client = await Client.connect("localhost:7233")
    input_data = SupportChainInput(
        sender_email=sender_email,
        subject=subject,
        body=body,
        agent_id=agent_id,
        org=org,
        message_id=message_id,
        sender_name=sender_name,
    )

    slug = re.sub(r'[^a-z0-9]+', '-', subject.lower())[:40].strip('-')
    workflow_id = f"support-chain-{slug}-{int(__import__('time').time())}"

    handle = await client.start_workflow(
        SupportEmailChainWorkflow.run,
        input_data,
        id=workflow_id,
        task_queue="ai-elevate-workflows",
    )

    return {"workflow_id": workflow_id, "run_id": handle.result_run_id, "status": "started"}
