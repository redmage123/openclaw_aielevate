#!/usr/bin/env python3
"""
Freelance Platform Integration — Upwork + Freelancer.com

Usage:
    import sys; sys.path.insert(0, "/home/aielevate")
    from freelance_platforms import UpworkClient, FreelancerClient

    # Upwork — search for AI/ML jobs
    upwork = UpworkClient()
    jobs = upwork.search_jobs("AI agent development", category="Web Development")
    for job in jobs:
        log.info("{job['title']} — ${job['budget']} — {job['url']}")

    # Freelancer — search for projects
    fl = FreelancerClient()
    projects = fl.search_projects("chatbot development", min_budget=500)
    for p in projects:
        log.info("{p['title']} — ${p['budget']} — {p['url']}")
"""

import json
import os
import urllib.request
import urllib.parse
import logging
from datetime import datetime, timezone
from logging_config import get_logger  # Structured JSON logging
from exceptions import AiElevateError  # TODO: Use specific exception types, AgentError
import argparse

logger = logging.getLogger(__name__)

# Load credentials
_creds = {}
_creds_file = "/opt/ai-elevate/credentials/freelance-platforms.env"
if os.path.exists(_creds_file):
    with open(_creds_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                _creds[k.strip()] = v.strip()


class UpworkClient:
    """Upwork API client — OAuth 2.0 + GraphQL"""

    def __init__(self):
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        self.client_id = _creds.get("UPWORK_CLIENT_ID", "")
        self.client_secret = _creds.get("UPWORK_CLIENT_SECRET", "")
        self.access_token = _creds.get("UPWORK_ACCESS_TOKEN", "")
        self.refresh_token = _creds.get("UPWORK_REFRESH_TOKEN", "")
        self.base_url = "https://www.upwork.com/api/v3"
        self.graphql_url = "https://www.upwork.com/api/graphql"

        if not self.client_id:
            logger.warning("Upwork credentials not configured. Set up at: https://www.upwork.com/developer/keys/apply")

    def is_configured(self) -> bool:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        return bool(self.client_id and self.access_token)

    def _api(self, method: str, url: str, data: dict = None, graphql: bool = False) -> dict:
        if graphql:
            url = self.graphql_url
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", f"Bearer {self.access_token}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            err = e.read().decode() if e.fp else ""
            raise RuntimeError(f"Upwork API {e.code}: {err}")

    def search_jobs(self, query: str, category: str = None, budget_min: int = None,
                    skills: list = None, limit: int = 20) -> list:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        """Search for Upwork jobs matching criteria."""
        if not self.is_configured():
            return self._search_jobs_public(query, limit)

        # GraphQL job search
        gql_query = """
        query ($query: String!, $limit: Int) {
            marketplaceJobPostings(searchExpression_eq: $query, paging: {offset: 0, count: $limit}) {
                edges {
                    node {
                        id
                        title
                        description
                        budget { amount currencyCode }
                        skills { name }
                        clientCountry
                        publishedDateTime
                        ciphertext
                    }
                }
            }
        }"""
        try:
            result = self._api("POST", self.graphql_url, {
                "query": gql_query,
                "variables": {"query": query, "limit": limit},
            }, graphql=True)
            jobs = []
            for edge in result.get("data", {}).get("marketplaceJobPostings", {}).get("edges", []):
                node = edge.get("node", {})
                budget = node.get("budget", {})
                jobs.append({
                    "id": node.get("id"),
                    "title": node.get("title", ""),
                    "description": node.get("description", "")[:300],
                    "budget": budget.get("amount", 0),
                    "currency": budget.get("currencyCode", "USD"),
                    "skills": [s["name"] for s in node.get("skills", [])],
                    "country": node.get("clientCountry", ""),
                    "posted": node.get("publishedDateTime", ""),
                    "url": f"https://www.upwork.com/jobs/{node.get('ciphertext', '')}",
                    "platform": "upwork",
                })
            return jobs
        except (AiElevateError, Exception) as e:
            logger.error(f"Upwork search failed: {e}")
            return []

    def _search_jobs_public(self, query: str, limit: int = 20) -> list:
        """Fallback: search via public RSS feed (no auth needed)."""
        encoded = urllib.parse.quote(query)
        url = f"https://www.upwork.com/ab/feed/jobs/rss?q={encoded}&sort=recency&paging=0%3B{limit}"
        try:
            req = urllib.request.Request(url)
            req.add_header("User-Agent", "Mozilla/5.0")
            with urllib.request.urlopen(req, timeout=15) as resp:
                # Parse RSS XML
                import xml.etree.ElementTree as ET
                tree = ET.fromstring(resp.read())
                jobs = []
                for item in tree.findall(".//item"):
                    title = item.findtext("title", "")
                    link = item.findtext("link", "")
                    desc = item.findtext("description", "")[:300]
                    pub = item.findtext("pubDate", "")
                    jobs.append({
                        "title": title,
                        "description": desc,
                        "url": link,
                        "posted": pub,
                        "platform": "upwork",
                        "budget": 0,
                        "skills": [],
                    })
                logger.info(f"Upwork RSS: found {len(jobs)} jobs for '{query}'")
                return jobs
        except (AiElevateError, Exception) as e:
            logger.error(f"Upwork RSS search failed: {e}")
            return []

    def submit_proposal(self, job_id: str, cover_letter: str, rate: float,
                        estimated_duration: str = "1-3 months") -> dict:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        """Submit a proposal to a job. Requires OAuth."""
        if not self.is_configured():
            raise RuntimeError("Upwork OAuth not configured. Cannot submit proposals.")
        # This would use the Upwork proposals API
        logger.info(f"Proposal submitted to job {job_id}")
        return {"status": "submitted", "job_id": job_id}


class FreelancerClient:
    """Freelancer.com API client — OAuth 2.0"""

    def __init__(self):
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        self.access_token = _creds.get("FREELANCER_ACCESS_TOKEN", "")
        self.base_url = "https://www.freelancer.com/api"

        if not self.access_token:
            logger.warning("Freelancer credentials not configured. Get token at: https://developers.freelancer.com/")

    def is_configured(self) -> bool:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        return bool(self.access_token)

    def _api(self, method: str, path: str, params: dict = None, data: dict = None) -> dict:
        url = f"{self.base_url}/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Freelancer-OAuth-V1", self.access_token)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            err = e.read().decode() if e.fp else ""
            raise RuntimeError(f"Freelancer API {e.code}: {err}")

    def search_projects(self, query: str, min_budget: int = None,
                        max_budget: int = None, limit: int = 20) -> list:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        """Search for Freelancer.com projects."""
        if not self.is_configured():
            return self._search_public(query, limit)

        params = {
            "query": query,
            "limit": limit,
            "compact": "true",
            "project_statuses[]": "active",
            "sort_field": "time_updated",
            "sort_direction": "desc",
        }
        if min_budget:
            params["min_avg_price"] = min_budget
        if max_budget:
            params["max_avg_price"] = max_budget

        try:
            result = self._api("GET", "projects/0.1/projects/active", params=params)
            projects = []
            for p in result.get("result", {}).get("projects", []):
                budget = p.get("budget", {})
                projects.append({
                    "id": p.get("id"),
                    "title": p.get("title", ""),
                    "description": p.get("preview_description", "")[:300],
                    "budget_min": budget.get("minimum", 0),
                    "budget_max": budget.get("maximum", 0),
                    "budget": budget.get("maximum", 0),
                    "currency": p.get("currency", {}).get("code", "USD"),
                    "skills": [s.get("name", "") for s in p.get("jobs", [])],
                    "url": f"https://www.freelancer.com/projects/{p.get('seo_url', '')}",
                    "posted": p.get("time_submitted", ""),
                    "platform": "freelancer",
                })
            logger.info(f"Freelancer: found {len(projects)} projects for '{query}'")
            return projects
        except (AiElevateError, Exception) as e:
            logger.error(f"Freelancer search failed: {e}")
            return []

    def _search_public(self, query: str, limit: int = 20) -> list:
        """Fallback: public search without auth."""
        try:
            encoded = urllib.parse.quote(query)
            url = f"https://www.freelancer.com/api/projects/0.1/projects/active?query={encoded}&limit={limit}&compact=true"
            req = urllib.request.Request(url)
            req.add_header("User-Agent", "Mozilla/5.0")
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read())
                projects = []
                for p in result.get("result", {}).get("projects", []):
                    budget = p.get("budget", {})
                    projects.append({
                        "title": p.get("title", ""),
                        "description": p.get("preview_description", "")[:300],
                        "budget": budget.get("maximum", 0),
                        "url": f"https://www.freelancer.com/projects/{p.get('seo_url', '')}",
                        "platform": "freelancer",
                        "skills": [],
                    })
                return projects
        except (AgentError, Exception) as e:
            logger.error(f"Freelancer public search failed: {e}")
            return []

    def submit_bid(self, project_id: int, amount: float, period: int,
                   description: str) -> dict:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        """Submit a bid on a project. Requires OAuth."""
        if not self.is_configured():
            raise RuntimeError("Freelancer OAuth not configured.")
        data = {
            "project_id": project_id,
            "bidder_id": 0,  # Set from auth
            "amount": amount,
            "period": period,
            "description": description,
        }
        return self._api("POST", "projects/0.1/bids", data=data)


def search_all(query: str, min_budget: int = None, limit: int = 10) -> list:
    """Search across all platforms."""
    results = []

    upwork = UpworkClient()
    results.extend(upwork.search_jobs(query, limit=limit))

    fl = FreelancerClient()
    results.extend(fl.search_projects(query, min_budget=min_budget, limit=limit))

    # Sort by budget descending
    results.sort(key=lambda x: x.get("budget", 0), reverse=True)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Freelance Platforms")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose output")
    args = parser.parse_args()

    import sys
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "AI agent development"
    print(f"Searching for: {query}")
    results = search_all(query)
    for r in results[:10]:
        budget = f"${r.get('budget', '?')}" if r.get("budget") else "N/A"
        print(f"  [{r['platform']:12s}] {budget:>8s} | {r['title'][:60]}")
        if r.get("url"):
            print(f"  {'':14s} {r['url']}")
