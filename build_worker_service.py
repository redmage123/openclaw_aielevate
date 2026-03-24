#!/usr/bin/env python3
from logging_config import get_logger  # Structured JSON logging
from exceptions import AiElevateError  # TODO: Use specific exception types
import asyncio, logging, sys
sys.path.insert(0, '/home/aielevate')
from temporalio.client import Client
from temporalio.worker import Worker
from build_workflow import (
    ProjectBuildWorkflow,
    create_milestones, pm_sprint_plan, engineering_consensus, write_software_spec, ux_design, 
    pair_scaffold, pair_cms_collections, pair_api_routes,
    pair_frontend_layout, pair_ui_components, pair_pages_part1,
    pair_pages_part2, pair_integration, verify_build,
    qa_test, ux_review, write_documentation, devops_deploy, send_preview_email, create_invoice,
    send_feedback, notify_customer_start, update_milestone, notify_ops,
)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [build-worker] %(message)s')
log = logging.getLogger('build-worker')
async def main():
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    client = await Client.connect('localhost:7233')
    worker = Worker(client, task_queue='build-projects',
        workflows=[ProjectBuildWorkflow],
        activities=[create_milestones, pm_sprint_plan, engineering_consensus, write_software_spec, ux_design, 
                    pair_scaffold, pair_cms_collections, pair_api_routes,
    pair_frontend_layout, pair_ui_components, pair_pages_part1,
    pair_pages_part2, pair_integration, verify_build,
    qa_test, ux_review, write_documentation, devops_deploy, send_preview_email, create_invoice,
                    send_feedback, notify_customer_start, update_milestone, notify_ops])
    log.info('Build worker started on task queue: build-projects')
    await worker.run()
if __name__ == '__main__':
    asyncio.run(main())
