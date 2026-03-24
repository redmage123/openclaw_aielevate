#!/usr/bin/env python3
"""Project Delivery — handles delivery for ALL project types, not just web apps.

Each project type has a different delivery mechanism:

  Web app/site      → Docker container + preview URL (preview_deploy.py)
  Mobile app        → Build APK/IPA, upload to file server, send download link
  API/backend       → Docker container + API docs URL
  Desktop app       → Build binary, upload to file server, send download link
  Script/CLI tool   → Package in zip/tar, upload to file server, send download link
  Automation/bot    → Docker container (if long-running) or zip (if one-shot)
  Video/animation   → Upload to file server, send download/stream link
  Document/report   → Generate PDF, send as email attachment or download link
  SEO/audit         → Generate PDF report, send as email attachment
  Shopify/platform  → Deploy to platform, send admin URL + credentials
  Browser extension → Package CRX/XPI, upload to file server, send link
  Data pipeline     → Docker compose stack, send monitoring URL
  ML model          → Package model + inference script, upload or deploy as API
  SaaS platform     → Full Docker compose stack + preview URL
  DevOps/infra      → Terraform/Ansible output, send access credentials + docs

Usage:
  from project_delivery import deliver_project, DELIVERY_TYPES

  result = deliver_project(
      project_type="mobile_app",
      project_dir="/path/to/project",
      slug="heritage-app",
      org="gigforge",
      customer_email="peter@example.com",
      production_domain="",
  )
"""

import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from exceptions import AiElevateError  # TODO: Use specific exception types

DELIVERY_DIR = Path("/opt/ai-elevate/deliveries")
FILE_SERVER_DIR = Path("/opt/ai-elevate/gigforge/projects/gigforge-website/public/deliveries")
FILE_SERVER_URL = "https://gigforge.ai/deliveries"
SERVER_IP = "78.47.104.139"

DELIVERY_TYPES = {
    "web_app":          {"method": "docker_preview", "description": "Web application — Docker container + preview URL"},
    "api":              {"method": "docker_preview", "description": "API/backend service — Docker container + API docs"},
    "saas":             {"method": "docker_compose", "description": "SaaS platform — full Docker compose stack"},
    "data_pipeline":    {"method": "docker_compose", "description": "Data pipeline — Docker compose + monitoring"},
    "mobile_app":       {"method": "build_upload",   "description": "Mobile app — build APK/IPA + download link"},
    "desktop_app":      {"method": "build_upload",   "description": "Desktop app — build binary + download link"},
    "cli_tool":         {"method": "package_upload",  "description": "CLI tool/script — packaged zip + download link"},
    "automation":       {"method": "package_upload",  "description": "Automation script/bot — packaged zip or Docker"},
    "browser_extension":{"method": "package_upload",  "description": "Browser extension — packaged CRX + download link"},
    "ml_model":         {"method": "model_upload",    "description": "ML model — model files + inference API or download"},
    "video":            {"method": "media_upload",    "description": "Video/animation — uploaded file + streaming link"},
    "document":         {"method": "document_send",   "description": "Document/report/audit — PDF sent via email"},
    "seo_audit":        {"method": "document_send",   "description": "SEO/technical audit — PDF report via email"},
    "shopify":          {"method": "platform_deploy", "description": "Shopify customization — deployed to client store"},
    "devops":           {"method": "infra_handoff",   "description": "DevOps/infra work — credentials + documentation"},
}


def _run(cmd, timeout=120):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return result.stdout.strip(), result.stderr.strip(), result.returncode


def _ensure_dirs():
    DELIVERY_DIR.mkdir(parents=True, exist_ok=True)
    FILE_SERVER_DIR.mkdir(parents=True, exist_ok=True)


def _log_delivery(delivery: dict):
    _ensure_dirs()
    log_file = DELIVERY_DIR / f"deliveries-{datetime.now(timezone.utc).strftime('%Y-%m')}.jsonl"
    with open(log_file, "a") as f:
        f.write(json.dumps(delivery) + "\n")


