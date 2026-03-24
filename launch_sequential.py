#!/usr/bin/env python3
"""Launch portfolio builds SEQUENTIALLY — one at a time.

log = get_logger("launch_sequential")

Wait for each to complete before starting the next.
5 parallel workflows overwhelmed the agent queue.
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
        "slug": "ai-chat-widget",
        "title": "AI Chat Widget",
        "requirements": "RAG chat system with embeddable widget. Express, PostgreSQL + pgvector, OpenAI embeddings, JWT auth. Read existing README.md. Internal portfolio — no billing. Pro-bono.",
    },
    {
        "slug": "job-board",
        "title": "Job Board",
        "requirements": "Full-stack job board with RBAC, full-text search, JWT auth. React 19 + Express + PostgreSQL. Docker Compose. Read existing README.md. Internal portfolio — no billing. Pro-bono.",
    },
    {
        "slug": "client-portal",
        "title": "GigForge Client Portal",
        "requirements": "Web dashboard for GigForge clients. Project progress, deliverables, team communication, reports. React + Express + PostgreSQL. Internal portfolio — no billing. Pro-bono.",
    },
]

async def main():
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    client = await Client.connect("localhost:7233")

    for i, proj in enumerate(PROJECTS):
        log.info("\n{'='*60}")
        log.info("[{i+1}/{len(PROJECTS)}] Starting: {proj['title']}")
        log.info("{'='*60}")

        inp = BuildInput(
            customer_email="internal@gigforge.ai",
            project_title=proj["title"],
            project_slug=proj["slug"],
            org="gigforge",
            requirements=proj["requirements"],
        )

        wf_id = f"build-seq-{proj['slug']}-{int(time.time())}"
        handle = await client.start_workflow(
            ProjectBuildWorkflow.run, inp,
            id=wf_id,
            task_queue="build-projects",
            execution_timeout=datetime.timedelta(hours=3),
        )
        log.info("  Workflow: %s", extra={"wf_id": wf_id})

        # Wait for completion
        SM = {1: "RUNNING", 2: "COMPLETED", 3: "FAILED", 4: "CANCELLED", 7: "TIMED_OUT"}
        while True:
            await asyncio.sleep(120)
            desc = await handle.describe()
            status = SM.get(desc.status, str(desc.status))

            # Get current step
            acts = []
            async for e in handle.fetch_history_events():
                if hasattr(e, "activity_task_scheduled_event_attributes"):
                    a = e.activity_task_scheduled_event_attributes
                    if a and a.activity_type and a.activity_type.name:
                        acts.append(a.activity_type.name)
            last = acts[-1] if acts else "pending"

            now = datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M")
            log.info("  %s | %s | step %s | %s", extra={"now": now, "status": status, "last": last})
            sys.stdout.flush()

            if desc.status != 1:  # Not RUNNING
                if desc.status == 2:
                    log.info("  COMPLETED ✓")
                elif desc.status == 3:
                    log.info("  FAILED ✗ — will continue to next project")
                break

    log.info("\n{'='*60}")
    log.info("ALL SEQUENTIAL BUILDS DONE")
    log.info("{'='*60}")

asyncio.run(main())
