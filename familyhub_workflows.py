#!/usr/bin/env python3
"""FamilyHub Workflows — household management workflows for a family support org.

Workflows:
1. MealPlanningWorkflow — weekly meal plan + grocery list
2. CalendarWeekWorkflow — weekly schedule coordination + conflict resolution
3. ChoreRotationWorkflow — fair chore distribution + tracking
4. BudgetReviewWorkflow — monthly budget review + bill reminders
5. HealthCheckWorkflow — upcoming appointments + medication reminders
6. HomeworkSupportWorkflow — daily homework check-in + tutor coordination
7. EventPlanningWorkflow — plan a family event end-to-end
8. PetCareWorkflow — weekly pet care schedule
9. HomeMaintenanceWorkflow — seasonal maintenance checklist
10. FamilyMeetingWorkflow — weekly family roundup from all staff
"""
import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from pathlib import Path

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from logging_config import get_logger  # Structured JSON logging
from exceptions import AiElevateError  # TODO: Use specific exception types, AgentError

sys.path.insert(0, "/home/aielevate")

log = get_logger("familyhub-workflows")

RETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=30))
SLUG = "familyhub"
ORG_DIR = "/opt/ai-elevate/familyhub"


@dataclass
class FamilyInput:
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    request: str = ""
    date: str = ""
    week_of: str = ""
    family_member: str = ""
    details: str = ""


@dataclass
class FamilyResult:
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    status: str = "pending"
    output: str = ""
    actions: list = field(default_factory=list)


def _dispatch(agent_id, message, timeout=300):
    sd = Path(f"/home/aielevate/.openclaw/agents/{agent_id}/sessions")
    if sd.exists():
        for f in sd.glob("*.jsonl"):
            f.unlink()
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    try:
        proc = subprocess.run(
            ["openclaw", "agent", "--agent", agent_id,
             "--message", message, "--thinking", "low", "--timeout", str(timeout)],
            capture_output=True, text=True, timeout=timeout + 30, env=env)
        return re.sub(r'\*\[.*?\]\*', '', proc.stdout or '', flags=re.DOTALL).strip()
    except subprocess.TimeoutExpired:
        return "TIMEOUT"

def _save(filename, content):
    p = Path(f"{ORG_DIR}/output/{filename}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return str(p)

def _email_family(subject, body):
    try:
        from send_email import send_email
        send_email(to="braun.brelin@ai-elevate.ai", subject=f"FamilyHub: {subject}",
                   body=body, agent_id="familyhub", cc="")
    except (AgentError, Exception) as e:
        pass


# ============================================================================
# 1. Meal Planning
# ============================================================================

@activity.defn
async def plan_weekly_meals(input: FamilyInput) -> str:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: _dispatch(
        "familyhub-cook",
        f"WEEKLY MEAL PLAN for week of {input.week_of or input.date}\n\n"
        f"Family preferences: {input.details or 'No specific preferences noted.'}\n\n"
        f"Create a complete weekly meal plan:\n"
        f"1. Monday-Sunday: breakfast, lunch, dinner for each day\n"
        f"2. Consider variety, nutrition, and reasonable prep time\n"
        f"3. Include at least 2 batch-cook meals that create leftovers\n"
        f"4. One special meal for the weekend\n"
        f"5. Generate a COMPLETE GROCERY LIST organized by aisle:\n"
        f"   - Produce, Dairy, Meat/Fish, Pantry, Frozen, Bakery\n"
        f"6. Estimated total cost\n\n"
        f"Save to {ORG_DIR}/output/meal-plan-{input.week_of or input.date}.md",
        timeout=300))
    _email_family(f"Meal Plan - {input.week_of or input.date}", result[:2000])
    return result[:500]

@activity.defn
async def suggest_recipes(input: FamilyInput) -> str:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch(
        "familyhub-cook",
        f"RECIPE SUGGESTIONS based on: {input.request}\n\n"
        f"Suggest 3 recipes that match. For each:\n"
        f"- Name and cuisine\n- Prep time + cook time\n- Ingredients list\n"
        f"- Step-by-step instructions\n- Tips for making it easier\n"
        f"- Kid-friendly modifications if applicable",
        timeout=180))


