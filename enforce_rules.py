#!/usr/bin/env python3
"""Code-level enforcement of ALL agent rules.

This module provides functions that agents MUST call. Each function
enforces a rule that AGENTS.md couldn't enforce via prompts.

1. get_credentials(project) — looks up real credentials, never fabricates
2. safe_send_email() — wraps send_email with all scrubbing + validation
3. validate_outbound(text) — checks for rule violations before any send

Also enhances usercustomize.py to intercept credential fabrication.
"""

import sys
import os
import json
import re
import hashlib
import logging
from pathlib import Path

sys.path.insert(0, "/home/aielevate")

log = logging.getLogger("enforce")

# ── Credential Vault ─────────────────────────────────────────────────

# Known project credentials — single source of truth
# Updated by: index_projects.py, build_workflow (on deploy)
CREDENTIAL_STORE = Path("/opt/ai-elevate/credentials/project-credentials.json")


def _load_credential_store():
    """Load the credential store."""
    if CREDENTIAL_STORE.exists():
        try:
            return json.loads(CREDENTIAL_STORE.read_text())
        except Exception as _e:

            import logging; logging.getLogger('enforce_rules.py').debug(f'Suppressed: {_e}')
    return {}


def _save_credential_store(data):
    """Save the credential store."""
    CREDENTIAL_STORE.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIAL_STORE.write_text(json.dumps(data, indent=2))
    os.chmod(str(CREDENTIAL_STORE), 0o600)


def register_credentials(project_slug, url, username, password, notes=""):
    """Register credentials for a project. Called by build_workflow on deploy."""
    store = _load_credential_store()
    store[project_slug.lower()] = {
        "url": url,
        "username": username,
        "password": password,
        "notes": notes,
        "updated": __import__("datetime").datetime.now().isoformat(),
    }
    _save_credential_store(store)
    log.info(f"Registered credentials for {project_slug}")


def get_credentials(project_name):
    """Look up credentials for a project. NEVER fabricates.

    Searches:
    1. Credential store (exact match)
    2. Semantic search (fuzzy match)

    Returns dict with url, username, password — or error message.
    """
    project_lower = project_name.lower().strip()

    # 1. Check credential store (exact and fuzzy key match)
    store = _load_credential_store()
    for key, creds in store.items():
        if key == project_lower or project_lower in key or key in project_lower:
            return {
                "found": True,
                "project": key,
                "url": creds["url"],
                "username": creds["username"],
                "password": creds["password"],
                "notes": creds.get("notes", ""),
            }

    # 2. Semantic search
    try:
        from semantic_search import search
        results = search(f"{project_name} credentials login URL", org="gigforge", top_k=5)
        for r in results:
            meta = r.get("metadata", {})
            if meta.get("credentials") or meta.get("admin_url"):
                creds_str = meta.get("credentials", "")
                url = meta.get("admin_url", meta.get("url", ""))
                username, password = "", ""
                if "/" in creds_str:
                    parts = creds_str.split("/", 1)
                    username, password = parts[0], parts[1]
                return {
                    "found": True,
                    "project": meta.get("project", project_name),
                    "url": url,
                    "username": username,
                    "password": password,
                    "notes": f"From semantic search (score={r['score']:.2f})",
                }
    except Exception as e:
        log.debug(f"Semantic search failed: {e}")

    # 3. NOT FOUND — return explicit error, never fabricate
    return {
        "found": False,
        "project": project_name,
        "error": f"No credentials found for '{project_name}'. "
                 "Check the credential store or ask the project owner. "
                 "DO NOT make up credentials.",
    }


# ── Outbound Validation ─────────────────────────────────────────────

# Patterns that indicate fabricated/hallucinated content
FABRICATION_PATTERNS = [
    # Random-looking passwords (high entropy strings)
    r'[A-Za-z0-9!@#$%^&*]{12,}',
    # URLs with common hallucinated domains
    r'https?://(?:carehaven|techuni|gigforge)\.(?:ai|io|com)/(?!admin)',
]

