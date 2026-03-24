#!/usr/bin/env python3
"""AI Elevate Stripe Payment Integration

Handles payments for both TechUni and GigForge:
- TechUni: subscription plans (Free/Pro/Enterprise)
- GigForge: project invoices and milestone payments

Usage:
    from stripe_payments import (
        create_checkout_session,    # Generate payment link
        create_invoice,             # Send an invoice
        create_subscription,        # Set up recurring billing
        get_payment_link,           # Quick payment link
        list_payments,              # View payment history
        check_subscription,         # Check subscription status
    )
"""

import json
import os
import stripe
from datetime import datetime, timezone
from pathlib import Path

# Load Stripe keys
_creds_path = "/opt/ai-elevate/credentials/stripe.env"
if os.path.exists(_creds_path):
    with open(_creds_path) as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ[k] = v

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")

PAYMENT_LOG = Path("/opt/ai-elevate/payments")
PAYMENT_LOG.mkdir(parents=True, exist_ok=True)

# ── TechUni Subscription Plans ───────────────────────────────────────────

TECHUNI_PLANS = {
    "free": {"name": "Free", "price_eur": 0, "features": ["1 course", "Basic editor", "No labs"]},
    "pro": {"name": "Pro", "price_eur": 4900, "features": ["Unlimited courses", "AI generation", "Docker labs", "Analytics"]},
    "enterprise": {"name": "Enterprise", "price_eur": 19900, "features": ["White-label", "SSO", "API access", "Priority support"]},
}

# ── GigForge Service Tiers ───────────────────────────────────────────────

GIGFORGE_SERVICES = {
    "consultation": {"name": "AI Consultation", "price_eur": 10000, "description": "1-hour AI strategy consultation"},
    "mvp": {"name": "MVP Build", "price_eur": 250000, "description": "Full MVP development (4-6 weeks)"},
    "retainer_basic": {"name": "Basic Retainer", "price_eur": 50000, "description": "Monthly maintenance + bug fixes"},
    "retainer_standard": {"name": "Standard Retainer", "price_eur": 100000, "description": "Monthly maintenance + features"},
    "retainer_premium": {"name": "Premium Retainer", "price_eur": 200000, "description": "Dedicated engineer, priority support"},
}


def _log_payment(event_type, data):
    """Log payment events."""
    log_file = PAYMENT_LOG / f"payments-{datetime.now(timezone.utc).strftime('%Y-%m')}.jsonl"
    with open(log_file, "a") as f:
        f.write(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            **data,
        }) + "\n")


# ── Stripe Products & Prices (create on first use) ──────────────────────

_product_cache = {}

def _ensure_product(name, description="", org="gigforge"):
    """Get or create a Stripe product."""
    cache_key = f"{org}:{name}"
    if cache_key in _product_cache:
        return _product_cache[cache_key]

    # Search existing products
    products = stripe.Product.list(limit=100)
    for p in products.data:
        if p.name == name and p.metadata.get("org") == org:
            _product_cache[cache_key] = p.id
            return p.id

    # Create new product
    product = stripe.Product.create(
        name=name,
        description=description,
        metadata={"org": org},
    )
    _product_cache[cache_key] = product.id
    return product.id


def _ensure_price(product_id, amount_cents, currency="eur", recurring=None):
    """Get or create a Stripe price for a product."""
    prices = stripe.Price.list(product=product_id, limit=10)
    for p in prices.data:
        if p.unit_amount == amount_cents and p.currency == currency:
            if recurring and p.recurring and p.recurring.interval == recurring:
                return p.id
            elif not recurring and not p.recurring:
                return p.id

    params = {
        "product": product_id,
        "unit_amount": amount_cents,
        "currency": currency,
    }
    if recurring:
        params["recurring"] = {"interval": recurring}

    price = stripe.Price.create(**params)
    return price.id


# ── Public API ───────────────────────────────────────────────────────────

def create_checkout_session(org, plan_or_service, customer_email="",
                              success_url="https://ai-elevate.ai/payment/success",
                              cancel_url="https://ai-elevate.ai/payment/cancel"):
    """Create a Stripe Checkout session and return the payment URL."""
    if org == "techuni" and plan_or_service in TECHUNI_PLANS:
        plan = TECHUNI_PLANS[plan_or_service]
        if plan["price_eur"] == 0:
            return {"url": None, "message": "Free plan — no payment required"}

        product_id = _ensure_product(f"TechUni {plan['name']}", ", ".join(plan["features"]), "techuni")
        price_id = _ensure_price(product_id, plan["price_eur"], recurring="month")
        mode = "subscription"

    elif org == "gigforge" and plan_or_service in GIGFORGE_SERVICES:
        svc = GIGFORGE_SERVICES[plan_or_service]
        product_id = _ensure_product(svc["name"], svc["description"], "gigforge")

        if "retainer" in plan_or_service:
            price_id = _ensure_price(product_id, svc["price_eur"], recurring="month")
            mode = "subscription"
        else:
            price_id = _ensure_price(product_id, svc["price_eur"])
            mode = "payment"
    else:
        return {"error": f"Unknown plan/service: {plan_or_service} for {org}"}

    params = {
        "mode": mode,
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": success_url,
        "cancel_url": cancel_url,
    }
    if customer_email:
        params["customer_email"] = customer_email

    session = stripe.checkout.Session.create(**params)

    _log_payment("checkout_created", {
        "org": org, "plan": plan_or_service,
        "customer_email": customer_email,
        "session_id": session.id, "url": session.url,
        "amount_cents": session.amount_total,
    })

    return {"url": session.url, "session_id": session.id}


