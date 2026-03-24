#!/usr/bin/env python3
"""Content Pipeline Workflows — blog posts, newsletters, and news aggregation.

3 workflows:
1. BlogPostWorkflow — daily/near-daily blog posts for GigForge and TechUni
2. NewsletterWorkflow — weekly newsletter compilation for GigForge and TechUni
3. NewsAggregationWorkflow — weekly AI news roundup for AI Elevate

Agents involved:
- GigForge: gigforge-social (Morgan Dell) writes blog + newsletter
- TechUni: techuni-social (Blake Moreno) writes blog + newsletter
- AI Elevate: ai-elevate-content (Luca Fontana) researches, ai-elevate-writer (Noor Abbasi) writes,
              ai-elevate-editor (Cleo Marchetti) edits the weekly AI report
"""
import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from dao import BaseDAO  # TODO: Replace inline DB calls with specific DAOs
from logging_config import get_logger  # Structured JSON logging
from exceptions import AiElevateError  # TODO: Use specific exception types, DatabaseError

sys.path.insert(0, "/home/aielevate")

log = get_logger("content-workflows")

RETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=30))
DB_CONN = dict(
    host=os.environ.get("DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("DB_PORT", "5434")),
    dbname=os.environ.get("DB_NAME", "rag"),
    user=os.environ.get("DB_USER", "rag"),
    password=os.environ.get("DB_PASSWORD", ""),
)


@dataclass
class ContentInput:
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    org: str = "gigforge"  # gigforge, techuni, ai-elevate
    topic: str = ""
    date: str = ""  # YYYY-MM-DD
    week_of: str = ""  # for newsletters: YYYY-MM-DD of the Monday


@dataclass
class ContentResult:
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    status: str = "pending"
    file_path: str = ""
    title: str = ""
    actions: list = field(default_factory=list)


def _dispatch(agent_id, message, timeout=300):
    session_dir = Path(f"/home/aielevate/.openclaw/agents/{agent_id}/sessions")
    if session_dir.exists():
        for f in session_dir.glob("*.jsonl"):
            f.unlink()
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    try:
        proc = subprocess.run(
            ["openclaw", "agent", "--agent", agent_id,
             "--message", message, "--thinking", "low", "--timeout", str(timeout)],
            capture_output=True, text=True, timeout=timeout + 30, env=env)
        output = proc.stdout or ""
        return re.sub(r'\*\[.*?\]\*', '', output, flags=re.DOTALL).strip()
    except subprocess.TimeoutExpired:
        return "TIMEOUT"


def _db():
    import psycopg2
    conn = psycopg2.connect(**DB_CONN)
    conn.autocommit = True
    return conn


# ============================================================================
# DB Tables
# ============================================================================

def init_content_tables():
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    try:
        conn = _db()
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS blog_posts (
            id SERIAL PRIMARY KEY,
            org TEXT NOT NULL,
            title TEXT NOT NULL,
            slug TEXT NOT NULL,
            content TEXT,
            author_agent TEXT,
            editor_agent TEXT,
            status TEXT DEFAULT 'draft',
            published_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS newsletters (
            id SERIAL PRIMARY KEY,
            org TEXT NOT NULL,
            week_of TEXT NOT NULL,
            subject TEXT,
            content TEXT,
            posts_included TEXT,
            status TEXT DEFAULT 'draft',
            sent_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS news_aggregations (
            id SERIAL PRIMARY KEY,
            week_of TEXT NOT NULL,
            title TEXT,
            content TEXT,
            sources TEXT,
            status TEXT DEFAULT 'draft',
            published_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""")
        conn.close()
    except (DatabaseError, Exception) as e:
        log.error(f"Content table init error: {e}")

init_content_tables()


# ============================================================================
# WORKFLOW 1: Blog Post
# ============================================================================

@activity.defn
async def research_blog_topic(input: ContentInput) -> str:
    """Research trending topics and pick one for today's blog post."""
    org_context = {
        "gigforge": "GigForge is an AI-powered development agency. Blog topics: web development, AI/ML, DevOps, SaaS architecture, freelancing tips, portfolio showcase, client case studies, React/Node/Python tutorials.",
        "techuni": "TechUni is an online education platform. Blog topics: AI in education, course creation, e-learning trends, EdTech tools, student engagement, instructor tips, learning science, online certification.",
    }
    agent = f"{input.org}-social"
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch(agent,
        f"RESEARCH BLOG TOPIC for {input.org} — date: {input.date}\n\n"
        f"Context: {org_context.get(input.org, '')}\n\n"
        f"1. Think about what's trending in our industry right now\n"
        f"2. Check what topics would attract our target audience\n"
        f"3. Pick ONE topic that's timely, useful, and shows our expertise\n"
        f"4. Write a brief outline (3-5 key points)\n\n"
        f"Output format:\n"
        f"TOPIC: [the topic]\n"
        f"HEADLINE: [catchy blog title]\n"
        f"OUTLINE:\n- point 1\n- point 2\n- ...\n"
        f"TARGET AUDIENCE: [who this is for]\n"
        f"WORD COUNT TARGET: 800-1200",
        timeout=120))


@activity.defn
async def write_blog_post(input: ContentInput) -> str:
    """Write the full blog post."""
    org_dir = {"gigforge": "/opt/ai-elevate/gigforge", "techuni": "/opt/ai-elevate/techuni"}.get(input.org, "/opt/ai-elevate/gigforge")
    blog_dir = f"{org_dir}/blog"
    Path(blog_dir).mkdir(parents=True, exist_ok=True)

    agent = f"{input.org}-social"
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch(agent,
        f"WRITE BLOG POST for {input.org}\n"
        f"Date: {input.date}\n"
        f"Topic: {input.topic}\n\n"
        f"Write a professional blog post (800-1200 words):\n"
        f"- Engaging opening that hooks the reader\n"
        f"- 3-5 sections with clear subheadings (##)\n"
        f"- Practical advice or insights (not generic fluff)\n"
        f"- Code examples or real-world scenarios where relevant\n"
        f"- Strong conclusion with call-to-action\n"
        f"- Include relevant links to our demos where natural:\n"
        f"  https://devops-demo.gigforge.ai\n"
        f"  https://billing-demo.gigforge.ai\n"
        f"  https://contacts-demo.gigforge.ai\n\n"
        f"Tone: expert but approachable. Not AI-sounding.\n"
        f"Write like a developer sharing hard-won knowledge, not a marketing department.\n\n"
        f"Save to: {blog_dir}/{input.date}-blog-post.md\n"
        f"Format: Markdown with YAML frontmatter (title, date, author, tags, excerpt)",
        timeout=300))


@activity.defn
async def edit_blog_post(input: ContentInput) -> str:
    """Editor reviews and polishes the blog post."""
    org_dir = {"gigforge": "/opt/ai-elevate/gigforge", "techuni": "/opt/ai-elevate/techuni"}.get(input.org, "/opt/ai-elevate/gigforge")
    blog_file = f"{org_dir}/blog/{input.date}-blog-post.md"

    # Use the social agent as editor too (same agent reviews their own work for quality)
    agent = f"{input.org}-social"
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch(agent,
        f"EDIT BLOG POST: {blog_file}\n\n"
        f"Read the blog post at {blog_file}.\n"
        f"Review and improve:\n"
        f"1. Does the opening hook the reader in the first sentence?\n"
        f"2. Is every paragraph necessary? Cut fluff ruthlessly.\n"
        f"3. Are there specific examples or just vague claims?\n"
        f"4. Does it sound human or AI-generated? Fix any AI-ish phrasing.\n"
        f"5. Check: no buzzwords (leverage, synergy, cutting-edge, unlock)\n"
        f"6. Verify links work and are relevant\n"
        f"7. SEO: does the title have a primary keyword? Is the excerpt compelling?\n"
        f"8. Grammar, spelling, punctuation\n\n"
        f"Edit the file directly. Make it something you'd actually want to read.",
        timeout=180))


@activity.defn
async def publish_blog_post(input: ContentInput) -> str:
    """Record the blog post as published in the DB."""
    org_dir = {"gigforge": "/opt/ai-elevate/gigforge", "techuni": "/opt/ai-elevate/techuni"}.get(input.org, "/opt/ai-elevate/gigforge")
    blog_file = Path(f"{org_dir}/blog/{input.date}-blog-post.md")

    title = input.topic
    slug = re.sub(r'[^a-z0-9]+', '-', input.topic.lower()).strip('-')[:60]
    content = blog_file.read_text() if blog_file.exists() else ""

    # Extract title from frontmatter
    title_match = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', content, re.MULTILINE)
    if title_match:
        title = title_match.group(1)

    conn = _db()
    conn.cursor().execute(
        "INSERT INTO blog_posts (org, title, slug, content, author_agent, status, published_at) "
        "VALUES (%s, %s, %s, %s, %s, 'published', NOW())",
        (input.org, title, slug, content[:5000], f"{input.org}-social"))
    conn.close()

    log.info(f"Blog published: {title} for {input.org}")
    return f"Published: {title}"


@workflow.defn
class BlogPostWorkflow:
    """Research topic → write post → edit → publish."""

    @workflow.run
    async def run(self, input: ContentInput) -> ContentResult:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        result = ContentResult()
        tl = timedelta(seconds=360)
        ts = timedelta(seconds=60)

        # Research
        topic_info = await workflow.execute_activity(research_blog_topic, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.actions.append("topic_researched")

        # Extract topic from research output
        topic_match = re.search(r'HEADLINE:\s*(.+)', topic_info)
        topic = topic_match.group(1).strip() if topic_match else f"Blog post for {input.date}"
        write_input = ContentInput(org=input.org, topic=topic, date=input.date)

        # Write
        await workflow.execute_activity(write_blog_post, write_input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.actions.append("written")

        # Edit
        await workflow.execute_activity(edit_blog_post, write_input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.actions.append("edited")

        # Publish
        pub = await workflow.execute_activity(publish_blog_post, write_input,
            start_to_close_timeout=ts, retry_policy=RETRY)
        result.actions.append("published")
        result.title = topic
        result.status = "published"
        return result


# ============================================================================
# WORKFLOW 2: Weekly Newsletter
# ============================================================================

@activity.defn
async def compile_newsletter(input: ContentInput) -> str:
    """Compile the week's blog posts into a newsletter."""
    conn = _db()
    cur = conn.cursor()
    cur.execute(
        "SELECT title, slug, content FROM blog_posts WHERE org=%s AND published_at >= %s::date "
        "AND published_at < (%s::date + INTERVAL '7 days') ORDER BY published_at",
        (input.org, input.week_of, input.week_of))
    posts = cur.fetchall()
    conn.close()

    post_summaries = []
    for title, slug, content in posts:
        # Extract excerpt from frontmatter or first paragraph
        excerpt_match = re.search(r'^excerpt:\s*(.+)$', content or "", re.MULTILINE)
        excerpt = excerpt_match.group(1) if excerpt_match else (content or "")[:200]
        post_summaries.append(f"- **{title}**: {excerpt}")

    posts_text = "\n".join(post_summaries) if post_summaries else "No blog posts this week."

    agent = f"{input.org}-social"
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch(agent,
        f"COMPILE WEEKLY NEWSLETTER for {input.org}\n"
        f"Week of: {input.week_of}\n\n"
        f"Blog posts published this week:\n{posts_text}\n\n"
        f"Write a newsletter email that:\n"
        f"1. Has an engaging subject line\n"
        f"2. Brief intro (2-3 sentences) about what happened this week\n"
        f"3. Summaries of each blog post with links\n"
        f"4. One tip or insight not covered in the blog posts\n"
        f"5. Call to action (visit our site, try a demo, share feedback)\n\n"
        f"Tone: conversational, like writing to a friend who's interested in tech.\n"
        f"Keep it under 500 words — people skim newsletters.\n\n"
        f"Save to /opt/ai-elevate/{input.org}/newsletters/{input.week_of}-newsletter.md",
        timeout=300))


@activity.defn
async def send_newsletter(input: ContentInput) -> bool:
    """Send the newsletter via email (logged, not actually sent without subscriber list)."""
    org_dir = f"/opt/ai-elevate/{input.org}"
    newsletter_file = Path(f"{org_dir}/newsletters/{input.week_of}-newsletter.md")
    content = newsletter_file.read_text() if newsletter_file.exists() else ""

    # Extract subject
    subj_match = re.search(r'^Subject:\s*(.+)$', content, re.MULTILINE)
    subject = subj_match.group(1) if subj_match else f"{input.org.title()} Weekly — {input.week_of}"

    conn = _db()
    conn.cursor().execute(
        "INSERT INTO newsletters (org, week_of, subject, content, status) VALUES (%s, %s, %s, %s, 'compiled')",
        (input.org, input.week_of, subject, content[:5000]))
    conn.close()

    # Send to Braun as preview
    try:
        from send_email import send_email
        send_email(
            to="braun.brelin@ai-elevate.ai",
            subject=f"[Newsletter Preview] {subject}",
            body=content[:3000],
            agent_id=f"{input.org}-social")
    except (DatabaseError, Exception) as e:
        pass

    log.info(f"Newsletter compiled: {subject}")
    return True


@workflow.defn
class NewsletterWorkflow:
    """Compile week's posts → write newsletter → send preview."""

    @workflow.run
    async def run(self, input: ContentInput) -> ContentResult:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        result = ContentResult()
        tl = timedelta(seconds=360)
        ts = timedelta(seconds=60)

        await workflow.execute_activity(compile_newsletter, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.actions.append("compiled")

        await workflow.execute_activity(send_newsletter, input,
            start_to_close_timeout=ts, retry_policy=RETRY)
        result.actions.append("preview_sent")

        result.status = "compiled"
        return result


# ============================================================================
# WORKFLOW 3: AI News Aggregation (AI Elevate — weekly Sunday report)
# ============================================================================

@activity.defn
async def research_ai_news(input: ContentInput) -> str:
    """Research the week's AI news."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch(
        "ai-elevate-researcher",
        f"WEEKLY AI NEWS RESEARCH — week of {input.week_of}\n\n"
        f"Research the most important AI/ML news from this past week.\n"
        f"Search for:\n"
        f"- Major model releases (OpenAI, Anthropic, Google, Meta, Mistral)\n"
        f"- Significant research papers\n"
        f"- Industry moves (acquisitions, partnerships, funding rounds)\n"
        f"- Regulatory developments (EU AI Act, US executive orders)\n"
        f"- Open source releases\n"
        f"- Notable applications or breakthroughs\n"
        f"- Developer tools and frameworks\n\n"
        f"For each item report:\n"
        f"- What happened\n"
        f"- Why it matters\n"
        f"- Source URL\n\n"
        f"Save research to /opt/ai-elevate/ai-elevate/reports/{input.week_of}-research.md\n"
        f"Aim for 10-15 items.",
        timeout=300))


@activity.defn
async def write_ai_report(input: ContentInput) -> str:
    """Writer turns research into a polished report."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch(
        "ai-elevate-writer",
        f"WRITE AI WEEKLY REPORT — week of {input.week_of}\n\n"
        f"Read the research at /opt/ai-elevate/ai-elevate/reports/{input.week_of}-research.md\n\n"
        f"Write the 'AI Elevate Weekly Report' covering this week's AI news:\n"
        f"1. Executive summary (3-4 sentences — the week in AI)\n"
        f"2. Top story (200-300 words on the biggest development)\n"
        f"3. Quick hits (5-8 bullet points of other notable news)\n"
        f"4. Research spotlight (one interesting paper, explained simply)\n"
        f"5. Tool of the week (one new tool or framework worth trying)\n"
        f"6. What to watch next week (upcoming events, expected releases)\n\n"
        f"Tone: informed, concise, opinionated. Like a smart colleague briefing you.\n"
        f"Total: 1000-1500 words.\n\n"
        f"Save to /opt/ai-elevate/ai-elevate/reports/{input.week_of}-weekly-report.md",
        timeout=300))


@activity.defn
async def edit_ai_report(input: ContentInput) -> str:
    """Editor reviews the report."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch(
        "ai-elevate-editor",
        f"EDIT AI WEEKLY REPORT — week of {input.week_of}\n\n"
        f"Read /opt/ai-elevate/ai-elevate/reports/{input.week_of}-weekly-report.md\n\n"
        f"Review:\n"
        f"1. Accuracy — are the facts correct? Dates, names, numbers.\n"
        f"2. Clarity — can a non-specialist understand the top story?\n"
        f"3. Brevity — cut anything that doesn't add value\n"
        f"4. Tone — informed and concise, not breathless or hype-driven\n"
        f"5. Sources — are claims attributed?\n"
        f"6. No AI-speak — remove 'groundbreaking', 'revolutionize', 'game-changer'\n\n"
        f"Edit the file directly.",
        timeout=180))


@activity.defn
async def publish_ai_report(input: ContentInput) -> bool:
    """Publish the report — save to DB and email to Braun."""
    report_file = Path(f"/opt/ai-elevate/ai-elevate/reports/{input.week_of}-weekly-report.md")
    content = report_file.read_text() if report_file.exists() else ""

    title = f"AI Elevate Weekly — {input.week_of}"
    title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
    if title_match:
        title = title_match.group(1)

    conn = _db()
    conn.cursor().execute(
        "INSERT INTO news_aggregations (week_of, title, content, status, published_at) "
        "VALUES (%s, %s, %s, 'published', NOW())",
        (input.week_of, title, content[:10000]))
    conn.close()

    # Email to Braun
    try:
        from send_email import send_email
        send_email(
            to="braun.brelin@ai-elevate.ai",
            subject=title,
            body=content[:5000],
            agent_id="ai-elevate-content")
    except (DatabaseError, Exception) as e:
        pass

    log.info(f"AI Weekly published: {title}")
    return True


@workflow.defn
class NewsAggregationWorkflow:
    """Research AI news → write report → edit → publish + email."""

    @workflow.run
    async def run(self, input: ContentInput) -> ContentResult:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        result = ContentResult()
        tl = timedelta(seconds=360)
        ts = timedelta(seconds=60)

        # Research
        await workflow.execute_activity(research_ai_news, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.actions.append("researched")

        # Write
        await workflow.execute_activity(write_ai_report, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.actions.append("written")

        # Edit
        await workflow.execute_activity(edit_ai_report, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.actions.append("edited")

        # Publish
        await workflow.execute_activity(publish_ai_report, input,
            start_to_close_timeout=ts, retry_policy=RETRY)
        result.actions.append("published")

        result.status = "published"
        return result


# ============================================================================
# Client functions + cron triggers
# ============================================================================

async def start_blog_post(org="gigforge", date=None):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    from temporalio.client import Client
    client = await Client.connect("localhost:7233")
    d = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    inp = ContentInput(org=org, date=d)
    h = await client.start_workflow(BlogPostWorkflow.run, inp,
        id=f"blog-{org}-{d}", task_queue="content-workflows",
        execution_timeout=timedelta(hours=1))
    return {"workflow_id": h.id}

async def start_newsletter(org="gigforge", week_of=None):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    from temporalio.client import Client
    client = await Client.connect("localhost:7233")
    w = week_of or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    inp = ContentInput(org=org, week_of=w)
    h = await client.start_workflow(NewsletterWorkflow.run, inp,
        id=f"newsletter-{org}-{w}", task_queue="content-workflows",
        execution_timeout=timedelta(hours=1))
    return {"workflow_id": h.id}

async def start_ai_weekly(week_of=None):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    from temporalio.client import Client
    client = await Client.connect("localhost:7233")
    w = week_of or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    inp = ContentInput(org="ai-elevate", week_of=w)
    h = await client.start_workflow(NewsAggregationWorkflow.run, inp,
        id=f"ai-weekly-{w}", task_queue="content-workflows",
        execution_timeout=timedelta(hours=2))
    return {"workflow_id": h.id}
