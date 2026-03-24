#!/usr/bin/env python3
"""Code Quality Scanner — scans codebases, scores them, builds prioritized remediation backlog.

Our own Desloppify. Runs static analysis, detects slop patterns, scores the codebase,
and provides a `next` command for agents to fix one issue at a time.

Usage:
  # Scan a project
  python3 code_quality.py scan --path /opt/ai-elevate/gigforge/projects/cryptoadvisor-dashboard

  # Get the score
  python3 code_quality.py score --path /opt/ai-elevate/gigforge/projects/cryptoadvisor-dashboard

  # Get the next issue to fix
  python3 code_quality.py next --path /opt/ai-elevate/gigforge/projects/cryptoadvisor-dashboard

  # Mark current issue as fixed and get the next one
  python3 code_quality.py resolve --path /opt/ai-elevate/gigforge/projects/cryptoadvisor-dashboard

  # Full report
  python3 code_quality.py report --path /opt/ai-elevate/gigforge/projects/cryptoadvisor-dashboard

Library usage:
  from code_quality import scan_project, get_score, get_next_issue, resolve_issue
"""

import json
import os
import re
import subprocess
import hashlib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from exceptions import AiElevateError  # TODO: Use specific exception types

SCAN_DB_DIR = Path("/opt/ai-elevate/code-quality")

# Issue severity weights (higher = worse)
SEVERITY = {"critical": 10, "high": 5, "medium": 3, "low": 1}

# File extensions we care about
CODE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java",
    ".rb", ".php", ".swift", ".kt", ".cs", ".vue", ".svelte",
}

IGNORE_DIRS = {
    "node_modules", ".venv", "venv", "__pycache__", ".git", ".next",
    "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
    "vendor", "target", "coverage", ".terraform",
    "static", "assets", "public", "migrations", "site-packages",
    ".eggs", ".cache", "htmlcov", "egg-info",
}

# Skip minified/generated files
IGNORE_PATTERNS = {
    ".min.js", ".min.css", ".bundle.js", ".chunk.js",
    ".map", ".lock", ".generated.",
}


def _find_source_files(project_dir: str) -> list:
    """Find all source code files, excluding vendored/generated dirs."""
    files = []
    abs_project = os.path.abspath(project_dir)
    for root, dirs, filenames in os.walk(abs_project):
        # Filter directories in-place AND check path segments
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        # Double-check: skip if any path segment is in IGNORE_DIRS
        rel_root = os.path.relpath(root, abs_project)
        if any(part in IGNORE_DIRS for part in rel_root.split(os.sep)):
            dirs.clear()
            continue
        for f in filenames:
            ext = os.path.splitext(f)[1]
            if ext in CODE_EXTENSIONS:
                # Skip minified/generated files
                if any(f.endswith(pat) for pat in IGNORE_PATTERNS):
                    continue
                files.append(os.path.join(root, f))
    return files


def _read_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:  # TODO: Replace with specific exception type from exceptions.py
        return ""


def _get_db_path(project_dir: str) -> Path:
    project_hash = hashlib.md5(project_dir.encode()).hexdigest()[:12]
    project_name = Path(project_dir).name
    return SCAN_DB_DIR / f"{project_name}-{project_hash}.json"


def _load_scan(project_dir: str) -> dict:
    db_path = _get_db_path(project_dir)
    if db_path.exists():
        return json.loads(db_path.read_text())
    return {"project": project_dir, "issues": [], "resolved": [], "scans": []}


def _save_scan(project_dir: str, data: dict):
    SCAN_DB_DIR.mkdir(parents=True, exist_ok=True)
    db_path = _get_db_path(project_dir)
    db_path.write_text(json.dumps(data, indent=2))


# --- Detectors ---