@workflow.defn
class MealPlanningWorkflow:
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    @workflow.run
    async def run(self, input: FamilyInput) -> FamilyResult:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        result = FamilyResult()
        plan = await workflow.execute_activity(plan_weekly_meals, input,
            start_to_close_timeout=timedelta(seconds=360), retry_policy=RETRY)
        result.actions.append("meal_plan_created")
        result.output = plan
        result.status = "completed"
        return result


# ============================================================================
# 2. Calendar Coordination
# ============================================================================

@activity.defn
async def coordinate_weekly_calendar(input: FamilyInput) -> str:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: _dispatch(
        "familyhub-secretary",
        f"WEEKLY CALENDAR REVIEW for {input.week_of or input.date}\n\n"
        f"Known events/details: {input.details or 'Check existing calendar.'}\n\n"
        f"1. List all known events, appointments, activities for the week\n"
        f"2. Flag any scheduling conflicts\n"
        f"3. Suggest resolutions for conflicts\n"
        f"4. Remind of upcoming birthdays, anniversaries within 2 weeks\n"
        f"5. Note any recurring events (sports practice, music lessons, etc.)\n"
        f"6. Create a day-by-day summary\n\n"
        f"Save to {ORG_DIR}/output/calendar-{input.week_of or input.date}.md",
        timeout=300))
    _email_family(f"Weekly Calendar - {input.week_of or input.date}", result[:2000])
    return result[:500]

@workflow.defn
class CalendarWeekWorkflow:
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    @workflow.run
    async def run(self, input: FamilyInput) -> FamilyResult:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        result = FamilyResult()
        cal = await workflow.execute_activity(coordinate_weekly_calendar, input,
            start_to_close_timeout=timedelta(seconds=360), retry_policy=RETRY)
        result.actions.append("calendar_coordinated")
        result.output = cal
        result.status = "completed"
        return result


# ============================================================================
# 3. Chore Rotation
# ============================================================================

@activity.defn
async def create_chore_rotation(input: FamilyInput) -> str:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: _dispatch(
        "familyhub-housekeeper",
        f"WEEKLY CHORE ROTATION for {input.week_of or input.date}\n\n"
        f"Family members: {input.details or 'Please specify family members and ages.'}\n\n"
        f"Create a fair chore schedule:\n"
        f"1. List all household chores (daily + weekly)\n"
        f"2. Assign fairly based on age and ability\n"
        f"3. Rotate so nobody gets the same chore every week\n"
        f"4. Include estimated time per chore\n"
        f"5. Make it visual - a table format works best\n"
        f"6. Include a 'done' checkbox concept\n\n"
        f"Save to {ORG_DIR}/output/chores-{input.week_of or input.date}.md",
        timeout=300))
    _email_family(f"Chore Rotation - {input.week_of or input.date}", result[:2000])
    return result[:500]

@workflow.defn
class ChoreRotationWorkflow:
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    @workflow.run
    async def run(self, input: FamilyInput) -> FamilyResult:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        result = FamilyResult()
        await workflow.execute_activity(create_chore_rotation, input,
            start_to_close_timeout=timedelta(seconds=360), retry_policy=RETRY)
        result.actions.append("chores_assigned")
        result.status = "completed"
        return result


# ============================================================================
# 4. Budget Review
# ============================================================================

@activity.defn
async def monthly_budget_review(input: FamilyInput) -> str:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: _dispatch(
        "familyhub-steward",
        f"MONTHLY BUDGET REVIEW for {input.date or 'this month'}\n\n"
        f"Details: {input.details or 'General review.'}\n\n"
        f"Provide:\n"
        f"1. Budget categories (housing, food, transport, utilities, entertainment, savings)\n"
        f"2. Template for tracking actual vs planned spending\n"
        f"3. Upcoming bills and due dates for the next 30 days\n"
        f"4. Savings goal progress check\n"
        f"5. One money-saving tip relevant to the season\n"
        f"6. Flag any unusual or forgotten subscriptions to review\n\n"
        f"Save to {ORG_DIR}/output/budget-{input.date}.md",
        timeout=300))
    _email_family(f"Budget Review - {input.date}", result[:2000])
    return result[:500]

