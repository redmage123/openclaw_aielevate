#!/usr/bin/env python3
"""Intent Classifier — replaces keyword matching with LLM-based classification.

Fixes issue #9: "I have a bug in my garden" won't trigger a support workflow.

Uses a lightweight agent call to classify customer email intent before
triggering workflows. The classifier returns structured JSON, not free text.

Usage:
    from intent_classifier import classify_intent

    intent = classify_intent(
        sender="sarah@example.com",
        subject="Re: Contact app",
        body="The search isn't working when I type special characters",
        has_active_project=True,
        project_delivered=True,
    )
    # intent = {"type": "bug_report", "confidence": 0.9, "details": "search broken with special chars"}
"""

import json
import logging
import os
import re
import subprocess
from typing import Optional
from logging_config import get_logger  # Structured JSON logging
from exceptions import AiElevateError  # TODO: Use specific exception types, AgentError

log = get_logger("intent-classifier")

INTENT_TYPES = [
    "new_inquiry",       # Customer asking about a new project
    "acceptance",        # Customer accepting a proposal / saying yes
    "revision_request",  # Customer wants changes to delivered work
    "scope_change",      # Customer wants new features beyond original spec
    "bug_report",        # Customer reporting something broken
    "question",          # Customer asking a question (no workflow needed)
    "feedback",          # Customer giving feedback/testimonial
    "payment",           # Payment-related (invoice question, receipt request)
    "cancellation",      # Customer wants to cancel
    "general",           # General conversation (no workflow trigger)
]


def classify_intent(sender: str, subject: str, body: str,
                    has_active_project: bool = False,
                    project_delivered: bool = False) -> dict:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Classify customer email intent using a fast LLM call.

    Returns:
        {
            "type": "bug_report",
            "confidence": 0.9,
            "details": "brief description of what they want",
            "trigger_workflow": True/False,
        }
    """
    prompt = f"""Classify this customer email intent. Return ONLY valid JSON, nothing else.

Email from: {sender}
Subject: {subject}
Body: {body[:500]}

Context:
- Has active project: {has_active_project}
- Project delivered: {project_delivered}

Intent types: {', '.join(INTENT_TYPES)}

Rules:
- "bug_report" ONLY if they describe something broken/not working in software we built for them AND the project is delivered
- "revision_request" ONLY if they want changes to work we already delivered AND the project is delivered
- "scope_change" ONLY if they want NEW features beyond what was agreed AND there's an active project
- "acceptance" if they clearly say yes/go ahead/approved/let's proceed
- "new_inquiry" if they're asking about a new project (no active project context)
- "general" if it's just conversation, thanks, or doesn't need a workflow

Return JSON: {{"type": "<intent>", "confidence": <0.0-1.0>, "details": "<brief>", "trigger_workflow": <true/false>}}"""

    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    try:
        proc = subprocess.run(
            ["openclaw", "agent", "--agent", "gigforge-sales",
             "--message", prompt, "--thinking", "low", "--timeout", "15"],
            capture_output=True, text=True, timeout=25, env=env,
        )
        output = (proc.stdout or "").strip()

        # Extract JSON from output
        json_match = re.search(r'\{[^}]+\}', output)
        if json_match:
            result = json.loads(json_match.group())
            # Validate
            if result.get("type") in INTENT_TYPES:
                log.info(f"Intent: {result['type']} ({result.get('confidence', '?')}) for {sender}")
                return result

        # Fallback if LLM didn't return valid JSON
        log.warning(f"Intent classifier returned invalid output: {output[:100]}")
    except subprocess.TimeoutExpired:
        log.warning("Intent classifier timed out")
    except json.JSONDecodeError:
        log.warning(f"Intent classifier returned non-JSON: {output[:100]}")
    except (AgentError, Exception) as e:
        log.warning(f"Intent classifier error: {e}")

    # Fallback to basic keyword matching (degraded mode)
    return _keyword_fallback(body, has_active_project, project_delivered)


def _keyword_fallback(body: str, has_active_project: bool, project_delivered: bool) -> dict:
    """Fallback keyword classifier when LLM is unavailable."""
    lower = body.lower()

    # Acceptance — high confidence keywords
    acceptance_kw = ["let's go ahead", "lets go ahead", "i accept", "go ahead",
                     "approved", "start the project", "lets proceed", "let's proceed"]
    if any(kw in lower for kw in acceptance_kw):
        return {"type": "acceptance", "confidence": 0.8, "details": "keyword match", "trigger_workflow": True}

    # Bug — only if project is delivered
    if project_delivered:
        bug_kw = ["not working", "broken", "error", "crash", "bug", "doesn't work", "won't load"]
        if any(kw in lower for kw in bug_kw):
            return {"type": "bug_report", "confidence": 0.6, "details": "keyword match", "trigger_workflow": True}

    # Revision — only if project is delivered
    if project_delivered:
        rev_kw = ["change the", "update the", "modify", "revision", "can you adjust"]
        if any(kw in lower for kw in rev_kw):
            return {"type": "revision_request", "confidence": 0.6, "details": "keyword match", "trigger_workflow": True}

    # Scope change — only if active project
    if has_active_project:
        scope_kw = ["add a feature", "new feature", "also need", "additional requirement"]
        if any(kw in lower for kw in scope_kw):
            return {"type": "scope_change", "confidence": 0.6, "details": "keyword match", "trigger_workflow": True}

    return {"type": "general", "confidence": 0.5, "details": "no clear intent", "trigger_workflow": False}
