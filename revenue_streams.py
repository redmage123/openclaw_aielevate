#!/usr/bin/env python3
"""Build all 6 revenue streams in parallel where possible."""
import subprocess
import os
import re
import time
from pathlib import Path
from exceptions import AiElevateError  # TODO: Use specific exception types

env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

def dispatch(agent_id, message, timeout=600):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    session_dir = Path(f"/home/aielevate/.openclaw/agents/{agent_id}/sessions")
    if session_dir.exists():
        for f in session_dir.glob("*.jsonl"):
            f.unlink()
    try:
        proc = subprocess.run(
            ["openclaw", "agent", "--agent", agent_id,
             "--message", message, "--thinking", "low", "--timeout", str(timeout)],
            capture_output=True, text=True, timeout=timeout + 30, env=env)
        output = proc.stdout or ""
        return re.sub(r'\*\[.*?\]\*', '', output, flags=re.DOTALL).strip()
    except subprocess.TimeoutExpired:
        return "TIMEOUT"

def log(msg):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ============================================================================
# 1. AI Company-in-a-Box — build-org.ai landing page
# ============================================================================
log("1/6: AI Company-in-a-Box landing page")
dispatch("build-org-designer",
    "BUILD LANDING PAGE for build-org.ai\n"
    "Project dir: /opt/ai-elevate/build-org/website\n\n"
    "build-org.ai is an AI Organization Factory. Given a business description, "
    "it creates an entire AI-powered company: agents, workflows, email routing, "
    "website, deployment - everything.\n\n"
    "Build a single-page landing site (React + Vite + Tailwind):\n\n"
    "HERO:\n"
    "- Headline: 'Build an AI-Powered Organization in Hours'\n"
    "- Subhead: 'Describe your business. We create the agents, workflows, website, and infrastructure.'\n"
    "- CTA: 'Start Building' (links to https://dashboard.gigforge.ai/build)\n\n"
    "HOW IT WORKS (3 steps):\n"
    "1. Describe your business (name, domain, what it does)\n"
    "2. Our AI architects design the org (departments, agents, roles)\n"
    "3. Everything is deployed: agents, email, website, workflows\n\n"
    "WHAT YOU GET:\n"
    "- 15-25 AI agents with names, bios, and specialized roles\n"
    "- Email routing (inbound emails auto-dispatched to the right agent)\n"
    "- Temporal workflow engine (sales, support, project delivery)\n"
    "- Professional website deployed and live\n"
    "- Knowledge graph and CRM\n"
    "- Ops dashboard for monitoring\n\n"
    "PRICING:\n"
    "- Starter: $5,000 (10 agents, basic workflows)\n"
    "- Professional: $15,000 (25 agents, full workflow suite)\n"
    "- Enterprise: $25,000+ (custom agent count, integrations, support)\n\n"
    "PROOF:\n"
    "- 'We run 4 organizations with 98 AI agents'\n"
    "- Link to live demos: gigforge.ai, techuni.ai, dashboard.gigforge.ai\n\n"
    "FOOTER: Contact form, 'Built by AI Elevate'\n\n"
    "Color scheme: purple/blue gradient (matches the build page on dashboard)\n"
    "Dark mode. Docker Compose for deployment.\n"
    "Include Dockerfile and docker-compose.yml.",
    timeout=600)
log("1/6 done")


# ============================================================================
# 2. Viral case study — 98 agents running 4 companies
# ============================================================================
log("2/6: Case study + viral post")
dispatch("ai-elevate-writer",
    "WRITE A VIRAL CASE STUDY about AI Elevate's autonomous AI company.\n\n"
    "Save to /opt/ai-elevate/ai-elevate/content/case-study-98-agents.md\n\n"
    "Title: 'How We Run 4 Companies With 98 AI Agents and Zero Employees'\n\n"
    "Structure:\n"
    "1. Hook: We built a system where AI agents handle sales emails, write code, "
    "deploy apps, manage customers, send invoices, and write blog posts. Autonomously.\n\n"
    "2. The Numbers:\n"
    "   - 4 organizations (GigForge, TechUni, AI Elevate, Build-Org)\n"
    "   - 98 AI agents with names, bios, and specialized roles\n"
    "   - 10 Temporal workflows (email, build, revision, support, closure, etc.)\n"
    "   - Pair programming: CTO briefs, drivers code, CTO reviews\n"
    "   - Built and deployed a Croatian judiciary website in one session\n"
    "   - Scouting Freelancer/Fiverr every 2 hours for opportunities\n\n"
    "3. How It Works (technical but accessible):\n"
    "   - OpenClaw as the agent framework\n"
    "   - Temporal for durable workflow orchestration\n"
    "   - Email gateway routes inbound to the right agent\n"
    "   - LLM intent classifier decides what workflow to trigger\n"
    "   - Circuit breaker prevents cascade failures\n"
    "   - Knowledge graph tracks customer journeys\n\n"
    "4. Real Examples:\n"
    "   - Customer emails sales@gigforge.ai asking for a web app\n"
    "   - Sales agent responds, qualifies, sends proposal\n"
    "   - Customer accepts, build pipeline kicks off\n"
    "   - PM writes sprint plan, CTO decides tech stack, writes ADRs\n"
    "   - Pair programming: navigator briefs, driver codes, navigator reviews\n"
    "   - QA tests, UX reviews, docs written, DevOps deploys\n"
    "   - Customer gets a preview link with a working app\n\n"
    "5. What We Learned:\n"
    "   - Agents hallucinate names (fixed with team roster injection)\n"
    "   - Build pipelines timeout (fixed with incremental pair steps)\n"
    "   - Deploy activities need retry logic (fixed with 3-attempt auto-diagnosis)\n"
    "   - Honest about what doesn't work yet\n\n"
    "6. Call to Action:\n"
    "   - Want us to build one for your company? build-org.ai\n"
    "   - Want us to build your app? gigforge.ai\n\n"
    "Tone: honest, technical but accessible, no hype. "
    "Write like a developer sharing what they built, not a marketing department.\n"
    "2000-3000 words. Include specific numbers and real examples.\n\n"
    "ALSO write a short LinkedIn version (300 words) saved to:\n"
    "/opt/ai-elevate/ai-elevate/content/linkedin-98-agents.md",
    timeout=600)
