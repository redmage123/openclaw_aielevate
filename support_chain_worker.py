#!/usr/bin/env python3
"""Temporal worker for the Support Email Chain workflow."""
import asyncio
import logging
import sys

sys.path.insert(0, "/home/aielevate")

from temporalio.client import Client
from temporalio.worker import Worker

from support_email_chain import (
    SupportEmailChainWorkflow,
    create_support_ticket,
    send_ack_email,
    run_investigation,
    send_status_update,
    run_rca,
    update_ticket_state,
    verify_fix,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [support-chain-worker] %(message)s")
log = logging.getLogger("support-chain-worker")


async def main():
    client = await Client.connect("localhost:7233")
    worker = Worker(
        client,
        task_queue="ai-elevate-workflows",
        workflows=[SupportEmailChainWorkflow],
        activities=[
            create_support_ticket,
            send_ack_email,
            run_investigation,
            send_status_update,
            run_rca,
            update_ticket_state,
            verify_fix,
        ],
    )
    log.info("Support chain worker started on task queue: ai-elevate-workflows")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