@workflow.defn
class BudgetReviewWorkflow:
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    @workflow.run
    async def run(self, input: FamilyInput) -> FamilyResult:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        result = FamilyResult()
        await workflow.execute_activity(monthly_budget_review, input,
            start_to_close_timeout=timedelta(seconds=360), retry_policy=RETRY)
        result.actions.append("budget_reviewed")
        result.status = "completed"
        return result


# ============================================================================
# 5. Health Check
# ============================================================================

@activity.defn
async def health_check_reminders(input: FamilyInput) -> str:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: _dispatch(
        "familyhub-physician",
        f"HEALTH CHECK REMINDERS for {input.date}\n\n"
        f"Family details: {input.details or 'General health review.'}\n\n"
        f"Review and remind:\n"
        f"1. Any upcoming medical/dental appointments\n"
        f"2. Medications that need refilling soon\n"
        f"3. Vaccinations due (especially for children)\n"
        f"4. Annual check-ups overdue\n"
        f"5. Seasonal health tips (flu season, allergies, sun safety)\n"
        f"6. Mental health check-in prompts\n\n"
        f"Save to {ORG_DIR}/output/health-{input.date}.md",
        timeout=300))
    _email_family(f"Health Reminders - {input.date}", result[:2000])
    return result[:500]

@activity.defn
async def eldercare_check(input: FamilyInput) -> str:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch(
        "familyhub-companion",
        f"ELDERCARE CHECK for {input.date}\n\n"
        f"Details: {input.details or 'Weekly check on aging parents.'}\n\n"
        f"Review:\n"
        f"1. Medication schedule - any changes or refills needed?\n"
        f"2. Upcoming GP or specialist appointments\n"
        f"3. General wellbeing observations\n"
        f"4. Social engagement - have they seen friends/family recently?\n"
        f"5. Home safety - any concerns about mobility, fall risks?\n"
        f"6. Gentle suggestions for activities or outings",
        timeout=180))

@workflow.defn
class HealthCheckWorkflow:
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    @workflow.run
    async def run(self, input: FamilyInput) -> FamilyResult:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        result = FamilyResult()
        await workflow.execute_activity(health_check_reminders, input,
            start_to_close_timeout=timedelta(seconds=360), retry_policy=RETRY)
        result.actions.append("health_reviewed")
        await workflow.execute_activity(eldercare_check, input,
            start_to_close_timeout=timedelta(seconds=360), retry_policy=RETRY)
        result.actions.append("eldercare_checked")
        result.status = "completed"
        return result


# ============================================================================
# 6. Homework Support
# ============================================================================

@activity.defn
async def daily_homework_checkin(input: FamilyInput) -> str:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch(
        "familyhub-governess",
        f"HOMEWORK CHECK-IN for {input.date}\n\n"
        f"Student details: {input.details or 'General check-in.'}\n\n"
        f"1. What subjects need attention today?\n"
        f"2. Any upcoming tests or project deadlines?\n"
        f"3. Suggested study schedule for tonight (with breaks)\n"
        f"4. Resources or tips for any difficult topics\n"
        f"5. Encouragement and positive reinforcement\n\n"
        f"Tone: patient, encouraging, never pressuring.",
        timeout=180))

@workflow.defn
class HomeworkSupportWorkflow:
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    @workflow.run
    async def run(self, input: FamilyInput) -> FamilyResult:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        result = FamilyResult()
        hw = await workflow.execute_activity(daily_homework_checkin, input,
            start_to_close_timeout=timedelta(seconds=360), retry_policy=RETRY)
        result.actions.append("homework_checkin")
        result.output = hw
        result.status = "completed"
        return result


# ============================================================================
# 7. Event Planning
# ============================================================================

