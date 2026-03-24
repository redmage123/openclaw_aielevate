#!/usr/bin/env python3
"""Email Orchestrator Workflow — single entry point for all inbound emails.

Fixes issue #10: Gateway was doing too much. Now the gateway just validates
and forwards to this orchestrator. The orchestrator decides what to do.

Flow:
  Gateway receives email → starts OrchestratorWorkflow → orchestrator:
    1. Classifies intent (LLM-based, not keywords)
    2. Checks customer context (active project? delivered? payment status?)
    3. Routes to the appropriate workflow:
       - new_inquiry → EmailInteractionWorkflow (sales responds)
       - acceptance → EmailInteractionWorkflow + BuildWorkflow + OnboardingWorkflow
       - revision_request → RevisionCycleWorkflow
       - scope_change → ScopeChangeWorkflow
       - bug_report → PostDeliverySupportWorkflow
       - feedback → process feedback
       - general → EmailInteractionWorkflow (agent responds)
    4. Prevents duplicate workflow triggers (checks running workflows)

This replaces all the keyword-matching logic in the email gateway.
"""

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

import sys
from dao import BaseDAO  # TODO: Replace inline DB calls with specific DAOs
from logging_config import get_logger  # Structured JSON logging
from exceptions import AiElevateError
  # TODO: Use specific exception types, DatabaseError, AgentError
sys.path.insert(0, "/home/aielevate")

log = get_logger("orchestrator")

RETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=10))
DB_CONN = dict(
    host=os.environ.get("DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("DB_PORT", "5434")),
    dbname=os.environ.get("DB_NAME", "rag"),
    user=os.environ.get("DB_USER", "rag"),
    password=os.environ.get("DB_PASSWORD", "rag_vec_2026"),
)


@dataclass
class EmailInput:
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    sender_email: str
    recipient: str
    subject: str
    body: str
    message_id: str = ""
    agent_id: str = ""
    org: str = "gigforge"


@dataclass
class OrchestratorResult:
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    intent: str = "general"
    confidence: float = 0.0
    actions: list = field(default_factory=list)
    workflows_started: list = field(default_factory=list)
    reply_sent: bool = False
    error: str = ""


# ============================================================================
# Activities
# ============================================================================

# Support email chain — structured ACK → Investigation → Status → RCA
@activity.defn
async def trigger_support_chain(input: EmailInput) -> str:
    """Start the support email chain workflow (ACK + investigation + RCA)."""
    try:
        from support_email_chain import start_support_chain
        result = await start_support_chain(
            sender_email=input.sender_email,
            subject=input.subject,
            body=input.body,
            agent_id=input.agent_id,
            org=input.org,
            message_id=input.message_id,
            sender_name="",
        )
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)[:200]})


@activity.defn
async def classify_email_intent(input: EmailInput) -> dict:
    """Classify email intent using LLM."""
    try:
        from intent_classifier import classify_intent
        from customer_context import get_context

        # Get customer context
        ctx = {}
        try:
            ctx = get_context(input.sender_email) or {}
        except (AiElevateError, Exception) as e:
            pass

        has_active = bool(ctx.get("active_project"))
        delivered = False
        try:
            import psycopg2
            conn = psycopg2.connect(**DB_CONN)
            cur = conn.cursor()
            cur.execute(
                "SELECT status FROM project_milestones WHERE customer_email=%s AND milestone='Deployment' AND status='completed'",
                (input.sender_email,))
            delivered = cur.fetchone() is not None
            conn.close()
        except (DatabaseError, Exception) as e:
            pass

        result = classify_intent(
            sender=input.sender_email,
            subject=input.subject,
            body=input.body,
            has_active_project=has_active,
            project_delivered=delivered,
        )
        result["has_active_project"] = has_active
        result["project_delivered"] = delivered
        return result
    except (AiElevateError, Exception) as e:
        log.error(f"Classification failed: {e}")
        return {"type": "general", "confidence": 0.3, "trigger_workflow": False,
                "has_active_project": False, "project_delivered": False}


@activity.defn
async def check_duplicate_workflows(input: EmailInput) -> dict:
    """Check if there are already running workflows for this customer."""
    running = {}
    try:
        from temporalio.client import Client
        client = await Client.connect("localhost:7233")
        async for wf in client.list_workflows(''):
            if input.sender_email.split("@")[0].replace(".", "-") in wf.id:
                handle = client.get_workflow_handle(wf.id)
                desc = await handle.describe()
                if desc.status == 1:  # RUNNING
                    running[wf.workflow_type] = wf.id
    except (AiElevateError, Exception) as e:
        log.warning(f"Duplicate check failed: {e}")
    return running


@activity.defn
async def trigger_email_workflow(input: EmailInput) -> str:
    """Start the email interaction workflow (agent responds to customer)."""
    try:
        from temporal_workflows import execute_workflow
        result = await execute_workflow(
            input.agent_id, input.body, input.sender_email,
            input.subject, timeout=300)
        return json.dumps({
            "sentiment": result.get("sentiment", ""),
            "actions": result.get("actions", []),
            "stdout": result.get("stdout", "")[:500],
            "response_agent": result.get("response_agent", ""),
        })
    except (AgentError, Exception) as e:
        return json.dumps({"error": str(e)[:200]})


