#!/usr/bin/env python3
from exceptions import AiElevateError  # TODO: Use specific exception types
"""
GigForge / TechUni — branded PDF proposal generator.

Usage (CLI):
    python3 proposal_generator.py \
        --client "Acme Corp" --email "john@acme.com" \
        --project "RAG Pipeline" \
        --scope "Build RAG,Deploy to prod,Train team" \
        --timeline 4 --price 5000 [--org techuni] [--send]

Programmatic:
    from proposal_generator import generate_proposal
    path = generate_proposal("Acme Corp", "john@acme.com", "RAG Pipeline",
                             ["Build RAG", "Deploy to prod"], 4, 5000)
"""

import argparse
import base64
import json
import os
import re
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    HRFlowable,
)

# ── Config ────────────────────────────────────────────────────────

COUNTER_PATH = "/opt/ai-elevate/data/proposal_counter.txt"
PROPOSALS_DIR = "/opt/ai-elevate/data/proposals"
MAILGUN_API_KEY = os.environ.get("MAILGUN_API_KEY", "")

ORG_CONFIG = {
    "gigforge": {
        "name": "GigForge",
        "tagline": "AI-Powered Digital Services",
        "email": "info@gigforge.ai",
        "domain": "gigforge.ai",
        "mailgun_domain": "gigforge.ai",
        "color": colors.HexColor("#6366f1"),
    },
    "techuni": {
        "name": "TechUni AI",
        "tagline": "AI-Powered Corporate Training at Scale",
        "email": "info@techuni.ai",
        "domain": "techuni.ai",
        "mailgun_domain": "techuni.ai",
        "color": colors.HexColor("#6366f1"),
    },
}


# ── Helpers ───────────────────────────────────────────────────────

def _next_proposal_number():
    """Auto-increment counter stored on disk."""
    os.makedirs(os.path.dirname(COUNTER_PATH), exist_ok=True)
    num = 1
    if os.path.exists(COUNTER_PATH):
        with open(COUNTER_PATH) as f:
            try:
                num = int(f.read().strip()) + 1
            except ValueError:
                num = 1
    with open(COUNTER_PATH, "w") as f:
        f.write(str(num))
    return num


def _safe_filename(name):
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)


# ── PDF Generation ────────────────────────────────────────────────

def _make_proposal_styles(accent):
    """Build and return the custom paragraph styles for a proposal PDF."""
    base = getSampleStyleSheet()
    s_body = ParagraphStyle(
        "Body", parent=base["Normal"], fontSize=11, leading=16,
        textColor=colors.HexColor("#1e293b"),
    )
    return {
        "company": ParagraphStyle(
            "Company", parent=base["Title"], fontSize=26, textColor=accent, spaceAfter=2,
        ),
        "tagline": ParagraphStyle(
            "Tagline", parent=base["Normal"], fontSize=11,
            textColor=colors.HexColor("#64748b"), spaceAfter=12,
        ),
        "heading": ParagraphStyle(
            "SectionHead", parent=base["Heading2"], fontSize=14,
            textColor=accent, spaceBefore=18, spaceAfter=8,
        ),
        "body": s_body,
        "bullet": ParagraphStyle(
            "Bullet", parent=s_body, leftIndent=20, bulletIndent=8, bulletFontSize=11,
        ),
        "small": ParagraphStyle(
            "Small", parent=base["Normal"], fontSize=9,
            textColor=colors.HexColor("#64748b"), leading=13,
        ),
        "bold": ParagraphStyle("BoldBody", parent=s_body, fontName="Helvetica-Bold"),
    }


def _build_proposal_story(cfg, client_name, client_email, project_title,
                          scope_items, timeline_weeks, price_eur,
                          prop_num, today, valid_until, st):
    """Assemble and return the ReportLab story (list of flowables) for the proposal."""
    accent = cfg["color"]
    story = []

    story.append(Paragraph(cfg["name"], st["company"]))
    story.append(Paragraph(cfg["tagline"], st["tagline"]))
    story.append(HRFlowable(width="100%", thickness=1, color=accent, spaceAfter=12))

    meta_table = Table([
        ["Date:", today.strftime("%B %d, %Y")],
        ["Proposal #:", "PROP-%04d" % prop_num],
        ["Prepared for:", client_name],
        ["Email:", client_email],
    ], colWidths=[3.5 * cm, 12 * cm])
    meta_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#334155")),
        ("TEXTCOLOR", (1, 0), (1, -1), colors.HexColor("#1e293b")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.extend([meta_table, Spacer(1, 10)])

    story.extend([Paragraph("Project", st["heading"]), Paragraph(project_title, st["bold"]), Spacer(1, 6)])

    story.append(Paragraph("Scope of Work", st["heading"]))
    for item in scope_items:
        story.append(Paragraph(item, st["bullet"], bulletText="\u2022"))
    story.append(Spacer(1, 6))

    week_word = "week" if timeline_weeks == 1 else "weeks"
    story.extend([
        Paragraph("Timeline", st["heading"]),
        Paragraph("Estimated delivery: <b>%d %s</b> from project kick-off." % (timeline_weeks, week_word), st["body"]),
        Spacer(1, 6),
    ])

    price_table = Table([
        ["Description", "Amount"],
        [project_title, "\u20ac{:,.2f}".format(price_eur)],
    ], colWidths=[12 * cm, 4 * cm])
    price_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), accent),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
    ]))
    story.extend([Paragraph("Pricing", st["heading"]), price_table, Spacer(1, 6)])

    story.append(Paragraph("Terms &amp; Conditions", st["heading"]))
    for t in [
        "This proposal is valid for <b>30 days</b> (until %s)." % valid_until.strftime("%B %d, %Y"),
        "Payment schedule: <b>50%% upfront</b> upon acceptance, remainder net-30 upon delivery.",
        "Scope changes after acceptance may affect timeline and pricing.",
        "All deliverables remain property of the client upon full payment.",
    ]:
        story.append(Paragraph(t, st["bullet"], bulletText="\u2022"))
    story.append(Spacer(1, 16))

    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cbd5e1"), spaceAfter=10))
    story.append(Paragraph(
        "%s | %s | https://%s" % (cfg["name"], cfg["email"], cfg["domain"]),
        st["small"],
    ))
    return story