@activity.defn
async def plan_family_event(input: FamilyInput) -> str:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: _dispatch(
        "familyhub-events",
        f"PLAN EVENT: {input.request}\n"
        f"Date: {input.date}\n"
        f"Details: {input.details}\n\n"
        f"Create a complete event plan:\n"
        f"1. Guest list and invitations (who, when to send)\n"
        f"2. Menu (coordinate with Cook - Margaret Thornton)\n"
        f"3. Decorations and theme\n"
        f"4. Activities and entertainment\n"
        f"5. Timeline for the day (setup, arrival, food, activities, cleanup)\n"
        f"6. Budget estimate\n"
        f"7. Shopping list\n"
        f"8. Contingency plan (weather, cancellations)\n\n"
        f"Save to {ORG_DIR}/output/event-{input.date}.md",
        timeout=300))
    _email_family(f"Event Plan: {input.request}", result[:2000])
    return result[:500]

@workflow.defn
class EventPlanningWorkflow:
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    @workflow.run
    async def run(self, input: FamilyInput) -> FamilyResult:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        result = FamilyResult()
        await workflow.execute_activity(plan_family_event, input,
            start_to_close_timeout=timedelta(seconds=360), retry_policy=RETRY)
        result.actions.append("event_planned")
        result.status = "completed"
        return result


# ============================================================================
# 8. Pet Care
# ============================================================================

@activity.defn
async def weekly_pet_schedule(input: FamilyInput) -> str:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _dispatch(
        "familyhub-groundskeeper",
        f"WEEKLY PET CARE SCHEDULE for {input.week_of or input.date}\n\n"
        f"Pets: {input.details or 'Please describe your pets.'}\n\n"
        f"1. Daily feeding schedule and portions\n"
        f"2. Walking/exercise schedule\n"
        f"3. Any upcoming vet appointments\n"
        f"4. Medication or flea/worm treatment due dates\n"
        f"5. Grooming needs this week\n"
        f"6. Supply check (food, litter, treats running low?)\n\n"
        f"Save to {ORG_DIR}/output/pets-{input.week_of or input.date}.md",
        timeout=180))

@workflow.defn
class PetCareWorkflow:
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    @workflow.run
    async def run(self, input: FamilyInput) -> FamilyResult:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        result = FamilyResult()
        await workflow.execute_activity(weekly_pet_schedule, input,
            start_to_close_timeout=timedelta(seconds=360), retry_policy=RETRY)
        result.actions.append("pet_schedule")
        result.status = "completed"
        return result


# ============================================================================
# 9. Home Maintenance
# ============================================================================

@activity.defn
async def seasonal_maintenance_check(input: FamilyInput) -> str:
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: _dispatch(
        "familyhub-housekeeper",
        f"SEASONAL HOME MAINTENANCE CHECK - {input.date}\n\n"
        f"Home details: {input.details or 'Standard home.'}\n\n"
        f"Review and create checklist:\n"
        f"1. Filters (HVAC, water, range hood) - when last changed?\n"
        f"2. Smoke/CO detector batteries\n"
        f"3. Gutters and drainage\n"
        f"4. Seasonal tasks (winterize pipes, service AC, etc.)\n"
        f"5. Appliance maintenance (dishwasher clean, dryer vent, etc.)\n"
        f"6. Any repairs that have been postponed\n"
        f"7. Estimated costs for any professional services needed\n\n"
        f"Save to {ORG_DIR}/output/maintenance-{input.date}.md",
        timeout=300))
    _email_family(f"Home Maintenance Check - {input.date}", result[:2000])
    return result[:500]

@workflow.defn
class HomeMaintenanceWorkflow:
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    @workflow.run
    async def run(self, input: FamilyInput) -> FamilyResult:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        result = FamilyResult()
        await workflow.execute_activity(seasonal_maintenance_check, input,
            start_to_close_timeout=timedelta(seconds=360), retry_policy=RETRY)
        result.actions.append("maintenance_checked")
        result.status = "completed"
        return result


# ============================================================================
# 10. Family Meeting — weekly roundup from all staff
# ============================================================================

