#!/usr/bin/env python3
"""
CMS Operations — Strapi headless CMS helper for all agents.

Usage:
    import sys; sys.path.insert(0, "/home/aielevate")
    from cms_ops import CMS

    cms = CMS()
    cms.create_post(title="...", content="...", excerpt="...", org="techuni", author="techuni-marketing")
    cms.list_posts(org="techuni", status="draft")
    cms.publish_post(post_id=1)
"""

import json
import os
import urllib.request
import urllib.parse
from datetime import datetime, timezone

STRAPI_URL = "http://localhost:1337"
STRAPI_TOKEN = ""

_creds_file = "/opt/ai-elevate/credentials/strapi.env"
if os.path.exists(_creds_file):
    with open(_creds_file) as f:
        for line in f:
            line = line.strip()
            if line.startswith("STRAPI_API_TOKEN="):
                STRAPI_TOKEN = line.split("=", 1)[1]
            elif line.startswith("STRAPI_URL="):
                STRAPI_URL = line.split("=", 1)[1]


class CMS:
    def __init__(self):
        self.base = STRAPI_URL
        self.token = STRAPI_TOKEN

    def _api(self, method, path, data=None):
        url = f"{self.base}/api/{path}"
        body = json.dumps({"data": data}).encode() if data else None
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            err = e.read().decode() if e.fp else ""
            raise RuntimeError(f"Strapi API {method} {path} => {e.code}: {err}")
        except Exception as e:
            raise RuntimeError(f"Strapi API error: {e}")

    # ── Blog Posts ──

    def create_post(self, title, content, excerpt, org, author,
                    category="general", tags=None, status="draft",
                    seo_title=None, seo_description=None):
        """Create a blog post draft."""
        slug = title.lower().replace(" ", "-").replace("'", "").replace('"', '')[:80]
        result = self._api("POST", "blog-posts", {
            "title": title,
            "slug": slug,
            "content": content,
            "excerpt": excerpt,
            "org": org,
            "author": author,
            "category": category,
            "tags": tags or [],
            "contentStatus": status,
            "seoTitle": seo_title or title,
            "seoDescription": seo_description or excerpt[:160] if excerpt else "",
        })
        post_id = result.get("data", {}).get("id", "?")
        print(f"[CMS] Created blog post #{post_id}: {title} ({org}/{status})")
        return result

    def list_posts(self, org=None, status=None):
        """List blog posts with optional filters."""
        params = ["sort=createdAt:desc", "pagination[pageSize]=50"]
        if org:
            params.append(f"filters[org][$eq]={org}")
        if status:
            params.append(f"filters[contentStatus][$eq]={status}")
        return self._api("GET", f"blog-posts?{'&'.join(params)}")

    def get_post(self, post_id):
        """Get a single blog post."""
        return self._api("GET", f"blog-posts/{post_id}")

    def update_post(self, post_id, **kwargs):
        """Update blog post fields."""
        result = self._api("PUT", f"blog-posts/{post_id}", kwargs)
        print(f"[CMS] Updated blog post #{post_id}")
        return result

    def publish_post(self, post_id):
        """Publish a blog post."""
        now = datetime.now(timezone.utc).isoformat()
        result = self._api("PUT", f"blog-posts/{post_id}", {
            "contentStatus": "published",
            "publishedDate": now,
        })
        print(f"[CMS] Published blog post #{post_id}")
        return result

    def schedule_post(self, post_id, scheduled_for):
        """Schedule a blog post for future publishing."""
        result = self._api("PUT", f"blog-posts/{post_id}", {
            "contentStatus": "scheduled",
            "scheduledFor": scheduled_for,
        })
        print(f"[CMS] Scheduled blog post #{post_id} for {scheduled_for}")
        return result

    # ── Newsletters ──

    def create_newsletter(self, title, org, html_content, status="draft",
                          scheduled_for=None):
        """Create a newsletter draft."""
        result = self._api("POST", "newsletters", {
            "title": title,
            "org": org,
            "htmlContent": html_content,
            "status": status,
            "scheduledFor": scheduled_for,
        })
        nl_id = result.get("data", {}).get("id", "?")
        print(f"[CMS] Created newsletter #{nl_id}: {title} ({org})")
        return result

    def list_newsletters(self, org=None, status=None):
        """List newsletters."""
        params = ["sort=createdAt:desc"]
        if org:
            params.append(f"filters[org][$eq]={org}")
        if status:
            params.append(f"filters[status][$eq]={status}")
        return self._api("GET", f"newsletters?{'&'.join(params)}")

    def update_newsletter(self, newsletter_id, **kwargs):
        """Update newsletter fields."""
        return self._api("PUT", f"newsletters/{newsletter_id}", kwargs)

    # ── Social Posts ──

    def create_social_post(self, content, org, platform, status="draft",
                           scheduled_for=None, media_url=None):
        """Create a social media post draft."""
        result = self._api("POST", "social-posts", {
            "content": content,
            "org": org,
            "platform": platform,
            "status": status,
            "scheduledFor": scheduled_for,
            "mediaUrl": media_url,
        })
        sp_id = result.get("data", {}).get("id", "?")
        print(f"[CMS] Created social post #{sp_id} for {platform} ({org})")
        return result

    def list_social_posts(self, org=None, status=None, platform=None):
        """List social posts."""
        params = ["sort=createdAt:desc"]
        if org:
            params.append(f"filters[org][$eq]={org}")
        if status:
            params.append(f"filters[status][$eq]={status}")
        if platform:
            params.append(f"filters[platform][$eq]={platform}")
        return self._api("GET", f"social-posts?{'&'.join(params)}")

    def update_social_post(self, post_id, **kwargs):
        """Update social post fields."""
        return self._api("PUT", f"social-posts/{post_id}", kwargs)

    # ── Case Studies ──

    def create_case_study(self, title, client, description, tech_stack,
                          outcomes, org, status="draft"):
        """Create a case study."""
        slug = title.lower().replace(" ", "-")[:80]
        result = self._api("POST", "case-studies", {
            "title": title,
            "slug": slug,
            "client": client,
            "description": description,
            "techStack": tech_stack if isinstance(tech_stack, list) else [tech_stack],
            "outcomes": outcomes,
            "org": org,
            "status": status,
        })
        cs_id = result.get("data", {}).get("id", "?")
        print(f"[CMS] Created case study #{cs_id}: {title} ({org})")
        return result

    def list_case_studies(self, org=None, status=None):
        """List case studies."""
        params = ["sort=createdAt:desc"]
        if org:
            params.append(f"filters[org][$eq]={org}")
        if status:
            params.append(f"filters[status][$eq]={status}")
        return self._api("GET", f"case-studies?{'&'.join(params)}")

    def update_case_study(self, study_id, **kwargs):
        """Update case study fields."""
        return self._api("PUT", f"case-studies/{study_id}", kwargs)


