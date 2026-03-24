#!/usr/bin/env python3
"""

log = get_logger("newsletter_generator")

Newsletter Generator — AI Elevate generates content, orgs distribute.

Usage:
    python3 newsletter_generator.py --generate   # Monday: AI Elevate generates drafts
    python3 newsletter_generator.py --send        # Tuesday: send to mailing lists
    python3 newsletter_generator.py --preview     # Send drafts to Braun only for review
    python3 newsletter_generator.py --list        # Show mailing list subscribers
"""

import argparse
import asyncio
import base64
import json
import os
import subprocess
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from exceptions import AiElevateError  # TODO: Use specific exception types, EmailError
from logging_config import get_logger

MG_KEY = Path("/opt/ai-elevate/credentials/mailgun-api-key.txt").read_text().strip()
DRAFT_DIR = Path("/opt/ai-elevate/newsletters/drafts")
SENT_DIR = Path("/opt/ai-elevate/newsletters/sent")
LOG_DIR = Path("/opt/ai-elevate/newsletters")

NEWSLETTERS = {
    "gigforge": {
        "list": "newsletter@gigforge.ai",
        "from": "GigForge Dispatch <newsletter@gigforge.ai>",
        "domain": "gigforge.ai",
        "subject_prefix": "GigForge Dispatch",
        "color": "#6366f1",
        "tagline": "AI-Powered Software Development",
        "url": "https://gigforge.ai",
        "blog_url": "https://gigforge.ai/portfolio",
        "unsubscribe_url": "mailto:newsletter@gigforge.ai?subject=Unsubscribe",
    },
    "techuni": {
        "list": "newsletter@techuni.ai",
        "from": "TechUni AI <ceo@techuni.ai>",
        "domain": "techuni.ai",
        "subject_prefix": "TechUni Weekly",
        "color": "#6366f1",
        "tagline": "AI-Powered Course Creation",
        "url": "https://techuni.ai",
        "blog_url": "https://techuni.ai/blog",
        "unsubscribe_url": "mailto:newsletter@techuni.ai?subject=Unsubscribe",
    },
}

TEMPLATE = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f5;padding:20px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:8px;overflow:hidden;">

<!-- Header -->
<tr><td style="background:{color};padding:32px 40px;">
<h1 style="margin:0;color:#ffffff;font-size:24px;font-weight:700;">{org_name}</h1>
<p style="margin:4px 0 0;color:rgba(255,255,255,0.8);font-size:14px;">{tagline}</p>
</td></tr>

<!-- Date -->
<tr><td style="padding:24px 40px 0;">
<p style="margin:0;color:#6b7280;font-size:13px;">{date}</p>
</td></tr>

<!-- Content -->
<tr><td style="padding:16px 40px 32px;">
{content}
</td></tr>

<!-- CTA -->
<tr><td style="padding:0 40px 32px;">
<a href="{blog_url}" style="display:inline-block;background:{color};color:#ffffff;padding:12px 24px;border-radius:6px;text-decoration:none;font-size:14px;font-weight:600;">Read more on our blog</a>
</td></tr>

