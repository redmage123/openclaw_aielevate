import argparse
#!/usr/bin/env python3
"""Email Templates — professional formatted templates for each project lifecycle stage.

10 templates, one per stage:
  1. initial_response — first reply to an inquiry
  2. proposal — formal proposal/quote
  3. acceptance_confirmation — confirming we received their go-ahead
  4. advocate_introduction — CS/advocate introduces themselves
  5. project_kickoff — project officially started
  6. progress_update — milestone/progress report
  7. scope_change — responding to scope change request
  8. preview_ready — preview URL for customer review
  9. delivery_complete — project delivered
  10. feedback_request — post-delivery thank you + feedback form

Usage:
  from email_templates import get_template, format_email, list_templates

  # Get a template
  template = get_template("initial_response")

  # Format with variables
  email = format_email("initial_response", {
      "customer_name": "Sarah",
      "agent_name": "Sam Carrington",
      "agent_title": "Sales Lead",
      "company": "GigForge",
      "body": "The actual response content...",
  })
"""

TEMPLATES = {
    "initial_response": {
        "stage": "Pre-contract",
        "description": "First reply to a customer inquiry",
        "template": """Hi {customer_name},

{body}

If you have any questions or want to discuss further, just reply to this email.

Best regards,
{agent_name}
{agent_title}, {company}""",
    },

    "proposal": {
        "stage": "Pre-contract",
        "description": "Formal proposal or quote",
        "template": """Hi {customer_name},

Thank you for your interest. Here is our proposal for your project:

{body}

To move forward, simply reply to this email confirming you'd like to proceed. We can start the same day.

Best regards,
{agent_name}
{agent_title}, {company}""",
    },

    "acceptance_confirmation": {
        "stage": "Contract",
        "description": "Confirming receipt of customer's acceptance",
        "template": """Hi {customer_name},

Great news — we're confirmed and ready to go.

{body}

You'll be hearing from your dedicated project liaison shortly. They'll be your single point of contact throughout the build.

Looking forward to delivering something you'll be proud of.

Best regards,
{agent_name}
{agent_title}, {company}""",
    },

    "advocate_introduction": {
        "stage": "Kickoff",
        "description": "CS/Advocate introduces themselves to the customer",
        "template": """Hi {customer_name},

I'm {agent_name}, your dedicated project liaison at {company}. I'll be your single point of contact from here through delivery.

{body}

If you need anything at all, just reply to this email. I'm here to make this as smooth as possible.

Best regards,
{agent_name}
{agent_title}, {company}""",
    },

    "project_kickoff": {
        "stage": "Build",
        "description": "Project officially started, engineering working",
        "template": """Hi {customer_name},

Your project is officially underway. Our engineering team has started building.

{body}

I'll keep you posted on progress. You'll receive a preview link as soon as we have something to show you.

Best regards,
{agent_name}
{agent_title}, {company}""",
    },

    "progress_update": {
        "stage": "Build",
        "description": "Milestone or progress update during build",
        "template": """Hi {customer_name},

Here's a quick update on your project:

{body}

Let me know if you have any questions or feedback.

Best regards,
{agent_name}
{agent_title}, {company}""",
    },

    "scope_change": {
        "stage": "Build",
        "description": "Responding to a customer scope change request",
        "template": """Hi {customer_name},

Thanks for the additional details. I've reviewed your request with the team.

{body}

Let me know how you'd like to proceed and we'll adjust accordingly.

Best regards,
{agent_name}
{agent_title}, {company}""",
    },

    "preview_ready": {
        "stage": "Review",
        "description": "Preview URL ready for customer review",
        "template": """Hi {customer_name},

Your project is ready for review. Here's your preview link:

{preview_url}

{body}

Take your time looking through it. When you're ready, let me know:
- Any changes you'd like
- Anything that looks great
- Or if you're happy to go live

Best regards,
{agent_name}
{agent_title}, {company}""",
    },

    "delivery_complete": {
        "stage": "Delivery",
        "description": "Project delivered and live",
        "template": """Hi {customer_name},

Your project is live and ready to use.

{body}

We include 30 days of bug fixes and support from today. If anything comes up, just email us.

It's been a pleasure working with you. Thank you for choosing {company}.

Best regards,
{agent_name}
{agent_title}, {company}""",
    },

    "feedback_request": {
        "stage": "Post-delivery",
        "description": "Thank you email with feedback form",
        "template": """Hi {customer_name},

Thank you for choosing {company} for your {project_title} project. We hope you're happy with the result.

We'd love to hear about your experience. Could you reply with:

1. Overall rating (1-10):
2. What went well?
3. What could we improve?
4. Would you recommend us? (yes/no)
5. Any other comments?

{body}

Thank you again for your trust.

Best regards,
{agent_name}
{agent_title}, {company}""",
    },
}


def get_template(template_name: str) -> dict:
    """Get a template by name."""
    return TEMPLATES.get(template_name)


def list_templates() -> list:
    """List all available templates."""
    return [{"name": k, "stage": v["stage"], "description": v["description"]} for k, v in TEMPLATES.items()]


def format_email(template_name: str, variables: dict) -> str:
    """Format a template with variables. Unknown variables are left as-is."""
    template = TEMPLATES.get(template_name)
    if not template:
        return variables.get("body", "")

    text = template["template"]
    for key, value in variables.items():
        text = text.replace("{" + key + "}", str(value))

    # Remove any remaining unfilled optional variables
    import re
    text = re.sub(r'\{preview_url\}', '', text)
    text = re.sub(r'\{project_title\}', 'your project', text)

    return text.strip()


def pick_template(subject: str, body: str, agent_id: str, is_first_email: bool = False) -> str:
    """Auto-pick the best template based on context."""
    lower_body = body.lower()
    lower_subject = subject.lower()

    if "feedback" in lower_subject or "how was" in lower_subject:
        return "feedback_request"
    if "preview" in lower_body and ("http" in lower_body or "url" in lower_body):
        return "preview_ready"
    if "delivered" in lower_body or "live" in lower_body or "launched" in lower_body:
        return "delivery_complete"
    if "scope" in lower_body or "additional" in lower_body or "change" in lower_body:
        return "scope_change"
    if "progress" in lower_body or "update" in lower_body or "milestone" in lower_body:
        return "progress_update"
    if "started" in lower_body or "underway" in lower_body or "kicked off" in lower_body:
        return "project_kickoff"
    if "advocate" in agent_id or "clientservices" in agent_id:
        if is_first_email:
            return "advocate_introduction"
        return "progress_update"
    if "proposal" in lower_body or "quote" in lower_body or "EUR" in body or "$" in body:
        return "proposal"
    if "confirmed" in lower_body or "go ahead" in lower_body or "accepted" in lower_body:
        return "acceptance_confirmation"
    if is_first_email:
        return "initial_response"

    return "initial_response"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Email Templates")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose output")
    args = parser.parse_args()

    for t in list_templates():
        print(f"  {t['name']:25s} [{t['stage']:15s}] {t['description']}")
