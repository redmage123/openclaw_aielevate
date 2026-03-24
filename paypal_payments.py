#!/usr/bin/env python3
"""AI Elevate PayPal Payment Integration

Handles PayPal payments for TechUni and GigForge alongside Stripe.

Usage:
    from paypal_payments import create_payment, create_subscription_plan, get_payment_status
"""

import json
import os
import paypalrestsdk
from datetime import datetime, timezone
from pathlib import Path
from exceptions import AiElevateError  # TODO: Use specific exception types

# Load PayPal credentials
_creds_path = "/opt/ai-elevate/credentials/paypal.env"
if os.path.exists(_creds_path):
    with open(_creds_path) as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ[k] = v

paypalrestsdk.configure({
    "mode": os.environ.get("PAYPAL_MODE", "sandbox"),
    "client_id": os.environ.get("PAYPAL_CLIENT_ID", ""),
    "client_secret": os.environ.get("PAYPAL_SECRET_KEY", ""),
})

PAYMENT_LOG = Path("/opt/ai-elevate/payments")
PAYMENT_LOG.mkdir(parents=True, exist_ok=True)


def _log(event, data):
    log_file = PAYMENT_LOG / f"paypal-{datetime.now(timezone.utc).strftime('%Y-%m')}.jsonl"
    with open(log_file, "a") as f:
        f.write(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(), "event": event, **data}) + "\n")


def create_payment(org, description, amount_eur, customer_email="",
                    success_url="", cancel_url=""):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    """Create a PayPal payment and return the approval URL."""
    if not success_url:
        domain = "gigforge.ai" if org == "gigforge" else "techuni.ai"
        success_url = f"https://{domain}/payment/success"
        cancel_url = f"https://{domain}/payment/cancel"

    payment = paypalrestsdk.Payment({
        "intent": "sale",
        "payer": {"payment_method": "paypal"},
        "transactions": [{
            "amount": {"total": f"{amount_eur:.2f}", "currency": "EUR"},
            "description": description,
            "custom": json.dumps({"org": org, "email": customer_email}),
        }],
        "redirect_urls": {"return_url": success_url, "cancel_url": cancel_url},
    })

    if payment.create():
        approval_url = next((l.href for l in payment.links if l.rel == "approval_url"), None)
        _log("payment_created", {
            "org": org, "payment_id": payment.id, "amount": amount_eur,
            "description": description, "customer": customer_email, "url": approval_url,
        })
        return {"payment_id": payment.id, "approval_url": approval_url, "status": "created"}
    else:
        return {"error": payment.error, "status": "failed"}


def execute_payment(payment_id, payer_id):
    """Execute an approved payment (called after customer approves on PayPal)."""
    payment = paypalrestsdk.Payment.find(payment_id)
    if payment.execute({"payer_id": payer_id}):
        _log("payment_executed", {"payment_id": payment_id, "payer_id": payer_id, "state": payment.state})
        return {"status": "completed", "state": payment.state}
    else:
        return {"error": payment.error, "status": "failed"}


def get_payment_status(payment_id):
    """Check payment status."""
    try:
        payment = paypalrestsdk.Payment.find(payment_id)
        return {
            "payment_id": payment.id,
            "state": payment.state,
            "amount": payment.transactions[0].amount.total if payment.transactions else "0",
            "currency": payment.transactions[0].amount.currency if payment.transactions else "EUR",
        }
    except (AiElevateError, Exception) as e:
        return {"error": str(e)}


def create_invoice(customer_email, org, items, note=""):
    """Create and send a PayPal invoice."""
    org_name = "GigForge" if org == "gigforge" else "TechUni AI"

    invoice_items = []
    for item in items:
        invoice_items.append({
            "name": item["name"],
            "quantity": 1,
            "unit_price": {"currency": "EUR", "value": f"{item['amount_eur']:.2f}"},
        })

    invoice = paypalrestsdk.Invoice({
        "merchant_info": {
            "business_name": org_name,
            "email": f"finance@{org}.ai",
        },
        "billing_info": [{"email": customer_email}],
        "items": invoice_items,
        "note": note or f"Invoice from {org_name}",
        "payment_term": {"term_type": "NET_14"},
    })

    if invoice.create():
        invoice.send()
        _log("invoice_sent", {
            "org": org, "invoice_id": invoice.id,
            "customer": customer_email, "items": len(items),
        })
        return {"invoice_id": invoice.id, "status": "sent"}
    else:
        return {"error": invoice.error, "status": "failed"}


def list_payments(limit=10):
    """List recent PayPal payments."""
    history = paypalrestsdk.Payment.all({"count": limit, "sort_by": "create_time", "sort_order": "desc"})
    results = []
    for p in history.payments:
        results.append({
            "id": p.id,
            "state": p.state,
            "amount": p.transactions[0].amount.total if p.transactions else "0",
            "currency": p.transactions[0].amount.currency if p.transactions else "EUR",
            "created": p.create_time,
            "description": p.transactions[0].description if p.transactions else "",
        })
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="PayPal Payments")
    parser.add_argument("command", choices=["pay", "invoice", "status", "list"])
    parser.add_argument("--org", default="gigforge")
    parser.add_argument("--amount", type=float, default=0)
    parser.add_argument("--description", default="")
    parser.add_argument("--email", default="")
    parser.add_argument("--id", default="")
    args = parser.parse_args()

    if args.command == "pay":
        print(json.dumps(create_payment(args.org, args.description, args.amount, args.email), indent=2))
    elif args.command == "invoice":
        items = [{"name": args.description or "Service", "amount_eur": args.amount}]
        print(json.dumps(create_invoice(args.email, args.org, items), indent=2))
    elif args.command == "status":
        print(json.dumps(get_payment_status(args.id), indent=2))
    elif args.command == "list":
        print(json.dumps(list_payments(), indent=2))
