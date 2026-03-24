#!/usr/bin/env python3
"""

log = get_logger("plane_ops")

Plane Project Management — Agent Helper

Bug lifecycle:
  Backlog -> Todo -> In Progress -> In Review (QA) -> Done
                   ^                |
                   +-- Reopened <---+

Rules:
  - Bug titles MUST include the app/component: "[Course Creator] crash-loop in ai-assistant"
  - PM triages: Backlog -> Todo (assigns engineer)
  - Engineers can move: Todo -> In Progress -> In Review
  - "In Review" means QA is testing the fix (functional + regression)
  - QA passes: notifies reporter for sign-off -> reporter moves to Done
  - QA fails: QA moves to Reopened with failure details -> back to engineer
  - Only the REPORTER can move In Review -> Done (final sign-off after QA passes)
  - Reporter or QA can Reopen

Usage:
    import sys; sys.path.insert(0, "/home/aielevate")
    from plane_ops import Plane

    p = Plane("gigforge")

    # File a bug -- app name is REQUIRED
    p.create_bug(
        app="Course Creator",
        title="ai-assistant-service crash-loop on deploy",
        description="Full details...",
        priority="high",
        labels=["crash-loop", "deployment"],
        reporter="gigforge-devops",
    )

    # PM triages and assigns
    p.assign_bug(project="BUG", issue_id="...", assignee="gigforge-dev-backend")

    # Engineer picks up and works
    p.set_state(project="BUG", issue_id="...", state="In Progress")
    p.add_comment(project="BUG", issue_id="...", author="gigforge-dev-backend",
        body="Root cause: shared volume mount timing. Fix: added retry logic.")

    # Engineer submits for QA review (NOT Done)
    p.submit_to_qa(project="BUG", issue_id="...", engineer="gigforge-dev-backend",
        comment="Fix deployed. Added import retry wrapper.")

    # QA tests (functional + regression) and either passes or fails
    p.qa_pass(project="BUG", issue_id="...", qa_agent="gigforge-qa",
        comment="All tests pass. Regression suite clean. Ready for reporter sign-off.")
    # OR
    p.qa_fail(project="BUG", issue_id="...", qa_agent="gigforge-qa",
        comment="Fix resolves crash-loop but introduces regression in health endpoint.")

    # Reporter signs off (only after QA passes)
    p.sign_off(project="BUG", issue_id="...", reporter="gigforge-devops",
        comment="Verified fix. QA passed. Closing.")
"""

import json
import os
import urllib.request
from datetime import datetime, timezone
from exceptions import AiElevateError  # TODO: Use specific exception types
from logging_config import get_logger
import argparse

log = get_logger("plane_ops")