def detect_dead_code(files: list, project_dir: str) -> list:
    """Find unused imports, unreachable code, unused variables."""
    issues = []

    for filepath in files:
        content = _read_file(filepath)
        lines = content.split("\n")
        rel = os.path.relpath(filepath, project_dir)

        for i, line in enumerate(lines, 1):
            stripped = line.strip()

            # Commented-out code (not regular comments)
            if stripped.startswith("#") and any(kw in stripped for kw in ["import ", "def ", "class ", "return ", "if ", "for ", "while "]):
                issues.append({
                    "type": "dead_code",
                    "subtype": "commented_out_code",
                    "file": rel,
                    "line": i,
                    "severity": "low",
                    "message": f"Commented-out code: {stripped[:80]}",
                })

            # TODO/FIXME/HACK/XXX
            if re.search(r"\b(TODO|FIXME|HACK|XXX)\b", stripped):
                issues.append({
                    "type": "tech_debt",
                    "subtype": "todo",
                    "file": rel,
                    "line": i,
                    "severity": "low",
                    "message": f"Unresolved marker: {stripped[:80]}",
                })

            # pass-only functions/methods
            if stripped == "pass" and i > 1:
                prev = lines[i-2].strip() if i >= 2 else ""
                if prev.startswith("def ") or prev.startswith("class "):
                    issues.append({
                        "type": "dead_code",
                        "subtype": "empty_function",
                        "file": rel,
                        "line": i-1,
                        "severity": "medium",
                        "message": f"Empty function/class: {prev[:60]}",
                    })

    return issues


def detect_complexity(files: list, project_dir: str) -> list:
    """Find overly complex files and functions."""
    issues = []

    for filepath in files:
        content = _read_file(filepath)
        lines = content.split("\n")
        rel = os.path.relpath(filepath, project_dir)
        loc = len([l for l in lines if l.strip() and not l.strip().startswith("#") and not l.strip().startswith("//")])

        # File too long
        if loc > 500:
            sev = "critical" if loc > 1000 else "high" if loc > 700 else "medium"
            issues.append({
                "type": "complexity",
                "subtype": "file_too_long",
                "file": rel,
                "line": 1,
                "severity": sev,
                "message": f"File has {loc} lines of code (target: <500)",
                "metric": loc,
            })

        # Function too long (Python)
        if filepath.endswith(".py"):
            func_start = None
            func_name = ""
            func_lines = 0
            indent_level = 0

            for i, line in enumerate(lines):
                match = re.match(r"(\s*)(?:async\s+)?def (\w+)", line)
                if match:
                    if func_start and func_lines > 50:
                        sev = "high" if func_lines > 100 else "medium"
                        issues.append({
                            "type": "complexity",
                            "subtype": "function_too_long",
                            "file": rel,
                            "line": func_start,
                            "severity": sev,
                            "message": f"Function '{func_name}' is {func_lines} lines (target: <50)",
                            "metric": func_lines,
                        })
                    func_start = i + 1
                    func_name = match.group(2)
                    func_lines = 0
                    indent_level = len(match.group(1))
                elif func_start and line.strip():
                    func_lines += 1

            # Check last function
            if func_start and func_lines > 50:
                sev = "high" if func_lines > 100 else "medium"
                issues.append({
                    "type": "complexity",
                    "subtype": "function_too_long",
                    "file": rel,
                    "line": func_start,
                    "severity": sev,
                    "message": f"Function '{func_name}' is {func_lines} lines (target: <50)",
                    "metric": func_lines,
                })

        # Deep nesting
        max_indent = 0
        for i, line in enumerate(lines, 1):
            if line.strip():
                indent = len(line) - len(line.lstrip())
                spaces = indent // 4 if not line.startswith("\t") else indent
                if spaces > max_indent:
                    max_indent = spaces
                if spaces >= 6:
                    issues.append({
                        "type": "complexity",
                        "subtype": "deep_nesting",
                        "file": rel,
                        "line": i,
                        "severity": "medium",
                        "message": f"Deeply nested code ({spaces} levels)",
                        "metric": spaces,
                    })
                    break  # Only report once per file

    return issues


