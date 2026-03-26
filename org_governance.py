#!/usr/bin/env python3
"""Organizational Governance Engine — enforces business rules in CODE.

Every agent action passes through this module. Rules are enforced
programmatically, not via prompts.

Rules:
1. Financial withdrawals → require Braun approval (block otherwise)
2. Website changes → require Braun + Peter approval
3. External customer emails → auto-CC Braun + Peter
4. Golden Rule → LLM tone check on outbound comms
5. Legal compliance → flag potential legal issues
6. Tax agent available for Irish/EU tax questions
"""

import sys
import os
import re
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, "/home/aielevate")

log = logging.getLogger("governance")

BRAUN = "braun.brelin@ai-elevate.ai"
PETER = "peter.munro@ai-elevate.ai"
OWNERS = {BRAUN, "bbrelin@gmail.com", PETER}

# Internal agent email domains — emails to these are agent-to-agent, not customer-facing
INTERNAL_DOMAINS = {
    "mg.ai-elevate.ai", "internal.ai-elevate.ai", "gigforge.ai",
    "techuni.ai", "mg.gigforge.ai", "mg.techuni.ai",
}


# ── Rule 1: Financial Gate ──────────────────────────────────────────

FINANCIAL_KEYWORDS = [
    "withdraw", "transfer funds", "wire transfer", "bank transfer",
    "pay out", "payout", "disbursement", "refund to bank",
    "stripe payout", "paypal withdrawal",
]

def check_financial_approval(action_description):
    """Returns True if the action is allowed, False if it needs approval."""
    desc_lower = action_description.lower()
    for kw in FINANCIAL_KEYWORDS:
        if kw in desc_lower:
            log.warning(f"FINANCIAL GATE: blocked — needs Braun approval: {action_description[:100]}")
            return False
    return True


def request_financial_approval(action_description, amount, agent_id):
    """Send approval request to Braun for financial actions."""
    try:
        from send_email import send_email
        send_email(
            to=BRAUN,
            subject=f"[APPROVAL REQUIRED] Financial action: {action_description[:50]}",
            body=f"A financial action requires your approval:\n\n"
                 f"Action: {action_description}\n"
                 f"Amount: {amount}\n"
                 f"Requested by: {agent_id}\n"
                 f"Time: {datetime.now(timezone.utc).isoformat()}\n\n"
                 f"Reply APPROVED or REJECTED.",
            agent_id="operations",
            cc=PETER,
        )
        return True
    except Exception as e:
        log.error(f"Failed to send approval request: {e}")
        return False


# ── Rule 2: Website Change Gate ─────────────────────────────────────

WEBSITE_DOMAINS = [
    "gigforge.ai", "techuni.ai", "cc.techuni.ai", "courses.techuni.ai",
    "carehaven", "sudacka-mreza", "ai-elevate.ai",
]

def check_website_change(action_description):
    """Returns True if allowed, False if needs Braun + Peter approval."""
    desc_lower = action_description.lower()
    website_change_signals = [
        "deploy", "push to production", "update website", "change landing",
        "modify homepage", "redesign", "new page", "css change", "dns change",
    ]
    has_website = any(d in desc_lower for d in WEBSITE_DOMAINS)
    has_change = any(s in desc_lower for s in website_change_signals)

    if has_website and has_change:
        log.warning(f"WEBSITE GATE: needs Braun + Peter approval: {action_description[:100]}")
        return False
    return True


# ── Rule 3: External Customer CC ────────────────────────────────────

def is_external_customer(email):
    """Check if an email is an external customer (not internal agent or owner)."""
    email_lower = email.lower().strip()
    # Owners get direct email
    if email_lower in OWNERS:
        return False
    # Internal agent emails
    domain = email_lower.split("@")[-1] if "@" in email_lower else ""
    if domain in INTERNAL_DOMAINS:
        return False
    return True


def enforce_customer_cc(to, cc):
    """Ensure Braun and Peter are CC'd on external customer emails.
    Returns the corrected CC string."""
    if not is_external_customer(to):
        return cc

    cc_list = [a.strip() for a in (cc or "").split(",") if a.strip()]
    if BRAUN not in cc_list:
        cc_list.append(BRAUN)
    if PETER not in cc_list:
        cc_list.append(PETER)

    return ", ".join(cc_list)


# ── Rule 4: Tone Check (Golden Rule) ────────────────────────────────

