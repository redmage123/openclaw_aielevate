#!/usr/bin/env python3
"""Plane Helper — fallback to Django shell when API tokens fail."""
import json
import subprocess
import sys

def create_bug_via_django(org, title, description, priority="high"):
    """Create a bug via Django shell (bypasses token auth)."""
    compose_dirs = {
        "gigforge": "/opt/ai-elevate/infra/plane/gigforge",
        "techuni": "/opt/ai-elevate/infra/plane/techuni",
        "ai-elevate": "/opt/ai-elevate/infra/plane/ai-elevate",
    }
    services = {
        "gigforge": "gf-plane-backend",
        "techuni": "tu-plane-backend",
        "ai-elevate": "plane-backend",
    }
    admins = {
        "gigforge": "admin@gigforge.ai",
        "techuni": "admin@techuni.ai",
        "ai-elevate": "admin@ai-elevate.ai",
    }
    
    compose_dir = compose_dirs.get(org)
    service = services.get(org)
    admin = admins.get(org)
    
    if not compose_dir:
        log.info("Unknown org: %s", extra={"org": org})
        return None
    
    # Escape quotes in title and description
    title_esc = title.replace("'", "\\'").replace('"', '\\"')
    desc_esc = description.replace("'", "\\'").replace('"', '\\"')
    
    cmd = f'''cd {compose_dir} && docker compose exec -T {service} python manage.py shell -c "
from plane.db.models import Issue, Project, User, State
import argparse
u = User.objects.get(email='{admin}')
p = Project.objects.get(identifier='BUG')
backlog = State.objects.filter(project=p, group='backlog').first()
issue = Issue.objects.create(
    name='{title_esc}',
    description_html='<p>{desc_esc}</p>',
    priority='{priority}',
    project=p,
    workspace=p.workspace,
    state=backlog,
    created_by=u,
)
log.info("BUG-{{issue.sequence_id}}|{{issue.id}}")
"'''
    
    result = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=30)
    output = result.stdout.strip().split("\n")[-1]
    if "|" in output:
        seq, uid = output.split("|")
        return {"sequence_id": seq.replace("BUG-", ""), "id": uid, "name": title}
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Plane Helper")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose output")
    args = parser.parse_args()

    if len(sys.argv) < 4:
        print("Usage: plane_helper.py <org> <title> <description> [priority]")
        sys.exit(1)
    org, title, desc = sys.argv[1], sys.argv[2], sys.argv[3]
    priority = sys.argv[4] if len(sys.argv) > 4 else "high"
    result = create_bug_via_django(org, title, desc, priority)
    if result:
        print(json.dumps(result, indent=2))
    else:
        print("Failed to create bug")