def create_payment_link(org, description, amount_eur, customer_email=""):
    """Create a simple payment link for any amount."""
    product_id = _ensure_product(f"{org.title()} — {description[:50]}", description, org)
    price_id = _ensure_price(product_id, int(amount_eur * 100))

    link = stripe.PaymentLink.create(
        line_items=[{"price": price_id, "quantity": 1}],
    )

    _log_payment("payment_link_created", {
        "org": org, "description": description,
        "amount_eur": amount_eur, "url": link.url,
    })

    return {"url": link.url, "id": link.id}


def create_invoice(customer_email, org, items, due_days=14):
    """Create and send a Stripe invoice."""
    # Get or create customer
    customers = stripe.Customer.list(email=customer_email, limit=1)
    if customers.data:
        customer = customers.data[0]
    else:
        customer = stripe.Customer.create(
            email=customer_email,
            metadata={"org": org},
        )

    invoice = stripe.Invoice.create(
        customer=customer.id,
        collection_method="send_invoice",
        days_until_due=due_days,
        metadata={"org": org},
    )

    for item in items:
        product_id = _ensure_product(item["name"], item.get("description", ""), org)
        price_id = _ensure_price(product_id, int(item["amount_eur"] * 100))
        stripe.InvoiceItem.create(
            customer=customer.id,
            invoice=invoice.id,
            price=price_id,
        )

    # Finalize and send
    invoice = stripe.Invoice.finalize_invoice(invoice.id)
    stripe.Invoice.send_invoice(invoice.id)

    _log_payment("invoice_sent", {
        "org": org, "customer_email": customer_email,
        "invoice_id": invoice.id, "amount_total": invoice.amount_due,
        "hosted_url": invoice.hosted_invoice_url,
    })

    return {
        "invoice_id": invoice.id,
        "hosted_url": invoice.hosted_invoice_url,
        "pdf_url": invoice.invoice_pdf,
        "amount_due": invoice.amount_due / 100,
        "currency": invoice.currency,
    }


def list_payments(org="", limit=20):
    """List recent payments."""
    params = {"limit": limit}
    charges = stripe.Charge.list(**params)
    results = []
    for c in charges.data:
        if org and c.metadata.get("org") != org:
            continue
        results.append({
            "id": c.id,
            "amount": c.amount / 100,
            "currency": c.currency,
            "status": c.status,
            "customer_email": c.billing_details.email if c.billing_details else "",
            "created": datetime.fromtimestamp(c.created, tz=timezone.utc).isoformat(),
            "description": c.description or "",
        })
    return results


def check_subscription(customer_email):
    """Check if a customer has an active subscription."""
    customers = stripe.Customer.list(email=customer_email, limit=1)
    if not customers.data:
        return {"status": "no_customer", "plan": "free"}

    subs = stripe.Subscription.list(customer=customers.data[0].id, status="active", limit=5)
    if not subs.data:
        return {"status": "no_subscription", "plan": "free"}

    active = subs.data[0]
    return {
        "status": "active",
        "subscription_id": active.id,
        "plan": active.items.data[0].price.product if active.items.data else "unknown",
        "current_period_end": datetime.fromtimestamp(active.current_period_end, tz=timezone.utc).isoformat(),
        "cancel_at_period_end": active.cancel_at_period_end,
    }


def get_revenue_summary():
    """Get revenue summary for reporting."""
    balance = stripe.Balance.retrieve()
    available = sum(b.amount for b in balance.available) / 100
    pending = sum(b.amount for b in balance.pending) / 100

    return {
        "available_eur": available,
        "pending_eur": pending,
        "currency": "eur",
    }


# ── CLI ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Stripe Payments")
    parser.add_argument("command", choices=["checkout", "link", "invoice", "payments", "subscription", "revenue"])
    parser.add_argument("--org", default="gigforge")
    parser.add_argument("--plan", default="")
    parser.add_argument("--email", default="")
    parser.add_argument("--amount", type=float, default=0)
    parser.add_argument("--description", default="")
    args = parser.parse_args()

    if args.command == "checkout":
        print(json.dumps(create_checkout_session(args.org, args.plan, args.email), indent=2))
    elif args.command == "link":
        print(json.dumps(create_payment_link(args.org, args.description, args.amount), indent=2))
    elif args.command == "invoice":
        items = [{"name": args.description or "Service", "amount_eur": args.amount}]
        print(json.dumps(create_invoice(args.email, args.org, items), indent=2))
    elif args.command == "payments":
        print(json.dumps(list_payments(args.org), indent=2))
    elif args.command == "subscription":
        print(json.dumps(check_subscription(args.email), indent=2))
    elif args.command == "revenue":
        print(json.dumps(get_revenue_summary(), indent=2))