AGGRESSIVE_PATTERNS = [
    r"you (must|need to|should have|failed to)",
    r"this is (unacceptable|not good enough|disappointing)",
    r"we (demand|insist|require you to)",
    r"(immediately|at once|right now) or (we will|consequences)",
    r"(your fault|your mistake|your problem)",
    r"(threaten|sue|legal action|lawyer)",
]

def check_tone(text):
    """Check if outbound text violates the Golden Rule.
    Returns list of tone violations."""
    violations = []
    text_lower = text.lower()
    for pattern in AGGRESSIVE_PATTERNS:
        if re.search(pattern, text_lower):
            match = re.search(pattern, text_lower).group()
            violations.append(f"Aggressive tone: '{match}'")
    return violations


# ── Rule 5: Legal Compliance Flags ──────────────────────────────────

LEGAL_FLAGS = [
    (r"(gdpr|data protection|personal data|right to erasure|data breach)", "GDPR"),
    (r"(non-compete|non-disclosure|nda|intellectual property|copyright)", "IP/NDA"),
    (r"(employment law|contractor|employee|dismissal|redundancy)", "Employment"),
    (r"(vat|tax|revenue commissioners|corporation tax|income tax)", "Tax"),
    (r"(consumer rights|refund policy|terms of service|warranty)", "Consumer"),
    (r"(anti-money laundering|aml|kyc|sanctions|pep)", "AML/KYC"),
]

def check_legal(text):
    """Flag potential legal issues in outbound communications."""
    flags = []
    text_lower = text.lower()
    for pattern, category in LEGAL_FLAGS:
        if re.search(pattern, text_lower):
            flags.append(category)
    return flags


# ── Rule 6: GigForge Business Domain Expansion ─────────────────────

GIGFORGE_DOMAINS = {
    "software_development": {
        "services": ["MVP builds", "SaaS platforms", "API development", "mobile apps"],
        "verticals": ["fintech", "healthtech", "edtech", "legaltech", "proptech"],
    },
    "ai_automation": {
        "services": ["AI agent systems", "RAG pipelines", "LLM integration", "chatbots", "automation workflows"],
        "verticals": ["customer support", "sales automation", "content generation", "data analysis"],
    },
    "devops_infrastructure": {
        "services": ["cloud deployment", "Docker/K8s", "CI/CD", "monitoring", "security hardening"],
        "verticals": ["startups", "scale-ups", "enterprise migration"],
    },
    "data_engineering": {
        "services": ["ETL pipelines", "data migration", "analytics dashboards", "database design"],
        "verticals": ["healthcare", "finance", "government", "retail"],
    },
    "video_content": {
        "services": ["AI video production", "motion graphics", "UGC content", "product demos"],
        "verticals": ["marketing agencies", "e-commerce", "training"],
    },
    "consulting": {
        "services": ["technical architecture", "AI strategy", "digital transformation", "security audit"],
        "verticals": ["SMBs", "enterprise", "government", "non-profit"],
    },
    "managed_services": {
        "services": ["website maintenance", "server management", "monitoring", "incident response"],
        "verticals": ["agencies", "SMBs without IT staff"],
    },
}

def get_business_domains():
    """Return GigForge's full business domain map."""
    return GIGFORGE_DOMAINS




# ── Rule 7: Irish + EU Regulatory Compliance ───────────────────────

REGULATORY_CHECKS = {
    "gdpr": {
        "triggers": ["personal data", "email list", "customer data", "user data", "data export", "data deletion"],
        "requirement": "GDPR: Data processing must have legal basis. Customer data requires consent or legitimate interest. Right to erasure must be honoured within 30 days.",
    },
    "vat": {
        "triggers": ["invoice", "pricing", "subscription", "payment", "charge"],
        "requirement": "VAT: Irish VAT (23%) applies to domestic B2C. EU B2B reverse charge applies. B2C cross-border uses OSS. Invoices must show VAT number, rate, and amount.",
    },
    "consumer_rights": {
        "triggers": ["refund", "cancel", "subscription", "trial", "free tier"],
        "requirement": "EU Consumer Rights: 14-day cooling-off period for online purchases. Clear cancellation process required. No hidden charges. Terms must be in plain language.",
    },
    "ecommerce": {
        "triggers": ["checkout", "payment", "stripe", "buy", "purchase", "subscribe"],
        "requirement": "EU E-Commerce Directive: Must display company name, address, VAT number, pricing including tax, delivery terms, and complaints procedure.",
    },
    "employment": {
        "triggers": ["contractor", "freelancer", "hire", "employ", "engage"],
        "requirement": "Irish Employment Law: Determine if relationship is employment or contractor. Written terms within 5 days. Minimum wage applies. PRSI obligations for employers.",
    },
    "data_retention": {
        "triggers": ["store", "retain", "archive", "backup", "log"],
        "requirement": "Data Retention: Only retain personal data as long as necessary for the purpose. Define retention periods. Automated deletion. Document retention policy.",
    },
    "accessibility": {
        "triggers": ["website", "web app", "frontend", "ui", "user interface"],
        "requirement": "EU Web Accessibility Directive: Public sector websites must meet WCAG 2.1 AA. Best practice for all websites.",
    },
}


