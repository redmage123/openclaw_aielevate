"""Auto-patches urllib to scrub all Mailgun sends.
Loaded automatically by Python on every startup for the aielevate user."""
import urllib.request

_original_urlopen = urllib.request.urlopen

def _patched_urlopen(req, *args, **kwargs):
    """Intercept Mailgun sends and scrub the body."""
    url = req if isinstance(req, str) else (req.full_url if hasattr(req, 'full_url') else str(req))

    if 'api.mailgun.net' in url and '/messages' in url:
        # This is a Mailgun send — scrub the body
        if hasattr(req, 'data') and req.data:
            try:
                import sys
                if '/home/aielevate' not in sys.path:
                    sys.path.insert(0, '/home/aielevate')
                from nlp_email_scrubber import scrub_email
                import urllib.parse

                data = req.data.decode('utf-8') if isinstance(req.data, bytes) else req.data
                params = urllib.parse.parse_qs(data, keep_blank_values=True)

                scrubbed = False
                for field in ['text', 'html']:
                    if field in params:
                        original = params[field][0]
                        cleaned = scrub_email(original)
                        if cleaned != original:
                            params[field] = [cleaned]
                            scrubbed = True

                if scrubbed:
                    req.data = urllib.parse.urlencode(
                        {k: v[0] for k, v in params.items()}, 
                        quote_via=urllib.parse.quote
                    ).encode('utf-8')

            except Exception:
                pass  # Don't break email sending if scrubber fails

    return _original_urlopen(req, *args, **kwargs)

urllib.request.urlopen = _patched_urlopen
