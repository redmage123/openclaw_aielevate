#!/usr/bin/env python3
"""Sweet Pea Gardens — Skills & Workflows

Workflows:
1. DailyBlogWorkflow — Violet writes a garden journal post daily (weekdays)
2. WeeklyNewsletterWorkflow — Poppy compiles + sends weekly Klaviyo newsletter
3. SocialMediaWorkflow — Daisy creates daily Instagram/Facebook content
4. CustomerResponseWorkflow — Ivy handles inbound customer questions
5. InventoryCheckWorkflow — Basil audits Shopify inventory weekly
6. SeasonalPlannerWorkflow — Sage runs monthly seasonal planning
7. ContentCalendarWorkflow — Rosemary coordinates the weekly content plan

Skills (agent capabilities):
- Growing guide generation (by variety, by zone)
- Planting date calculator (zone + variety → dates)
- Customer FAQ responder (germination, pests, trellising, harvesting)
- Product description writer (for new seed varieties)
- Social media caption generator (from photo descriptions)
- Email subject line generator (seasonal, tested formats)
- Inventory alert system (low stock, out of stock, pre-order triggers)
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

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from dao import BaseDAO  # TODO: Replace inline DB calls with specific DAOs
from logging_config import get_logger  # Structured JSON logging
from exceptions import AiElevateError  # TODO: Use specific exception types, DatabaseError

sys.path.insert(0, "/home/aielevate")

log = get_logger("sweetpea-workflows")

RETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=30))
DB_CONN = dict(
    host=os.environ.get("DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("DB_PORT", "5434")),
    dbname=os.environ.get("DB_NAME", "rag"),
    user=os.environ.get("DB_USER", "rag"),
    password=os.environ.get("DB_PASSWORD", ""),
)
ORG_DIR = "/opt/ai-elevate/sweetpea"


@dataclass
class SweetPeaInput:
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    task_type: str = ""  # blog, newsletter, social, customer, inventory, planner, calendar
    date: str = ""
    content: str = ""  # customer message, photo description, etc.
    zone: str = ""  # USDA hardiness zone
    variety: str = ""  # sweet pea variety


@dataclass
class SweetPeaResult:
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    status: str = "pending"
    output_file: str = ""
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

def init_tables():
    """"""
    try:
        conn = _db()
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS sweetpea_content (
            id SERIAL PRIMARY KEY,
            content_type TEXT NOT NULL,
            title TEXT,
            body TEXT,
            author_agent TEXT,
            status TEXT DEFAULT 'draft',
            scheduled_date DATE,
            published_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS sweetpea_inventory (
            id SERIAL PRIMARY KEY,
            variety TEXT NOT NULL,
            packets_available INTEGER DEFAULT 0,
            packets_reserved INTEGER DEFAULT 0,
            status TEXT DEFAULT 'in_stock',
            last_checked TIMESTAMPTZ DEFAULT NOW(),
            notes TEXT
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS sweetpea_seasonal_plan (
            id SERIAL PRIMARY KEY,
            month INTEGER,
            year INTEGER,
            tasks TEXT,
            status TEXT DEFAULT 'planned',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""")
        conn.close()
    except Exception as e:
        log.error(f"Table init error: {e}")

init_tables()


# ============================================================================
# WORKFLOW 1: Daily Blog Post
# ============================================================================

@activity.defn
async def research_garden_topic(input: SweetPeaInput) -> str:
    """Violet researches a topic for today's Garden Journal post."""
    month = datetime.now().strftime("%B")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch(
        "sweetpea-content",
        f"RESEARCH BLOG TOPIC for Sweet Pea Gardens — {input.date}\n\n"
        f"Current month: {month}\n\n"
        f"Pick a topic that's timely for this season. Ideas by season:\n"
        f"- Winter: planning your sweet pea garden, choosing varieties, seed soaking techniques\n"
        f"- Early Spring: starting seeds indoors, hardening off, soil preparation\n"
        f"- Spring: transplanting, trellising, companion planting, pest management\n"
        f"- Summer: growing tips, deadheading for more blooms, seed saving\n"
        f"- Fall: harvesting seeds, end-of-season care, planning next year\n\n"
        f"Also consider: variety spotlights, customer success stories, behind-the-scenes farm life,\n"
        f"flower arranging with sweet peas, the history/meaning of sweet peas, sustainable gardening.\n\n"
        f"Output: TOPIC, HEADLINE, OUTLINE (3-5 points), TARGET AUDIENCE",
        timeout=120))


