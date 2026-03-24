#!/usr/bin/env python3
"""Temporal worker for the email orchestrator workflow."""
import asyncio
import logging
import sys

sys.path.insert(0, "/home/aielevate")

from temporalio.client import Client
from temporalio.worker import Worker

from orchestrator_workflow import (
    EmailOrchestratorWorkflow,
    classify_email_intent,
    check_duplicate_workflows,
    trigger_email_workflow,
    trigger_build_workflow,
    trigger_onboarding_workflow,
    trigger_revision_workflow,
    trigger_scope_change_workflow,
    trigger_support_workflow,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [orchestrator-worker] %(message)s")
log = logging.getLogger("orchestrator-worker")


async def main():
    client = await Client.connect("localhost:7233")
    worker = Worker(
        client,
        task_queue="email-orchestrator",
        workflows=[EmailOrchestratorWorkflow],
        activities=[
            classify_email_intent,
            check_duplicate_workflows,
            trigger_email_workflow,
            trigger_build_workflow,
            trigger_onboarding_workflow,
            trigger_revision_workflow,
            trigger_scope_change_workflow,
            trigger_support_workflow,
        ],
    )
    log.info("Orchestrator worker started on task queue: email-orchestrator")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