# Known valid URLs
VALID_URLS = {
    "carehaven": "http://78.47.104.139:4093/admin",
    "course-creator": "https://cc.techuni.ai",
    "gigforge": "https://gigforge.ai",
    "techuni": "https://techuni.ai",
    "sudacka": "http://78.47.104.139:4092",
}


def validate_outbound(text):
    """Validate outbound message for rule violations.

    Checks:
    1. No fabricated credentials (passwords that aren't in the store)
    2. No wrong URLs (validates against known URL list)
    3. No internal metadata (Trigger:, workflow:)
    4. No call suggestions

    Returns (is_valid, cleaned_text, violations)
    """
    violations = []

    # Check for passwords that look fabricated
    # Real passwords in our system: "admin", known API keys
    store = _load_credential_store()
    known_passwords = set()
    for creds in store.values():
        known_passwords.add(creds.get("password", ""))
    known_passwords.add("admin")  # Default dev password

    # Find password-like strings after "password:" or "Password:"
    pw_matches = re.findall(r'[Pp]assword[:\s]+([^\s\n,]+)', text)
    pw_matches += re.findall(r'password (?:is|=) ([^\s\n,]+)', text, re.IGNORECASE)
    pw_matches += re.findall(r'Password</b>[:\s]*([^\s\n<,]+)', text)
    for pw in pw_matches:
        if pw not in known_passwords and len(pw) > 6:
            violations.append(f"Possibly fabricated password: {pw[:20]}...")
            # Replace with warning
            text = text.replace(pw, "[CREDENTIALS REDACTED — verify before sending]")

    # Check URLs against known valid URLs
    url_matches = re.findall(r'https?://[^\s<>"\']+', text)
    for url in url_matches:
        url_lower = url.lower()
        is_known = any(valid in url_lower for valid in VALID_URLS.values())
        is_external = any(d in url_lower for d in [
            "github.com", "stripe.com", "mailgun.net", "upwork.com",
            "freelancer.com", "linkedin.com", "cloudflare.com",
        ])
        if not is_known and not is_external:
            # Check if it's a valid internal URL
            if any(domain in url_lower for domain in [
                "carehaven", "techuni", "gigforge", "ai-elevate",
                "78.47.104.139", "176.9.99.103", "localhost"
            ]):
                # Internal URL — verify it's a real endpoint
                if not any(valid in url_lower for valid in VALID_URLS.values()):
                    violations.append(f"Unverified internal URL: {url}")

    # Scrub metadata and calls (belt + suspenders with usercustomize.py)
    try:
        from nlp_email_scrubber import scrub_email
        text = scrub_email(text)
    except Exception as _e:

        import logging; logging.getLogger('enforce_rules.py').debug(f'Suppressed: {_e}')

    is_valid = len(violations) == 0
    return is_valid, text, violations


def safe_send_email(to, subject, body, agent_id, cc="braun.brelin@ai-elevate.ai"):
    """Send email with full validation. Use this instead of send_email directly.

    Validates the body for fabricated content before sending.
    """
    is_valid, cleaned_body, violations = validate_outbound(body)

    if violations:
        log.warning(f"Outbound violations for {to}: {violations}")
        # Still send but with cleaned body — violations are logged for review

    from send_email import send_email
    return send_email(to=to, subject=subject, body=cleaned_body,
                      agent_id=agent_id, cc=cc)


# ── Credential Store Initialization ─────────────────────────────────

def init_credential_store():
    """Initialize the credential store with known project credentials."""
    store = _load_credential_store()

    defaults = {
        "carehaven-platform": {
            "url": "http://78.47.104.139:4093/admin",
            "username": "admin", "password": "admin",
            "notes": "CareHaven Payload CMS admin panel",
        },
        "carehaven-website": {
            "url": "http://78.47.104.139:4092",
            "username": "", "password": "",
            "notes": "CareHaven public website (no auth)",
        },
        "course-creator": {
            "url": "https://cc.techuni.ai",
            "username": "admin", "password": "admin",
            "notes": "TechUni Course Creator platform",
        },
        "sudacka-mreza-frontend": {
            "url": "http://78.47.104.139:4092",
            "username": "", "password": "",
            "notes": "Sudacka Mreza frontend",
        },
        "sudacka-mreza-cms": {
            "url": "http://78.47.104.139:4093/admin",
            "username": "admin", "password": "admin",
            "notes": "Sudacka Mreza Payload CMS",
        },
    }

    for key, creds in defaults.items():
        if key not in store:
            store[key] = {**creds, "updated": __import__("datetime").datetime.now().isoformat()}

    _save_credential_store(store)
    return len(store)


