#!/usr/bin/env python3
"""Sales Pipeline — automates the flow from quote acceptance through to project kickoff.

Flow:
  1. Customer accepts quote → generate_proposal() creates formal PDF proposal
  2. Customer accepts proposal → create_invoice() generates Stripe payment link
  3. Payment received → kickoff_project() creates Plane tickets and dispatches engineering

Usage:
  from sales_pipeline import generate_proposal, create_invoice, kickoff_project

  # After customer says yes to a quote
  proposal = generate_proposal(
      org="gigforge",
      customer_name="Peter Munro",
      customer_email="peter.munro@ai-elevate.ai",
      project_title="Kilcock Heritage Society Website",
      scope_items=["Main website (8-10 pages)", "Events calendar", "Membership portal", "Donation system"],
      price_eur=5000,
      tech_stack="Next.js + Strapi CMS",
      client_dependencies=["Logo", "Historical photos", "Article content", "Committee approval on design"],
  )

  # After customer accepts proposal
  invoice = create_invoice(
      org="gigforge",
      customer_email="peter.munro@ai-elevate.ai",
      project_title="Kilcock Heritage Society Website",
      amount_eur=5000,
      deposit_percent=30,  # 30% upfront, rest on delivery
  )

  # After payment received
  kickoff_project(
      org="gigforge",
      project_code="GFWEB",
      project_title="Kilcock Heritage Society Website",
      customer_email="peter.munro@ai-elevate.ai",
      assigned_engineer="gigforge-engineer",
      scope_items=["Main website", "Events calendar", "Membership portal", "Donation system"],
  )
"""

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, "/home/aielevate")

# --- Proposal Generation ---

PROPOSAL_TEMPLATE = """
# Project Proposal

**From:** GigForge — AI-Powered Development Agency
**To:** {customer_name} ({customer_email})
**Date:** {date}
**Proposal ID:** {proposal_id}
**Valid Until:** {valid_until}

---

## Project: {project_title}

### Scope of Work

{scope_section}

### Technical Approach

{tech_stack}

### Timeline

- **Kickoff:** Same day as deposit receipt
- **Development:** We build your project the moment we have your assets — our side takes hours, not weeks
- **Your project timeline depends entirely on how quickly you provide content, assets, and approvals**
- Traditional agencies take months. We deliver the moment you are ready.

{client_deps_section}

### Pricing

| Item | Amount |
|------|--------|
| Total project fee | EUR {price_eur:,.2f} |
| Deposit ({deposit_percent}% upfront) | EUR {deposit_eur:,.2f} |
| Final payment (on delivery) | EUR {final_eur:,.2f} |

### Payment Terms

- {deposit_percent}% deposit required before work begins
- Remaining {100 - deposit_percent}% due on project delivery and acceptance
- Payment via Stripe (card) or PayPal

### What's Included

- Source code ownership transferred on final payment
- 30 days of bug fixes post-delivery
- Documentation and deployment guide
- 1 hour of post-launch support

### Before We Begin — What We Need From You

1. Reply confirming you accept this proposal
2. **Your production domain** (e.g. kilcockheritage.ie) — if not yet registered, let us know and we can advise
3. The client assets listed above

### How to Accept

Reply to this email with "I accept this proposal", your production domain, and pay the deposit:

**Deposit Payment Link:** {payment_link}

---

*GigForge | AI-Powered Freelance Agency | https://gigforge.ai*
"""


def generate_proposal(
    org: str,
    customer_name: str,
    customer_email: str,
    project_title: str,
    scope_items: list,
    price_eur: float,
    tech_stack: str = "To be determined based on requirements",
    deposit_percent: int = 30,
    client_dependencies: list = None,
    timeline_weeks: int = 0,  # deprecated, kept for backwards compat
) -> dict:
    """Generate a formal proposal and save it. Returns proposal metadata."""

    now = datetime.now(timezone.utc)
    proposal_id = f"GF-PROP-{now.strftime('%Y%m%d')}-{int(time.time()) % 10000:04d}"
    valid_until = (now + timedelta(days=14)).strftime("%Y-%m-%d")

    deposit_eur = price_eur * deposit_percent / 100
    final_eur = price_eur - deposit_eur

    # Create Stripe payment link for the deposit
    payment_link = "PENDING"
    try:
        from stripe_payments import create_payment_link
        result = create_payment_link(
            name=f"Deposit: {project_title}",
            amount_cents=int(deposit_eur * 100),
            currency="eur",
            description=f"Deposit for {project_title} ({deposit_percent}%)",
        )
        payment_link = result.get("url", "PENDING")
    except Exception as e:
        payment_link = f"Payment link generation failed: {e}. Contact sales@gigforge.ai for manual invoicing."

    scope_section = "\n".join(f"- {item}" for item in scope_items)

    if client_dependencies:
        client_deps_section = "**What we need from you before we can deliver:**\n" + "\n".join(f"- {dep}" for dep in client_dependencies)
    else:
        client_deps_section = ""

    proposal_text = PROPOSAL_TEMPLATE.format(
        customer_name=customer_name,
        customer_email=customer_email,
        date=now.strftime("%Y-%m-%d"),
        proposal_id=proposal_id,
        valid_until=valid_until,
        project_title=project_title,
        scope_section=scope_section,
        tech_stack=tech_stack,
        client_deps_section=client_deps_section,
        price_eur=price_eur,
        deposit_percent=deposit_percent,
        deposit_eur=deposit_eur,
        final_eur=final_eur,
        payment_link=payment_link,
    )

    # Save proposal
    proposals_dir = Path(f"/opt/ai-elevate/{org}/memory/proposals")
    proposals_dir.mkdir(parents=True, exist_ok=True)
    proposal_file = proposals_dir / f"{proposal_id}.md"
    proposal_file.write_text(proposal_text)

    # Log to payments journal
    payments_dir = Path("/opt/ai-elevate/payments")
    payments_dir.mkdir(parents=True, exist_ok=True)
    log_file = payments_dir / f"proposals-{now.strftime('%Y-%m')}.jsonl"
    with open(log_file, "a") as f:
        f.write(json.dumps({
            "timestamp": now.isoformat(),
            "proposal_id": proposal_id,
            "org": org,
            "customer": customer_email,
            "project": project_title,
            "amount": price_eur,
            "deposit": deposit_eur,
            "status": "sent",
            "payment_link": payment_link,
            "file": str(proposal_file),
        }) + "\n")

    return {
        "proposal_id": proposal_id,
        "proposal_text": proposal_text,
        "proposal_file": str(proposal_file),
        "payment_link": payment_link,
        "deposit_eur": deposit_eur,
        "total_eur": price_eur,
    }


