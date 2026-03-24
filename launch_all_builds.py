#!/usr/bin/env python3
"""Launch all 5 scaffold projects through the full Temporal build workflow.


log = get_logger("launch_all_builds")

Each project goes through the complete agile sprint lifecycle:
  PM sprint plan → Engineering consensus (TECH_STACK + ADRs) →
  Software spec (functional + technical) → UX design →
  Pair programming build (CTO briefs, drivers code, CTO reviews) →
  QA test → UX review → Documentation → Deploy

With Plane tickets, KG updates, Postgres milestones, CMS archival.
"""
import asyncio
import time
import sys
import datetime

sys.path.insert(0, "/home/aielevate")

from temporalio.client import Client
from build_workflow import ProjectBuildWorkflow, BuildInput
from logging_config import get_logger

PROJECTS = [
    {
        "slug": "todo-rest-api",
        "title": "Todo REST API",
        "email": "internal@gigforge.ai",
        "requirements": (
            "Production-ready REST API demo for GigForge portfolio. "
            "TypeScript, Express 4, PostgreSQL 16, JWT auth, Zod validation, Jest tests. "
            "Must have: CRUD endpoints, auth (register/login), input validation, error handling, "
            "Dockerfile, docker-compose.yml, landing page at root showing API docs. "
            "Read existing README.md for full spec. Complete any missing functionality. "
            "This is an internal portfolio project — no billing."
        ),
    },
    {
        "slug": "ai-chat-widget",
        "title": "AI Chat Widget",
        "email": "internal@gigforge.ai",
        "requirements": (
            "RAG chat system with embeddable vanilla JS widget for GigForge portfolio. "
            "Express, PostgreSQL + pgvector for vector search, OpenAI embeddings (mock when no key), "
            "JWT auth, document upload + chunking + embedding, semantic search + LLM response. "
            "Must have: upload API, chat API, widget.js embed script, landing page demo. "
            "Read existing README.md for full spec. Complete any missing functionality. "
            "This is an internal portfolio project — no billing."
        ),
    },
    {
        "slug": "job-board",
        "title": "Job Board",
        "email": "internal@gigforge.ai",
        "requirements": (
            "Full-stack job board for GigForge portfolio. "
            "React 19 + Vite frontend, Express 4 backend, PostgreSQL 16. "
            "RBAC (admin/employer/candidate), full-text search, JWT auth, "
            "job posting CRUD, application system, employer dashboard, candidate profiles. "
            "Docker Compose (3 services: db, api, web). "
            "Read existing README.md for full spec. Complete any missing functionality. "
            "This is an internal portfolio project — no billing."
        ),
    },
    {
        "slug": "client-portal",
        "title": "GigForge Client Portal",
        "email": "internal@gigforge.ai",
        "requirements": (
            "Web dashboard for GigForge clients — internal tool. "
            "Clients can: see project progress/sprint status, view deliverables, "
            "download artifacts, communicate with project team, access quality reports. "
            "React 19 + Tailwind CSS frontend, Express/Node.js backend, PostgreSQL. "
            "JWT auth, role-based access (client/admin). Docker Compose. "
            "This is an internal portfolio project — no billing."
        ),
    },
    {
        "slug": "cryptoadvisor-web",
        "title": "CryptoAdvisor Web Dashboard",
        "email": "internal@gigforge.ai",
        "requirements": (
            "Web frontend for CryptoAdvisor — portfolio demo. "
            "Dashboard showing: crypto portfolio overview, price charts (candlestick), "
            "AI-powered trading signals, alerts, portfolio allocation pie chart, "
            "transaction history, watchlist. "
            "React 19 + Tailwind CSS + Recharts/Chart.js for charts. "
            "Mock API data (no real crypto API needed). Docker. "
            "Dark theme by default — financial dashboard aesthetic. "
            "This is an internal portfolio project — no billing."
        ),
    },
]


async def main():
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    client = await Client.connect("localhost:7233")
    started = []

    for proj in PROJECTS:
        inp = BuildInput(
            customer_email=proj["email"],
            project_title=proj["title"],
            project_slug=proj["slug"],
            org="gigforge",
            requirements=proj["requirements"],
        )

        wf_id = f"build-portfolio-{proj['slug']}-{int(time.time())}"
        handle = await client.start_workflow(
            ProjectBuildWorkflow.run, inp,
            id=wf_id,
            task_queue="build-projects",
            execution_timeout=datetime.timedelta(hours=3),
        )
        started.append((proj["title"], wf_id))
        log.info("Started: %s -> %s", extra={"wf_id": wf_id})

        # Stagger launches by 10s to avoid overwhelming the agent queue
        await asyncio.sleep(10)

    log.info("\n{len(started)} workflows launched")
    log.info("\nMonitor with:")
    for title, wf_id in started:
        log.info("  python3 /home/aielevate/monitor_workflow.py %s", extra={"wf_id": wf_id})


asyncio.run(main())