@activity.defn
async def write_garden_blog(input: SweetPeaInput) -> str:
    """Violet writes the full blog post."""
    blog_dir = f"{ORG_DIR}/blog"
    Path(blog_dir).mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch(
        "sweetpea-content",
        f"WRITE BLOG POST for Sweet Pea Gardens Garden Journal\n"
        f"Date: {input.date}\n"
        f"Topic: {input.content}\n\n"
        f"Write a warm, knowledgeable blog post (600-1000 words):\n"
        f"- Opening that draws gardeners in (personal anecdote or seasonal observation)\n"
        f"- Practical, actionable advice (not generic fluff)\n"
        f"- Specific sweet pea variety mentions where relevant\n"
        f"- Growing tips tied to the current season\n"
        f"- Beautiful descriptive language — sweet peas are romantic flowers\n"
        f"- Closing with encouragement and a soft CTA (visit the shop, sign up for newsletter)\n\n"
        f"Tone: like a letter from a gardener friend. Warm, not corporate.\n"
        f"Include: variety names, specific growing advice, seasonal context.\n\n"
        f"Save to: {blog_dir}/{input.date}-garden-journal.md\n"
        f"Format: Markdown with YAML frontmatter (title, date, author: Violet Ashby, tags, excerpt)",
        timeout=300))


@activity.defn
async def publish_garden_blog(input: SweetPeaInput) -> bool:
    """Record the blog post in DB."""
    blog_file = Path(f"{ORG_DIR}/blog/{input.date}-garden-journal.md")
    content = blog_file.read_text() if blog_file.exists() else ""
    title_match = re.search(r'^title:\s*(.+)$', content, re.MULTILINE)
    title = title_match.group(1).strip('"\'') if title_match else f"Garden Journal — {input.date}"
    conn = _db()
    conn.cursor().execute(
        "INSERT INTO sweetpea_content (content_type, title, body, author_agent, status, published_at) "
        "VALUES ('blog', %s, %s, 'sweetpea-content', 'published', NOW())",
        (title, content[:5000]))
    conn.close()
    return True