# ── Enhanced usercustomize.py ────────────────────────────────────────

def install_enhanced_patch():
    """Install enhanced usercustomize.py that also validates credentials in outbound."""
    patch_path = Path("/home/aielevate/.local/lib/python3.12/site-packages/usercustomize.py")

    patch_code = '''"""Auto-patches urllib to scrub + validate all Mailgun sends.
Loaded automatically by Python on every startup for the aielevate user."""
import urllib.request

_original_urlopen = urllib.request.urlopen

def _patched_urlopen(req, *args, **kwargs):
    """Intercept Mailgun sends: scrub metadata, validate credentials, block fabrication."""
    url = req if isinstance(req, str) else (req.full_url if hasattr(req, 'full_url') else str(req))

    if 'api.mailgun.net' in url and '/messages' in url:
        if hasattr(req, 'data') and req.data:
            try:
                import sys
                if '/home/aielevate' not in sys.path:
                    sys.path.insert(0, '/home/aielevate')
                import urllib.parse

                data = req.data.decode('utf-8') if isinstance(req.data, bytes) else req.data
                params = urllib.parse.parse_qs(data, keep_blank_values=True)

                for field in ['text', 'html']:
                    if field in params:
                        original = params[field][0]

                        # 1. Scrub metadata + calls
                        try:
                            from nlp_email_scrubber import scrub_email
                            cleaned = scrub_email(original)
                        except Exception:
                            cleaned = original

                        # 2. Validate credentials — redact fabricated ones
                        try:
                            from enforce_rules import validate_outbound
                            _, cleaned, violations = validate_outbound(cleaned)
                            if violations:
                                import logging
                                logging.getLogger("enforce").warning(
                                    f"Outbound violations caught by urllib patch: {violations}")
                        except Exception as _e:

                            import logging; logging.getLogger('enforce_rules.py').debug(f'Suppressed: {_e}')

                        if cleaned != original:
                            params[field] = [cleaned]

                req.data = urllib.parse.urlencode(
                    {k: v[0] for k, v in params.items()},
                    quote_via=urllib.parse.quote
                ).encode('utf-8')

            except Exception as _e:


                import logging; logging.getLogger('enforce_rules.py').debug(f'Suppressed: {_e}')

    return _original_urlopen(req, *args, **kwargs)

urllib.request.urlopen = _patched_urlopen
'''

    patch_path.write_text(patch_code)
    return str(patch_path)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Rule Enforcement")
    parser.add_argument("--init", action="store_true", help="Initialize credential store")
    parser.add_argument("--lookup", type=str, help="Look up credentials for a project")
    parser.add_argument("--validate", type=str, help="Validate text for violations")
    parser.add_argument("--install-patch", action="store_true", help="Install enhanced urllib patch")
    parser.add_argument("--list", action="store_true", help="List all stored credentials")
    args = parser.parse_args()

    if args.init:
        count = init_credential_store()
        print(f"Credential store initialized: {count} entries")
    elif args.lookup:
        result = get_credentials(args.lookup)
        print(json.dumps(result, indent=2))
    elif args.validate:
        valid, cleaned, violations = validate_outbound(args.validate)
        print(f"Valid: {valid}")
        if violations:
            print(f"Violations: {violations}")
        print(f"Cleaned: {cleaned}")
    elif args.install_patch:
        path = install_enhanced_patch()
        print(f"Enhanced patch installed at {path}")
    elif args.list:
        store = _load_credential_store()
        for key, creds in store.items():
            print(f"  {key}: {creds['url']} ({creds['username']}/{creds['password'][:4]}...)")
    else:
        parser.print_help()