<!-- Footer -->
<tr><td style="background:#f9fafb;padding:24px 40px;border-top:1px solid #e5e7eb;">
<p style="margin:0;color:#9ca3af;font-size:12px;">
{org_name} | {url}<br>
You're receiving this because you subscribed to {subject_prefix}.<br>
<a href="{unsubscribe_url}" style="color:#6b7280;">Unsubscribe</a>
</p>
</td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def generate_content(org: str) -> str:
    """Use AI Elevate content agent to generate newsletter content."""
    cfg = NEWSLETTERS[org]
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")

    if org == "techuni":
        prompt = f"""Write the content sections for this week's TechUni Weekly newsletter ({today}).

Write 3-4 short sections covering:
1. An AI-in-education trend or insight (something happening this week in the industry)
2. A practical course creation tip that L&D managers would find useful
3. A TechUni platform highlight or feature (SSO/SAML integration, LTI 1.3, AI course generation, lab environments)
4. A brief industry stat or quote to close with

Format each section as HTML with <h2> headers and <p> paragraphs. Use inline styles:
- h2: style="margin:24px 0 8px;color:#111827;font-size:18px;font-weight:600;"
- p: style="margin:0 0 12px;color:#374151;font-size:15px;line-height:1.6;"

Write like a knowledgeable colleague sharing useful info, NOT like a marketing department. No buzzwords, no hype. Concise and direct. 200-300 words total.

Output ONLY the HTML content sections. No wrapper, no doctype, no head/body tags."""
    else:
        prompt = f"""Write the content sections for this week's GigForge Dispatch newsletter ({today}).

Write 3-4 short sections covering:
1. A recent project showcase or technical achievement (AI/ML pipelines, full-stack apps, automation)
2. A tech deep-dive or engineering insight (something practical developers would care about)
3. An AI development trend or tool worth knowing about
4. A brief closing thought or industry observation

Format each section as HTML with <h2> headers and <p> paragraphs. Use inline styles:
- h2: style="margin:24px 0 8px;color:#111827;font-size:18px;font-weight:600;"
- p: style="margin:0 0 12px;color:#374151;font-size:15px;line-height:1.6;"

Write like a senior engineer sharing insights with peers, NOT like a marketing department. No buzzwords, no hype. Concise and direct. 200-300 words total.

Output ONLY the HTML content sections. No wrapper, no doctype, no head/body tags."""

    try:
        result = subprocess.run(
            ["claude", "-p", prompt,
             "--model", "sonnet",
             "--permission-mode", "bypassPermissions"],
            capture_output=True, text=True, timeout=120,
            env={k: v for k, v in os.environ.items() if k != "CLAUDECODE"},
        )
        content = result.stdout.strip()
        if not content or len(content) < 50 or "error" in content.lower()[:100] or "authenticate" in content.lower()[:200] or "401" in content[:100]:
            log.info("  Content generation returned error or empty, using fallback")
            return _fallback_content(org)
        # Strip any markdown code fences
        if content.startswith("```"):
            content = "\n".join(content.split("\n")[1:])
        if content.endswith("```"):
            content = "\n".join(content.split("\n")[:-1])
        return content
    except (AiElevateError, Exception) as e:
        log.info("Content generation failed for %s: %s", extra={"org": org, "e": e})
        return _fallback_content(org)


def _fallback_content(org: str) -> str:
    """Fallback content if AI generation fails."""
    if org == "techuni":
        return """<h2 style="margin:24px 0 8px;color:#111827;font-size:18px;font-weight:600;">This Week in AI Education</h2>
<p style="margin:0 0 12px;color:#374151;font-size:15px;line-height:1.6;">We're continuing to see strong adoption of AI-powered course creation across enterprise L&amp;D teams. This week we shipped improvements to our LTI 1.3 integration, making it even easier to connect Course Creator with your existing LMS.</p>
<h2 style="margin:24px 0 8px;color:#111827;font-size:18px;font-weight:600;">Tip: Structure Before Content</h2>
<p style="margin:0 0 12px;color:#374151;font-size:15px;line-height:1.6;">When generating courses, start with a clear learning objective hierarchy. Feed the AI a structured outline and it will produce significantly better module content than a vague topic description.</p>"""
    else:
        return """<h2 style="margin:24px 0 8px;color:#111827;font-size:18px;font-weight:600;">Engineering Update</h2>
<p style="margin:0 0 12px;color:#374151;font-size:15px;line-height:1.6;">This week the team shipped several infrastructure improvements including automated bug tracking via Plane, enhanced deployment pipelines with post-deploy log scanning, and security gate integration for all code releases.</p>
<h2 style="margin:24px 0 8px;color:#111827;font-size:18px;font-weight:600;">Tech Spotlight: FastAPI Middleware Patterns</h2>
<p style="margin:0 0 12px;color:#374151;font-size:15px;line-height:1.6;">A quick note on Starlette's BaseHTTPMiddleware: if you raise HTTPException inside dispatch(), it crashes the ASGI handler instead of returning a proper response. Use JSONResponse directly or switch to a pure ASGI middleware pattern.</p>"""


