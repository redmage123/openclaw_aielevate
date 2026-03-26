#!/usr/bin/env python3
"""Mailgun Send Proxy — intercepts ALL outbound email, scrubs, then forwards to Mailgun.

Runs on localhost:8066. All email sends go through this proxy instead of direct to Mailgun.
The Mailgun API key file is replaced with a redirect to this proxy.

This is the nuclear option: no matter how the agent sends email (send_email.py, urllib,
requests, curl), it goes through the proxy which scrubs metadata and call suggestions.

Architecture:
    Agent code → POST localhost:8066/send → scrub body → POST api.mailgun.net → response
"""

import json
import logging
import sys
import base64
import urllib.request
import urllib.parse
from pathlib import Path

sys.path.insert(0, "/home/aielevate")

from fastapi import FastAPI, Form, UploadFile, File, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("mailgun-proxy")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [mailgun-proxy] %(message)s")

app = FastAPI()

REAL_MG_KEY = Path("/opt/ai-elevate/credentials/mailgun-api-key-real.txt").read_text().strip()


def _scrub(text):
    """Scrub email body — remove metadata and call suggestions."""
    if not text:
        return text
    try:
        from nlp_email_scrubber import scrub_email
        return scrub_email(text)
    except Exception:
        # Fallback: line-by-line trigger removal
        lines = text.split("\n")
        clean = [l for l in lines if not l.strip().lower().startswith("trigger:")]
        return "\n".join(clean)


@app.post("/send/{domain}")
async def proxy_send(domain: str, request: Request):
    """Proxy a Mailgun send — scrub the body, forward to real Mailgun API."""
    form = await request.form()
    form_dict = {}
    for key in form:
        form_dict[key] = form[key]

    # Scrub text and html bodies
    if "text" in form_dict:
        original = str(form_dict["text"])
        scrubbed = _scrub(original)
        if scrubbed != original:
            log.info(f"Scrubbed {len(original) - len(scrubbed)} chars from text body")
        form_dict["text"] = scrubbed

    if "html" in form_dict:
        form_dict["html"] = _scrub(str(form_dict["html"]))

    # Index for semantic search
    try:
        from semantic_search import index_document
        subject = str(form_dict.get("subject", ""))
        body = str(form_dict.get("text", ""))
        sender = str(form_dict.get("from", ""))
        recipient = str(form_dict.get("to", ""))
        import time, hashlib
        doc_id = hashlib.sha256(f"{sender}{recipient}{subject}{time.time()}".encode()).hexdigest()[:16]
        index_document("email", f"{subject} {body[:500]}",
                      doc_id=f"proxy-{doc_id}",
                      metadata={"sender": sender[:50], "recipient": recipient[:50],
                               "subject": subject[:100], "direction": "outbound", "domain": domain},
                      org="gigforge")
    except Exception:
        pass

    # Forward to real Mailgun
    creds = base64.b64encode(f"api:{REAL_MG_KEY}".encode()).decode()
    data = urllib.parse.urlencode({k: v for k, v in form_dict.items() if not hasattr(v, 'read')}).encode()
    url = f"https://api.mailgun.net/v3/{domain}/messages"

    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Basic {creds}")

    try:
        resp = urllib.request.urlopen(req, timeout=15)
        result = resp.read().decode()
        log.info(f"Sent via {domain}: {form_dict.get('to', '?')} — {form_dict.get('subject', '?')[:40]}")
        return JSONResponse(content=json.loads(result))
    except Exception as e:
        log.error(f"Mailgun send failed: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "mailgun-proxy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8067)
