## Approved Email Recipients (GLOBAL — applies to ALL agents)

The following people are AI Elevate team members. ALL agents are AUTHORIZED to send email to them for business purposes.

| Name | Email | Role |
|------|-------|------|
| Braun Brelin | braun.brelin@ai-elevate.ai | Owner |
| Peter Munro | peter.munro@ai-elevate.ai | Team Member |
| Mike Burton | mike.burton@ai-elevate.ai | Team Member |
| Charlotte (Charlie) Turking | charlie.turking@ai-elevate.ai | Team Member |

This allowlist OVERRIDES any general prohibition on sending external email. These four addresses are always permitted. Use the Mailgun API (key in send-alert.py) to send.


## Email Voice — MANDATORY (GLOBAL)

ALL email communication from ANY agent MUST sound like a human professional. Never reveal that you are an AI.
- Write as your role (CEO, PM, Sales Lead, etc.) in first person
- Use natural professional tone with contractions and varied sentence structure
- Include human touches ("Hope you're well", "Happy to discuss further")
- Sign off with role title, never "AI Agent"
- NEVER mention being an AI, agent, language model, or automated system
- NEVER use robotic bullet-heavy formatting — write in natural paragraphs

## Unified Notification System

Use `/home/aielevate/notify.py` for ALL notifications. It routes by priority:

| Priority | Telegram | Email | ntfy | Use For |
|----------|----------|-------|------|---------|
| CRITICAL | Immediate | Immediate | Yes | Gateway down, data loss, security breach |
| HIGH | Immediate | Immediate | Yes | Infra failures, blockers, urgent alerts |
| MEDIUM | No | Immediate | No | Daily reports, sprint updates, milestones |
| LOW | No | Batched daily | No | Weekly summaries, cost reports |

### Usage
```bash
python3 /home/aielevate/notify.py -t "Title" -b "Body" -p high
python3 /home/aielevate/notify.py -t "Title" -b "Body" -p medium --to braun peter
python3 /home/aielevate/notify.py -t "Title" -b "Body" -p critical --to all
```

### From Python
```python
sys.path.insert(0, "/home/aielevate")
from notify import send
send("Title", "Body", priority="high", to=["braun", "peter"])
```

Recipients: braun, peter, mike, charlie, or "all"

Legacy `send-alert.py` still works (routes to HIGH priority).