def generate_drafts():
    """Generate newsletter drafts for both orgs."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    date_display = datetime.now(timezone.utc).strftime("%B %d, %Y")

    for org, cfg in NEWSLETTERS.items():
        log.info("Generating {cfg['subject_prefix']} content...")
        content = generate_content(org)

        html = TEMPLATE.format(
            color=cfg["color"],
            org_name=cfg["subject_prefix"],
            tagline=cfg["tagline"],
            date=date_display,
            content=content,
            blog_url=cfg["blog_url"],
            url=cfg["url"],
            subject_prefix=cfg["subject_prefix"],
            unsubscribe_url=cfg["unsubscribe_url"],
        )

        draft_path = DRAFT_DIR / f"{today}-{org}.html"
        draft_path.write_text(html)
        log.info("  Saved: %s", extra={"draft_path": draft_path})

    log.info("Drafts ready. Use --preview to review or --send to distribute.")


def _fetch_list_members(list_addr, creds):
    """Fetch active subscribers from a Mailgun mailing list."""
    req = urllib.request.Request(
        f"https://api.mailgun.net/v3/lists/{list_addr}/members?subscribed=true&limit=100",
    )
    req.add_header("Authorization", f"Basic {creds}")
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
        return [(m.get("name", ""), m["address"]) for m in data.get("items", []) if m.get("subscribed")]
    except (EmailError, Exception) as e:
        log.info("  Failed to fetch list members: %s", extra={"e": e})
        return []


def send_newsletter(preview=False):
    """Send newsletters to mailing lists (or preview to Braun)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    date_display = datetime.now(timezone.utc).strftime("%B %d, %Y")

    for org, cfg in NEWSLETTERS.items():
        draft_path = DRAFT_DIR / f"{today}-{org}.html"

        # If no draft for today, check yesterday or most recent
        if not draft_path.exists():
            drafts = sorted(DRAFT_DIR.glob(f"*-{org}.html"), reverse=True)
            if drafts:
                draft_path = drafts[0]
            else:
                log.info("No draft found for %s. Run --generate first.", extra={"org": org})
                continue

        html = draft_path.read_text()
        
        # Gate: do NOT send if content contains error indicators
        error_markers = ["authentication_error", "401", "403", "Failed to authenticate", 
                         "API Error", "error_type", "Invalid authentication", "fallback content",
                         "rate_limit", "500 Internal"]
        if any(marker.lower() in html.lower() for marker in error_markers):
            log.info("  {cfg['subject_prefix']}: BLOCKED — draft contains error content. Fix and regenerate.")
            continue
        
        # Gate: do NOT send if content section is suspiciously short
        import re
        content_match = re.search(r'<!-- Content -->(.+?)<!-- CTA -->', html, re.DOTALL)
        if content_match and len(content_match.group(1).strip()) < 200:
            log.info("  {cfg['subject_prefix']}: BLOCKED — content too short ({len(content_match.group(1).strip())} chars). Regenerate.")
            continue

        subject = f"{cfg['subject_prefix']} — {date_display}"

        creds = base64.b64encode(f"api:{MG_KEY}".encode()).decode()
        api_url = f"https://api.mailgun.net/v3/{cfg['domain']}/messages"

        if preview:
            recipients = [("Braun Brelin", "braun.brelin@ai-elevate.ai")]
            subject = f"[PREVIEW] {subject}"
        else:
            # Fetch subscribers from the mailing list, then send individually
            # This avoids Mailgun mailing list headers that Zoho drops
            recipients = _fetch_list_members(cfg["list"], creds)
            if not recipients:
                log.info("  {cfg['subject_prefix']}: No subscribers found!")
                continue

        sent_count = 0
        for name, addr in recipients:
            data = urllib.parse.urlencode({
                "from": cfg["from"],
                "to": f"{name} <{addr}>" if name else addr,
                "subject": subject,
                "html": html,
            }).encode("utf-8")

            req = urllib.request.Request(api_url, data=data, method="POST")
            req.add_header("Authorization", f"Basic {creds}")

            try:
                resp = urllib.request.urlopen(req, timeout=30)
                sent_count += 1
            except (AiElevateError, Exception) as e:
                log.info("  %s: FAILED for %s — %s", extra={"addr": addr, "e": e})

        log.info("  %s: sent to %s/%s recipients", extra={"sent_count": sent_count})

        if not preview:
            sent_path = SENT_DIR / f"{today}-{org}.html"
            import shutil
            shutil.copy2(draft_path, sent_path)


def list_subscribers():
    """List subscribers for both mailing lists."""
    creds = base64.b64encode(f"api:{MG_KEY}".encode()).decode()
    for org, cfg in NEWSLETTERS.items():
        log.info("\n{cfg['subject_prefix']} ({cfg['list']}):")
        req = urllib.request.Request(
            f"https://api.mailgun.net/v3/lists/{cfg['list']}/members",
        )
        req.add_header("Authorization", f"Basic {creds}")
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read())
            for m in data.get("items", []):
                status = "active" if m.get("subscribed") else "unsubscribed"
                log.info("  %s %s [%s]", extra={"status": status})
        except (EmailError, Exception) as e:
            log.info("  Error: %s", extra={"e": e})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Newsletter Generator")
    parser.add_argument("--generate", action="store_true", help="Generate drafts")
    parser.add_argument("--send", action="store_true", help="Send to mailing lists")
    parser.add_argument("--preview", action="store_true", help="Send preview to Braun")
    parser.add_argument("--list", action="store_true", help="List subscribers")
    args = parser.parse_args()

    if args.generate:
        generate_drafts()
    elif args.send:
        send_newsletter(preview=False)
    elif args.preview:
        send_newsletter(preview=True)
    elif args.list:
        list_subscribers()
    else:
        parser.print_help()