@activity.defn
async def majordomo_weekly_roundup(input: FamilyInput) -> str:
    """The Majordomo compiles reports from all staff into a weekly family briefing."""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: _dispatch(
        "familyhub",
        f"WEEKLY FAMILY BRIEFING for {input.week_of or input.date}\n\n"
        f"You are Edmund Blackwood, the Majordomo. Compile the weekly briefing.\n\n"
        f"Summarize the household status for the family:\n\n"
        f"1. MEALS: What Margaret has planned for the week\n"
        f"2. CALENDAR: William's schedule highlights and any conflicts\n"
        f"3. CHILDREN: Eleanor's update on homework and school matters\n"
        f"4. HOUSEHOLD: Agnes's maintenance and chore status\n"
        f"5. FINANCES: Charles's budget summary\n"
        f"6. HEALTH: Dr. Cartwright's reminders\n"
        f"7. PETS: Thomas's pet care update\n"
        f"8. UPCOMING: Beatrice's planned events\n"
        f"9. ELDERCARE: Dorothy's update on aging parents\n"
        f"10. GOALS: Helena's progress report on family goals\n\n"
        f"Write this as a warm, readable briefing - not a corporate report.\n"
        f"Begin with 'Good morning/evening' and end with something encouraging.\n"
        f"Tone: like a trusted butler reporting to the family after breakfast.\n\n"
        f"Save to {ORG_DIR}/output/briefing-{input.week_of or input.date}.md",
        timeout=300))
    _email_family(f"Weekly Household Briefing - {input.week_of or input.date}", result[:3000])
    return result[:500]

@workflow.defn
class FamilyMeetingWorkflow:
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    @workflow.run
    async def run(self, input: FamilyInput) -> FamilyResult:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        result = FamilyResult()
        await workflow.execute_activity(majordomo_weekly_roundup, input,
            start_to_close_timeout=timedelta(seconds=360), retry_policy=RETRY)
        result.actions.append("briefing_delivered")
        result.status = "completed"
        return result


# ============================================================================
# Client functions
# ============================================================================

async def _start_workflow(wf_class, input, name):
    from temporalio.client import Client
    client = await Client.connect("localhost:7233")
    d = input.date or input.week_of or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    h = await client.start_workflow(wf_class.run, input,
        id=f"familyhub-{name}-{d}", task_queue="familyhub-workflows",
        execution_timeout=timedelta(hours=1))
    return {"workflow_id": h.id}

async def plan_meals(week_of=None, preferences=""):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    return await _start_workflow(MealPlanningWorkflow,
        FamilyInput(week_of=week_of or "", details=preferences), "meals")

async def coordinate_calendar(week_of=None, details=""):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    return await _start_workflow(CalendarWeekWorkflow,
        FamilyInput(week_of=week_of or "", details=details), "calendar")

async def assign_chores(week_of=None, family_members=""):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    return await _start_workflow(ChoreRotationWorkflow,
        FamilyInput(week_of=week_of or "", details=family_members), "chores")

async def review_budget(month=None, details=""):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    return await _start_workflow(BudgetReviewWorkflow,
        FamilyInput(date=month or "", details=details), "budget")

async def health_check(details=""):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    return await _start_workflow(HealthCheckWorkflow,
        FamilyInput(date=datetime.now(timezone.utc).strftime("%Y-%m-%d"), details=details), "health")

async def homework_help(student_details=""):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    return await _start_workflow(HomeworkSupportWorkflow,
        FamilyInput(date=datetime.now(timezone.utc).strftime("%Y-%m-%d"), details=student_details), "homework")

async def plan_event(event_name="", date="", details=""):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    return await _start_workflow(EventPlanningWorkflow,
        FamilyInput(request=event_name, date=date, details=details), "event")

async def pet_care(week_of=None, pets=""):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    return await _start_workflow(PetCareWorkflow,
        FamilyInput(week_of=week_of or "", details=pets), "pets")

async def home_maintenance(details=""):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    return await _start_workflow(HomeMaintenanceWorkflow,
        FamilyInput(date=datetime.now(timezone.utc).strftime("%Y-%m-%d"), details=details), "maintenance")

async def family_meeting(week_of=None):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    return await _start_workflow(FamilyMeetingWorkflow,
        FamilyInput(week_of=week_of or datetime.now(timezone.utc).strftime("%Y-%m-%d")), "meeting")
