#!/usr/bin/env python3
"""Collaborative Proposal Generator — multi-agent proposal pipeline.

Each department contributes to the proposal. The pipeline runs sequentially
through the agent queue, each agent adding their section. The final output
goes to the approval queue for Braun.

Pipeline:
  1. Scout finds job → bid_strategy scores it → passes to Engineering
  2. Engineering assesses feasibility, estimates effort, flags risks
  3. Marketing crafts narrative, selects portfolio items, writes proposal
  4. Sales sets pricing, payment terms, commercial strategy
  5. CS evaluates client fit, recommends relationship approach
  6. Ops reviews resource availability, approves or blocks
  7. Final proposal queued for human approval

All communication between agents is via the proposal document itself —
each agent reads what the previous agents wrote and adds their section.
No sessions_send needed. The document is the medium.

Usage:
  from collaborative_proposal import start_proposal_pipeline

  start_proposal_pipeline(job={
      "id": "abc123",
      "platform": "upwork",
      "title": "Build AI chatbot",
      "description": "...",
      "skills": "python, fastapi, llm",
      "budget_min": 5000,
      "budget_max": 8000,
      "url": "https://upwork.com/jobs/...",
  })
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/aielevate")

PROPOSALS_DIR = Path("/opt/ai-elevate/gigforge/memory/proposals/collaborative")


def start_proposal_pipeline(job: dict) -> dict:
    """Start the collaborative proposal pipeline for a job."""
    PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)

    job_id = job.get("id", "unknown")
    slug = job.get("title", "job")[:40].lower().replace(" ", "-").replace("/", "-")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    filename = f"{timestamp}-{slug}.md"
    filepath = PROPOSALS_DIR / filename

    # Step 0: Bid strategy analysis
    from game_theory_v2 import analyze_job_v2 as analyze_job
    analysis = analyze_job(job)

    # Step 0b: Portfolio match
    from portfolio import portfolio_summary
    portfolio = portfolio_summary(f"{job.get('title', '')} {job.get('description', '')} {job.get('skills', '')}")

    # Create the proposal document — each agent adds their section
    doc = f"""# Collaborative Proposal: {job.get('title', 'Untitled')}

**Job ID:** {job_id}
**Platform:** {job.get('platform', '?')}
**URL:** {job.get('url', 'N/A')}
**Client Budget:** {job.get('budget', f"${job.get('budget_min', 0)}-${job.get('budget_max', 0)}")}
**Created:** {datetime.now(timezone.utc).isoformat()[:19]} UTC

---

## 1. Bid Strategy Analysis (automated)

- **Fit Score:** {analysis['fit_score']}/100
- **Win Probability:** {analysis['win_probability']}/100
- **Value Score:** {analysis['value_score']}/100
- **Overall Score:** {analysis['overall_score']:.0f}/100
- **Recommended Bid:** ${analysis['recommended_bid']:.0f}
- **Strategy:** {analysis['bid_strategy']}
- **Proposal Angle:** {analysis['proposal_angle']}

## 2. Portfolio Match (automated)

{portfolio}

## 3. Job Description

{job.get('description', 'No description')}

**Skills requested:** {job.get('skills', 'None listed')}

---

## 4. Engineering Assessment

*[PENDING — gigforge-engineer to fill in]*

Questions to answer:
- Can we deliver this? What tech stack would we use?
- Estimated effort (in hours, not weeks)?
- Any technical risks or unknowns?
- Do we need any external services or APIs?

## 5. Marketing Narrative

*[PENDING — gigforge-sales to fill in]*

Write the actual proposal text the client will read. Include:
- Opening hook (tailored to their specific need)
- Why GigForge (our differentiator for THIS job)
- Relevant portfolio with live links
- Our delivery approach (AI-speed framing)
- Closing call to action

## 6. Commercial Terms

*[PENDING — gigforge-sales to fill in]*

- Proposed price (informed by bid strategy above)
- Payment structure (deposit + milestones)
- Timeline framing
- What's included / excluded

## 7. Client Fit Assessment

*[PENDING — gigforge-advocate to fill in]*

- Is this client likely to be a good long-term relationship?
- Any red flags in the job description?
- Follow-on potential?
- Recommended communication approach?