def generate_proposal(
    client_name,
    client_email,
    project_title,
    scope_items,
    timeline_weeks,
    price_eur,
    org="gigforge",
):
    """Generate a branded PDF proposal and return the file path."""
    cfg = ORG_CONFIG.get(org, ORG_CONFIG["gigforge"])
    prop_num = _next_proposal_number()
    today = datetime.now()
    valid_until = today + timedelta(days=30)

    filename = "PROP-%04d-%s.pdf" % (prop_num, _safe_filename(client_name))
    filepath = os.path.join(PROPOSALS_DIR, filename)
    os.makedirs(PROPOSALS_DIR, exist_ok=True)

    doc = SimpleDocTemplate(
        filepath, pagesize=A4,
        leftMargin=2.5 * cm, rightMargin=2.5 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
    )
    st = _make_proposal_styles(cfg["color"])
    story = _build_proposal_story(cfg, client_name, client_email, project_title,
                                  scope_items, timeline_weeks, price_eur,
                                  prop_num, today, valid_until, st)
    doc.build(story)
    print("Proposal saved: %s" % filepath)
    return filepath


# ── Email via Mailgun (with attachment) ───────────────────────────

def send_proposal(filepath, client_email, client_name, project_title, org="gigforge"):
    """Send the proposal PDF to the client via Mailgun with attachment."""
    cfg = ORG_CONFIG.get(org, ORG_CONFIG["gigforge"])
    domain = cfg["mailgun_domain"]
    from_addr = "%s <proposals@%s>" % (cfg["name"], domain)
    subject = "Proposal: %s — %s" % (project_title, cfg["name"])
    body = (
        "Hi %s,\n\n"
        "Please find attached our proposal for \"%s\".\n\n"
        "This proposal is valid for 30 days. To accept, simply reply to this "
        "email or contact us at %s.\n\n"
        "We look forward to working with you!\n\n"
        "Best regards,\n%s Team\n%s"
    ) % (client_name, project_title, cfg["email"], cfg["name"], cfg["email"])

    # Build multipart/form-data manually (stdlib only, supports file attachment)
    boundary = "----ProposalBoundary9f8e7d6c"
    lines = []

    def add_field(name, value):
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        lines.append(("--%s" % boundary).encode())
        lines.append(('Content-Disposition: form-data; name="%s"' % name).encode())
        lines.append(b"")
        lines.append(value.encode("utf-8"))

    add_field("from", from_addr)
    add_field("to", client_email)
    add_field("subject", subject)
    add_field("text", body)

    # File attachment
    fname = os.path.basename(filepath)
    with open(filepath, "rb") as f:
        file_data = f.read()
    lines.append(("--%s" % boundary).encode())
    lines.append(
        ('Content-Disposition: form-data; name="attachment"; filename="%s"' % fname).encode()
    )
    lines.append(b"Content-Type: application/pdf")
    lines.append(b"")
    lines.append(file_data)
    lines.append(("--%s--" % boundary).encode())

    body_bytes = b"\r\n".join(lines)
    content_type = "multipart/form-data; boundary=%s" % boundary

    url = "https://api.mailgun.net/v3/%s/messages" % domain
    creds = base64.b64encode(("api:%s" % MAILGUN_API_KEY).encode()).decode()

    req = urllib.request.Request(url, data=body_bytes, method="POST")
    req.add_header("Authorization", "Basic %s" % creds)
    req.add_header("Content-Type", content_type)

    try:
        resp = urllib.request.urlopen(req)
        result = json.loads(resp.read().decode())
        print("Email sent to %s: %s" % (client_email, result.get("message", "OK")))
        return True
    except urllib.error.HTTPError as e:
        err_body = e.read().decode() if e.fp else str(e)
        print("Error sending email: %s — %s" % (e.code, err_body), file=sys.stderr)
        return False
    except Exception as e:
        print("Error sending email: %s" % e, file=sys.stderr)
        return False


# ── CLI ───────────────────────────────────────────────────────────

def main():
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    parser = argparse.ArgumentParser(description="Generate a branded PDF proposal")
    parser.add_argument("--client", required=True, help="Client name")
    parser.add_argument("--email", required=True, help="Client email")
    parser.add_argument("--project", required=True, help="Project title")
    parser.add_argument("--scope", required=True,
                        help="Comma-separated scope items")
    parser.add_argument("--timeline", required=True, type=int,
                        help="Timeline in weeks")
    parser.add_argument("--price", required=True, type=float,
                        help="Price in EUR")
    parser.add_argument("--org", default="gigforge",
                        choices=["gigforge", "techuni"],
                        help="Organization (default: gigforge)")
    parser.add_argument("--send", action="store_true",
                        help="Email the PDF to the client via Mailgun")
    args = parser.parse_args()

    scope_items = [s.strip() for s in args.scope.split(",") if s.strip()]

    filepath = generate_proposal(
        client_name=args.client,
        client_email=args.email,
        project_title=args.project,
        scope_items=scope_items,
        timeline_weeks=args.timeline,
        price_eur=args.price,
        org=args.org,
    )

    if args.send:
        send_proposal(filepath, args.email, args.client, args.project, args.org)


if __name__ == "__main__":
    main()