def detect_duplication(files: list, project_dir: str) -> list:
    """Find duplicated code blocks."""
    issues = []
    # Hash blocks of 5+ consecutive non-empty lines
    block_locations = defaultdict(list)

    for filepath in files:
        content = _read_file(filepath)
        lines = [l.strip() for l in content.split("\n")]
        rel = os.path.relpath(filepath, project_dir)

        for i in range(len(lines) - 4):
            block = "\n".join(lines[i:i+5])
            if len(block) > 50 and not all(l.startswith("#") or l.startswith("//") for l in lines[i:i+5] if l):
                block_hash = hashlib.md5(block.encode()).hexdigest()
                block_locations[block_hash].append({"file": rel, "line": i+1, "preview": lines[i][:60]})

    for block_hash, locations in block_locations.items():
        if len(locations) > 1 and len(locations) <= 10:
            # Dedupe by file (don't report same block in same file multiple times)
            unique_files = set(loc["file"] for loc in locations)
            if len(unique_files) > 1:
                issues.append({
                    "type": "duplication",
                    "subtype": "duplicated_block",
                    "file": locations[0]["file"],
                    "line": locations[0]["line"],
                    "severity": "medium",
                    "message": f"Duplicated code block found in {len(unique_files)} files: {', '.join(sorted(unique_files)[:3])}",
                    "locations": locations[:5],
                })

    return issues


def detect_naming(files: list, project_dir: str) -> list:
    """Find naming issues — single-letter vars, inconsistent conventions."""
    issues = []

    for filepath in files:
        content = _read_file(filepath)
        rel = os.path.relpath(filepath, project_dir)

        if filepath.endswith(".py"):
            # camelCase functions in Python (should be snake_case)
            for match in re.finditer(r"def ([a-z][a-z0-9]*[A-Z]\w*)\s*\(", content):
                issues.append({
                    "type": "naming",
                    "subtype": "wrong_case",
                    "file": rel,
                    "line": content[:match.start()].count("\n") + 1,
                    "severity": "low",
                    "message": f"camelCase function name in Python: {match.group(1)} (should be snake_case)",
                })

        elif filepath.endswith((".ts", ".tsx", ".js", ".jsx")):
            # snake_case functions in JS/TS (should be camelCase)
            for match in re.finditer(r"function ([a-z]+_[a-z_]+)\s*\(", content):
                issues.append({
                    "type": "naming",
                    "subtype": "wrong_case",
                    "file": rel,
                    "line": content[:match.start()].count("\n") + 1,
                    "severity": "low",
                    "message": f"snake_case function name in JS/TS: {match.group(1)} (should be camelCase)",
                })

    return issues


def detect_security(files: list, project_dir: str) -> list:
    """Find potential security issues."""
    issues = []

    for filepath in files:
        content = _read_file(filepath)
        lines = content.split("\n")
        rel = os.path.relpath(filepath, project_dir)

        for i, line in enumerate(lines, 1):
            stripped = line.strip()

            # Hardcoded secrets
            if re.search(r"(password|secret|api_key|apikey|token|private_key)\s*=\s*['\"][^'\"]{8,}", stripped, re.IGNORECASE):
                if "example" not in stripped.lower() and "placeholder" not in stripped.lower() and "your_" not in stripped.lower():
                    issues.append({
                        "type": "security",
                        "subtype": "hardcoded_secret",
                        "file": rel,
                        "line": i,
                        "severity": "critical",
                        "message": f"Potential hardcoded secret: {stripped[:60]}",
                    })

            # SQL injection risk
            if re.search(r"(execute|query)\s*\(\s*f['\"]|\.format\(.*\).*(?:SELECT|INSERT|UPDATE|DELETE)", stripped, re.IGNORECASE):
                issues.append({
                    "type": "security",
                    "subtype": "sql_injection",
                    "file": rel,
                    "line": i,
                    "severity": "high",
                    "message": f"Potential SQL injection (string formatting in query): {stripped[:60]}",
                })

            # eval/exec
            if re.search(r"\b(eval|exec)\s*\(", stripped) and not stripped.startswith("#"):
                issues.append({
                    "type": "security",
                    "subtype": "code_execution",
                    "file": rel,
                    "line": i,
                    "severity": "high",
                    "message": f"Dangerous eval/exec usage: {stripped[:60]}",
                })

    return issues


