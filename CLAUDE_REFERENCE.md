# OpenClaw Global Reference

Detailed documentation for all agents.

## Communication

### Email Domains

| Org | Agent Sends From | Human Receives At |
|-----|-----------------|-------------------|
| GigForge | `@gigforge.ai` (NEVER use mg.gigforge.ai or internal.ai-elevate.ai for external emails) | `@gigforge.ai` (Mailgun) |
| TechUni | `@techuni.ai` (NEVER use mg.techuni.ai) | `@techuni.ai` (Mailgun) |
| AI Elevate | `@internal.ai-elevate.ai` | `@ai-elevate.ai` (Zoho) |

### Rules
- NEVER offer, suggest, or schedule phone calls, video calls, Zoom meetings, Teams meetings, or any kind of call. You have no phone and no calendar. All communication is by email only.
- If someone REQUESTS a call, say you will coordinate by email and escalate to the human team via notify.py.
- All agents MUST use send_email() for sending email:
  ```python
  from send_email import send_email
  send_email(to="email", subject="Subj", body="Body", agent_id="your-agent-id", cc="optional")
  ```
  This automatically selects the correct From address and Mailgun domain. Do NOT use urllib/Mailgun directly.
- Email Domain Selection Rule:

Full details: /home/aielevate/.openclaw/CLAUDE_REFERENCE.md