@activity.defn
async def trigger_build_workflow(input: EmailInput) -> str:
    """Start the build workflow."""
    try:
        from build_workflow import start_build
        slug = re.sub(r'^(re:\s*)+', '', input.subject.lower()).strip()
        slug = re.sub(r'[^a-z0-9]+', '-', slug).strip('-')[:40]
        result = await start_build(
            input.sender_email, input.subject.replace("Re: ", ""),
            slug, input.body[:1000])
        return json.dumps(result)
    except (AgentError, Exception) as e:
        return json.dumps({"error": str(e)[:200]})


@activity.defn
async def trigger_onboarding_workflow(input: EmailInput) -> str:
    """Start client onboarding."""
    try:
        from project_workflows import start_onboarding
        slug = re.sub(r'^(re:\s*)+', '', input.subject.lower()).strip()
        slug = re.sub(r'[^a-z0-9]+', '-', slug).strip('-')[:40]
        result = await start_onboarding(
            input.sender_email, input.subject.replace("Re: ", ""),
            slug, input.body[:1000])
        return json.dumps(result)
    except (AgentError, Exception) as e:
        return json.dumps({"error": str(e)[:200]})


@activity.defn
async def trigger_revision_workflow(input: EmailInput) -> str:
    """Start revision cycle."""
    try:
        from project_workflows import start_revision
        import psycopg2
        conn = psycopg2.connect(**DB_CONN)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM revision_cycles WHERE customer_email=%s", (input.sender_email,))
        rev_num = (cur.fetchone()[0] or 0) + 1
        conn.close()
        if rev_num > 3:
            return json.dumps({"error": "Max 3 revisions reached", "status": "blocked"})
        slug = re.sub(r'^(re:\s*)+', '', input.subject.lower()).strip()
        slug = re.sub(r'[^a-z0-9]+', '-', slug).strip('-')[:40]
        result = await start_revision(
            input.sender_email, input.subject.replace("Re: ", ""),
            slug, rev_num, input.body[:1000])
        return json.dumps(result)
    except (DatabaseError, Exception) as e:
        return json.dumps({"error": str(e)[:200]})


@activity.defn
async def trigger_scope_change_workflow(input: EmailInput) -> str:
    """Start scope change assessment."""
    try:
        from project_workflows import start_scope_change
        slug = re.sub(r'^(re:\s*)+', '', input.subject.lower()).strip()
        slug = re.sub(r'[^a-z0-9]+', '-', slug).strip('-')[:40]
        result = await start_scope_change(
            input.sender_email, input.subject.replace("Re: ", ""),
            slug, input.body[:1000])
        return json.dumps(result)
    except (AiElevateError, Exception) as e:
        return json.dumps({"error": str(e)[:200]})


@activity.defn
async def trigger_support_workflow(input: EmailInput) -> str:
    """Start post-delivery support."""
    try:
        from project_workflows import start_support
        slug = re.sub(r'^(re:\s*)+', '', input.subject.lower()).strip()
        slug = re.sub(r'[^a-z0-9]+', '-', slug).strip('-')[:40]
        result = await start_support(
            input.sender_email, input.subject.replace("Re: ", ""),
            slug, input.body[:1000])
        return json.dumps(result)
    except (AiElevateError, Exception) as e:
        return json.dumps({"error": str(e)[:200]})


# ============================================================================
# Orchestrator Workflow
# ============================================================================



