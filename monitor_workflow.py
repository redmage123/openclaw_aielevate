#!/usr/bin/env python3
"""Workflow monitor — prints status every 2 minutes until complete."""
import asyncio
import sys
import time
from datetime import datetime, timezone

from temporalio.client import Client
import argparse

WORKFLOW_ID = sys.argv[1] if len(sys.argv) > 1 else "build-sudacka-v2-1774107134"
INTERVAL = int(sys.argv[2]) if len(sys.argv) > 2 else 120

STATUS_MAP = {1: "RUNNING", 2: "COMPLETED", 3: "FAILED", 4: "CANCELLED", 7: "TIMED_OUT"}


async def check(client):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    h = client.get_workflow_handle(WORKFLOW_ID)
    desc = await h.describe()
    status = STATUS_MAP.get(desc.status, str(desc.status))

    acts = []
    fails = []
    async for e in h.fetch_history_events():
        if hasattr(e, "activity_task_scheduled_event_attributes"):
            a = e.activity_task_scheduled_event_attributes
            if a and a.activity_type and a.activity_type.name:
                acts.append(a.activity_type.name)
        if hasattr(e, "activity_task_failed_event_attributes"):
            a = e.activity_task_failed_event_attributes
            if a and a.failure and a.failure.message:
                fails.append(a.failure.message[:150])

    last = acts[-1] if acts else "none"
    now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

    print(f"[{now}] {status} | step {len(acts)} | current: {last}")
    if fails:
        print(f"  └─ FAIL: {fails[-1]}")
    sys.stdout.flush()

    return status != "RUNNING"


async def main():
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    client = await Client.connect("localhost:7233")
    print(f"Monitoring {WORKFLOW_ID} every {INTERVAL}s")
    print("-" * 60)
    sys.stdout.flush()

    while True:
        done = await check(client)
        if done:
            print("=" * 60)
            print("WORKFLOW FINISHED")
            break
        await asyncio.sleep(INTERVAL)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Monitor Workflow")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose output")
    args = parser.parse_args()

    asyncio.run(main())