if __name__ == "__main__":
    import sys
    cms = CMS()
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "--list-posts":
            org = sys.argv[2] if len(sys.argv) > 2 else None
            result = cms.list_posts(org=org)
            for p in result.get("data", []):
                a = p.get("attributes", p)
                print(f"  #{p.get('id','?')}: [{a.get('contentStatus','?')}] {a.get('title','?')} ({a.get('org','?')})")
        elif cmd == "--list-newsletters":
            result = cms.list_newsletters()
            for n in result.get("data", []):
                a = n.get("attributes", n)
                print(f"  #{n.get('id','?')}: [{a.get('status','?')}] {a.get('title','?')} ({a.get('org','?')})")
        elif cmd == "--list-social":
            result = cms.list_social_posts()
            for s in result.get("data", []):
                a = s.get("attributes", s)
                print(f"  #{s.get('id','?')}: [{a.get('status','?')}] {a.get('platform','?')}/{a.get('org','?')}: {a.get('content','')[:50]}")
        elif cmd == "--list-cases":
            result = cms.list_case_studies()
            for c in result.get("data", []):
                a = c.get("attributes", c)
                print(f"  #{c.get('id','?')}: [{a.get('status','?')}] {a.get('title','?')} ({a.get('org','?')})")
        else:
            print("Usage: cms_ops.py [--list-posts|--list-newsletters|--list-social|--list-cases] [org]")
    else:
        print("Strapi CMS Helper")
        print(f"  URL: {STRAPI_URL}")
        print(f"  Token: {'configured' if STRAPI_TOKEN else 'NOT SET'}")
        print("  Commands: --list-posts, --list-newsletters, --list-social, --list-cases")