# --- Invoice Creation ---

def create_invoice(
    org: str,
    customer_email: str,
    project_title: str,
    amount_eur: float,
    deposit_percent: int = 30,
    description: str = "",
) -> dict:
    """Create a Stripe invoice for the deposit. Returns invoice metadata with payment link."""

    deposit_eur = amount_eur * deposit_percent / 100

    try:
        from stripe_payments import create_invoice as stripe_invoice
        result = stripe_invoice(
            customer_email=customer_email,
            items=[{
                "description": f"Deposit ({deposit_percent}%): {project_title}",
                "amount_cents": int(deposit_eur * 100),
                "currency": "eur",
            }],
            memo=description or f"Deposit for {project_title}",
        )
        return {
            "invoice_id": result.get("id"),
            "payment_url": result.get("hosted_invoice_url", result.get("url")),
            "amount_eur": deposit_eur,
            "status": "sent",
        }
    except Exception as e:
        # Fallback: create a payment link instead
        try:
            from stripe_payments import create_payment_link
            result = create_payment_link(
                name=f"Invoice: {project_title} deposit",
                amount_cents=int(deposit_eur * 100),
                currency="eur",
                description=f"Deposit for {project_title}",
            )
            return {
                "invoice_id": f"LINK-{int(time.time())}",
                "payment_url": result.get("url"),
                "amount_eur": deposit_eur,
                "status": "link_created",
            }
        except Exception as e2:
            return {
                "invoice_id": None,
                "payment_url": None,
                "amount_eur": deposit_eur,
                "status": f"failed: {e2}",
                "error": str(e),
            }


# --- Project Kickoff ---

def kickoff_project(
    org: str,
    project_code: str,
    project_title: str,
    customer_email: str,
    production_domain: str = "",
    project_dir: str = "",
    slug: str = "",
    assigned_engineer: str = "gigforge-engineer",
    scope_items: list = None,
    priority: str = "high",
) -> dict:
    """Create Plane tickets, dispatch engineering, and deploy preview container."""

    result = {
        "plane_ticket": None,
        "project_code": project_code,
        "status": "created",
        "assigned_to": assigned_engineer,
        "preview_url": None,
        "production_domain": production_domain,
    }

    # Create Plane ticket
    try:
        from plane_ops import Plane
        p = Plane(org)

        description = f"Customer: {customer_email}\nProject: {project_title}\n"
        if production_domain:
            description += f"Production domain: {production_domain}\n"
        description += "\nScope:\n"
        if scope_items:
            description += "\n".join(f"- {item}" for item in scope_items)

        issue = p.create_issue(
            project=project_code,
            title=f"[PROJECT] {project_title}",
            description=description,
            priority=priority,
        )

        result["plane_ticket"] = issue.get("id") if isinstance(issue, dict) else str(issue)
    except Exception as e:
        result["plane_error"] = str(e)

    # Deploy preview container if project_dir and slug provided
    if project_dir and slug:
        try:
            from preview_deploy import deploy_preview
            preview = deploy_preview(
                project_dir=project_dir,
                slug=slug,
                org=org,
                customer_email=customer_email,
            )
            result["preview_url"] = preview.get("url") or preview.get("direct_url")
            result["preview_direct"] = preview.get("direct_url")
            result["preview_status"] = preview.get("status")
        except Exception as e:
            result["preview_error"] = str(e)

    return result


# --- Pipeline Status Check ---

def pipeline_status(customer_email: str) -> dict:
    """Check the current pipeline status for a customer."""
    proposals = Path("/opt/ai-elevate/payments")
    status = {"customer": customer_email, "proposals": [], "invoices": [], "projects": []}

    for jsonl in proposals.glob("proposals-*.jsonl"):
        with open(jsonl) as f:
            for line in f:
                d = json.loads(line)
                if d.get("customer") == customer_email:
                    status["proposals"].append(d)

    for jsonl in proposals.glob("payments-*.jsonl"):
        with open(jsonl) as f:
            for line in f:
                d = json.loads(line)
                if d.get("customer") == customer_email:
                    status["invoices"].append(d)

    return status


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Sales Pipeline")
    parser.add_argument("--status", help="Check pipeline status for customer email")
    args = parser.parse_args()
    if args.status:
        s = pipeline_status(args.status)
        print(json.dumps(s, indent=2))