def detect_error_handling(files: list, project_dir: str) -> list:
    """Find bare except clauses and swallowed exceptions."""
    issues = []

    for filepath in files:
        content = _read_file(filepath)
        lines = content.split("\n")
        rel = os.path.relpath(filepath, project_dir)

        if filepath.endswith(".py"):
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if stripped in ("except:", "except Exception:  # TODO: Replace with specific exception type from exceptions.py", "except Exception as e:  # TODO: Replace with specific exception type from exceptions.py"):
                    # Check if the next non-empty line is just 'pass'
                    for j in range(i, min(i+3, len(lines))):
                        if lines[j].strip() == "pass":
                            issues.append({
                                "type": "error_handling",
                                "subtype": "swallowed_exception",
                                "file": rel,
                                "line": i,
                                "severity": "medium",
                                "message": f"Swallowed exception (except + pass): {stripped}",
                            })
                            break

    return issues


# --- External tool integration ---

def run_ruff(project_dir: str) -> list:
    """Run ruff linter if available."""
    issues = []
    try:
        result = subprocess.run(
            ["ruff", "check", project_dir, "--output-format", "json", "--select", "E,W,F,C,I"],
            capture_output=True, text=True, timeout=60,
        )
        if result.stdout:
            findings = json.loads(result.stdout)
            for f in findings[:100]:  # Cap at 100
                issues.append({
                    "type": "lint",
                    "subtype": f.get("code", "unknown"),
                    "file": os.path.relpath(f.get("filename", ""), project_dir),
                    "line": f.get("location", {}).get("row", 0),
                    "severity": "low",
                    "message": f.get("message", ""),
                })
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return issues


def run_eslint(project_dir: str) -> list:
    """Run eslint if available."""
    issues = []
    try:
        result = subprocess.run(
            ["npx", "eslint", project_dir, "-f", "json", "--max-warnings", "100"],
            capture_output=True, text=True, timeout=60, cwd=project_dir,
        )
        if result.stdout:
            findings = json.loads(result.stdout)
            for file_result in findings[:50]:
                for msg in file_result.get("messages", [])[:10]:
                    issues.append({
                        "type": "lint",
                        "subtype": msg.get("ruleId", "unknown"),
                        "file": os.path.relpath(file_result.get("filePath", ""), project_dir),
                        "line": msg.get("line", 0),
                        "severity": "medium" if msg.get("severity", 1) == 2 else "low",
                        "message": msg.get("message", ""),
                    })
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return issues


# --- Scoring ---

def calculate_score(issues: list, file_count: int) -> dict:
    """Calculate a quality score from 0-100."""
    if file_count == 0:
        return {"score": 100, "grade": "A+", "breakdown": {}}

    # Weight issues by severity
    total_penalty = sum(SEVERITY.get(i["severity"], 1) for i in issues)

    # Normalize: penalty per file, capped
    penalty_per_file = total_penalty / max(file_count, 1)

    # Score: start at 100, subtract penalty (diminishing returns)
    score = max(0, 100 - min(penalty_per_file * 5, 100))

    # Grade
    if score >= 95: grade = "A+"
    elif score >= 90: grade = "A"
    elif score >= 80: grade = "B"
    elif score >= 70: grade = "C"
    elif score >= 60: grade = "D"
    else: grade = "F"

    # Breakdown by type
    breakdown = Counter(i["type"] for i in issues)

    return {
        "score": round(score, 1),
        "grade": grade,
        "total_issues": len(issues),
        "total_penalty": total_penalty,
        "file_count": file_count,
        "issues_per_file": round(len(issues) / max(file_count, 1), 1),
        "breakdown": dict(breakdown),
        "by_severity": dict(Counter(i["severity"] for i in issues)),
    }