log("2/6 done")


# ============================================================================
# 3. AI Agency OS — license page
# ============================================================================
log("3/6: AI Agency OS license offering")
dispatch("gigforge-social",
    "CREATE PRODUCT PAGE CONTENT for 'AI Agency OS'\n\n"
    "Save to /opt/ai-elevate/gigforge/content/ai-agency-os.md\n\n"
    "AI Agency OS is our Temporal-based workflow engine packaged as a product "
    "for other agencies and companies to run their own AI operations.\n\n"
    "Write a product page (markdown) covering:\n\n"
    "HEADLINE: 'AI Agency OS - Run Your Agency With AI Agents'\n\n"
    "WHAT IT INCLUDES:\n"
    "- Email gateway with intent classification\n"
    "- 10 Temporal workflows (email, build, revision, scope change, support, etc.)\n"
    "- Pair programming build pipeline\n"
    "- Ops dashboard with real-time monitoring\n"
    "- Agent dispatch with circuit breaker\n"
    "- Knowledge graph for customer journeys\n"
    "- PostgreSQL-backed everything (dedup, sentiment, milestones)\n"
    "- Organization builder (create new agent teams from a prompt)\n\n"
    "PRICING:\n"
    "- Self-Hosted License: $2,500/year (install on your server, full source)\n"
    "- Managed: $5,000/year (we host and maintain)\n"
    "- Enterprise: $10,000/year (custom workflows, dedicated support)\n\n"
    "WHO IT'S FOR:\n"
    "- Dev agencies wanting to automate project delivery\n"
    "- SaaS companies wanting AI-powered support\n"
    "- Consulting firms wanting to scale operations\n\n"
    "Tone: product page, not blog post. Clear value prop, clear pricing.\n"
    "Under 1000 words.",
    timeout=300)
log("3/6 done")


# ============================================================================
# 4. TechUni course — Build Your Own AI Agency
# ============================================================================
log("4/6: TechUni course outline")
dispatch("techuni-marketing",
    "CREATE COURSE OUTLINE for TechUni: 'Build Your Own AI-Powered Agency'\n\n"
    "Save to /opt/ai-elevate/techuni/courses/ai-agency-course-outline.md\n\n"
    "This course teaches developers how to build what we built - an autonomous "
    "AI agency with agents, workflows, and automated project delivery.\n\n"
    "COURSE DETAILS:\n"
    "- Price: $299 (or $49/mo subscription)\n"
    "- Duration: 8 weeks, self-paced\n"
    "- Level: Intermediate (needs Python/TypeScript, basic Docker)\n"
    "- Format: Video lessons + hands-on projects\n\n"
    "MODULES:\n"
    "1. Week 1: Agent Architecture (OpenClaw, agent files, sessions)\n"
    "2. Week 2: Email Gateway (receive, classify, dispatch)\n"
    "3. Week 3: Workflow Engine (Temporal, activities, retries)\n"
    "4. Week 4: Build Pipeline (sprint plan, spec, pair programming)\n"
    "5. Week 5: Customer Lifecycle (onboarding, revision, support, closure)\n"
    "6. Week 6: Infrastructure (Docker, monitoring, circuit breaker)\n"
    "7. Week 7: Knowledge Graph + Analytics\n"
    "8. Week 8: Deploy Your Agency (go live, first client)\n\n"
    "CAPSTONE PROJECT: Student builds a 10-agent org that handles email, "
    "builds projects, and manages customers.\n\n"
    "Write a compelling course description (sales page copy) + detailed module outline.\n"
    "Include learning outcomes for each module.",
    timeout=300)
log("4/6 done")