@workflow.defn
class DailyBlogWorkflow:
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    @workflow.run
    async def run(self, input: SweetPeaInput) -> SweetPeaResult:
        """"""
        result = SweetPeaResult()
        tl = timedelta(seconds=360)
        ts = timedelta(seconds=60)
        topic = await workflow.execute_activity(research_garden_topic, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.actions.append("topic_researched")
        write_input = SweetPeaInput(date=input.date, content=topic[:500])
        _write_garden_blog_result = await workflow.execute_activity(write_garden_blog, write_input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.actions.append("written")
        _publish_garden_blog_result = await workflow.execute_activity(publish_garden_blog, write_input,
            start_to_close_timeout=ts, retry_policy=RETRY)
        result.actions.append("published")
        result.status = "published"
        return result


# ============================================================================
# WORKFLOW 2: Social Media Content
# ============================================================================

@activity.defn
async def create_social_post(input: SweetPeaInput) -> str:
    """Daisy creates social media content."""
    month = datetime.now().strftime("%B")
    social_dir = f"{ORG_DIR}/social"
    Path(social_dir).mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch(
        "sweetpea-social",
        f"CREATE SOCIAL MEDIA POST for Sweet Pea Gardens — {input.date}\n"
        f"Month: {month}\n\n"
        f"Create an Instagram/Facebook post:\n"
        f"1. Caption (150-250 chars for Instagram, can be longer for Facebook)\n"
        f"2. Hashtags (10-15 relevant: #sweetpeas #heirloomseeds #cottagegarden etc.)\n"
        f"3. Photo suggestion (what to photograph today)\n"
        f"4. Best posting time suggestion\n"
        f"5. Story idea (quick 15-sec behind-the-scenes concept)\n\n"
        f"Content types to rotate through the week:\n"
        f"- Mon: Variety spotlight (photo of a specific sweet pea variety)\n"
        f"- Tue: Growing tip (practical advice with photo)\n"
        f"- Wed: Behind the scenes (farm life, seed processing, packing orders)\n"
        f"- Thu: Customer feature (repost customer garden photos with permission)\n"
        f"- Fri: Weekend inspiration (arrangement ideas, garden planning)\n"
        f"- Sat: Garden question (engage followers with a question)\n"
        f"- Sun: Seasonal beauty (best photo of the week)\n\n"
        f"Save to: {social_dir}/{input.date}-social.md",
        timeout=180))


@activity.defn
async def log_social_content(input: SweetPeaInput) -> bool:
    """"""
    conn = _db()
    conn.cursor().execute(
        "INSERT INTO sweetpea_content (content_type, title, author_agent, status, scheduled_date) "
        "VALUES ('social', %s, 'sweetpea-social', 'scheduled', %s)",
        (f"Social post — {input.date}", input.date))
    conn.close()
    return True


@workflow.defn
class SocialMediaWorkflow:
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    @workflow.run
    async def run(self, input: SweetPeaInput) -> SweetPeaResult:
        """"""
        result = SweetPeaResult()
        tl = timedelta(seconds=360)
        ts = timedelta(seconds=60)
        _create_social_post_result = await workflow.execute_activity(create_social_post, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.actions.append("social_created")
        _log_social_content_result = await workflow.execute_activity(log_social_content, input,
            start_to_close_timeout=ts, retry_policy=RETRY)
        result.actions.append("logged")
        result.status = "scheduled"
        return result


# ============================================================================
# WORKFLOW 3: Weekly Newsletter
# ============================================================================

@activity.defn
async def compile_garden_newsletter(input: SweetPeaInput) -> str:
    """Poppy compiles the weekly newsletter."""
    newsletter_dir = f"{ORG_DIR}/newsletters"
    Path(newsletter_dir).mkdir(parents=True, exist_ok=True)
    conn = _db()
    cur = conn.cursor()
    cur.execute(
        "SELECT title, body FROM sweetpea_content WHERE content_type='blog' "
        "AND published_at >= %s::date AND published_at < (%s::date + INTERVAL '7 days') "
        "ORDER BY published_at", (input.date, input.date))
    posts = cur.fetchall()
    conn.close()
    post_list = "\n".join([f"- {t}" for t, _ in posts]) if posts else "No blog posts this week."

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch(
        "sweetpea-email",
        f"WRITE WEEKLY NEWSLETTER for Sweet Pea Gardens\n"
        f"Week of: {input.date}\n\n"
        f"Blog posts this week:\n{post_list}\n\n"
        f"Write a Klaviyo-style email newsletter:\n"
        f"1. Subject line (A/B test: one curiosity-driven, one benefit-driven)\n"
        f"2. Preview text (40-60 chars)\n"
        f"3. Personal greeting from the Sweet Pea Gardens family\n"
        f"4. This week in the garden (seasonal update, what we're doing on the farm)\n"
        f"5. Blog post summaries with links\n"
        f"6. Featured variety of the week (with shop link)\n"
        f"7. Quick growing tip (one actionable takeaway)\n"
        f"8. Closing with warmth\n\n"
        f"Tone: like a letter from a friend who gardens. Personal, not marketing.\n"
        f"Keep under 500 words — people skim newsletters.\n\n"
        f"Save to: {newsletter_dir}/{input.date}-newsletter.md",
        timeout=300))


@activity.defn
async def log_newsletter(input: SweetPeaInput) -> bool:
    """"""
    conn = _db()
    conn.cursor().execute(
        "INSERT INTO sweetpea_content (content_type, title, author_agent, status) "
        "VALUES ('newsletter', %s, 'sweetpea-email', 'compiled')",
        (f"Weekly Newsletter — {input.date}",))
    conn.close()
    return True


@workflow.defn
class WeeklyNewsletterWorkflow:
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    @workflow.run
    async def run(self, input: SweetPeaInput) -> SweetPeaResult:
        """"""
        result = SweetPeaResult()
        tl = timedelta(seconds=360)
        ts = timedelta(seconds=60)
        _compile_garden_newsletter_result = await workflow.execute_activity(compile_garden_newsletter, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.actions.append("compiled")
        _log_newsletter_result = await workflow.execute_activity(log_newsletter, input,
            start_to_close_timeout=ts, retry_policy=RETRY)
        result.actions.append("logged")
        result.status = "compiled"
        return result


# ============================================================================
# WORKFLOW 4: Customer Response
# ============================================================================

@activity.defn
async def respond_to_customer(input: SweetPeaInput) -> str:
    """Ivy responds to a customer question."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch(
        "sweetpea-customer",
        f"CUSTOMER QUESTION — respond with warmth and expertise.\n\n"
        f"Customer message:\n{input.content}\n\n"
        f"Respond as Ivy Callahan, Customer Care at Sweet Pea Gardens.\n"
        f"- Be warm, patient, encouraging\n"
        f"- Give specific, actionable advice\n"
        f"- Mention specific varieties if relevant\n"
        f"- If they're having trouble, reassure them — sweet peas can be finicky\n"
        f"- If it's an order issue, be solution-oriented\n"
        f"- Include a relevant growing tip they didn't ask for (bonus value)\n"
        f"- Sign as Ivy",
        timeout=180))


@workflow.defn
class CustomerResponseWorkflow:
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    @workflow.run
    async def run(self, input: SweetPeaInput) -> SweetPeaResult:
        """"""
        result = SweetPeaResult()
        response = await workflow.execute_activity(respond_to_customer, input,
            start_to_close_timeout=timedelta(seconds=240, retry_policy=RETRY), retry_policy=RETRY)
        result.actions.append("responded")
        result.output_file = response[:500]
        result.status = "responded"
        return result


# ============================================================================
# WORKFLOW 5: Inventory Check
# ============================================================================

@activity.defn
async def audit_inventory(input: SweetPeaInput) -> str:
    """Basil audits Shopify inventory and flags issues."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch(
        "sweetpea-shop",
        f"WEEKLY INVENTORY AUDIT for Sweet Pea Gardens — {input.date}\n\n"
        f"Review the current inventory state:\n"
        f"1. Which varieties are running low (< 20 packets)?\n"
        f"2. Which varieties are sold out?\n"
        f"3. Any varieties that should be put on pre-order for next season?\n"
        f"4. Pricing review — any adjustments needed for rare/popular varieties?\n"
        f"5. Product listing quality — any descriptions that need updating?\n\n"
        f"Write your audit report to {ORG_DIR}/inventory/{input.date}-audit.md\n\n"
        f"Flag anything that needs immediate attention.",
        timeout=180))