def check_regulatory_compliance(action_description, body=""):
    """Check if an action triggers regulatory requirements.
    Returns list of applicable regulations and requirements."""
    combined = f"{action_description} {body}".lower()
    applicable = []
    for reg_name, reg in REGULATORY_CHECKS.items():
        if any(trigger in combined for trigger in reg["triggers"]):
            applicable.append({"regulation": reg_name, "requirement": reg["requirement"]})
    return applicable


# ── Master Validation — called on every outbound action ─────────────

def validate_action(action_type, description, agent_id, **kwargs):
    """Master validation gate for all agent actions.

    Args:
        action_type: "email", "deploy", "financial", "website_change"
        description: what the agent is doing
        agent_id: which agent
        **kwargs: additional context (to, cc, amount, etc.)

    Returns:
        {"allowed": True/False, "violations": [...], "cc": "corrected cc"}
    """
    result = {"allowed": True, "violations": [], "cc": kwargs.get("cc", "")}

    # Financial gate
    if action_type == "financial" or not check_financial_approval(description):
        if action_type == "financial":
            result["allowed"] = False
            result["violations"].append("Financial action requires Braun approval")
            request_financial_approval(description, kwargs.get("amount", "unknown"), agent_id)

    # Website change gate
    if action_type == "website_change" or (action_type == "deploy" and not check_website_change(description)):
        result["allowed"] = False
        result["violations"].append("Website change requires Braun + Peter approval")

    # Customer CC enforcement
    if action_type == "email" and kwargs.get("to"):
        result["cc"] = enforce_customer_cc(kwargs["to"], kwargs.get("cc", ""))

    # Tone check
    if kwargs.get("body"):
        tone_issues = check_tone(kwargs["body"])
        if tone_issues:
            result["violations"].extend(tone_issues)
            # Don't block — just flag

    # Legal flags
    if kwargs.get("body"):
        legal_flags = check_legal(kwargs["body"])
        if legal_flags:
            result["violations"].append(f"Legal topics detected: {', '.join(legal_flags)} — verify compliance")

    # Regulatory compliance check
    if kwargs.get("body") or description:
        regs = check_regulatory_compliance(description, kwargs.get("body", ""))
        if regs:
            for reg in regs:
                result["violations"].append(f"Regulatory: {reg['regulation']} — {reg['requirement'][:100]}")

    return result


# ── Tax Expert ──────────────────────────────────────────────────────

TAX_CONTEXT = """Irish and EU Tax Expert Knowledge:

IRISH TAX:
- Corporation Tax: 12.5% on trading income (one of lowest in EU)
- Higher rate: 25% on non-trading/passive income
- VAT: Standard 23%, reduced 13.5% and 9%
- Employer PRSI: 11.05%
- R&D Tax Credit: 25% of qualifying expenditure
- Knowledge Development Box: 6.25% on qualifying IP profits
- Revenue Commissioners: www.revenue.ie

EU TAX:
- VAT MOSS/OSS: One-Stop Shop for cross-border digital services
- ATAD: Anti-Tax Avoidance Directive
- DAC6/DAC7: Mandatory disclosure rules for intermediaries/platforms
- Transfer pricing: arm's length principle for intra-group transactions
- Digital Services Tax: varies by country (France 3%, Italy 3%, Spain 3%)
- Pillar Two: 15% global minimum tax for large multinationals

SAAS BUSINESS TAX CONSIDERATIONS:
- Revenue recognition: subscription vs one-time, deferred revenue
- Cross-border VAT: where customer is located determines VAT rate
- R&D credits: AI/ML development qualifies in most EU jurisdictions
- IP structuring: consider IP holding in Ireland for KDB rate
- Contractor vs employee: IR35 equivalent in Ireland (Employment Status Group)
"""

def get_tax_context():
    """Return tax expert knowledge for financial agents."""
    return TAX_CONTEXT