def _upload_file(local_path: str, slug: str, filename: str) -> str:
    """Copy a file to the public file server directory and return the URL."""
    _ensure_dirs()
    dest_dir = FILE_SERVER_DIR / slug
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    shutil.copy2(local_path, str(dest))
    return f"{FILE_SERVER_URL}/{slug}/{filename}"


def _deliver_docker_preview(result: dict, project_dir: str, slug: str, org: str,
                            customer_email: str, production_domain: str) -> None:
    from preview_deploy import deploy_preview
    preview = deploy_preview(project_dir, slug, org, customer_email, production_domain)
    result["delivery_url"] = preview.get("url") or preview.get("direct_url")
    result["status"] = preview.get("status", "error")
    result["instructions"] = f"Your project is live for review at {result['delivery_url']}"


def _deliver_docker_compose(result: dict, project_dir: str, slug: str) -> None:
    stdout, stderr, rc = _run(f"cd {project_dir} && docker compose -p {slug} up -d 2>&1", timeout=300)
    if rc == 0:
        result["status"] = "live"
        result["delivery_url"] = f"http://{SERVER_IP}  (check docker compose ports)"
        result["instructions"] = f"Stack deployed. Check container ports with: docker compose -p {slug} ps"
    else:
        result["status"] = "error"
        result["error"] = stderr or stdout


def _deliver_build_upload(result: dict, project_path: Path, project_dir: str, slug: str) -> None:
    artifacts = [p for pat in ["*.apk", "*.aab", "*.ipa", "*.dmg", "*.exe", "*.AppImage", "*.deb", "*.msi"]
                 for p in project_path.rglob(pat)]
    if not artifacts and ((project_path / "android").exists() or (project_path / "pubspec.yaml").exists()):
        _run(f"cd {project_dir} && flutter build apk --release 2>&1", timeout=600)
        artifacts = list(project_path.rglob("*.apk"))
    for artifact in artifacts[:5]:
        result["delivery_files"].append({"name": artifact.name, "url": _upload_file(str(artifact), slug, artifact.name)})
    if result["delivery_files"]:
        result["status"] = "uploaded"
        result["delivery_url"] = result["delivery_files"][0]["url"]
        result["instructions"] = "Download links:\n" + "\n".join(f"  - {f['name']}: {f['url']}" for f in result["delivery_files"])
    else:
        result["status"] = "no_artifacts"
        result["instructions"] = "No build artifacts found. Build the project first, then re-run delivery."


def _deliver_package_upload(result: dict, project_dir: str, slug: str, now: datetime) -> None:
    _ensure_dirs()
    zip_name = f"{slug}-{now.strftime('%Y%m%d')}"
    zip_path = DELIVERY_DIR / zip_name
    _run(f"cd {project_dir} && zip -r {zip_path}.zip . -x '*.git*' 'node_modules/*' '__pycache__/*' '.venv/*' '*.pyc' '.env'", timeout=120)
    if Path(f"{zip_path}.zip").exists():
        url = _upload_file(f"{zip_path}.zip", slug, f"{zip_name}.zip")
        result["delivery_files"].append({"name": f"{zip_name}.zip", "url": url})
        result["status"] = "uploaded"
        result["delivery_url"] = url
        result["instructions"] = f"Download: {url}\nExtract and follow the README for setup instructions."
    else:
        result["status"] = "error"
        result["error"] = "Failed to create zip archive"


