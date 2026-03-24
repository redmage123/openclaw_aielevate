#!/usr/bin/env python3
"""CMS Workflows — document lifecycle management through Strapi.

Every significant document flows through Strapi:
  Draft → Review → Approved → Published (for external) or Filed (for internal)

Workflows:
  1. Proposal workflow: sales creates → CMS draft → review → file
  2. Invoice workflow: billing creates → CMS filed → sent to customer
  3. Feedback workflow: customer submits → CMS filed → improvement actions
  4. Progress report: PM generates → CMS filed → shared with stakeholders
  5. Case study: project completes → auto-draft from project data → marketing reviews → publish
  6. Correspondence: every email → CMS archived → searchable

Usage:
  from cms_workflows import store_proposal, store_invoice, store_feedback,
                           store_progress_report, generate_case_study, archive_email
"""

import sys
import json
from datetime import datetime, timezone
from pathlib import Path
from exceptions import AiElevateError  # TODO: Use specific exception types, AgentError

sys.path.insert(0, "/home/aielevate")


def _cms():
    """Get CMS client."""
    from cms_ops import CMS
    return CMS()


def store_proposal(org: str, customer_email: str, project_title: str,
                   proposal_text: str, amount_eur: float, agent_id: str = "gigforge-sales") -> dict:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Store a proposal in the CMS."""
    try:
        cms = _cms()
        result = cms.create_post(
            org=org,
            title=f"Proposal: {project_title} — EUR {amount_eur:.0f}",
            content=proposal_text,
            category="proposal",
            author=agent_id,
            tags=["proposal", customer_email.split("@")[0], org],
        )
        return {"status": "stored", "cms_id": result.get("id") if isinstance(result, dict) else str(result)}
    except (AgentError, Exception) as e:
        return {"status": "error", "error": str(e)}


def store_invoice(org: str, customer_email: str, project_title: str,
                  milestone: str, amount_eur: float, invoice_id: int = 0) -> dict:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Store an invoice in the CMS."""
    try:
        cms = _cms()
        content = (
            f"Invoice #{invoice_id}\n"
            f"Customer: {customer_email}\n"
            f"Project: {project_title}\n"
            f"Milestone: {milestone}\n"
            f"Amount: EUR {amount_eur:.2f}\n"
            f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
        )
        result = cms.create_post(
            org=org,
            title=f"Invoice #{invoice_id}: {project_title} — {milestone}",
            content=content,
            category="invoice",
            author="gigforge-billing",
            tags=["invoice", customer_email.split("@")[0], milestone.lower().replace(" ", "-")],
        )
        return {"status": "stored", "cms_id": result.get("id") if isinstance(result, dict) else str(result)}
    except (AiElevateError, Exception) as e:
        return {"status": "error", "error": str(e)}


def store_feedback(org: str, customer_email: str, project_title: str,
                   rating: int, comments: str, what_went_well: str = "",
                   what_to_improve: str = "") -> dict:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Store customer feedback in the CMS."""
    try:
        cms = _cms()
        content = (
            f"Customer: {customer_email}\n"
            f"Project: {project_title}\n"
            f"Rating: {rating}/10\n\n"
            f"What went well:\n{what_went_well}\n\n"
            f"What to improve:\n{what_to_improve}\n\n"
            f"Comments:\n{comments}"
        )
        result = cms.create_post(
            org=org,
            title=f"Feedback: {project_title} — {rating}/10",
            content=content,
            category="feedback",
            author="feedback-system",
            tags=["feedback", customer_email.split("@")[0], f"rating-{rating}"],
        )
        return {"status": "stored", "cms_id": result.get("id") if isinstance(result, dict) else str(result)}
    except (AiElevateError, Exception) as e:
        return {"status": "error", "error": str(e)}


def store_progress_report(org: str, customer_email: str, project_title: str,
                         milestones: list, completion_pct: int, agent_id: str = "gigforge-pm") -> dict:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Store a progress report in the CMS."""
    try:
        cms = _cms()
        content = (
            f"Project: {project_title}\n"
            f"Customer: {customer_email}\n"
            f"Progress: {completion_pct}%\n"
            f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n\n"
            f"Milestones:\n"
        )
        for m in milestones:
            status = m.get("status", "pending")
            emoji = "✅" if status == "completed" else "🔄" if status == "in_progress" else "⏳"
            content += f"  {emoji} {m.get('milestone', '?')} — {status}\n"

        result = cms.create_post(
            org=org,
            title=f"Progress: {project_title} — {completion_pct}%",
            content=content,
            category="progress-report",
            author=agent_id,
            tags=["progress", customer_email.split("@")[0], f"{completion_pct}pct"],
        )
        return {"status": "stored", "cms_id": result.get("id") if isinstance(result, dict) else str(result)}
    except (AgentError, Exception) as e:
        return {"status": "error", "error": str(e)}


def generate_case_study(org: str, customer_email: str, project_title: str,
                        tech_stack: str = "", feedback_rating: int = 0,
                        description: str = "") -> dict:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Auto-generate a case study draft from project data."""
    try:
        cms = _cms()
        content = (
            f"# Case Study: {project_title}\n\n"
            f"## Client\n{customer_email}\n\n"
            f"## Challenge\n{description[:500]}\n\n"
            f"## Solution\nTech stack: {tech_stack}\n\n"
            f"## Results\n"
            f"Client rating: {feedback_rating}/10\n\n"
            f"---\n"
            f"*This case study was auto-generated. Marketing should review, "
            f"get client permission, and publish.*"
        )
        result = cms.create_post(
            org=org,
            title=f"Case Study: {project_title}",
            content=content,
            category="case-study",
            author="marketing",
            tags=["case-study", "draft"],
            status="draft",
        )
        return {"status": "draft_created", "cms_id": result.get("id") if isinstance(result, dict) else str(result)}
    except (AiElevateError, Exception) as e:
        return {"status": "error", "error": str(e)}


def archive_email(org: str, sender: str, agent_id: str, subject: str,
                  inbound_body: str, outbound_body: str = "") -> dict:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Archive an email exchange in the CMS."""
    try:
        cms = _cms()
        content = (
            f"From: {sender}\n"
            f"Agent: {agent_id}\n"
            f"Subject: {subject}\n"
            f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n"
            f"--- Inbound ---\n{inbound_body[:2000]}\n\n"
        )
        if outbound_body:
            content += f"--- Outbound ---\n{outbound_body[:2000]}\n"

        result = cms.create_post(
            org=org,
            title=f"Email: {subject[:50]} ({sender})",
            content=content,
            category="correspondence",
            author=agent_id,
            tags=["email", sender.split("@")[0], agent_id],
        )
        return {"status": "archived", "cms_id": result.get("id") if isinstance(result, dict) else str(result)}
    except (AgentError, Exception) as e:
        return {"status": "error", "error": str(e)}
