#!/usr/bin/env python3
from logging_config import get_logger  # Structured JSON logging
from exceptions import AiElevateError  # TODO: Use specific exception types
"""Temporal worker for all 7 project lifecycle workflows."""
import asyncio
import logging
import sys

sys.path.insert(0, "/home/aielevate")

from temporalio.client import Client
from temporalio.worker import Worker

from project_workflows import (
    # Workflows
    RevisionCycleWorkflow,
    ScopeChangeWorkflow,
    ClientOnboardingWorkflow,
    ProposalToContractWorkflow,
    PaymentMilestoneWorkflow,
    PostDeliverySupportWorkflow,
    ProjectClosureWorkflow,
    # Revision activities
    log_revision_request, engineer_revision, qa_revision,
    redeploy_preview, send_revision_ready, complete_revision,
    # Scope change activities
    log_scope_change, pm_impact_assessment, engineer_effort_estimate,
    sales_price_change, send_scope_change_proposal,
    # Onboarding activities
    create_onboarding_record, advocate_introduction, request_assets,
    create_project_workspace, confirm_kickoff,
    # Proposal activities
    engineering_feasibility, draft_proposal, queue_proposal_approval,
    send_proposal_to_customer,
    # Payment activities
    create_payment_schedule, send_milestone_invoice, send_payment_receipt,
    # Support activities
    create_support_ticket, qa_triage_bug, engineer_fix_bug,
    send_bug_fix_notification, close_support_ticket,
    # Closure activities
    create_closure_record, generate_handover_docs, deploy_to_production,
    generate_case_study, request_testimonial, archive_project,
    send_delivery_complete,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [project-worker] %(message)s")
log = logging.getLogger("project-worker")


async def main():
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    client = await Client.connect("localhost:7233")
    worker = Worker(
        client,
        task_queue="project-workflows",
        workflows=[
            RevisionCycleWorkflow,
            ScopeChangeWorkflow,
            ClientOnboardingWorkflow,
            ProposalToContractWorkflow,
            PaymentMilestoneWorkflow,
            PostDeliverySupportWorkflow,
            ProjectClosureWorkflow,
        ],
        activities=[
            # Revision
            log_revision_request, engineer_revision, qa_revision,
            redeploy_preview, send_revision_ready, complete_revision,
            # Scope change
            log_scope_change, pm_impact_assessment, engineer_effort_estimate,
            sales_price_change, send_scope_change_proposal,
            # Onboarding
            create_onboarding_record, advocate_introduction, request_assets,
            create_project_workspace, confirm_kickoff,
            # Proposal
            engineering_feasibility, draft_proposal, queue_proposal_approval,
            send_proposal_to_customer,
            # Payment
            create_payment_schedule, send_milestone_invoice, send_payment_receipt,
            # Support
            create_support_ticket, qa_triage_bug, engineer_fix_bug,
            send_bug_fix_notification, close_support_ticket,
            # Closure
            create_closure_record, generate_handover_docs, deploy_to_production,
            generate_case_study, request_testimonial, archive_project,
            send_delivery_complete,
        ],
    )
    log.info("Project lifecycle worker started on task queue: project-workflows")
    log.info("Workflows: revision, scope-change, onboarding, proposal, payment, support, closure")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
