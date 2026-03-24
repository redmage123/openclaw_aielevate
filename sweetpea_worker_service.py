#!/usr/bin/env python3
from logging_config import get_logger  # Structured JSON logging
from exceptions import AiElevateError  # TODO: Use specific exception types
"""Sweet Pea Gardens Temporal worker."""
import asyncio, logging, sys
sys.path.insert(0, "/home/aielevate")
from temporalio.client import Client
from temporalio.worker import Worker
from sweetpea_workflows import (
    DailyBlogWorkflow, SocialMediaWorkflow, WeeklyNewsletterWorkflow,
    CustomerResponseWorkflow, InventoryCheckWorkflow, SeasonalPlannerWorkflow,
    ContentCalendarWorkflow,
    research_garden_topic, write_garden_blog, publish_garden_blog,
    create_social_post, log_social_content,
    compile_garden_newsletter, log_newsletter,
    respond_to_customer,
    audit_inventory,
    monthly_seasonal_plan,
    create_content_calendar,
)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [sweetpea-worker] %(message)s")
async def main():
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    client = await Client.connect("localhost:7233")
    worker = Worker(client, task_queue="sweetpea-workflows",
        workflows=[DailyBlogWorkflow, SocialMediaWorkflow, WeeklyNewsletterWorkflow,
                   CustomerResponseWorkflow, InventoryCheckWorkflow, SeasonalPlannerWorkflow,
                   ContentCalendarWorkflow],
        activities=[research_garden_topic, write_garden_blog, publish_garden_blog,
                    create_social_post, log_social_content,
                    compile_garden_newsletter, log_newsletter,
                    respond_to_customer, audit_inventory, monthly_seasonal_plan,
                    create_content_calendar])
    logging.getLogger("sweetpea-worker").info("Sweet Pea Gardens worker started: 7 workflows")
    await worker.run()
if __name__ == "__main__":
    asyncio.run(main())
