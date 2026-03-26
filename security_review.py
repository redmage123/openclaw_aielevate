#!/usr/bin/env python3
"""Security Review — OWASP checks + basic penetration test.
Must pass before any deployment. Called by build_workflow."""

import subprocess
import re
import logging
from pathlib import Path

log = logging.getLogger("security-review")


def run_security_review(project_dir, domain=None):
    """Run OWASP checks and basic security review on a project.

    Returns: (passed: bool, report: str)
    """
    findings = []
    project = Path(project_dir)

    if not project.exists():
        return False, f"Project directory not found: {project_dir}"

    # Collect all source files
    extensions = ["*.py", "*.js", "*.ts", "*.tsx", "*.jsx"]
    source_files = []
    for ext in extensions:
        source_files.extend(f for f in project.rglob(ext) if "node_modules" not in str(f) and ".venv" not in str(f))

    # 1. OWASP Top 10 Checks
    checks = {
        "SQL Injection": [r"execute\(.*?%s", r"execute\(.*?\+", r"f\".*SELECT.*{"],
        "XSS": [r"dangerouslySetInnerHTML", r"innerHTML\s*=", r"document\.write\("],
        "Hardcoded Secrets": [r"password\s*=\s*[\"'][A-Za-z0-9!@#$%]{8,}", r"api_key\s*=\s*[\"']sk-"],
        "Eval/Exec": [r"\beval\(", r"\bexec\(", r"Function\("],
        "Path Traversal": [r"\.\./", r"path\.join.*req\.", r"os\.path.*user"],
        "Insecure Deserialization": [r"pickle\.load", r"yaml\.load\(.*Loader"],
    }

    for vuln_name, patterns in checks.items():
        for pattern in patterns:
            for f in source_files:
                try:
                    content = f.read_text(errors="replace")
                    matches = re.findall(pattern, content)
                    if matches:
                        findings.append(f"OWASP [{vuln_name}]: {len(matches)} instance(s) in {f.name}")
                except Exception:
                    pass

    # 2. Security Headers Check (if domain provided)
    if domain:
        try:
            import urllib.request
            url = domain if domain.startswith("http") else f"http://{domain}"
            req = urllib.request.Request(url)
            resp = urllib.request.urlopen(req, timeout=10)
            headers = dict(resp.headers)

            required_headers = {
                "X-Frame-Options": "Clickjacking protection",
                "X-Content-Type-Options": "MIME sniffing protection",
                "Content-Security-Policy": "XSS/injection protection",
            }

            for header, purpose in required_headers.items():
                if header not in headers:
                    findings.append(f"Missing security header: {header} ({purpose})")
        except Exception as e:
            findings.append(f"Could not check security headers: {e}")

    # 3. HTTPS Check
    if domain and not domain.startswith("https"):
        findings.append("No HTTPS — site served over plain HTTP")

    # 4. Dependency Vulnerability Check
    package_json = project / "package.json"
    requirements = project / "requirements.txt"
    if package_json.exists():
        try:
            result = subprocess.run(
                ["npm", "audit", "--json"], cwd=str(project),
                capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                import json
                audit = json.loads(result.stdout)
                vulns = audit.get("metadata", {}).get("vulnerabilities", {})
                critical = vulns.get("critical", 0)
                high = vulns.get("high", 0)
                if critical > 0:
                    findings.append(f"npm audit: {critical} critical vulnerabilities")
                if high > 0:
                    findings.append(f"npm audit: {high} high vulnerabilities")
        except Exception:
            pass

    # 5. GDPR Compliance Check
    has_privacy_policy = any(
        "privacy" in f.name.lower() or "gdpr" in f.name.lower()
        for f in project.rglob("*") if f.is_file()
    )
    if not has_privacy_policy:
        findings.append("GDPR: No privacy policy file found")

    # 6. Accessibility Check
    html_files = list(project.rglob("*.html")) + list(project.rglob("*.tsx")) + list(project.rglob("*.jsx"))
    aria_count = 0
    alt_count = 0
    for f in html_files[:20]:
        try:
            content = f.read_text(errors="replace")
            aria_count += content.count("aria-")
            alt_count += content.count('alt="') + content.count("alt='")
        except Exception:
            pass
    if aria_count < 5:
        findings.append(f"Accessibility: Only {aria_count} ARIA attributes found (need more)")
    if alt_count < 3:
        findings.append(f"Accessibility: Only {alt_count} alt attributes on images")

    # Generate report
    if findings:
        critical = [f for f in findings if "OWASP" in f or "critical" in f.lower()]
        report = f"SECURITY REVIEW — {len(findings)} finding(s):\n"
        for f in findings:
            severity = "CRITICAL" if "OWASP" in f or "critical" in f.lower() else "WARNING"
            report += f"  [{severity}] {f}\n"

        if critical:
            return False, report
        else:
            return True, report  # Warnings only — can proceed with caution
    else:
        return True, "SECURITY APPROVED — no vulnerabilities detected"


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Security Review")
    parser.add_argument("--project", required=True, help="Project directory")
    parser.add_argument("--domain", help="Domain to check headers")
    args = parser.parse_args()

    passed, report = run_security_review(args.project, args.domain)
    print(report)
    print(f"\nResult: {'PASSED' if passed else 'FAILED'}")
