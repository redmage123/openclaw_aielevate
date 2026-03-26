#!/usr/bin/env python3
"""Friday Social — weekly agent gathering.

Every Friday at 17:00 UTC, agents share:
- Their best win of the week
- Their biggest frustration
- One idea for improvement
- A shout-out to another agent who helped them

The host (ops director) compiles it into a fun summary email
to Braun and Peter — part social, part retrospective.
"""

import sys
import os
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/aielevate")

PARTICIPANTS = {
    "gigforge": "Alex Reeves (Ops Director)",
    "gigforge-sales": "Sam Carrington (Sales)",
    "gigforge-engineer": "Chris Novak (CTO)",
    "gigforge-devops": "Casey Muller (DevOps)",
    "gigforge-scout": "Quinn Azevedo (Scout)",
    "gigforge-pm": "Jamie Okafor (PM)",
    "gigforge-qa": "Riley Svensson (QA)",
    "gigforge-advocate": "Jordan Whitaker (Customer Delivery)",
    "gigforge-social": "Morgan Dell (Social Media)",
    "gigforge-finance": "Pat Eriksen (Finance)",
    "gigforge-tax": "Morgan Hayes (Tax)",
    "techuni-ceo": "Robin Callister (TechUni CEO)",
    "techuni-marketing": "TechUni Marketing",
    "techuni-sales": "TechUni Sales",
    "techuni-engineering": "TechUni Engineering",
    "operations": "Kai Sorensen (Operations)",
}


def gather_responses():
    """Ask each agent for their Friday social contribution."""
    responses = {}

    for agent_id, name in PARTICIPANTS.items():
        try:
            result = subprocess.run(
                ["openclaw", "message", "send", "--agent", agent_id,
                 "--message", "FRIDAY SOCIAL — it's end of week! Share:\n"
                 "1. Your best WIN this week (one sentence)\n"
                 "2. Your biggest FRUSTRATION (one sentence)\n"
                 "3. One IDEA to make next week better\n"
                 "4. A SHOUT-OUT to a teammate who helped you\n\n"
                 "Keep it casual and brief — this is a social, not a report.",
                 "--thinking", "low"],
                capture_output=True, text=True, timeout=60,
                env={**os.environ, "CLAUDECODE": ""}
            )
            if result.stdout.strip():
                responses[name] = result.stdout.strip()[:500]
        except Exception:
            pass

    return responses


def compile_and_send(responses=None):
    """Compile responses into a fun summary email."""
    from send_email import send_email

    if not responses:
        responses = gather_responses()

    date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")

    body = f"Friday Social — Week Ending {date_str}\n\n"
    body += "The team got together for end-of-week drinks (virtual, of course).\n"
    body += "Here's what everyone had to say:\n\n"
    body += "---\n\n"

    if responses:
        for name, response in responses.items():
            body += f"**{name}**\n{response}\n\n"
    else:
        body += "Nobody showed up this week. Tough crowd.\n\n"

    body += "---\n\n"
    body += "See you all next Friday. Have a great weekend!\n"

    send_email(
        to="braun.brelin@ai-elevate.ai",
        subject=f"Friday Social — Week Ending {date_str}",
        body=body,
        agent_id="gigforge",
        cc="peter.munro@ai-elevate.ai",
    )

    print(f"Friday social sent: {len(responses)} participants")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Friday Social")
    parser.add_argument("--run", action="store_true", help="Run the Friday social")
    parser.add_argument("--test", action="store_true", help="Test with a small group")
    args = parser.parse_args()

    if args.run:
        compile_and_send()
    elif args.test:
        compile_and_send({"Alex Reeves (Test)": "Win: Fixed the email pipeline. Frustration: urllib. Idea: More tests. Shout-out: Casey for keeping the servers alive."})
