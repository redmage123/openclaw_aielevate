#!/usr/bin/env python3
"""Email Formatter — converts plain text agent output into properly formatted HTML emails.

Every outbound email passes through this formatter before sending.
Produces clean, professional HTML with:
- Company header/footer
- Proper paragraphs with spacing
- Bulleted and numbered lists
- Bold section headers
- Readable font, proper line height
- Mobile responsive
"""

import re
import logging

log = logging.getLogger("email-formatter")


def format_email(body, agent_id="gigforge", agent_name="", agent_title=""):
    """Convert plain text to formatted HTML email.

    Args:
        body: Plain text email body
        agent_id: Agent identifier (for branding)
        agent_name: Sender's display name
        agent_title: Sender's title

    Returns:
        HTML formatted email string
    """
    if not body:
        return body

    # Determine branding
    if "techuni" in agent_id:
        brand_color = "#2563eb"
        brand_name = "TechUni"
        brand_tagline = "AI-Powered Learning Platform"
    elif "carehaven" in agent_id:
        brand_color = "#0d9488"
        brand_name = "CareHaven"
        brand_tagline = "Intelligent Care Management"
    else:
        brand_color = "#1e40af"
        brand_name = "GigForge"
        brand_tagline = "AI-Powered Software Delivery"

    # Convert plain text to HTML paragraphs
    html_body = _text_to_html(body)

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  body {{ margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; font-size: 15px; line-height: 1.6; color: #1f2937; background: #f3f4f6; }}
  .wrapper {{ max-width: 640px; margin: 0 auto; background: #ffffff; }}
  .header {{ background: {brand_color}; padding: 20px 30px; }}
  .header h1 {{ color: #ffffff; font-size: 20px; margin: 0; font-weight: 600; }}
  .header p {{ color: rgba(255,255,255,0.8); font-size: 12px; margin: 4px 0 0; }}
  .content {{ padding: 30px; }}
  .content p {{ margin: 0 0 16px; }}
  .content ul, .content ol {{ margin: 0 0 16px; padding-left: 24px; }}
  .content li {{ margin: 0 0 8px; }}
  .content h2 {{ font-size: 17px; font-weight: 600; color: #111827; margin: 24px 0 12px; border-bottom: 1px solid #e5e7eb; padding-bottom: 6px; }}
  .content h3 {{ font-size: 15px; font-weight: 600; color: #374151; margin: 20px 0 8px; }}
  .content strong {{ color: #111827; }}
  .content a {{ color: {brand_color}; }}
  .content code {{ background: #f3f4f6; padding: 2px 6px; border-radius: 4px; font-size: 13px; }}
  .content pre {{ background: #f3f4f6; padding: 16px; border-radius: 8px; overflow-x: auto; font-size: 13px; margin: 0 0 16px; }}
  .content blockquote {{ border-left: 3px solid {brand_color}; margin: 0 0 16px; padding: 8px 16px; color: #4b5563; background: #f9fafb; }}
  .content table {{ width: 100%; border-collapse: collapse; margin: 0 0 16px; }}
  .content th, .content td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #e5e7eb; font-size: 14px; }}
  .content th {{ font-weight: 600; color: #374151; background: #f9fafb; }}
  .signature {{ padding: 0 30px 20px; color: #6b7280; font-size: 13px; }}
  .signature .name {{ color: #1f2937; font-weight: 600; }}
  .footer {{ background: #f9fafb; padding: 16px 30px; border-top: 1px solid #e5e7eb; text-align: center; font-size: 11px; color: #9ca3af; }}
</style>
</head>
<body>
<div class="wrapper">
  <div class="header">
    <h1>{brand_name}</h1>
    <p>{brand_tagline}</p>
  </div>
  <div class="content">
    {html_body}
  </div>
  <div class="signature">
    <p class="name">{agent_name}</p>
    <p>{agent_title}, {brand_name}</p>
  </div>
  <div class="footer">
    <p>&copy; 2026 {brand_name} &middot; AI Elevate</p>
  </div>
</div>
</body>
</html>"""

    return html


def _text_to_html(text):
    """Convert plain text to semantic HTML."""
    lines = text.strip().split("\n")
    html_parts = []
    in_list = False
    in_numbered = False
    list_buffer = []

    def flush_list():
        nonlocal in_list, in_numbered, list_buffer
        if list_buffer:
            tag = "ol" if in_numbered else "ul"
            html_parts.append(f"<{tag}>")
            for item in list_buffer:
                html_parts.append(f"  <li>{item}</li>")
            html_parts.append(f"</{tag}>")
            list_buffer = []
        in_list = False
        in_numbered = False

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Empty line — end current context
        if not line:
            flush_list()
            i += 1
            continue

        # Markdown-style headers
        if line.startswith("### "):
            flush_list()
            html_parts.append(f"<h3>{_inline_format(line[4:])}</h3>")
            i += 1
            continue
        if line.startswith("## ") or line.startswith("**") and line.endswith("**") and len(line) < 80:
            flush_list()
            clean = line.strip("*# ").strip()
            html_parts.append(f"<h2>{_inline_format(clean)}</h2>")
            i += 1
            continue

        # Horizontal rule
        if line in ("---", "===", "***", "- - -"):
            flush_list()
            html_parts.append("<hr>")
            i += 1
            continue

        # Bulleted list
        if re.match(r'^[-•*]\s+', line):
            if not in_list:
                flush_list()
                in_list = True
                in_numbered = False
            item = re.sub(r'^[-•*]\s+', '', line)
            list_buffer.append(_inline_format(item))
            i += 1
            continue

        # Numbered list
        if re.match(r'^\d+[.)]\s+', line):
            if not in_numbered:
                flush_list()
                in_list = True
                in_numbered = True
            item = re.sub(r'^\d+[.)]\s+', '', line)
            list_buffer.append(_inline_format(item))
            i += 1
            continue

        # Table (pipe-delimited)
        if "|" in line and line.count("|") >= 2:
            flush_list()
            table_lines = []
            while i < len(lines) and "|" in lines[i].strip():
                table_lines.append(lines[i].strip())
                i += 1
            html_parts.append(_parse_table(table_lines))
            continue

        # Code block
        if line.startswith("```"):
            flush_list()
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # skip closing ```
            html_parts.append(f"<pre><code>{chr(10).join(code_lines)}</code></pre>")
            continue

        # Blockquote
        if line.startswith("> "):
            flush_list()
            quote = line[2:]
            html_parts.append(f"<blockquote>{_inline_format(quote)}</blockquote>")
            i += 1
            continue

        # Regular paragraph — collect consecutive non-empty lines
        flush_list()
        para_lines = [line]
        while i + 1 < len(lines) and lines[i + 1].strip() and not _is_special(lines[i + 1].strip()):
            i += 1
            para_lines.append(lines[i].strip())
        html_parts.append(f"<p>{_inline_format(' '.join(para_lines))}</p>")
        i += 1

    flush_list()
    return "\n".join(html_parts)


def _inline_format(text):
    """Apply inline formatting: bold, italic, code, links."""
    # Bold
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # Italic
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    # Inline code
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    # Links
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
    # Bare URLs
    text = re.sub(r'(?<!["\'>])(https?://\S+)', r'<a href="\1">\1</a>', text)
    return text


def _is_special(line):
    """Check if a line starts a special block."""
    if re.match(r'^[-•*]\s+', line): return True
    if re.match(r'^\d+[.)]\s+', line): return True
    if line.startswith("## ") or line.startswith("### "): return True
    if line.startswith("```"): return True
    if line.startswith("> "): return True
    if line in ("---", "===", "***"): return True
    if "|" in line and line.count("|") >= 2: return True
    return False


def _parse_table(lines):
    """Convert pipe-delimited lines to HTML table."""
    if len(lines) < 2:
        return ""

    html = '<table>'

    # Header row
    cells = [c.strip() for c in lines[0].strip("|").split("|")]
    html += "<tr>" + "".join(f"<th>{_inline_format(c)}</th>" for c in cells) + "</tr>"

    # Skip separator row (---|---|---)
    start = 1
    if len(lines) > 1 and re.match(r'^[\s|:-]+$', lines[1]):
        start = 2

    # Data rows
    for line in lines[start:]:
        cells = [c.strip() for c in line.strip("|").split("|")]
        html += "<tr>" + "".join(f"<td>{_inline_format(c)}</td>" for c in cells) + "</tr>"

    html += "</table>"
    return html


# Agent name/title lookup
AGENT_INFO = {
    "gigforge": ("Alex Reeves", "Operations Director"),
    "gigforge-sales": ("Sam Carrington", "Sales Lead"),
    "gigforge-advocate": ("Jordan Whitaker", "Customer Delivery Liaison"),
    "gigforge-pm": ("Jamie Okafor", "Project Manager"),
    "gigforge-engineer": ("Chris Novak", "Lead Engineer / CTO"),
    "gigforge-devops": ("Casey Muller", "DevOps Engineer"),
    "gigforge-qa": ("Riley Svensson", "QA Engineer"),
    "gigforge-scout": ("Quinn Azevedo", "Business Development Scout"),
    "gigforge-finance": ("Pat Eriksen", "Finance Manager"),
    "gigforge-tax": ("Morgan Hayes", "Tax & Compliance Expert"),
    "gigforge-billing": ("GigForge Billing", "Billing"),
    "gigforge-social": ("Morgan Dell", "Social Media"),
    "operations": ("Kai Sorensen", "Operations Agent"),
    "techuni-ceo": ("TechUni CEO", "Chief Executive"),
    "techuni-sales": ("TechUni Sales", "Sales"),
    "techuni-marketing": ("TechUni Marketing", "Marketing"),
}


def get_agent_info(agent_id):
    """Get agent name and title."""
    return AGENT_INFO.get(agent_id, (agent_id, "Team Member"))
