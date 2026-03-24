#!/usr/bin/env python3
"""Temporal worker for the Organization Builder."""
import asyncio
import logging
import sys

sys.path.insert(0, "/home/aielevate")

from temporalio.client import Client
from temporalio.worker import Worker

from org_builder import (
    OrganizationBuildWorkflow,
    design_org_structure,
    create_agent_files,
    register_agents_in_db,
    setup_email_routing,
    setup_team_roster,
    setup_knowledge_graph,
    setup_dns,
    create_org_website,
    deploy_org,
    generate_org_summary,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [org-builder] %(message)s")
log = logging.getLogger("org-builder")


async def main():
    client = await Client.connect("localhost:7233")
    worker = Worker(
        client,
        task_queue="org-builder",
        workflows=[OrganizationBuildWorkflow],
        activities=[
            design_org_structure,
            create_agent_files,
            register_agents_in_db,
            setup_email_routing,
            setup_team_roster,
            setup_knowledge_graph,
            setup_dns,
            create_org_website,
            deploy_org,
            generate_org_summary,
        ],
    )
    log.info("Organization Builder worker started on task queue: org-builder")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