# ============================================================================
# 5. Managed AI Operations offering
# ============================================================================
log("5/6: Managed AI Ops offering")
dispatch("gigforge-sales",
    "CREATE SALES PAGE for Managed AI Operations service\n\n"
    "Save to /opt/ai-elevate/gigforge/content/managed-ai-ops.md\n\n"
    "We offer managed AI operations as a monthly service. Instead of hiring "
    "a team, companies get an AI-powered operations layer.\n\n"
    "SERVICES:\n"
    "- AI Customer Support: agents handle tickets, route to humans when needed\n"
    "- AI Content Operations: blog posts, newsletters, social media\n"
    "- AI Dev Operations: automated builds, testing, deployment\n"
    "- AI Sales Operations: lead qualification, proposal drafting, follow-ups\n\n"
    "PRICING:\n"
    "- Starter: $2,000/mo (one department, 5 agents)\n"
    "- Growth: $5,000/mo (3 departments, 15 agents)\n"
    "- Enterprise: $10,000/mo (full company, 30+ agents, custom workflows)\n\n"
    "COMPARISON:\n"
    "- Hiring 5 people: $25,000+/mo in salaries\n"
    "- Our AI ops: $2,000-10,000/mo, runs 24/7, scales instantly\n\n"
    "Write compelling sales copy. Not too long. Clear pricing. Clear value.\n"
    "Include a 'What You Get' section with specific deliverables per tier.\n"
    "Sign off as Braun.",
    timeout=300)
log("5/6 done")


# ============================================================================
# 6. White-label SaaS starters
# ============================================================================
log("6/6: White-label SaaS starters")
dispatch("gigforge-social",
    "CREATE PRODUCT LISTING for White-Label SaaS Starter Kits\n\n"
    "Save to /opt/ai-elevate/gigforge/content/saas-starters.md\n\n"
    "We sell production-ready SaaS starter kits. Full source code, Docker, "
    "tests, documentation. Customer gets a running app in minutes.\n\n"
    "PRODUCTS:\n\n"
    "1. DevOps API Starter ($149)\n"
    "   - TypeScript + Express + Prometheus + Docker\n"
    "   - Health monitoring, rate limiting, security headers\n"
    "   - Jest test suite, CI/CD ready\n"
    "   - Live demo: https://devops-demo.gigforge.ai\n\n"
    "2. SaaS Billing Backend ($299)\n"
    "   - TypeScript + Express + PostgreSQL + Stripe\n"
    "   - Multi-tenant orgs, 3-tier plans, usage metering\n"
    "   - Invoice generation, webhook handling\n"
    "   - Live demo: https://billing-demo.gigforge.ai\n\n"
    "3. Contact Management App ($199)\n"
    "   - React + Node.js + PostgreSQL + Docker\n"
    "   - CRUD, search, CSV import/export, dark mode\n"
    "   - JWT auth, responsive design\n"
    "   - Live demo: https://contacts-demo.gigforge.ai\n\n"
    "4. Full-Stack SaaS Bundle ($499 - all 3 + bonus)\n"
    "   - All 3 starters + integration guide\n"
    "   - 30 days email support\n"
    "   - Priority bug fixes\n\n"
    "Write product listings for Gumroad/CodeCanyon format.\n"
    "Each product: description, features list, what's included, tech stack.\n"
    "Keep it concise and scannable.",
    timeout=300)
log("6/6 done")


# ============================================================================
# Deploy build-org website
# ============================================================================
log("Deploying build-org.ai website...")
dispatch("build-org-devops",
    "DEPLOY the build-org.ai website\n"
    "Project dir: /opt/ai-elevate/build-org/website\n\n"
    "1. Find a free port (4102-4199)\n"
    "2. docker compose up -d --build\n"
    "3. If no docker-compose, use docker build + run\n"
    "4. Verify it responds\n"
    "5. Report URL",
    timeout=300)
log("Deploy done")


# ============================================================================
# Email everything to Braun + Peter
# ============================================================================
log("Emailing results...")
from send_email import send_email

# Collect all content
content_files = {
    "Case Study (98 Agents)": "/opt/ai-elevate/ai-elevate/content/case-study-98-agents.md",
    "LinkedIn Post": "/opt/ai-elevate/ai-elevate/content/linkedin-98-agents.md",
    "AI Agency OS": "/opt/ai-elevate/gigforge/content/ai-agency-os.md",
    "TechUni Course": "/opt/ai-elevate/techuni/courses/ai-agency-course-outline.md",
    "Managed AI Ops": "/opt/ai-elevate/gigforge/content/managed-ai-ops.md",
    "SaaS Starters": "/opt/ai-elevate/gigforge/content/saas-starters.md",
}

body = "6 revenue streams ready for review:\n\n"
for title, fpath in content_files.items():
    p = Path(fpath)
    if p.exists():
        body += f"=== {title} ===\n{p.read_text()[:1500]}\n\n"
    else:
        body += f"=== {title} === (file not created)\n\n"

send_email(
    to="braun.brelin@ai-elevate.ai",
    subject="6 Revenue Streams Ready - Review and Launch",
    body=body[:8000],
    agent_id="operations")

send_email(
    to="peter.munro@ai-elevate.ai",
    subject="New revenue streams - FYI",
    body="Braun is launching 6 new revenue streams. See below for what's coming.\n\n" + body[:5000],
    agent_id="operations")

log("Emails sent")

log("=" * 60)
log("ALL 6 REVENUE STREAMS BUILT")
log("=" * 60)
