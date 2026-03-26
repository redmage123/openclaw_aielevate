"""Auto-patches urllib: rule scrub + LLM validation on all Mailgun sends."""
import urllib.request
import hashlib
import time as _time

_original_urlopen = urllib.request.urlopen

# Dedup cache: hash → timestamp. Prevents sending same email twice within 5 min.
_send_cache = {}
_DEDUP_TTL = 300  # 5 minutes


def _llm_validate(text):
    """Ask the email-validator model to check the email. Returns (pass, cleaned, violations)."""
    import json, re

    try:
        payload = json.dumps({
            "model": "email-validator",
            "prompt": text[:2000],
            "stream": False,
        }).encode()

        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        resp = _original_urlopen(req, timeout=15)
        result = json.loads(resp.read())["response"].strip()

        m = re.search(r'\{.*\}', result, re.DOTALL)
        if m:
            parsed = json.loads(m.group())
            violations = parsed.get("violations", [])
            if violations:
                # Remove lines that contain violations
                cleaned = text
                for v in violations:
                    # Try to find and remove the offending content
                    for line in text.split("\n"):
                        if any(keyword in line.lower() for keyword in
                               ["trigger:", "call", "zoom", "screen share", "meeting",
                                "walk through", "set up a time", "reach out",
                                "carehaven.ai", "techuni.com", "gigforge.com"]):
                            cleaned = cleaned.replace(line, "")
                cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
                return False, cleaned, violations
            return True, text, []

    except Exception:
        pass
    return True, text, []


def _patched_urlopen(req, *args, **kwargs):
    """Intercept Mailgun sends: rule scrub + LLM validate."""
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

                        # Pass 1: Rule-based scrub (fast, <1ms)
                        try:
                            from nlp_email_scrubber import scrub_email
                            cleaned = scrub_email(original)
                        except Exception:
                            cleaned = original

                        # Pass 2: LLM validation (catches what regex misses)
                        passed, cleaned, violations = _llm_validate(cleaned)
                        if violations:
                            import logging
                            logging.getLogger("llm-validator").warning(
                                f"LLM caught: {violations}")

                        # Pass 3: Credential validation
                        try:
                            from enforce_rules import validate_outbound
                            _, cleaned, _ = validate_outbound(cleaned)
                        except Exception:
                            pass

                        # Governance: enforce CC on external customer emails
                try:
                    from org_governance import enforce_customer_cc
                    import urllib.parse as _up
                    _params = _up.parse_qs(req.data.decode() if isinstance(req.data, bytes) else req.data, keep_blank_values=True)
                    _to = _params.get('to', [''])[0]
                    _cc = _params.get('cc', [''])[0]
                    _new_cc = enforce_customer_cc(_to, _cc)
                    if _new_cc != _cc:
                        _params['cc'] = [_new_cc]
                        req.data = _up.urlencode({k: v[0] for k, v in _params.items()}, quote_via=_up.quote).encode('utf-8')
                except Exception:
                    pass

                if cleaned != original:
                            params[field] = [cleaned]

                req.data = urllib.parse.urlencode(
                    {k: v[0] for k, v in params.items()},
                    quote_via=urllib.parse.quote
                ).encode('utf-8')

            except Exception:
                pass


        # Fix sending domain — agents using wrong Mailgun domain
        try:
            import urllib.parse as _uparse
            _params = _uparse.parse_qs(req.data.decode() if isinstance(req.data, bytes) else req.data, keep_blank_values=True)
            _from = _params.get('from', [''])[0].lower()
            _domain_map = {
                'ceo@mg.ai-elevate.ai': ('techuni.ai', 'Robin Callister <ceo@techuni.ai>'),
                'ceo@internal.ai-elevate.ai': ('techuni.ai', 'Robin Callister <ceo@techuni.ai>'),
                'robin@mg.ai-elevate.ai': ('techuni.ai', 'Robin Callister <ceo@techuni.ai>'),
            }
            for wrong_from, (correct_domain, correct_from) in _domain_map.items():
                if wrong_from in _from:
                    # Rewrite the URL to use the correct Mailgun domain
                    url = req.full_url.replace('mg.ai-elevate.ai', correct_domain)
                    req.full_url = url
                    _params['from'] = [correct_from]
                    _params['h:Reply-To'] = [correct_from.split('<')[1].rstrip('>')]
                    req.data = _uparse.urlencode({k: v[0] for k, v in _params.items()}, quote_via=_uparse.quote).encode('utf-8')
                    break
        except Exception:
            pass  # fix_sending_domain

    # HTML formatting — convert plain text to branded HTML
    if 'api.mailgun.net' in url and '/messages' in url and hasattr(req, 'data') and req.data:
        try:
            import sys
            if '/home/aielevate' not in sys.path:
                sys.path.insert(0, '/home/aielevate')
            import urllib.parse as _fp
            _fparams = _fp.parse_qs(req.data.decode() if isinstance(req.data, bytes) else req.data, keep_blank_values=True)
            if 'html' not in _fparams and 'text' in _fparams:
                from email_formatter import format_email, get_agent_info
                _from = _fparams.get('from', [''])[0]
                # Extract agent_id from From address
                _agent_id = 'gigforge'
                if 'techuni' in _from.lower(): _agent_id = 'techuni-ceo'
                elif 'sales' in _from.lower(): _agent_id = 'gigforge-sales'
                elif 'engineer' in _from.lower(): _agent_id = 'gigforge-engineer'
                elif 'devops' in _from.lower() or 'casey' in _from.lower(): _agent_id = 'gigforge-devops'
                elif 'scout' in _from.lower() or 'quinn' in _from.lower(): _agent_id = 'gigforge-scout'
                _name, _title = get_agent_info(_agent_id)
                _html = format_email(_fparams['text'][0], agent_id=_agent_id, agent_name=_name, agent_title=_title)
                _fparams['html'] = [_html]
                req.data = _fp.urlencode({k: v[0] for k, v in _fparams.items()}, quote_via=_fp.quote).encode('utf-8')
        except Exception:
            pass

    # Dedup: block duplicate Mailgun sends within 5 minutes
    if 'api.mailgun.net' in url and '/messages' in url and hasattr(req, 'data') and req.data:
        dedup_key = hashlib.sha256(req.data[:500] if isinstance(req.data, bytes) else req.data.encode()[:500]).hexdigest()[:16]
        now = _time.time()
        # Clean expired entries
        _send_cache.update({k: v for k, v in _send_cache.items() if now - v < _DEDUP_TTL})
        if dedup_key in _send_cache:
            import logging
            logging.getLogger("urllib-dedup").warning("Blocked duplicate Mailgun send")
            # Return a fake successful response instead of sending twice
            import io
            class FakeResponse:
                def read(self): return b'{"id":"dedup-blocked","message":"Duplicate blocked"}'
                def getcode(self): return 200
                def __enter__(self): return self
                def __exit__(self, *a): pass
            return FakeResponse()
        _send_cache[dedup_key] = now

    return _original_urlopen(req, *args, **kwargs)

urllib.request.urlopen = _patched_urlopen