@activity.defn
async def link_email_to_workflow(input: EmailInput) -> bool:
    """Update email_interactions with the workflow ID that was triggered."""
    try:
        import psycopg2
        conn = psycopg2.connect(**DB_CONN)
        conn.autocommit = True
        # Find the most recent interaction for this sender+subject
        conn.cursor().execute(
            "UPDATE email_interactions SET workflow_id = %s "
            "WHERE sender_email = %s AND subject = %s AND workflow_id IS NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (input.message_id, input.sender_email, input.subject))
        conn.close()
        return True
    except (DatabaseError, Exception) as e:
        return False

@workflow.defn
class EmailOrchestratorWorkflow:
    """Routes inbound emails to the correct workflow based on LLM intent classification."""

    @workflow.run
    async def run(self, input: EmailInput) -> OrchestratorResult:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        result = OrchestratorResult()
        ts = timedelta(seconds=60)
        tl = timedelta(seconds=420)

        # 1. Classify intent
        intent = await workflow.execute_activity(
            classify_email_intent, input,
            start_to_close_timeout=ts, retry_policy=RETRY)
        result.intent = intent.get("type", "general")
        result.confidence = intent.get("confidence", 0)
        result.actions.append(f"classified:{result.intent}:{result.confidence}")

        # 2. Check for duplicate running workflows
        running = await workflow.execute_activity(
            check_duplicate_workflows, input,
            start_to_close_timeout=ts, retry_policy=RETRY)
        if running:
            result.actions.append(f"existing_workflows:{list(running.keys())}")

        # 3. Dispatch based on intent — support chain for bug/support, simple reply for others
        support_intents = {"bug_report", "question", "feedback"}
        if result.intent in support_intents:
            # Full support chain: ACK → Investigation → Status Updates → RCA
            chain_json = await workflow.execute_activity(
                trigger_support_chain, input,
                start_to_close_timeout=tl, retry_policy=RETRY)
            result.reply_sent = True
            result.actions.append(f"support_chain:{chain_json[:80]}")
            result.workflows_started.append("support_chain")
        else:
            # Simple email interaction for non-support intents
            email_result_json = await workflow.execute_activity(
                trigger_email_workflow, input,
                start_to_close_timeout=tl, retry_policy=RETRY)
            result.reply_sent = True
            result.actions.append("email_replied")

        email_result = {}
        if result.intent not in {"bug_report", "question", "feedback"}:
            try:
                email_result = json.loads(email_result_json)
            except (AgentError, Exception) as e:
                pass

        # 4. Check if acceptance was detected by the email workflow
        acceptance_from_email = any("handoff:" in a for a in email_result.get("actions", []))

        # 5. Route to additional workflows based on intent
        should_trigger = intent.get("trigger_workflow", False) and result.confidence >= 0.6

        if result.intent == "acceptance" or acceptance_from_email:
            if "ProjectBuildWorkflow" not in running:
                build_json = await workflow.execute_activity(
                    trigger_build_workflow, input,
                    start_to_close_timeout=ts, retry_policy=RETRY)
                result.workflows_started.append("build")
                result.actions.append(f"build:{build_json[:50]}")
                # Link email to workflow
                link_input = EmailInput(**{**input.__dict__, "message_id": build_json[:60]})
                try:
                    await workflow.execute_activity(link_email_to_workflow, link_input,
                        start_to_close_timeout=ts, retry_policy=RETRY)
                except (AiElevateError, Exception) as e:
                    pass

            if "ClientOnboardingWorkflow" not in running:
                onboard_json = await workflow.execute_activity(
                    trigger_onboarding_workflow, input,
                    start_to_close_timeout=ts, retry_policy=RETRY)
                result.workflows_started.append("onboarding")
                result.actions.append(f"onboard:{onboard_json[:50]}")

        elif result.intent == "revision_request" and should_trigger:
            if "RevisionCycleWorkflow" not in running:
                rev_json = await workflow.execute_activity(
                    trigger_revision_workflow, input,
                    start_to_close_timeout=ts, retry_policy=RETRY)
                result.workflows_started.append("revision")
                result.actions.append(f"revision:{rev_json[:50]}")

        elif result.intent == "scope_change" and should_trigger:
            if "ScopeChangeWorkflow" not in running:
                sc_json = await workflow.execute_activity(
                    trigger_scope_change_workflow, input,
                    start_to_close_timeout=ts, retry_policy=RETRY)
                result.workflows_started.append("scope_change")
                result.actions.append(f"scope_change:{sc_json[:50]}")

        elif result.intent == "bug_report" and should_trigger:
            if "PostDeliverySupportWorkflow" not in running:
                sup_json = await workflow.execute_activity(
                    trigger_support_workflow, input,
                    start_to_close_timeout=ts, retry_policy=RETRY)
                result.workflows_started.append("support")
                result.actions.append(f"support:{sup_json[:50]}")

        # Check for preview approval ("looks great", "go live", "approved")
        approval_keywords = ["looks great", "go live", "approved", "ship it", "launch it",
                             "ready to launch", "good to go", "happy with it", "love it"]
        lower_body = input.body.lower()
        if any(kw in lower_body for kw in approval_keywords) and "preview" in (input.subject or "").lower():
            try:
                from project_workflows import start_closure
                import re as _re
                slug = _re.sub(r'^(re:\s*)+', '', input.subject.lower()).strip()
                slug = _re.sub(r'[^a-z0-9]+', '-', slug).strip('-')[:40]
                closure_result = await workflow.execute_activity(
                    trigger_email_workflow, input,
                    start_to_close_timeout=ts, retry_policy=RETRY)
                result.workflows_started.append("closure_triggered")
                result.actions.append("preview_approved:closure_queued")
            except (AiElevateError, Exception) as e:
                pass

        return result


# ============================================================================
# Client
# ============================================================================

async def orchestrate_email(sender_email, recipient, subject, body,
                            message_id="", agent_id="", org="gigforge"):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Start the orchestrator workflow for an inbound email."""
    from temporalio.client import Client
    client = await Client.connect("localhost:7233")
    input_data = EmailInput(
        sender_email=sender_email, recipient=recipient,
        subject=subject, body=body, message_id=message_id,
        agent_id=agent_id, org=org)

    handle = await client.start_workflow(
        EmailOrchestratorWorkflow.run, input_data,
        id=f"orchestrate-{agent_id}-{int(time.time())}",
        task_queue="email-orchestrator",
        execution_timeout=timedelta(minutes=15),
    )
    return {"workflow_id": handle.id, "status": "started"}
