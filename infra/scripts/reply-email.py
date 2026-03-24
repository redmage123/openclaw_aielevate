#!/usr/bin/env python3
"""Reply to an email — used by agents to respond to inbound messages."""
import os
import sys
import urllib.request
import urllib.parse
import base64

MAILGUN_API_KEY = os.environ.get("MAILGUN_API_KEY", "")
MAILGUN_DOMAIN = "internal.ai-elevate.ai"

if len(sys.argv) < 4:
    print("Usage: reply-email.py TO SUBJECT BODY [FROM_NAME]")
    sys.exit(1)

to = sys.argv[1]
subject = sys.argv[2]
body = sys.argv[3]
from_name = sys.argv[4] if len(sys.argv) > 4 else "AI Elevate"
from_addr = sys.argv[5] if len(sys.argv) > 5 else "support"

data = urllib.parse.urlencode({
    "from": f"{from_name} <{from_addr}@internal.ai-elevate.ai>",
    "to": to,
    "h:Reply-To": f"{from_addr}@internal.ai-elevate.ai",
    "subject": subject,
    "text": body,
}).encode("utf-8")
creds = base64.b64encode(f"api:{MAILGUN_API_KEY}".encode()).decode()
req = urllib.request.Request(f"https://api.mailgun.net/v3/{MAILGUN_DOMAIN}/messages", data=data, method="POST")
req.add_header("Authorization", f"Basic {creds}")
urllib.request.urlopen(req, timeout=15)
print("sent")
