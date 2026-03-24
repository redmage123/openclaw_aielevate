#!/usr/bin/env python3
"""Build out all 5 scaffold projects in sequence.

log = get_logger("build_all_scaffolds")

Each gets: engineer build → UX review → deploy.
For empty projects (client-portal, cryptoadvisor-web): full spec first.
For existing scaffolds (todo, ai-chat, job-board): complete the build + deploy.
"""
import subprocess
import os
import re
import time
from logging_config import get_logger

env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
log = get_logger("build_all_scaffolds")

def dispatch(agent_id, message, timeout=600):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    from pathlib import Path
    session_dir = Path(f"/home/aielevate/.openclaw/agents/{agent_id}/sessions")
    if session_dir.exists():
        for f in session_dir.glob("*.jsonl"):
            f.unlink()
    proc = subprocess.run(
        ["openclaw", "agent", "--agent", agent_id,
         "--message", message, "--thinking", "low", "--timeout", str(timeout)],
        capture_output=True, text=True, timeout=timeout + 30, env=env,
    )
    output = proc.stdout or ""
    return re.sub(r'\*\[.*?\]\*', '', output, flags=re.DOTALL).strip()

projects = [
    {
        "slug": "todo-rest-api",
        "title": "Todo REST API",
        "has_code": True,
        "desc": "Production-ready REST API. TypeScript, Express 4, PostgreSQL, JWT auth, Zod validation, Jest tests. Needs: verify build works, add Dockerfile if missing, add landing page, deploy.",
    },
    {
        "slug": "ai-chat-widget",
        "title": "AI Chat Widget",
        "has_code": True,
        "desc": "RAG chat system with embeddable widget. Express, PostgreSQL + pgvector, OpenAI embeddings, JWT auth, 37 tests. Needs: verify build, Docker, landing page, deploy.",
    },
    {
        "slug": "job-board",
        "title": "Job Board",
        "has_code": True,
        "desc": "Full-stack job board with RBAC, full-text search, JWT auth. React 19 + Express + PostgreSQL. Docker Compose. Needs: verify frontend builds, deploy.",
    },
    {
        "slug": "client-portal",
        "title": "GigForge Client Portal",
        "has_code": False,
        "desc": "Web dashboard for GigForge clients. See project progress, view deliverables, communicate with team, access reports. React + FastAPI + PostgreSQL. Needs: full build from scratch.",
    },
    {
        "slug": "cryptoadvisor-web",
        "title": "CryptoAdvisor Web",
        "has_code": False,
        "desc": "Web frontend for the CryptoAdvisor dashboard. Needs: check what cryptoadvisor-dashboard has and build a web UI for it.",
    },
]


def _build_project(proj: dict) -> None:
    """Run the 3-step build pipeline for a single project: engineer → UX review → deploy."""
    slug = proj["slug"]
    title = proj["title"]
    project_dir = f"/opt/ai-elevate/gigforge/projects/{slug}"

    if proj["has_code"]:
        log.info("  Step 1/3: Engineer completing build...")
        dispatch("gigforge-engineer",
            f"COMPLETE BUILD: {title}\n"
            f"Project dir: {project_dir}\n\n"
            f"Read the README.md and existing source code.\n"
            f"{proj['desc']}\n\n"
            f"1. Read all existing code — understand what's already built\n"
            f"2. Fix any TypeScript/build errors\n"
            f"3. Ensure Dockerfile and docker-compose.yml exist\n"
            f"4. Add a landing page at GET / that showcases the API/app\n"
            f"   (dark theme, cards showing endpoints, feature list — like devops-starter-kit)\n"
            f"5. Try: npm run build (or tsc)\n"
            f"6. Try: docker compose build\n"
            f"7. Fix any errors\n"
            f"8. Update CHANGELOG.md",
            timeout=300)
    else:
        log.info("  Step 1/3: Engineer building from scratch...")
        dispatch("gigforge-engineer",
            f"BUILD FROM SCRATCH: {title}\n"
            f"Project dir: {project_dir}\n\n"
            f"Read the README.md if it exists for context.\n"
            f"{proj['desc']}\n\n"
            f"Build a complete, working application:\n"
            f"1. Choose appropriate stack (TypeScript/React for frontend, FastAPI or Express for backend)\n"
            f"2. Create all source files — real working code, not stubs\n"
            f"3. Create Dockerfile and docker-compose.yml\n"
            f"4. Add a landing page at the root route\n"
            f"5. Write README.md with setup instructions\n"
            f"6. Try: docker compose build\n"
            f"7. Fix any errors",
            timeout=600)
    log.info("  Step 1/3 done")

    log.info("  Step 2/3: UX review...")
    dispatch("gigforge-ux-designer",
        f"UX REVIEW: {title}\n"
        f"Project dir: {project_dir}\n\n"
        f"Read the source code — focus on any frontend/UI components.\n"
        f"If this is an API-only project, review the landing page for visual quality.\n"
        f"If it has a frontend (React), review:\n"
        f"- Color consistency, typography, spacing\n"
        f"- Dark mode support\n"
        f"- Mobile responsiveness\n"
        f"- Accessibility basics\n\n"
        f"Fix any P0/P1 issues directly.\n"
        f"Write findings to {project_dir}/UX_REVIEW.md",
        timeout=300)
    log.info("  Step 2/3 done")

    log.info("  Step 3/3: Deploying...")
    dispatch("gigforge-devops",
        f"DEPLOY: {title}\n"
        f"Project dir: {project_dir}\n\n"
        f"1. Check for docker-compose.yml or Dockerfile\n"
        f"2. Find a free port in 4102-4199 range (check ss -ltnp)\n"
        f"3. Build and deploy: docker compose up -d --build\n"
        f"   Or: docker build + docker run\n"
        f"4. If it fails, read the error, fix it, retry\n"
        f"5. Verify the app responds on the port\n"
        f"6. Report the URL",
        timeout=300)
    log.info("  Step 3/3 done")


def main() -> None:
    """Build all scaffold projects in sequence."""
    for i, proj in enumerate(projects):
        log.info("[%d/5] Building: %s", i + 1, proj["title"])
        _build_project(proj)
        log.info("  [%s] COMPLETE", proj["title"])
    log.info("ALL 5 PROJECTS BUILT")


if __name__ == "__main__":
    main()
