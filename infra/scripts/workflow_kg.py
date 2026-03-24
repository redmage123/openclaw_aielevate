#!/usr/bin/env python3
"""Workflow Knowledge Graph — records decisions, relationships, and outcomes
from all project lifecycle workflows into the knowledge graph.

Captures:
  - Customer journey (inquiry → acceptance → onboarding → build → delivery → support → closure)
  - Agent involvement (who did what on which project)
  - Technical decisions (stack choices, ADRs, spec decisions)
  - Outcomes (revision count, scope changes, bug count, payment status, satisfaction)
  - Cross-project patterns (stack → quality, customer type → revision rate)

Does NOT capture:
  - Raw email content (too noisy)
  - Temporal execution state (Temporal handles that)
  - Operational logs (use DB tables for that)

Entity types:
  customer, project, agent, tech_stack, adr, spec, revision, scope_change,
  bug, payment, proposal, milestone, case_study

Relationship types:
  INQUIRED, ACCEPTED, ONBOARDED_BY, BUILT_BY, DESIGNED_BY, TESTED_BY,
  DEPLOYED_BY, USED_STACK, DECIDED_BY, REVISED, SCOPE_CHANGED,
  REPORTED_BUG, FIXED_BY, PAID, INVOICED, PROPOSED_TO, APPROVED_BY,
  CLOSED_BY, GENERATED_CASE_STUDY, ESCALATED_TO, REFERRED_BY,
  DEPENDS_ON, PART_OF, PRODUCED
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("workflow-kg")


def _kg(org="gigforge"):
    """Get KG instance for org."""
    try:
        from knowledge_graph import KG
        return KG(org)
    except Exception as e:
        log.warning(f"KG unavailable: {e}")
        return None


def _now():
    return datetime.now(timezone.utc).isoformat()[:19]


# ============================================================================
# Customer lifecycle events
# ============================================================================

def record_inquiry(org, customer_email, project_title, requirements=""):
    """Customer sends initial inquiry."""
    kg = _kg(org)
    if not kg:
        return
    cid = customer_email.replace("@", "_at_").replace(".", "_")
    pid = project_title.lower().replace(" ", "-")[:40]

    kg.add("customer", cid, {
        "email": customer_email,
        "first_contact": _now(),
        "status": "lead",
    })
    kg.add("project", pid, {
        "title": project_title,
        "customer": customer_email,
        "status": "inquiry",
        "requirements": requirements[:500],
        "created": _now(),
    })
    kg.link("customer", cid, "project", pid, "INQUIRED", {"date": _now()})
    log.info(f"KG: recorded inquiry {cid} → {pid}")


def record_acceptance(org, customer_email, project_title):
    """Customer accepts proposal."""
    kg = _kg(org)
    if not kg:
        return
    cid = customer_email.replace("@", "_at_").replace(".", "_")
    pid = project_title.lower().replace(" ", "-")[:40]

    kg.add("project", pid, {
        "status": "accepted",
        "accepted_at": _now(),
    })
    kg.link("customer", cid, "project", pid, "ACCEPTED", {"date": _now()})


def record_onboarding(org, customer_email, project_slug, advocate_agent):
    """Client onboarding completed."""
    kg = _kg(org)
    if not kg:
        return
    cid = customer_email.replace("@", "_at_").replace(".", "_")
    kg.link("customer", cid, "agent", advocate_agent, "ONBOARDED_BY", {"date": _now()})

    kg.add("project", project_slug, {"status": "onboarded", "onboarded_at": _now()})


# ============================================================================
# Build pipeline events
# ============================================================================

def record_tech_stack(org, project_slug, stack_details):
    """Engineering consensus — tech stack decided."""
    kg = _kg(org)
    if not kg:
        return
    stack_id = f"{project_slug}-stack"
    kg.add("tech_stack", stack_id, {
        "project": project_slug,
        "details": stack_details[:500],
        "decided_at": _now(),
    })
    kg.link("project", project_slug, "tech_stack", stack_id, "USED_STACK", {"date": _now()})


def record_adr(org, project_slug, adr_name, decision_summary):
    """Architecture Decision Record created."""
    kg = _kg(org)
    if not kg:
        return
    adr_id = f"{project_slug}-{adr_name}"
    kg.add("adr", adr_id, {
        "project": project_slug,
        "name": adr_name,
        "summary": decision_summary[:300],
        "created": _now(),
    })
    kg.link("project", project_slug, "adr", adr_id, "DECIDED_BY", {"date": _now()})


def record_spec(org, project_slug, spec_summary):
    """Software specification written."""
    kg = _kg(org)
    if not kg:
        return
    spec_id = f"{project_slug}-spec"
    kg.add("spec", spec_id, {
        "project": project_slug,
        "summary": spec_summary[:500],
        "created": _now(),
    })
    kg.link("project", project_slug, "spec", spec_id, "PRODUCED", {"date": _now()})


def record_build(org, project_slug, agents_involved):
    """Build completed — record which agents worked on it."""
    kg = _kg(org)
    if not kg:
        return
    roles = {
        "pm": "MANAGED_BY",
        "engineer": "BUILT_BY",
        "ux-designer": "DESIGNED_BY",
        "qa": "TESTED_BY",
        "devops": "DEPLOYED_BY",
    }
    for agent_id in agents_involved:
        for role_key, rel_type in roles.items():
            if role_key in agent_id:
                kg.link("project", project_slug, "agent", agent_id, rel_type, {"date": _now()})

    kg.add("project", project_slug, {"status": "built", "built_at": _now()})


def record_deployment(org, project_slug, preview_url):
    """Project deployed."""
    kg = _kg(org)
    if not kg:
        return
    kg.add("project", project_slug, {
        "status": "deployed",
        "preview_url": preview_url,
        "deployed_at": _now(),
    })


# ============================================================================
# Revision & scope change events
# ============================================================================

def record_revision(org, customer_email, project_slug, revision_number, description):
    """Customer requested revision."""
    kg = _kg(org)
    if not kg:
        return
    cid = customer_email.replace("@", "_at_").replace(".", "_")
    rev_id = f"{project_slug}-rev-{revision_number}"

    kg.add("revision", rev_id, {
        "project": project_slug,
        "number": revision_number,
        "description": description[:300],
        "date": _now(),
    })
    kg.link("customer", cid, "revision", rev_id, "REVISED", {"date": _now()})
    kg.link("project", project_slug, "revision", rev_id, "PART_OF", {"date": _now()})

    # Update project revision count
    kg.add("project", project_slug, {"revision_count": revision_number})


def record_scope_change(org, customer_email, project_slug, description, price_delta=0):
    """Scope change requested."""
    kg = _kg(org)
    if not kg:
        return
    cid = customer_email.replace("@", "_at_").replace(".", "_")
    sc_id = f"{project_slug}-sc-{int(datetime.now(timezone.utc).timestamp())}"

    kg.add("scope_change", sc_id, {
        "project": project_slug,
        "description": description[:300],
        "price_delta": price_delta,
        "date": _now(),
    })
    kg.link("customer", cid, "scope_change", sc_id, "SCOPE_CHANGED", {"date": _now()})
    kg.link("project", project_slug, "scope_change", sc_id, "PART_OF", {"date": _now()})


# ============================================================================
# Support events
# ============================================================================

def record_bug_report(org, customer_email, project_slug, description, ticket_id):
    """Customer reported a bug."""
    kg = _kg(org)
    if not kg:
        return
    cid = customer_email.replace("@", "_at_").replace(".", "_")
    bug_id = f"{project_slug}-bug-{ticket_id}"

    kg.add("bug", bug_id, {
        "project": project_slug,
        "ticket_id": ticket_id,
        "description": description[:300],
        "status": "open",
        "date": _now(),
    })
    kg.link("customer", cid, "bug", bug_id, "REPORTED_BUG", {"date": _now()})
    kg.link("project", project_slug, "bug", bug_id, "PART_OF", {"date": _now()})


def record_bug_fix(org, project_slug, ticket_id, fixed_by_agent):
    """Bug fixed and deployed."""
    kg = _kg(org)
    if not kg:
        return
    bug_id = f"{project_slug}-bug-{ticket_id}"
    kg.add("bug", bug_id, {"status": "resolved", "resolved_at": _now()})
    kg.link("bug", bug_id, "agent", fixed_by_agent, "FIXED_BY", {"date": _now()})


# ============================================================================
# Payment events
# ============================================================================

def record_payment(org, customer_email, project_slug, milestone_name, amount_eur, status="paid"):
    """Payment received or invoiced."""
    kg = _kg(org)
    if not kg:
        return
    cid = customer_email.replace("@", "_at_").replace(".", "_")
    pay_id = f"{project_slug}-pay-{milestone_name.lower().replace(' ', '-')[:20]}"

    kg.add("payment", pay_id, {
        "project": project_slug,
        "milestone": milestone_name,
        "amount_eur": amount_eur,
        "status": status,
        "date": _now(),
    })
    rel = "PAID" if status == "paid" else "INVOICED"
    kg.link("customer", cid, "payment", pay_id, rel, {"date": _now(), "amount": amount_eur})
    kg.link("project", project_slug, "payment", pay_id, "PART_OF")


# ============================================================================
# Proposal events
# ============================================================================

def record_proposal(org, customer_email, project_title, platform="direct", price_eur=0):
    """Proposal sent to customer."""
    kg = _kg(org)
    if not kg:
        return
    cid = customer_email.replace("@", "_at_").replace(".", "_")
    prop_id = f"proposal-{project_title.lower().replace(' ', '-')[:30]}"

    kg.add("proposal", prop_id, {
        "title": project_title,
        "platform": platform,
        "price_eur": price_eur,
        "status": "sent",
        "date": _now(),
    })
    kg.link("agent", f"{org}-sales", "proposal", prop_id, "PROPOSED_TO", {"date": _now()})
    if customer_email:
        kg.link("proposal", prop_id, "customer", cid, "PROPOSED_TO", {"date": _now()})


# ============================================================================
# Closure events
# ============================================================================

def record_closure(org, customer_email, project_slug):
    """Project closed."""
    kg = _kg(org)
    if not kg:
        return
    cid = customer_email.replace("@", "_at_").replace(".", "_")
    kg.add("project", project_slug, {"status": "closed", "closed_at": _now()})
    kg.link("customer", cid, "project", project_slug, "CLOSED", {"date": _now()})


def record_case_study(org, project_slug):
    """Case study generated."""
    kg = _kg(org)
    if not kg:
        return
    cs_id = f"case-study-{project_slug}"
    kg.add("case_study", cs_id, {
        "project": project_slug,
        "generated_at": _now(),
    })
    kg.link("project", project_slug, "case_study", cs_id, "GENERATED_CASE_STUDY", {"date": _now()})


def record_satisfaction(org, customer_email, project_slug, rating, feedback=""):
    """Customer satisfaction recorded."""
    kg = _kg(org)
    if not kg:
        return
    cid = customer_email.replace("@", "_at_").replace(".", "_")
    kg.add("customer", cid, {
        "satisfaction_rating": rating,
        "last_feedback": feedback[:200],
    })
    kg.add("project", project_slug, {"satisfaction": rating})


# ============================================================================
# Query helpers — for agent context enrichment
# ============================================================================

def customer_journey(org, customer_email):
    """Get full customer journey as a readable string for agent context."""
    kg = _kg(org)
    if not kg:
        return ""
    cid = customer_email.replace("@", "_at_").replace(".", "_")
    return kg.context("customer", cid, max_depth=3)


def project_history(org, project_slug):
    """Get full project history."""
    kg = _kg(org)
    if not kg:
        return ""
    return kg.context("project", project_slug, max_depth=2)


def stack_outcomes(org, stack_keyword):
    """Query outcomes for projects using a particular tech stack."""
    kg = _kg(org)
    if not kg:
        return ""
    results = kg.search(stack_keyword, entity_type="tech_stack")
    if not results:
        return f"No projects found using '{stack_keyword}'"

    lines = [f"Projects using '{stack_keyword}':"]
    for r in results:
        project_slug = r.get("properties", {}).get("project", "")
        if project_slug:
            proj = kg.get("project", project_slug)
            if proj:
                props = proj.get("properties", {})
                lines.append(f"  - {props.get('title', project_slug)}: "
                           f"status={props.get('status', '?')}, "
                           f"revisions={props.get('revision_count', 0)}, "
                           f"satisfaction={props.get('satisfaction', 'n/a')}")
    return "\n".join(lines)


def agent_workload(org, agent_id):
    """Query what an agent is currently working on."""
    kg = _kg(org)
    if not kg:
        return ""
    neighbors = kg.neighbors("agent", agent_id, depth=1)
    active = [n for n in neighbors if n.get("properties", {}).get("status") in ("active", "built", "deployed", "onboarded")]
    return f"{agent_id} is working on {len(active)} active projects: " + \
           ", ".join(n.get("key", "") for n in active[:5])