@workflow.defn
class InventoryCheckWorkflow:
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    @workflow.run
    async def run(self, input: SweetPeaInput) -> SweetPeaResult:
        """"""
        result = SweetPeaResult()
        Path(f"{ORG_DIR}/inventory").mkdir(parents=True, exist_ok=True)
        _audit_inventory_result = await workflow.execute_activity(audit_inventory, input,
            start_to_close_timeout=timedelta(seconds=240, retry_policy=RETRY), retry_policy=RETRY)
        result.actions.append("audited")
        result.status = "completed"
        return result


# ============================================================================
# WORKFLOW 6: Seasonal Planner
# ============================================================================

@activity.defn
async def monthly_seasonal_plan(input: SweetPeaInput) -> str:
    """Sage creates the monthly operational plan."""
    month = datetime.strptime(input.date, "%Y-%m-%d").strftime("%B %Y")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch(
        "sweetpea-planner",
        f"MONTHLY SEASONAL PLAN for Sweet Pea Gardens — {month}\n"
        f"Location: Chehalis, WA (Zone 8b)\n\n"
        f"Create the monthly operations plan covering:\n\n"
        f"GROWING:\n"
        f"- What to plant/sow this month\n"
        f"- What to harvest\n"
        f"- Key growing tasks (fertilizing, pest watch, trellising, deadheading)\n"
        f"- Weather considerations for Chehalis\n\n"
        f"BUSINESS:\n"
        f"- Shipping schedule (when to start/stop shipping live plants)\n"
        f"- Inventory: which varieties to prepare/process\n"
        f"- Marketing focus for this month\n"
        f"- Email campaigns to schedule\n"
        f"- Social media themes\n\n"
        f"CONTENT:\n"
        f"- Blog post topics for the month (4-5)\n"
        f"- Newsletter themes\n"
        f"- Photography priorities (what's blooming, what to capture)\n\n"
        f"Save to: {ORG_DIR}/plans/{month.replace(' ', '-').lower()}-plan.md",
        timeout=300))