# --- Main API ---

def scan_project(project_dir: str) -> dict:
    """Full scan of a project. Returns scan results with issues and score."""

    files = _find_source_files(project_dir)
    if not files:
        return {"error": "No source files found", "project": project_dir}

    all_issues = []
    all_issues.extend(detect_dead_code(files, project_dir))
    all_issues.extend(detect_complexity(files, project_dir))
    all_issues.extend(detect_duplication(files, project_dir))
    all_issues.extend(detect_naming(files, project_dir))
    all_issues.extend(detect_security(files, project_dir))
    all_issues.extend(detect_error_handling(files, project_dir))

    # Try external tools
    py_files = [f for f in files if f.endswith(".py")]
    js_files = [f for f in files if f.endswith((".ts", ".tsx", ".js", ".jsx"))]
    if py_files:
        all_issues.extend(run_ruff(project_dir))
    if js_files:
        all_issues.extend(run_eslint(project_dir))

    # Deduplicate (same file+line+type)
    seen = set()
    unique_issues = []
    for issue in all_issues:
        key = (issue["file"], issue["line"], issue["type"], issue.get("subtype", ""))
        if key not in seen:
            seen.add(key)
            issue["id"] = hashlib.md5(f"{key}".encode()).hexdigest()[:8]
            unique_issues.append(issue)

    # Sort by severity (critical first)
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    unique_issues.sort(key=lambda i: (severity_order.get(i["severity"], 9), i["file"], i["line"]))

    score = calculate_score(unique_issues, len(files))

    # Save scan
    data = _load_scan(project_dir)
    data["issues"] = unique_issues
    data["last_scan"] = datetime.now(timezone.utc).isoformat()
    data["score"] = score
    data["file_count"] = len(files)
    data["scans"].append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "score": score["score"],
        "issues": len(unique_issues),
        "files": len(files),
    })
    # Keep only last 50 scans
    data["scans"] = data["scans"][-50:]
    _save_scan(project_dir, data)

    return {
        "project": project_dir,
        "score": score,
        "issues": unique_issues,
        "file_count": len(files),
    }


def get_score(project_dir: str) -> dict:
    """Get the current score without rescanning."""
    data = _load_scan(project_dir)
    return data.get("score", {"score": 0, "grade": "?", "total_issues": 0})


def get_next_issue(project_dir: str) -> dict:
    """Get the next highest-priority unresolved issue to fix."""
    data = _load_scan(project_dir)
    resolved_ids = set(r["id"] for r in data.get("resolved", []))

    for issue in data.get("issues", []):
        if issue["id"] not in resolved_ids:
            return {
                "issue": issue,
                "remaining": len([i for i in data["issues"] if i["id"] not in resolved_ids]) - 1,
                "instruction": f"Fix this issue in {issue['file']}:{issue['line']} — {issue['message']}. "
                              f"Then run: python3 code_quality.py resolve --path {project_dir} --id {issue['id']}",
            }

    return {"message": "All issues resolved! Run a rescan to check for new ones."}


def resolve_issue(project_dir: str, issue_id: str = None) -> dict:
    """Mark the current/specified issue as resolved."""
    data = _load_scan(project_dir)
    resolved_ids = set(r["id"] for r in data.get("resolved", []))

    if issue_id:
        target = next((i for i in data["issues"] if i["id"] == issue_id), None)
    else:
        target = next((i for i in data["issues"] if i["id"] not in resolved_ids), None)

    if not target:
        return {"message": "No issue to resolve"}

    data.setdefault("resolved", []).append({
        "id": target["id"],
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    })
    _save_scan(project_dir, data)

    # Return the next issue
    return {"resolved": target, "next": get_next_issue(project_dir)}


