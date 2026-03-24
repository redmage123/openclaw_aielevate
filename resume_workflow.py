#!/usr/bin/env python3
"""Resume the Sudačka Mreža build from step 9 (write_software_spec).

log = get_logger("resume_workflow")

Steps 1-8 already completed — artifacts on disk."""
import asyncio
import time
import datetime
import sys
from exceptions import AiElevateError  # TODO: Use specific exception types

sys.path.insert(0, "/home/aielevate")

from temporalio.client import Client
from temporalio import workflow
from temporalio.common import RetryPolicy
from build_workflow import (
    BuildInput, BuildResult,
    write_software_spec, ux_design, engineer_build, qa_test,
    write_documentation, devops_deploy, send_preview_email,
    create_invoice, send_feedback, update_milestone, notify_ops,
    record_build_to_kg,
)
import re
from logging_config import get_logger
import argparse

RETRY = RetryPolicy(maximum_attempts=5, initial_interval=datetime.timedelta(seconds=30),
                     maximum_interval=datetime.timedelta(seconds=120))


@workflow.defn
class ResumeBuildWorkflow:
    """Resume build from step 9 — software spec onward."""

    @workflow.run
    async def run(self, input: BuildInput) -> BuildResult:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        result = BuildResult()
        result.project_dir = f"/opt/ai-elevate/gigforge/projects/{input.project_slug}"
        tl = datetime.timedelta(seconds=960)
        ts = datetime.timedelta(seconds=60)

        def mi(milestone):
            """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
            return BuildInput(**{**input.__dict__, "requirements": milestone})

        result.actions.append("resumed_from_step_9")

        # 9. Software specification
        spec = await workflow.execute_activity(write_software_spec, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        await workflow.execute_activity(update_milestone, mi("Specification"),
            start_to_close_timeout=ts, retry_policy=RETRY)
        await workflow.execute_activity(notify_ops, mi("Software spec written"),
            start_to_close_timeout=ts, retry_policy=RETRY)
        result.actions.append("software_spec")

        # 10. UX design
        design = await workflow.execute_activity(ux_design, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        result.actions.append("ux_design")

        # 11. Engineer build
        build = await workflow.execute_activity(engineer_build, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        await workflow.execute_activity(update_milestone, mi("Backend"),
            start_to_close_timeout=ts, retry_policy=RETRY)
        await workflow.execute_activity(update_milestone, mi("Frontend"),
            start_to_close_timeout=ts, retry_policy=RETRY)
        result.actions.append(f"build:{build[:50]}")

        # 12. QA test
        qa = await workflow.execute_activity(qa_test, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        await workflow.execute_activity(update_milestone, mi("Testing"),
            start_to_close_timeout=ts, retry_policy=RETRY)
        result.actions.append(f"qa:{qa[:50]}")

        # 13. Documentation
        docs = await workflow.execute_activity(write_documentation, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        await workflow.execute_activity(update_milestone, mi("Documentation"),
            start_to_close_timeout=ts, retry_policy=RETRY)
        await workflow.execute_activity(notify_ops, mi("Documentation written"),
            start_to_close_timeout=ts, retry_policy=RETRY)
        result.actions.append("documentation")

        # 14. Deploy
        deploy_result = await workflow.execute_activity(devops_deploy, input,
            start_to_close_timeout=tl, retry_policy=RETRY)
        await workflow.execute_activity(update_milestone, mi("Deployment"),
            start_to_close_timeout=ts, retry_policy=RETRY)
        url_match = re.search(r'https?://\S+', deploy_result)
        preview_url = url_match.group(0) if url_match else deploy_result
        result.preview_url = preview_url
        result.actions.append(f"deployed:{preview_url}")

        # 15. Preview email
        preview_input = BuildInput(**{**input.__dict__, "domain": preview_url})
        await workflow.execute_activity(send_preview_email, preview_input,
            start_to_close_timeout=ts, retry_policy=RETRY)
        result.actions.append("preview_sent")

        # 16. Invoice + feedback
        await workflow.execute_activity(create_invoice, input,
            start_to_close_timeout=ts, retry_policy=RETRY)
        await workflow.execute_activity(send_feedback, input,
            start_to_close_timeout=ts, retry_policy=RETRY)
        result.actions.append("invoice_and_feedback")

        # 17. KG recording
        try:
            await workflow.execute_activity(record_build_to_kg, input,
                start_to_close_timeout=ts)
            result.actions.append("kg_recorded")
        except (AiElevateError, Exception) as e:
            pass

        # 18. Done
        await workflow.execute_activity(notify_ops, mi("Project build complete"),
            start_to_close_timeout=ts, retry_policy=RETRY)
        result.status = "delivered"
        result.actions.append("build_complete")
        return result


async def main():
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    # Cancel stuck workflow
    client = await Client.connect("localhost:7233")
    try:
        h = client.get_workflow_handle("build-sudacka-mreza-1774100903")
        await h.cancel()
        log.info("Cancelled stuck workflow")
    except (AiElevateError, Exception) as e:
        log.info("Cancel: %s", extra={"e": e})

    # Register worker with resume workflow + start it
    from temporalio.worker import Worker

    # Start the resume workflow
    inp = BuildInput(
        customer_email="drazen.komarica@gmail.com",
        project_title="Sudacka Mreza Website Rebuild",
        project_slug="sudacka-mreza",
        org="gigforge",
        requirements="React 19 + Vite 6 + React Router 7 + Tailwind CSS 4 frontend. Payload CMS 3 standalone backend. PostgreSQL 16. Leaflet maps. Bilingual HR/EN. Docker Compose. Full spec at /opt/ai-elevate/gigforge/projects/sudacka-mreza/SPEC.md — READ IT FIRST.",
    )

    handle = await client.start_workflow(
        ResumeBuildWorkflow.run, inp,
        id=f"resume-sudacka-mreza-{int(time.time())}",
        task_queue="build-resume",
        execution_timeout=datetime.timedelta(hours=3),
    )
    log.info("Started resume workflow: {handle.id}")

    # Run worker inline until workflow completes
    worker = Worker(
        client,
        task_queue="build-resume",
        workflows=[ResumeBuildWorkflow],
        activities=[
            write_software_spec, ux_design, engineer_build, qa_test,
            write_documentation, devops_deploy, send_preview_email,
            create_invoice, send_feedback, update_milestone, notify_ops,
            record_build_to_kg,
        ],
    )
    log.info("Resume worker running...")
    await worker.run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Resume Workflow")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose output")
    args = parser.parse_args()

    asyncio.run(main())
