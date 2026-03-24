#!/usr/bin/env python3
from logging_config import get_logger  # Structured JSON logging
from exceptions import AiElevateError  # TODO: Use specific exception types
"""Temporal worker for FamilyHub household workflows."""
import asyncio
import logging
import sys

sys.path.insert(0, "/home/aielevate")

from temporalio.client import Client
from temporalio.worker import Worker

from familyhub_workflows import (
    MealPlanningWorkflow, CalendarWeekWorkflow, ChoreRotationWorkflow,
    BudgetReviewWorkflow, HealthCheckWorkflow, HomeworkSupportWorkflow,
    EventPlanningWorkflow, PetCareWorkflow, HomeMaintenanceWorkflow,
    FamilyMeetingWorkflow,
    plan_weekly_meals, suggest_recipes, coordinate_weekly_calendar,
    create_chore_rotation, monthly_budget_review, health_check_reminders,
    eldercare_check, daily_homework_checkin, plan_family_event,
    weekly_pet_schedule, seasonal_maintenance_check, majordomo_weekly_roundup,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [familyhub] %(message)s")
log = logging.getLogger("familyhub")

async def main():
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    client = await Client.connect("localhost:7233")
    worker = Worker(client, task_queue="familyhub-workflows",
        workflows=[
            MealPlanningWorkflow, CalendarWeekWorkflow, ChoreRotationWorkflow,
            BudgetReviewWorkflow, HealthCheckWorkflow, HomeworkSupportWorkflow,
            EventPlanningWorkflow, PetCareWorkflow, HomeMaintenanceWorkflow,
            FamilyMeetingWorkflow,
        ],
        activities=[
            plan_weekly_meals, suggest_recipes, coordinate_weekly_calendar,
            create_chore_rotation, monthly_budget_review, health_check_reminders,
            eldercare_check, daily_homework_checkin, plan_family_event,
            weekly_pet_schedule, seasonal_maintenance_check, majordomo_weekly_roundup,
        ])
    log.info("FamilyHub worker started — 10 household workflows")
    await worker.run()

if __name__ == "__main__":
    asyncio.run(main())
