#!/usr/bin/env python3
"""Monitor all 6 builds every 2 minutes."""
import asyncio, sys
from datetime import datetime, timezone
from temporalio.client import Client
from exceptions import AiElevateError  # TODO: Use specific exception types
from logging_config import get_logger


log = get_logger("monitor_all")

IDS = [
    ("Todo REST API",       "build-portfolio-todo-rest-api-1774141973"),
    ("AI Chat Widget",      "build-portfolio-ai-chat-widget-1774141983"),
    ("Job Board",           "build-portfolio-job-board-1774141993"),
    ("Client Portal",       "build-portfolio-client-portal-1774142003"),
    ("CryptoAdvisor Web",   "build-portfolio-cryptoadvisor-web-1774142013"),
    ("TRADING DESK",        "build-trading-desk-1774143518"),
]
SM = {1: "RUN", 2: "DONE", 3: "FAIL", 4: "CANC", 7: "TOUT"}

async def main():
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    c = await Client.connect("localhost:7233")
    while True:
        now = datetime.now(timezone.utc).strftime("%H:%M")
        all_done = True
        for name, wfid in IDS:
            try:
                h = c.get_workflow_handle(wfid)
                desc = await h.describe()
                acts = []
                async for e in h.fetch_history_events():
                    if hasattr(e, "activity_task_scheduled_event_attributes"):
                        a = e.activity_task_scheduled_event_attributes
                        if a and a.activity_type and a.activity_type.name:
                            acts.append(a.activity_type.name)
                last = acts[-1] if acts else "pending"
                status = SM.get(desc.status, str(desc.status))
                log.info("%s | %s | step %s | %s | %s", extra={"now": now, "status": status, "last": last})
                if desc.status == 1: all_done = False
            except (AiElevateError, Exception) as e:
                log.info("%s | ERR  |         | %s | %s", extra={"now": now, "e": e})
                all_done = False
        print("-" * 80)
        sys.stdout.flush()
        if all_done:
            log.info("ALL WORKFLOWS COMPLETE")
            break
        await asyncio.sleep(120)

asyncio.run(main())