def get_report(project_dir: str) -> str:
    """Human-readable quality report."""
    data = _load_scan(project_dir)
    score = data.get("score", {})
    issues = data.get("issues", [])
    resolved = set(r["id"] for r in data.get("resolved", []))

    lines = [
        f"=== Code Quality Report: {Path(project_dir).name} ===",
        f"Score: {score.get('score', '?')}/100 (Grade: {score.get('grade', '?')})",
        f"Files: {score.get('file_count', '?')}",
        f"Issues: {score.get('total_issues', '?')} ({len(resolved)} resolved)",
        f"Issues per file: {score.get('issues_per_file', '?')}",
        "",
        "By severity:",
    ]

    for sev in ["critical", "high", "medium", "low"]:
        count = score.get("by_severity", {}).get(sev, 0)
        if count:
            lines.append(f"  {sev}: {count}")

    lines.append("")
    lines.append("By type:")
    for typ, count in sorted(score.get("breakdown", {}).items()):
        lines.append(f"  {typ}: {count}")

    # Top 10 unresolved issues
    unresolved = [i for i in issues if i["id"] not in resolved]
    if unresolved:
        lines.append("")
        lines.append(f"Top unresolved issues ({len(unresolved)} remaining):")
        for issue in unresolved[:10]:
            lines.append(f"  [{issue['severity']}] {issue['file']}:{issue['line']} — {issue['message']}")

    # Score history
    scans = data.get("scans", [])
    if len(scans) > 1:
        lines.append("")
        lines.append("Score history:")
        for s in scans[-5:]:
            lines.append(f"  {s['timestamp'][:19]}: {s['score']}/100 ({s['issues']} issues)")

    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Code Quality Scanner")
    parser.add_argument("command", choices=["scan", "score", "next", "resolve", "report"])
    parser.add_argument("--path", required=True, help="Project directory")
    parser.add_argument("--id", help="Issue ID to resolve")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    if args.command == "scan":
        result = scan_project(args.path)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Scanned {result.get('file_count', 0)} files")
            s = result.get("score", {})
            print(f"Score: {s.get('score', '?')}/100 (Grade: {s.get('grade', '?')})")
            print(f"Issues: {s.get('total_issues', 0)}")
            print(f"  Critical: {s.get('by_severity', {}).get('critical', 0)}")
            print(f"  High: {s.get('by_severity', {}).get('high', 0)}")
            print(f"  Medium: {s.get('by_severity', {}).get('medium', 0)}")
            print(f"  Low: {s.get('by_severity', {}).get('low', 0)}")

    elif args.command == "score":
        s = get_score(args.path)
        print(f"{s.get('score', '?')}/100 (Grade: {s.get('grade', '?')})")

    elif args.command == "next":
        result = get_next_issue(args.path)
        if "issue" in result:
            i = result["issue"]
            print(f"[{i['severity']}] {i['file']}:{i['line']}")
            print(f"  {i['message']}")
            print(f"  Type: {i['type']}/{i.get('subtype', '?')}")
            print(f"  ID: {i['id']}")
            print(f"  Remaining: {result['remaining']}")
        else:
            print(result.get("message", "Done"))

    elif args.command == "resolve":
        result = resolve_issue(args.path, args.id)
        if "resolved" in result:
            print(f"Resolved: {result['resolved']['file']}:{result['resolved']['line']}")
            nxt = result.get("next", {})
            if "issue" in nxt:
                print(f"Next: [{nxt['issue']['severity']}] {nxt['issue']['file']}:{nxt['issue']['line']} — {nxt['issue']['message']}")
        else:
            print(result.get("message", "Nothing to resolve"))

    elif args.command == "report":
        print(get_report(args.path))