# Load credentials
_creds = {}
_creds_file = "/opt/ai-elevate/credentials/plane.env"
if os.path.exists(_creds_file):
    with open(_creds_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                _creds[k.strip()] = v.strip()

_ORG_CONFIG = {
    "gigforge": {
        "url": _creds.get("PLANE_GF_URL", "http://localhost:8801"),
        "token": _creds.get("PLANE_GF_TOKEN", ""),
        "workspace": "gigforge",
        "projects": {
            "BUG": _creds.get("PLANE_GF_BUG_PROJECT", ""),
            "CRM": _creds.get("PLANE_GF_CRM_PROJECT", ""),
            "FEAT": _creds.get("PLANE_GF_FEAT_PROJECT", ""),
            "TKT": _creds.get("PLANE_GF_TKT_PROJECT", ""),
            "GFWEB": _creds.get("PLANE_GF_GFWEB_PROJECT", ""),
            "CRYPTO": _creds.get("PLANE_GF_CRYPTO_PROJECT", ""),
            "BACSWN": _creds.get("PLANE_GF_BACSWN_PROJECT", ""),
            "ALICE": _creds.get("PLANE_GF_ALICE_PROJECT", ""),
        },
        "bug_project": "BUG",
    },
    "techuni": {
        "url": _creds.get("PLANE_TU_URL", "http://localhost:8802"),
        "token": _creds.get("PLANE_TU_TOKEN", ""),
        "workspace": "techuni",
        "projects": {
            "BUG": _creds.get("PLANE_TU_BUG_PROJECT", ""),
            "CC": _creds.get("PLANE_TU_CC_PROJECT", ""),
            "WEB": _creds.get("PLANE_TU_WEB_PROJECT", ""),
            "FEAT": _creds.get("PLANE_TU_FEAT_PROJECT", ""),
            "TKT": _creds.get("PLANE_TU_TKT_PROJECT", ""),
        },
        "bug_project": "BUG",
    },
    "ai-elevate": {
        "url": _creds.get("PLANE_AIE_URL", "http://localhost:8800"),
        "token": _creds.get("PLANE_AIE_TOKEN", ""),
        "workspace": "ai-elevate",
        "projects": {
            "BUG": _creds.get("PLANE_AIE_BUG_PROJECT", ""),
            "PUB": _creds.get("PLANE_AIE_PUB_PROJECT", ""),
        },
        "bug_project": "BUG",
    },
}

PRIORITY_MAP = {"urgent": 1, "high": 2, "medium": 3, "low": 4, "none": 0}

# Org prefix for ticket IDs
ORG_PREFIX = {
    "gigforge": "GF",
    "techuni": "TU",
    "ai-elevate": "AIE",
}


class Plane:
    """TODO: Add docstring — what this class does, why it exists, how to use it."""
    def __init__(self, org: str):
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        if org not in _ORG_CONFIG:
            raise ValueError(f"Unknown org: {org}. Use: {list(_ORG_CONFIG.keys())}")
        cfg = _ORG_CONFIG[org]
        self.org = org
        self.base_url = cfg["url"]
        self.token = cfg["token"]
        # Reload token from file (tokens refreshed by cron)
        try:
            _tk = {"gigforge": "PLANE_GF_TOKEN", "techuni": "PLANE_TU_TOKEN", "ai-elevate": "PLANE_AIE_TOKEN"}.get(org)
            if _tk:
                with open("/opt/ai-elevate/credentials/plane.env") as _f:
                    for _line in _f:
                        if _line.strip().startswith(_tk + "="):
                            self.token = _line.strip().split("=", 1)[1]
        except:
            pass
        self.workspace = cfg["workspace"]
        self.projects = cfg["projects"]
        self.bug_project = cfg["bug_project"]
        self._state_cache = {}

    def _api(self, method: str, path: str, data: dict = None) -> dict:
        url = f"{self.base_url}/api/v1/workspaces/{self.workspace}/{path}"
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("X-API-Key", self.token)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            err_body = e.read().decode() if e.fp else ""
            raise RuntimeError(f"Plane API {method} {path} => {e.code}: {err_body}")

    def _resolve_project(self, project: str) -> str:
        pid = self.projects.get(project.upper())
        if not pid:
            raise ValueError(f"Unknown project '{project}' for {self.org}. Available: {list(self.projects.keys())}")
        return pid

    def _get_states(self, project_id: str) -> dict:
        if project_id not in self._state_cache:
            result = self._api("GET", f"projects/{project_id}/states/")
            states = result.get("results", []) if isinstance(result, dict) else result
            self._state_cache[project_id] = {s["name"]: s["id"] for s in states}
        return self._state_cache[project_id]

    def _resolve_labels(self, project_id: str, label_names: list) -> list:
        if not label_names:
            return []
        labels = self._api("GET", f"projects/{project_id}/labels/")
        label_list = labels.get("results", labels) if isinstance(labels, dict) else labels
        if isinstance(label_list, dict):
            label_list = label_list.get("results", [])
        name_to_id = {l["name"].lower(): l["id"] for l in label_list}
        return [name_to_id[n.lower()] for n in label_names if n.lower() in name_to_id]

    def _html(self, *parts):
        return "".join(parts)

    # == Bug Reports ==

    def create_bug(self, app: str, title: str, description: str,
                   priority: str = "high", labels: list = None,
                   reporter: str = "unknown") -> dict:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        """
        File a bug. App name is REQUIRED and prepended to the title.
        """
        full_title = f"[{app}] {title}"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        full_desc = self._html(
            "<h3>Bug Report</h3>",
            f"<p><b>App:</b> {app}</p>",
            f"<p><b>Reporter:</b> {reporter}</p>",
            f"<p><b>Filed:</b> {now}</p>",
            f"<p><b>Priority:</b> {priority}</p>",
            "<hr/>",
            "<h4>Description</h4>",
            f"<p>{description}</p>",
            "<hr/>",
            f"<p><i>Workflow: Engineer fixes -> submits to QA -> QA tests (functional + regression) -> ",
            f"QA passes -> reporter ({reporter}) signs off -> Done. ",
            "Only the reporter can give final sign-off. QA or reporter can reopen.</i></p>",
        )

        pid = self._resolve_project(self.bug_project)
        label_ids = self._resolve_labels(pid, labels or ["bug"])
        states = self._get_states(pid)

        data = {
            "name": full_title,
            "description_html": full_desc,
            "priority": priority if priority in PRIORITY_MAP else "high",
        }
        if label_ids:
            data["label_ids"] = label_ids
        if "Backlog" in states:
            data["state"] = states["Backlog"]

        result = self._api("POST", f"projects/{pid}/issues/", data)
        seq = result.get("sequence_id", "?")
        log.info("[Plane] Filed %s/BUG-%s: [%s] %s", extra={"seq": seq, "full_title": full_title})
        log.info("[Plane] Reporter: %s -- only they can sign off", extra={"reporter": reporter})
        return result

    # == State Management ==

    def set_state(self, project: str, issue_id: str, state: str) -> dict:
        """
        Change issue state.
        Valid: Backlog, Todo, In Progress, In Review, Done, Reopened, Cancelled.
        Engineers: Todo -> In Progress -> In Review.
        Reporter only: In Review -> Done or Reopened.
        PM: Backlog -> Todo.
        """
        pid = self._resolve_project(project)
        states = self._get_states(pid)
        if state not in states:
            raise ValueError(f"Unknown state '{state}'. Available: {list(states.keys())}")
        result = self._api("PATCH", f"projects/{pid}/issues/{issue_id}/", {
            "state": states[state]
        })
        log.info("[Plane] %s-%s issue -> %s", extra={"project": project, "state": state})
        return result

    def assign_bug(self, project: str, issue_id: str, assignee: str) -> dict:
        """Assign a bug to an engineer."""
        pid = self._resolve_project(project)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        self._api("POST", f"projects/{pid}/issues/{issue_id}/comments/", {
            "comment_html": f"<p><b>Assigned to:</b> {assignee} at {now}</p>"
        })
        states = self._get_states(pid)
        issue = self._api("GET", f"projects/{pid}/issues/{issue_id}/")
        current_state_id = issue.get("state")
        if current_state_id == states.get("Backlog"):
            self._api("PATCH", f"projects/{pid}/issues/{issue_id}/", {
                "state": states["Todo"]
            })
            log.info("[Plane] %s-%s issue assigned to %s, moved to Todo", extra={"project": project, "assignee": assignee})
        else:
            log.info("[Plane] %s-%s issue assigned to %s", extra={"project": project, "assignee": assignee})
        return issue

    def submit_to_qa(self, project: str, issue_id: str, engineer: str,
                     comment: str = "") -> dict:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        """Engineer submits fix for QA testing. Moves to In Review and notifies QA."""
        pid = self._resolve_project(project)
        states = self._get_states(pid)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        self._api("POST", f"projects/{pid}/issues/{issue_id}/comments/", {
            "comment_html": self._html(
                f"<p><b>SUBMITTED FOR QA by {engineer}</b></p>",
                f"<p>{comment or 'Fix ready for QA testing.'}</p>",
                f"<p><i>QA: please run functional tests and regression suite. {now}</i></p>",
            )
        })
        result = self._api("PATCH", f"projects/{pid}/issues/{issue_id}/", {
            "state": states["In Review"]
        })
        log.info("[Plane] %s-%s issue submitted to QA by %s -> In Review", extra={"project": project, "engineer": engineer})
        return result

    def qa_pass(self, project: str, issue_id: str, qa_agent: str,
                comment: str = "") -> dict:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        """QA approves the fix. Stays in In Review — awaits reporter sign-off."""
        pid = self._resolve_project(project)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        self._api("POST", f"projects/{pid}/issues/{issue_id}/comments/", {
            "comment_html": self._html(
                f"<p><b>QA PASSED by {qa_agent}</b></p>",
                f"<p>{comment or 'All tests pass. Regression suite clean.'}</p>",
                f"<p><i>Ready for reporter sign-off. {now}</i></p>",
            )
        })
        log.info("[Plane] %s-%s issue QA PASSED by %s — awaiting reporter sign-off", extra={"project": project, "qa_agent": qa_agent})
        return {"status": "qa_passed", "awaiting": "reporter_sign_off"}

    def qa_fail(self, project: str, issue_id: str, qa_agent: str,
                comment: str = "") -> dict:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        """QA rejects the fix. Moves to Reopened — back to engineer."""
        pid = self._resolve_project(project)
        states = self._get_states(pid)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        self._api("POST", f"projects/{pid}/issues/{issue_id}/comments/", {
            "comment_html": self._html(
                f"<p><b>QA FAILED by {qa_agent}</b></p>",
                f"<p>{comment or 'Tests failed. See details below.'}</p>",
                f"<p><i>Returning to engineer for rework. {now}</i></p>",
            )
        })
        target = states.get("Reopened", states.get("In Progress"))
        result = self._api("PATCH", f"projects/{pid}/issues/{issue_id}/", {
            "state": target
        })
        log.info("[Plane] %s-%s issue QA FAILED by %s -> Reopened", extra={"project": project, "qa_agent": qa_agent})
        return result

    def sign_off(self, project: str, issue_id: str, reporter: str,
                 comment: str = "") -> dict:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        """Reporter signs off -- moves to Done. ONLY after QA has passed."""
        pid = self._resolve_project(project)
        states = self._get_states(pid)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        self._api("POST", f"projects/{pid}/issues/{issue_id}/comments/", {
            "comment_html": self._html(
                f"<p><b>SIGNED OFF by {reporter}</b></p>",
                f"<p>{comment or 'Fix verified. Closing.'}</p>",
                f"<p><i>{now}</i></p>",
            )
        })
        result = self._api("PATCH", f"projects/{pid}/issues/{issue_id}/", {
            "state": states["Done"]
        })
        log.info("[Plane] %s-%s issue SIGNED OFF by %s -> Done", extra={"project": project, "reporter": reporter})
        return result

    def reopen(self, project: str, issue_id: str, reporter: str,
               comment: str = "") -> dict:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        """Reporter reopens -- moves to Reopened. ONLY the reporter can do this."""
        pid = self._resolve_project(project)
        states = self._get_states(pid)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        self._api("POST", f"projects/{pid}/issues/{issue_id}/comments/", {
            "comment_html": self._html(
                f"<p><b>REOPENED by {reporter}</b></p>",
                f"<p>{comment or 'Fix did not resolve the issue.'}</p>",
                f"<p><i>{now}</i></p>",
            )
        })
        target = states.get("Reopened", states.get("In Progress"))
        result = self._api("PATCH", f"projects/{pid}/issues/{issue_id}/", {
            "state": target
        })
        log.info("[Plane] %s-%s issue REOPENED by %s", extra={"project": project, "reporter": reporter})
        return result

    # == Comments ==

    def add_comment(self, project: str, issue_id: str, body: str,
                    author: str = "unknown") -> dict:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        """Add a comment with author attribution."""
        pid = self._resolve_project(project)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return self._api("POST", f"projects/{pid}/issues/{issue_id}/comments/", {
            "comment_html": f"<p><b>{author}:</b> {body}</p><p><i>{now}</i></p>"
        })

    def list_comments(self, project: str, issue_id: str) -> list:
        """List all comments on an issue."""
        pid = self._resolve_project(project)
        result = self._api("GET", f"projects/{pid}/issues/{issue_id}/comments/")
        return result.get("results", result) if isinstance(result, dict) else result

    # == Issues (generic) ==

    def create_issue(self, project: str, title: str, description: str = "",
                     priority: str = "medium", labels: list = None,
                     state: str = None) -> dict:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        """Create an issue in the specified project."""
        pid = self._resolve_project(project)
        label_ids = self._resolve_labels(pid, labels or [])
        data = {
            "name": title,
            "description_html": f"<p>{description}</p>" if description else "",
            "priority": priority if priority in PRIORITY_MAP else "medium",
        }
        if label_ids:
            data["label_ids"] = label_ids
        if state:
            states = self._get_states(pid)
            if state in states:
                data["state"] = states[state]
        result = self._api("POST", f"projects/{pid}/issues/", data)
        seq = result.get("sequence_id", "?")
        prefix = ORG_PREFIX.get(self.org, self.org.upper()[:3])
        ticket_id = f"{prefix}-{project.upper()}-{str(seq).zfill(3)}"
        result["ticket_id"] = ticket_id
        log.info("[Plane] Created %s: %s", extra={"ticket_id": ticket_id, "title": title})
        return result

    def list_issues(self, project: str, state: str = None, priority: str = None,
                    page: int = 1) -> dict:
        """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
        """List issues with optional filters."""
        pid = self._resolve_project(project)
        params = [f"page={page}"]
        if priority:
            params.append(f"priority={priority}")
        qs = "&".join(params)
        return self._api("GET", f"projects/{pid}/issues/?{qs}")

    def get_issue(self, project: str, issue_id: str) -> dict:
        """Get issue details."""
        pid = self._resolve_project(project)
        return self._api("GET", f"projects/{pid}/issues/{issue_id}/")

    def update_issue(self, project: str, issue_id: str, **kwargs) -> dict:
        """Update issue fields."""
        pid = self._resolve_project(project)
        return self._api("PATCH", f"projects/{pid}/issues/{issue_id}/", kwargs)

    def list_projects(self) -> list:
        """List all projects in the workspace."""
        result = self._api("GET", "projects/")
        return result.get("results", result) if isinstance(result, dict) else result

    def my_bugs(self) -> dict:
        """List all bugs in the org."""
        return self.list_issues(project=self.bug_project)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Plane Ops")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose output")
    args = parser.parse_args()

    import sys
    if len(sys.argv) < 5:
        print("Usage: python3 plane_ops.py <org> <app> <title> <description> [priority] [labels] [reporter]")
        print("  org: gigforge, techuni, ai-elevate")
        print("  app: Course Creator, CRM, Infrastructure, etc.")
        sys.exit(1)
    org, app, title, desc = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    prio = sys.argv[5] if len(sys.argv) > 5 else "high"
    labels = sys.argv[6].split(",") if len(sys.argv) > 6 else ["bug"]
    reporter = sys.argv[7] if len(sys.argv) > 7 else "unknown"
    p = Plane(org)
    result = p.create_bug(app=app, title=title, description=desc, priority=prio,
                          labels=labels, reporter=reporter)
    print(json.dumps({k: result[k] for k in ["id", "sequence_id", "name", "priority"]
                      if k in result}, indent=2, default=str))