def _deliver_model_upload(result: dict, project_path: Path, project_dir: str, slug: str,
                          org: str, customer_email: str, now: datetime) -> None:
    _ensure_dirs()
    zip_name = f"{slug}-model-{now.strftime('%Y%m%d')}"
    zip_path = DELIVERY_DIR / zip_name
    _run(f"cd {project_dir} && zip -r {zip_path}.zip . -x '*.git*' 'node_modules/*' '__pycache__/*' '.venv/*'", timeout=120)
    if Path(f"{zip_path}.zip").exists():
        url = _upload_file(f"{zip_path}.zip", slug, f"{zip_name}.zip")
        result["delivery_files"].append({"name": f"{zip_name}.zip", "url": url})
    if (project_path / "Dockerfile").exists():
        from preview_deploy import deploy_preview
        preview = deploy_preview(project_dir, slug + "-api", org, customer_email)
        if preview.get("status") == "live":
            result["delivery_files"].append({"name": "Inference API", "url": preview.get("url")})
    result["status"] = "uploaded" if result["delivery_files"] else "no_artifacts"
    result["delivery_url"] = result["delivery_files"][0]["url"] if result["delivery_files"] else None
    result["instructions"] = "Model package:\n" + "\n".join(f"  - {f['name']}: {f['url']}" for f in result["delivery_files"])


def _deliver_media_upload(result: dict, project_path: Path, slug: str) -> None:
    artifacts = [p for pat in ["*.mp4", "*.mov", "*.avi", "*.mkv", "*.webm", "*.gif", "*.mp3", "*.wav"]
                 for p in project_path.rglob(pat)]
    for artifact in artifacts[:10]:
        result["delivery_files"].append({"name": artifact.name,
                                         "url": _upload_file(str(artifact), slug, artifact.name),
                                         "size_mb": artifact.stat().st_size / 1024 / 1024})
    result["status"] = "uploaded" if result["delivery_files"] else "no_artifacts"
    result["delivery_url"] = result["delivery_files"][0]["url"] if result["delivery_files"] else None
    result["instructions"] = "Media files:\n" + "\n".join(
        f"  - {f['name']} ({f.get('size_mb', 0):.1f} MB): {f['url']}" for f in result["delivery_files"])


def _deliver_document_send(result: dict, project_path: Path, slug: str) -> None:
    pdfs = list(project_path.rglob("*.pdf"))
    if not pdfs:
        for md in list(project_path.rglob("*.md"))[:3]:
            pdf_path = project_path / (md.stem + ".pdf")
            _run(f"pandoc '{md}' -o '{pdf_path}' 2>/dev/null || "
                 f"python3 -c \"from weasyprint import HTML; HTML(filename='{md}').write_pdf('{pdf_path}')\" 2>/dev/null")
            if pdf_path.exists():
                pdfs.append(pdf_path)
    for pdf in pdfs[:5]:
        result["delivery_files"].append({"name": pdf.name, "url": _upload_file(str(pdf), slug, pdf.name)})
    result["status"] = "uploaded" if result["delivery_files"] else "no_documents"
    result["delivery_url"] = result["delivery_files"][0]["url"] if result["delivery_files"] else None
    result["instructions"] = "Documents:\n" + "\n".join(
        f"  - {f['name']}: {f['url']}" for f in result["delivery_files"]) + "\n\nThese can also be sent as email attachments."


def _deliver_platform_deploy(result: dict, project_dir: str, slug: str, now: datetime) -> None:
    result["status"] = "manual_deploy"
    result["instructions"] = (
        "This project requires deployment to the client's platform (Shopify, WordPress, etc.).\n"
        "Provide the client with:\n1. Access credentials for their platform (they must provide these)\n"
        "2. Step-by-step deployment instructions\n3. A zip of all customization files\n"
        "4. A screen recording of the deployment process (optional but recommended)")
    _ensure_dirs()
    zip_name = f"{slug}-platform-{now.strftime('%Y%m%d')}"
    zip_path = DELIVERY_DIR / zip_name
    _run(f"cd {project_dir} && zip -r {zip_path}.zip . -x '*.git*' 'node_modules/*' '__pycache__/*' '.venv/*'")
    if Path(f"{zip_path}.zip").exists():
        url = _upload_file(f"{zip_path}.zip", slug, f"{zip_name}.zip")
        result["delivery_files"].append({"name": f"{zip_name}.zip", "url": url})
        result["delivery_url"] = url