## 8. Resource Check

*[PENDING — operations to fill in]*

- Do we have capacity for this project?
- Any conflicts with current projects?
- GO / NO-GO recommendation?

---

## Status: PIPELINE IN PROGRESS
"""

    filepath.write_text(doc)

    # Dispatch agents sequentially — each reads the document, adds their section
    # Engineering first (technical feasibility gates everything)
    _dispatch_agent("gigforge-engineer", filepath, "4. Engineering Assessment", job)

    return {
        "proposal_file": str(filepath),
        "job_title": job.get("title", ""),
        "overall_score": analysis["overall_score"],
        "recommended_bid": analysis["recommended_bid"],
        "strategy": analysis["bid_strategy"],
    }


def _dispatch_agent(agent_id: str, filepath: Path, section: str, job: dict):
    """Dispatch an agent to fill in their section of the proposal."""
    msg = (
        f"COLLABORATIVE PROPOSAL — Fill in your section.\n\n"
        f"Read the proposal document at: {filepath}\n"
        f"Find section '{section}' and replace the [PENDING] placeholder with your assessment.\n\n"
        f"Job: {job.get('title', '')}\n"
        f"Platform: {job.get('platform', '')}\n"
        f"Budget: ${job.get('budget_min', 0)}-${job.get('budget_max', 0)}\n\n"
        f"After writing your section, dispatch the NEXT agent in the pipeline:\n\n"
    )

    # Define the chain
    chain = {
        "gigforge-engineer": ("gigforge-sales", "5. Marketing Narrative AND 6. Commercial Terms"),
        "gigforge-sales": ("gigforge-advocate", "7. Client Fit Assessment"),
        "gigforge-advocate": ("operations", "8. Resource Check"),
        "operations": (None, None),  # Final — queue for approval
    }

    next_agent, next_section = chain.get(agent_id, (None, None))

    if next_agent:
        msg += (
            f"After filling in your section, dispatch {next_agent} by running:\n"
            f"  python3 -c \"import subprocess; subprocess.Popen(['agent-queue', '--agent', '{next_agent}', "
            f"'--message', 'COLLABORATIVE PROPOSAL — Fill in section {next_section}. "
            f"Read: {filepath}', '--thinking', 'low', '--timeout', '300'])\"\n"
        )
    else:
        msg += (
            f"You are the LAST agent in the pipeline. After filling in your section:\n"
            f"1. Read the complete proposal document\n"
            f"2. If GO: queue it for human approval using proposal_queue:\n"
            f"   from proposal_queue import queue_proposal\n"
            f"   queue_proposal(platform='{job.get('platform', 'unknown')}', "
            f"job_title='{job.get('title', '')[:50]}', "
            f"job_url='{job.get('url', '')}', "
            f"job_budget='${job.get('budget_min', 0)}-${job.get('budget_max', 0)}', "
            f"proposal_text=open('{filepath}').read(), "
            f"recommended_bid='from document', org='gigforge', drafted_by='collaborative')\n"
            f"3. If NO-GO: update the document status to DECLINED and notify ops.\n"
        )

    subprocess.Popen(
        ["agent-queue", "--agent", agent_id, "--message", msg,
         "--thinking", "low", "--timeout", "300"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Collaborative Proposal")
    parser.add_argument("--title", required=True)
    parser.add_argument("--platform", default="upwork")
    parser.add_argument("--description", default="")
    parser.add_argument("--skills", default="")
    parser.add_argument("--budget-min", type=float, default=0)
    parser.add_argument("--budget-max", type=float, default=0)
    parser.add_argument("--url", default="")
    args = parser.parse_args()

    result = start_proposal_pipeline({
        "id": f"manual-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}",
        "platform": args.platform,
        "title": args.title,
        "description": args.description,
        "skills": args.skills,
        "budget_min": args.budget_min,
        "budget_max": args.budget_max,
        "url": args.url,
    })
    print(f"Pipeline started: {result['proposal_file']}")
    print(f"Score: {result['overall_score']:.0f}, Bid: ${result['recommended_bid']:.0f}, Strategy: {result['strategy']}")
