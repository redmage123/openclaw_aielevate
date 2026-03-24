#!/usr/bin/env python3
from logging_config import get_logger  # Structured JSON logging
from exceptions import AiElevateError  # TODO: Use specific exception types
"""Temporal worker for content workflows — blog, newsletter, AI weekly."""
import asyncio
import logging
import sys

sys.path.insert(0, "/home/aielevate")

from temporalio.client import Client
from temporalio.worker import Worker

from content_workflows import (
    BlogPostWorkflow, NewsletterWorkflow, NewsAggregationWorkflow,
    research_blog_topic, write_blog_post, edit_blog_post, publish_blog_post,
    compile_newsletter, send_newsletter,
    research_ai_news, write_ai_report, edit_ai_report, publish_ai_report,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [content-worker] %(message)s")
log = logging.getLogger("content-worker")

async def main():
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    client = await Client.connect("localhost:7233")
    worker = Worker(client, task_queue="content-workflows",
        workflows=[BlogPostWorkflow, NewsletterWorkflow, NewsAggregationWorkflow],
        activities=[
            research_blog_topic, write_blog_post, edit_blog_post, publish_blog_post,
            compile_newsletter, send_newsletter,
            research_ai_news, write_ai_report, edit_ai_report, publish_ai_report,
        ])
    log.info("Content worker started: blog, newsletter, AI weekly")
    await worker.run()

if __name__ == "__main__":
    asyncio.run(main())