def _deliver_infra_handoff(result: dict, project_dir: str, slug: str, now: datetime) -> None:
    result["status"] = "documentation"
    result["instructions"] = (
        "Infrastructure/DevOps deliverable. Provide the client with:\n"
        "1. Architecture documentation (PDF)\n2. Access credentials (securely)\n"
        "3. Runbooks for common operations\n4. Monitoring dashboard URL (if applicable)\n"
        "5. Terraform/Ansible code (packaged zip)\n"
        "6. A handoff call agenda (if client requested one, escalate to human team)")
    _ensure_dirs()
    zip_name = f"{slug}-infra-{now.strftime('%Y%m%d')}"
    zip_path = DELIVERY_DIR / zip_name
    _run(f"cd {project_dir} && zip -r {zip_path}.zip . -x '*.git*' '.terraform/*' '*.tfstate*'")
    if Path(f"{zip_path}.zip").exists():
        url = _upload_file(f"{zip_path}.zip", slug, f"{zip_name}.zip")
        result["delivery_files"].append({"name": f"{zip_name}.zip", "url": url})
        result["delivery_url"] = url


_DELIVERY_HANDLERS = {
    "docker_preview": lambda r, p, pdir, s, o, ce, pd, now: _deliver_docker_preview(r, pdir, s, o, ce, pd),
    "docker_compose": lambda r, p, pdir, s, o, ce, pd, now: _deliver_docker_compose(r, pdir, s),
    "build_upload":   lambda r, p, pdir, s, o, ce, pd, now: _deliver_build_upload(r, p, pdir, s),
    "package_upload": lambda r, p, pdir, s, o, ce, pd, now: _deliver_package_upload(r, pdir, s, now),
    "model_upload":   lambda r, p, pdir, s, o, ce, pd, now: _deliver_model_upload(r, p, pdir, s, o, ce, now),
    "media_upload":   lambda r, p, pdir, s, o, ce, pd, now: _deliver_media_upload(r, p, s),
    "document_send":  lambda r, p, pdir, s, o, ce, pd, now: _deliver_document_send(r, p, s),
    "platform_deploy": lambda r, p, pdir, s, o, ce, pd, now: _deliver_platform_deploy(r, pdir, s, now),
    "infra_handoff":  lambda r, p, pdir, s, o, ce, pd, now: _deliver_infra_handoff(r, pdir, s, now),
}


def deliver_project(
    project_type: str,
    project_dir: str,
    slug: str,
    org: str = "gigforge",
    customer_email: str = "",
    production_domain: str = "",
    notes: str = "",
) -> dict:
    """Deliver a project using the appropriate method for its type."""
    if project_type not in DELIVERY_TYPES:
        return {"status": "error", "error": f"Unknown project type: {project_type}. Valid: {list(DELIVERY_TYPES.keys())}"}

    method = DELIVERY_TYPES[project_type]["method"]
    project_path = Path(project_dir)
    now = datetime.now(timezone.utc)

    result = {"project_type": project_type, "method": method, "slug": slug, "org": org,
              "customer_email": customer_email, "timestamp": now.isoformat(),
              "delivery_url": None, "delivery_files": [], "instructions": "", "status": "pending"}

    try:
        handler = _DELIVERY_HANDLERS.get(method)
        if handler:
            handler(result, project_path, project_dir, slug, org, customer_email, production_domain, now)
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)

    _log_delivery(result)
    return result


def list_delivery_types() -> dict:
    """Return all supported delivery types."""
    return DELIVERY_TYPES


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Project Delivery")
    parser.add_argument("--types", action="store_true", help="List delivery types")
    parser.add_argument("--type", help="Project type")
    parser.add_argument("--project-dir", help="Path to project")
    parser.add_argument("--slug", help="Short name")
    parser.add_argument("--org", default="gigforge")
    parser.add_argument("--customer", default="")
    parser.add_argument("--domain", default="")
    args = parser.parse_args()

    if args.types:
        for k, v in DELIVERY_TYPES.items():
            print(f"  {k:20s} — {v['description']}")
    elif args.type and args.project_dir and args.slug:
        result = deliver_project(args.type, args.project_dir, args.slug, args.org, args.customer, args.domain)
        print(json.dumps(result, indent=2))
    else:
        parser.print_help()
