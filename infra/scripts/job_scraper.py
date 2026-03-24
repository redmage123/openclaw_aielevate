#!/usr/bin/env python3
"""Job Scraper — scrapes public job listings from freelance platforms.

No login required. Job listings are public pages.
Uses Playwright for JavaScript-rendered content.

Usage:
  from job_scraper import scrape_upwork, scrape_freelancer, scrape_all, score_job

  jobs = scrape_upwork(keywords=["react", "fastapi", "ai agent"], max_pages=3)
  jobs = scrape_freelancer(keywords=["python", "web app"], max_pages=2)
  jobs = scrape_all(keywords=["react", "python", "ai"], max_pages=2)

CLI:
  python3 job_scraper.py --platform upwork --keywords "react,python,ai" --max-pages 2
  python3 job_scraper.py --all --keywords "react,python,ai,fastapi,video" --max-pages 2
"""

import json
import os
import re
import hashlib
import sys
import logging
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras

sys.path.insert(0, "/home/aielevate")

DB = dict(
    host=os.environ.get("DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("DB_PORT", "5434")),
    dbname=os.environ.get("DB_NAME", "rag"),
    user=os.environ.get("DB_USER", "rag"),
    password=os.environ.get("DB_PASSWORD", ""),
)
LOG = logging.getLogger("job-scraper")

# Our skills for scoring
OUR_SKILLS = {
    "python", "fastapi", "react", "next.js", "nextjs", "typescript", "javascript",
    "node.js", "nodejs", "vue", "svelte", "astro", "docker", "kubernetes",
    "postgresql", "sqlite", "mongodb", "redis", "ai", "machine learning", "ml",
    "llm", "chatbot", "agent", "automation", "devops", "ci/cd", "aws", "gcp",
    "azure", "terraform", "video", "animation", "motion graphics",
    "seo", "marketing", "content", "wordpress", "shopify", "stripe",
    "flutter", "react native", "ios", "android", "mobile",
}


def _get_db():
    conn = psycopg2.connect(**DB)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""CREATE TABLE IF NOT EXISTS scraped_jobs (
        id TEXT PRIMARY KEY,
        platform TEXT NOT NULL,
        title TEXT NOT NULL,
        url TEXT,
        budget TEXT,
        budget_min REAL,
        budget_max REAL,
        description TEXT,
        skills TEXT,
        client_info TEXT,
        posted_at TEXT,
        scraped_at TIMESTAMPTZ DEFAULT NOW(),
        score REAL DEFAULT 0,
        status TEXT DEFAULT 'new',
        proposal_id INTEGER,
        notes TEXT
    )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON scraped_jobs(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_score ON scraped_jobs(score DESC)")
    return conn, cur


def score_job(title: str, description: str, skills: str, budget_min: float, budget_max: float) -> float:
    """Score a job 0-100 based on skill match, budget, and fit."""
    score = 0
    text = f"{title} {description} {skills}".lower()

    # Skill match (0-50 points)
    matched = sum(1 for s in OUR_SKILLS if s in text)
    score += min(matched * 10, 50)

    # Budget (0-25 points)
    avg_budget = (budget_min + budget_max) / 2 if budget_max > 0 else budget_min
    if avg_budget >= 5000:
        score += 25
    elif avg_budget >= 2000:
        score += 20
    elif avg_budget >= 500:
        score += 15
    elif avg_budget >= 100:
        score += 10
    elif avg_budget > 0:
        score += 5

    # Complexity bonus (0-15 points) — longer descriptions = more serious client
    desc_len = len(description)
    if desc_len > 1000:
        score += 15
    elif desc_len > 500:
        score += 10
    elif desc_len > 200:
        score += 5

    # Penalty for likely spam/low quality
    spam_signals = ["urgent", "asap", "need immediately", "very simple", "easy job", "quick fix"]
    if any(s in text for s in spam_signals):
        score -= 10

    # Bonus for AI/agent work (our specialty)
    if any(k in text for k in ["ai agent", "llm", "chatbot", "automation", "rag", "langchain"]):
        score += 10

    return max(0, min(100, score))


def _parse_budget(budget_text: str) -> tuple:
    """Parse budget text into (min, max) floats."""
    if not budget_text:
        return 0, 0
    nums = re.findall(r'[\$€£]?([\d,]+(?:\.\d+)?)', budget_text.replace(",", ""))
    if len(nums) >= 2:
        return float(nums[0]), float(nums[1])
    elif len(nums) == 1:
        return float(nums[0]), float(nums[0])
    return 0, 0


def scrape_upwork(keywords: list, max_pages: int = 2) -> list:
    """Scrape Upwork job listings for given keywords."""
    from playwright.sync_api import sync_playwright

    jobs = []
    conn, cur = _get_db()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36")

        for keyword in keywords:
            for page_num in range(1, max_pages + 1):
                url = f"https://www.upwork.com/nx/search/jobs/?q={keyword}&page={page_num}&sort=recency"
                LOG.info(f"Scraping Upwork: {keyword} page {page_num}")

                try:
                    page.goto(url, timeout=30000, wait_until="domcontentloaded")
                    page.wait_for_timeout(3000)  # Let JS render

                    # Extract job cards
                    cards = page.query_selector_all('[data-test="job-tile-list"] section, .job-tile')
                    if not cards:
                        # Try alternate selectors
                        cards = page.query_selector_all('article, [data-ev-label="search_results_impression"]')

                    for card in cards:
                        try:
                            title_el = card.query_selector('h2 a, .job-title a, [data-test="job-title"] a')
                            title = title_el.inner_text().strip() if title_el else ""
                            link = title_el.get_attribute("href") if title_el else ""
                            if link and not link.startswith("http"):
                                link = "https://www.upwork.com" + link

                            desc_el = card.query_selector('[data-test="job-description-text"], .job-description, p')
                            desc = desc_el.inner_text().strip() if desc_el else ""

                            budget_el = card.query_selector('[data-test="budget"], .budget, [data-test="is-fixed-price"]')
                            budget = budget_el.inner_text().strip() if budget_el else ""

                            skills_els = card.query_selector_all('[data-test="attr-item"], .skill-tag, .air3-badge')
                            skills = ", ".join(el.inner_text().strip() for el in skills_els)

                            if title:
                                job_id = hashlib.md5(f"upwork:{title}:{link}".encode()).hexdigest()[:16]
                                bmin, bmax = _parse_budget(budget)
                                s = score_job(title, desc, skills, bmin, bmax)

                                job = {
                                    "id": job_id, "platform": "upwork", "title": title,
                                    "url": link, "budget": budget, "budget_min": bmin,
                                    "budget_max": bmax, "description": desc[:2000],
                                    "skills": skills, "score": s,
                                }
                                jobs.append(job)

                                cur.execute("""INSERT INTO scraped_jobs
                                    (id, platform, title, url, budget, budget_min, budget_max, description, skills, score)
                                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                    ON CONFLICT (id) DO NOTHING""",
                                    (job_id, "upwork", title, link, budget, bmin, bmax, desc[:2000], skills, s))

                        except Exception as e:
                            LOG.warning(f"Failed to parse card: {e}")

                except Exception as e:
                    LOG.warning(f"Failed to scrape {url}: {e}")

        browser.close()

    conn.close()
    LOG.info(f"Upwork: scraped {len(jobs)} jobs")
    return jobs


def scrape_freelancer(keywords: list, max_pages: int = 2) -> list:
    """Scrape Freelancer.com job listings."""
    from playwright.sync_api import sync_playwright

    jobs = []
    conn, cur = _get_db()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36")

        for keyword in keywords:
            for page_num in range(1, max_pages + 1):
                url = f"https://www.freelancer.com/jobs/?keyword={keyword}&page={page_num}"
                LOG.info(f"Scraping Freelancer: {keyword} page {page_num}")

                try:
                    page.goto(url, timeout=30000, wait_until="domcontentloaded")
                    page.wait_for_timeout(3000)

                    cards = page.query_selector_all('.JobSearchCard-item, .project-card, [data-project-card]')

                    for card in cards:
                        try:
                            title_el = card.query_selector('.JobSearchCard-primary-heading a, .project-title a, a.JobSearchCard-primary-heading-link')
                            title = title_el.inner_text().strip() if title_el else ""
                            link = title_el.get_attribute("href") if title_el else ""
                            if link and not link.startswith("http"):
                                link = "https://www.freelancer.com" + link

                            desc_el = card.query_selector('.JobSearchCard-primary-description, .project-description, p')
                            desc = desc_el.inner_text().strip() if desc_el else ""

                            budget_el = card.query_selector('.JobSearchCard-secondary-price, .project-budget')
                            budget = budget_el.inner_text().strip() if budget_el else ""

                            skills_els = card.query_selector_all('.JobSearchCard-primary-tags a, .skill-tag, .TagItem')
                            skills = ", ".join(el.inner_text().strip() for el in skills_els)

                            if title:
                                job_id = hashlib.md5(f"freelancer:{title}:{link}".encode()).hexdigest()[:16]
                                bmin, bmax = _parse_budget(budget)
                                s = score_job(title, desc, skills, bmin, bmax)

                                job = {
                                    "id": job_id, "platform": "freelancer", "title": title,
                                    "url": link, "budget": budget, "budget_min": bmin,
                                    "budget_max": bmax, "description": desc[:2000],
                                    "skills": skills, "score": s,
                                }
                                jobs.append(job)

                                cur.execute("""INSERT INTO scraped_jobs
                                    (id, platform, title, url, budget, budget_min, budget_max, description, skills, score)
                                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                    ON CONFLICT (id) DO NOTHING""",
                                    (job_id, "freelancer", title, link, budget, bmin, bmax, desc[:2000], skills, s))

                        except Exception as e:
                            LOG.warning(f"Failed to parse card: {e}")

                except Exception as e:
                    LOG.warning(f"Failed to scrape {url}: {e}")

        browser.close()

    conn.close()
    LOG.info(f"Freelancer: scraped {len(jobs)} jobs")
    return jobs


def scrape_all(keywords: list, max_pages: int = 2) -> list:
    """Scrape all platforms."""
    all_jobs = []
    all_jobs.extend(scrape_upwork(keywords, max_pages))
    all_jobs.extend(scrape_freelancer(keywords, max_pages))
    # Sort by score descending
    all_jobs.sort(key=lambda j: j["score"], reverse=True)
    return all_jobs


def get_top_jobs(limit: int = 20, min_score: float = 30) -> list:
    """Get top scored new jobs from the database."""
    conn, cur = _get_db()
    cur.execute(
        "SELECT * FROM scraped_jobs WHERE status='new' AND score >= %s ORDER BY score DESC LIMIT %s",
        (min_score, limit)
    )
    jobs = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jobs


def mark_sent_to_marketing(job_id: str):
    """Mark a job as sent to marketing for proposal drafting."""
    conn, cur = _get_db()
    cur.execute("UPDATE scraped_jobs SET status='drafting' WHERE id=%s", (job_id,))
    conn.close()


def mark_proposal_queued(job_id: str, proposal_id: int):
    """Mark a job as having a proposal in the approval queue."""
    conn, cur = _get_db()
    cur.execute("UPDATE scraped_jobs SET status='queued', proposal_id=%s WHERE id=%s", (proposal_id, job_id))
    conn.close()


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="Job Scraper")
    parser.add_argument("--platform", choices=["upwork", "freelancer"], help="Platform to scrape")
    parser.add_argument("--all", action="store_true", help="Scrape all platforms")
    parser.add_argument("--keywords", required=True, help="Comma-separated keywords")
    parser.add_argument("--max-pages", type=int, default=2)
    parser.add_argument("--top", type=int, help="Show top N jobs from DB")
    args = parser.parse_args()

    keywords = [k.strip() for k in args.keywords.split(",")]

    if args.top:
        for j in get_top_jobs(args.top):
            print(f"  [{j['score']:.0f}] [{j['platform']}] {j['title'][:60]} | {j['budget']} | {j['url']}")
    elif args.all:
        jobs = scrape_all(keywords, args.max_pages)
        print(f"Scraped {len(jobs)} jobs")
        for j in jobs[:10]:
            print(f"  [{j['score']:.0f}] [{j['platform']}] {j['title'][:60]} | {j['budget']}")
    elif args.platform == "upwork":
        jobs = scrape_upwork(keywords, args.max_pages)
        print(f"Scraped {len(jobs)} Upwork jobs")
        for j in jobs[:10]:
            print(f"  [{j['score']:.0f}] {j['title'][:60]} | {j['budget']}")
    elif args.platform == "freelancer":
        jobs = scrape_freelancer(keywords, args.max_pages)
        print(f"Scraped {len(jobs)} Freelancer jobs")
        for j in jobs[:10]:
            print(f"  [{j['score']:.0f}] {j['title'][:60]} | {j['budget']}")