@workflow.defn
class SeasonalPlannerWorkflow:
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    @workflow.run
    async def run(self, input: SweetPeaInput) -> SweetPeaResult:
        """"""
        result = SweetPeaResult()
        Path(f"{ORG_DIR}/plans").mkdir(parents=True, exist_ok=True)
        _monthly_seasonal_plan_result = await workflow.execute_activity(monthly_seasonal_plan, input,
            start_to_close_timeout=timedelta(seconds=360, retry_policy=RETRY), retry_policy=RETRY)
        result.actions.append("planned")
        result.status = "completed"
        return result


# ============================================================================
# WORKFLOW 7: Weekly Content Calendar
# ============================================================================

@activity.defn
async def create_content_calendar(input: SweetPeaInput) -> str:
    """Rosemary coordinates the weekly content plan across all agents."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch(
        "sweetpea",
        f"WEEKLY CONTENT CALENDAR for Sweet Pea Gardens — week of {input.date}\n\n"
        f"Coordinate the content plan for this week across the team:\n\n"
        f"For each day (Mon-Sun), assign:\n"
        f"- Blog post (Violet) — topic and angle\n"
        f"- Social media (Daisy) — platform, content type, photo idea\n"
        f"- Email (Poppy) — any campaign or flow to send\n"
        f"- Customer focus (Ivy) — FAQ to proactively address\n"
        f"- Shop (Basil) — any product updates, new listings, sale to run\n\n"
        f"Ensure variety across the week — don't repeat content types.\n"
        f"Tie everything to the current season.\n"
        f"Consider any upcoming events: holidays, planting deadlines, sale dates.\n\n"
        f"Save to: {ORG_DIR}/calendars/week-{input.date}.md",
        timeout=300))


@workflow.defn
class ContentCalendarWorkflow:
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    @workflow.run
    async def run(self, input: SweetPeaInput) -> SweetPeaResult:
        """"""
        result = SweetPeaResult()
        Path(f"{ORG_DIR}/calendars").mkdir(parents=True, exist_ok=True)
        _create_content_calendar_result = await workflow.execute_activity(create_content_calendar, input,
            start_to_close_timeout=timedelta(seconds=360, retry_policy=RETRY), retry_policy=RETRY)
        result.actions.append("calendar_created")
        result.status = "completed"
        return result


# ============================================================================
# Skills (standalone functions, not workflows)
# ============================================================================

def generate_growing_guide(variety, zone="8b"):
    """Generate a growing guide for a specific sweet pea variety and zone."""
    return _dispatch("sweetpea-content",
        f"Write a growing guide for {variety} sweet peas in USDA Zone {zone}.\n"
        f"Cover: when to sow, soaking, soil prep, trellising, spacing, watering, "
        f"feeding, common problems, harvesting seeds. 400-600 words.")


def calculate_planting_dates(zone):
    """Calculate planting dates for a specific zone."""
    return _dispatch("sweetpea-planner",
        f"Calculate sweet pea planting dates for USDA Zone {zone}.\n"
        f"Include: indoor sowing date, last frost date, transplant date, "
        f"direct sow date, expected bloom start, seed harvest window.")


def write_product_description(variety, traits):
    """Write a Shopify product description for a seed variety."""
    return _dispatch("sweetpea-shop",
        f"Write a Shopify product description for {variety} sweet peas.\n"
        f"Traits: {traits}\n"
        f"Include: bloom description, fragrance notes, growing height, best uses, "
        f"packet contents (15-20 seeds). Warm, evocative language. Under 200 words.")


def generate_caption(photo_description):
    """Generate an Instagram caption from a photo description."""
    return _dispatch("sweetpea-social",
        f"Write an Instagram caption for this photo: {photo_description}\n"
        f"Include: caption (150-250 chars), 10-15 hashtags, posting suggestion.")


def answer_faq(question):
    """Answer a common customer FAQ about sweet peas."""
    return _dispatch("sweetpea-customer",
        f"Answer this sweet pea growing question from a customer:\n{question}\n"
        f"Be warm, specific, and encouraging. Include a bonus tip.")


# ============================================================================
# Client functions
# ============================================================================

async def start_daily_blog(date=None):
    """"""
    from temporalio.client import Client
    client = await Client.connect("localhost:7233")
    d = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    inp = SweetPeaInput(task_type="blog", date=d)
    h = await client.start_workflow(DailyBlogWorkflow.run, inp,
        id=f"sweetpea-blog-{d}", task_queue="sweetpea-workflows",
        execution_timeout=timedelta(hours=1))
    return {"workflow_id": h.id}


async def start_social_post(date=None):
    """"""
    from temporalio.client import Client
    client = await Client.connect("localhost:7233")
    d = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    inp = SweetPeaInput(task_type="social", date=d)
    h = await client.start_workflow(SocialMediaWorkflow.run, inp,
        id=f"sweetpea-social-{d}", task_queue="sweetpea-workflows",
        execution_timeout=timedelta(hours=1))
    return {"workflow_id": h.id}


async def start_newsletter(date=None):
    """"""
    from temporalio.client import Client
    client = await Client.connect("localhost:7233")
    d = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    inp = SweetPeaInput(task_type="newsletter", date=d)
    h = await client.start_workflow(WeeklyNewsletterWorkflow.run, inp,
        id=f"sweetpea-newsletter-{d}", task_queue="sweetpea-workflows",
        execution_timeout=timedelta(hours=1))
    return {"workflow_id": h.id}


async def start_inventory_check(date=None):
    """"""
    from temporalio.client import Client
    client = await Client.connect("localhost:7233")
    d = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    inp = SweetPeaInput(task_type="inventory", date=d)
    h = await client.start_workflow(InventoryCheckWorkflow.run, inp,
        id=f"sweetpea-inventory-{d}", task_queue="sweetpea-workflows",
        execution_timeout=timedelta(hours=1))
    return {"workflow_id": h.id}


async def start_seasonal_plan(date=None):
    """"""
    from temporalio.client import Client
    client = await Client.connect("localhost:7233")
    d = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    inp = SweetPeaInput(task_type="planner", date=d)
    h = await client.start_workflow(SeasonalPlannerWorkflow.run, inp,
        id=f"sweetpea-plan-{d}", task_queue="sweetpea-workflows",
        execution_timeout=timedelta(hours=1))
    return {"workflow_id": h.id}


async def start_content_calendar(date=None):
    """"""
    from temporalio.client import Client
    client = await Client.connect("localhost:7233")
    d = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    inp = SweetPeaInput(task_type="calendar", date=d)
    h = await client.start_workflow(ContentCalendarWorkflow.run, inp,
        id=f"sweetpea-calendar-{d}", task_queue="sweetpea-workflows",
        execution_timeout=timedelta(hours=1))
    return {"workflow_id": h.id}


async def handle_customer_question(question):
    """"""
    from temporalio.client import Client
    client = await Client.connect("localhost:7233")
    inp = SweetPeaInput(task_type="customer", content=question,
                        date=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    h = await client.start_workflow(CustomerResponseWorkflow.run, inp,
        id=f"sweetpea-customer-{int(time.time())}", task_queue="sweetpea-workflows",
        execution_timeout=timedelta(minutes=10))
    return {"workflow_id": h.id}
