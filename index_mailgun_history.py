#!/usr/bin/env python3
"""Add Mailgun outbound history to the semantic index — catches emails sent
before the real-time indexing was wired in."""
import sys, json, time, hashlib
sys.path.insert(0, "/home/aielevate")

from pathlib import Path
from semantic_search import index_document

MG_KEY = Path("/opt/ai-elevate/credentials/mailgun-api-key.txt").read_text().strip()

import urllib.request, urllib.parse, base64

indexed = 0
for domain in ["mg.ai-elevate.ai", "gigforge.ai", "techuni.ai"]:
    try:
        creds = base64.b64encode(f"api:{MG_KEY}".encode()).decode()
        url = f"https://api.mailgun.net/v3/{domain}/events?event=accepted&limit=100"
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Basic {creds}")
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())

        for item in data.get("items", []):
            sender = item.get("message", {}).get("headers", {}).get("from", "")
            rcpt = item.get("recipient", "")
            subj = item.get("message", {}).get("headers", {}).get("subject", "")
            ts = item.get("timestamp", 0)

            if not subj:
                continue

            # Determine direction
            agent_domains = ["mg.ai-elevate.ai", "gigforge.ai", "techuni.ai", "internal.ai-elevate.ai"]
            is_outbound = any(d in sender.lower() for d in agent_domains)

            content = f"{subj} (from {sender} to {rcpt})"
            doc_id = hashlib.sha256(f"{sender}{rcpt}{subj}{ts}".encode()).hexdigest()[:16]

            index_document("email", content, doc_id=f"mg-{doc_id}",
                          metadata={
                              "sender": sender[:50], "recipient": rcpt[:50],
                              "subject": subj[:100], "direction": "outbound" if is_outbound else "inbound",
                              "domain": domain,
                              "date": time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)),
                          }, org="gigforge")
            indexed += 1

    except Exception as e:
        print(f"  {domain}: {e}")

print(f"Indexed {indexed} Mailgun events into semantic search")
