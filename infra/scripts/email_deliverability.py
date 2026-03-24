#!/usr/bin/env python3
"""Email Deliverability Monitoring for AI Elevate.

Checks Mailgun stats API for each sending domain and alerts on issues.

Alerts:
  - Bounce rate > 5%
  - Complaint rate > 0.1%

Usage:
  python3 email_deliverability.py --check    # Last 24h stats, alert on issues
  python3 email_deliverability.py --report   # Weekly summary
"""

import argparse
import base64
import json
import logging
import os
import subprocess
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [deliverability] %(levelname)s %(message)s",
)
logger = logging.getLogger("deliverability")

MAILGUN_API_KEY = os.environ.get("MAILGUN_API_KEY", "")
DOMAINS = ["gigforge.ai", "techuni.ai", "ai-elevate.ai"]

BOUNCE_THRESHOLD = 0.05    # 5%
COMPLAINT_THRESHOLD = 0.001  # 0.1%


def mailgun_get(url):
    """Make an authenticated GET request to the Mailgun API."""
    creds = base64.b64encode(("api:" + MAILGUN_API_KEY).encode()).decode()
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", "Basic " + creds)
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.error("Mailgun API error for %s: %s", url, e)
        return None


def get_domain_stats(domain, days=1):
    """Fetch stats for a domain over the specified number of days."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    # Mailgun stats endpoint
    event_types = "accepted,delivered,failed,complained,unsubscribed"
    url = (
        "https://api.mailgun.net/v3/%s/stats/total"
        "?event=%s&start=%s&end=%s&duration=%dd"
        % (domain, event_types, start.strftime("%a,+%d+%b+%Y+%H:%M:%S+GMT"),
           end.strftime("%a,+%d+%b+%Y+%H:%M:%S+GMT"), days)
    )

    data = mailgun_get(url)
    if data is None:
        return None

    stats = {
        "domain": domain,
        "period_days": days,
        "accepted": 0,
        "delivered": 0,
        "failed_temporary": 0,
        "failed_permanent": 0,
        "complained": 0,
        "unsubscribed": 0,
    }

    # Parse Mailgun stats response
    raw_stats = data.get("stats", [])
    for entry in raw_stats:
        accepted = entry.get("accepted", {})
        stats["accepted"] += accepted.get("total", 0) if isinstance(accepted, dict) else 0

        delivered = entry.get("delivered", {})
        stats["delivered"] += delivered.get("total", 0) if isinstance(delivered, dict) else 0

        failed = entry.get("failed", {})
        if isinstance(failed, dict):
            stats["failed_temporary"] += failed.get("temporary", {}).get("total", 0) if isinstance(failed.get("temporary"), dict) else 0
            perm = failed.get("permanent", {})
            stats["failed_permanent"] += perm.get("total", 0) if isinstance(perm, dict) else 0

        complained = entry.get("complained", {})
        stats["complained"] += complained.get("total", 0) if isinstance(complained, dict) else 0

        unsub = entry.get("unsubscribed", {})
        stats["unsubscribed"] += unsub.get("total", 0) if isinstance(unsub, dict) else 0

    # Compute rates
    total_sent = stats["accepted"]
    if total_sent > 0:
        stats["delivery_rate"] = stats["delivered"] / total_sent
        stats["bounce_rate"] = stats["failed_permanent"] / total_sent
        stats["complaint_rate"] = stats["complained"] / total_sent
        stats["temp_fail_rate"] = stats["failed_temporary"] / total_sent
    else:
        stats["delivery_rate"] = 1.0
        stats["bounce_rate"] = 0.0
        stats["complaint_rate"] = 0.0
        stats["temp_fail_rate"] = 0.0

    return stats


def check_deliverability():
    """Check last 24h stats for all domains and alert on issues."""
    issues = []
    all_stats = []

    for domain in DOMAINS:
        stats = get_domain_stats(domain, days=1)
        if stats is None:
            issues.append("Could not fetch stats for %s" % domain)
            continue

        all_stats.append(stats)
        logger.info(
            "%s: sent=%d delivered=%d bounced=%d complained=%d (delivery=%.1f%% bounce=%.1f%% complaint=%.2f%%)",
            domain, stats["accepted"], stats["delivered"],
            stats["failed_permanent"], stats["complained"],
            stats["delivery_rate"] * 100, stats["bounce_rate"] * 100,
            stats["complaint_rate"] * 100,
        )

        if stats["bounce_rate"] > BOUNCE_THRESHOLD and stats["accepted"] > 5:
            issues.append(
                "%s: bounce rate %.1f%% (threshold: %.1f%%) -- %d/%d bounced"
                % (domain, stats["bounce_rate"] * 100, BOUNCE_THRESHOLD * 100,
                   stats["failed_permanent"], stats["accepted"])
            )

        if stats["complaint_rate"] > COMPLAINT_THRESHOLD and stats["accepted"] > 5:
            issues.append(
                "%s: complaint rate %.2f%% (threshold: %.1f%%) -- %d/%d complained"
                % (domain, stats["complaint_rate"] * 100, COMPLAINT_THRESHOLD * 100,
                   stats["complained"], stats["accepted"])
            )

    # Print summary
    print("Email Deliverability Check -- " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    print("-" * 50)
    for s in all_stats:
        print("  %s: sent=%d delivered=%d bounced=%d complained=%d" % (
            s["domain"], s["accepted"], s["delivered"],
            s["failed_permanent"], s["complained"]))
        print("    delivery=%.1f%% bounce=%.1f%% complaint=%.2f%%" % (
            s["delivery_rate"] * 100, s["bounce_rate"] * 100,
            s["complaint_rate"] * 100))

    if issues:
        alert_body = "Email deliverability issues detected:\n\n" + "\n".join("  - " + i for i in issues)
        print("\nALERT: " + alert_body)
        try:
            subprocess.run(
                ["python3", "/home/aielevate/notify.py",
                 "--priority", "high",
                 "--title", "Email Deliverability Alert: %d issues" % len(issues),
                 "--body", alert_body],
                timeout=30,
            )
            logger.info("Deliverability alert sent for %d issues", len(issues))
        except Exception as e:
            logger.error("Failed to send deliverability alert: %s", e)
    else:
        print("\nAll domains within healthy thresholds.")

    return issues


def weekly_report():
    """Generate weekly deliverability summary."""
    lines = []
    lines.append("=" * 60)
    lines.append("Email Deliverability Weekly Report")
    lines.append(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    lines.append("=" * 60)

    total_sent = 0
    total_delivered = 0
    total_bounced = 0
    total_complained = 0

    for domain in DOMAINS:
        stats = get_domain_stats(domain, days=7)
        if stats is None:
            lines.append("\n%s: COULD NOT FETCH STATS" % domain)
            continue

        total_sent += stats["accepted"]
        total_delivered += stats["delivered"]
        total_bounced += stats["failed_permanent"]
        total_complained += stats["complained"]

        lines.append("")
        lines.append("%s:" % domain)
        lines.append("  Sent: %d" % stats["accepted"])
        lines.append("  Delivered: %d (%.1f%%)" % (stats["delivered"], stats["delivery_rate"] * 100))
        lines.append("  Bounced (permanent): %d (%.1f%%)" % (stats["failed_permanent"], stats["bounce_rate"] * 100))
        lines.append("  Bounced (temporary): %d (%.1f%%)" % (stats["failed_temporary"], stats["temp_fail_rate"] * 100))
        lines.append("  Complained: %d (%.2f%%)" % (stats["complained"], stats["complaint_rate"] * 100))
        lines.append("  Unsubscribed: %d" % stats["unsubscribed"])

        # Status indicator
        status = "HEALTHY"
        if stats["bounce_rate"] > BOUNCE_THRESHOLD:
            status = "WARNING: High bounce rate"
        if stats["complaint_rate"] > COMPLAINT_THRESHOLD:
            status = "WARNING: High complaint rate"
        lines.append("  Status: %s" % status)

    # Totals
    if total_sent > 0:
        lines.append("")
        lines.append("TOTALS (all domains):")
        lines.append("  Sent: %d" % total_sent)
        lines.append("  Delivered: %d (%.1f%%)" % (total_delivered, total_delivered / total_sent * 100))
        lines.append("  Bounced: %d (%.1f%%)" % (total_bounced, total_bounced / total_sent * 100))
        lines.append("  Complained: %d (%.2f%%)" % (total_complained, total_complained / total_sent * 100))

    lines.append("")
    lines.append("=" * 60)

    report = "\n".join(lines)
    print(report)

    # Send report via notify
    try:
        subprocess.run(
            ["python3", "/home/aielevate/notify.py",
             "--priority", "low",
             "--title", "Email Deliverability Weekly Report",
             "--body", report],
            timeout=30,
        )
    except Exception as e:
        logger.error("Failed to send weekly report: %s", e)

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Email Deliverability Monitoring")
    parser.add_argument("--check", action="store_true", help="Check last 24h stats, alert on issues")
    parser.add_argument("--report", action="store_true", help="Weekly summary report")
    args = parser.parse_args()

    if args.check:
        check_deliverability()
    elif args.report:
        weekly_report()
    else:
        parser.print_help()
