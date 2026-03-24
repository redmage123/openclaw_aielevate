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
        return {"status": "error", "error": f"Unknown project type: {project_type}. Valid types: {list(DELIVERY_TYPES.keys())}"}

    method = DELIVERY_TYPES[project_type]["method"]
    project_path = Path(project_dir)
    now = datetime.now(timezone.utc)

    result = {
        "project_type": project_type,
        "method": method,
        "slug": slug,
        "org": org,
        "customer_email": customer_email,
        "timestamp": now.isoformat(),
        "delivery_url": None,
        "delivery_files": [],
        "instructions": "",
        "status": "pending",
    }

    try:
        if method == "docker_preview":
            # Web apps, APIs — use existing preview_deploy
            from preview_deploy import deploy_preview
            preview = deploy_preview(project_dir, slug, org, customer_email, production_domain)
            result["delivery_url"] = preview.get("url") or preview.get("direct_url")
            result["status"] = preview.get("status", "error")
            result["instructions"] = f"Your project is live for review at {result['delivery_url']}"

        elif method == "docker_compose":
            # SaaS/data pipelines — docker compose up
            stdout, stderr, rc = _run(f"cd {project_dir} && docker compose -p {slug} up -d 2>&1", timeout=300)
            if rc == 0:
                # Find exposed ports
                stdout2, _, _ = _run(f"docker compose -p {slug} ps --format json 2>/dev/null")
                result["status"] = "live"
                result["delivery_url"] = f"http://{SERVER_IP}  (check docker compose ports)"
                result["instructions"] = f"Stack deployed. Check container ports with: docker compose -p {slug} ps"
            else:
                result["status"] = "error"
                result["error"] = stderr or stdout

        elif method == "build_upload":
            # Mobile/desktop apps — look for build artifacts and upload
            artifacts = []
            for pattern in ["*.apk", "*.aab", "*.ipa", "*.dmg", "*.exe", "*.AppImage", "*.deb", "*.msi"]:
                artifacts.extend(project_path.rglob(pattern))

            if not artifacts:
                # Try to build
                if (project_path / "android").exists() or (project_path / "pubspec.yaml").exists():
                    _run(f"cd {project_dir} && flutter build apk --release 2>&1", timeout=600)
                    artifacts = list(project_path.rglob("*.apk"))

            for artifact in artifacts[:5]:
                url = _upload_file(str(artifact), slug, artifact.name)
                result["delivery_files"].append({"name": artifact.name, "url": url})

            if result["delivery_files"]:
                result["status"] = "uploaded"
                result["delivery_url"] = result["delivery_files"][0]["url"]
                result["instructions"] = "Download links:\n" + "\n".join(
                    f"  - {f['name']}: {f['url']}" for f in result["delivery_files"]
                )
            else:
                result["status"] = "no_artifacts"
                result["instructions"] = "No build artifacts found. Build the project first, then re-run delivery."

        elif method == "package_upload":
            # Scripts, CLIs, extensions, bots — zip and upload
            _ensure_dirs()
            zip_name = f"{slug}-{now.strftime('%Y%m%d')}"
            zip_path = DELIVERY_DIR / zip_name

            # Create zip excluding common junk
            _run(f"cd {project_dir} && zip -r {zip_path}.zip . "
                 f"-x '*.git*' 'node_modules/*' '__pycache__/*' '.venv/*' '*.pyc' '.env'",
                 timeout=120)

            zip_file = f"{zip_path}.zip"
            if Path(zip_file).exists():
                url = _upload_file(zip_file, slug, f"{zip_name}.zip")
                result["delivery_files"].append({"name": f"{zip_name}.zip", "url": url})
                result["status"] = "uploaded"
                result["delivery_url"] = url
                result["instructions"] = f"Download: {url}\nExtract and follow the README for setup instructions."
            else:
                result["status"] = "error"
                result["error"] = "Failed to create zip archive"

        elif method == "model_upload":
            # ML models — upload model files + optional inference API
            artifacts = []
            for pattern in ["*.pt", "*.pth", "*.onnx", "*.pkl", "*.joblib", "*.h5", "*.safetensors", "*.bin"]:
                artifacts.extend(project_path.rglob(pattern))

            # Also package the inference code
            _ensure_dirs()
            zip_name = f"{slug}-model-{now.strftime('%Y%m%d')}"
            zip_path = DELIVERY_DIR / zip_name
            _run(f"cd {project_dir} && zip -r {zip_path}.zip . "
                 f"-x '*.git*' 'node_modules/*' '__pycache__/*' '.venv/*'",
                 timeout=120)

            if Path(f"{zip_path}.zip").exists():
                url = _upload_file(f"{zip_path}.zip", slug, f"{zip_name}.zip")
                result["delivery_files"].append({"name": f"{zip_name}.zip", "url": url})

            # If there's a Dockerfile, also deploy an inference API
            if (project_path / "Dockerfile").exists():
                from preview_deploy import deploy_preview
                preview = deploy_preview(project_dir, slug + "-api", org, customer_email)
                if preview.get("status") == "live":
                    result["delivery_files"].append({"name": "Inference API", "url": preview.get("url")})

            result["status"] = "uploaded" if result["delivery_files"] else "no_artifacts"
            result["delivery_url"] = result["delivery_files"][0]["url"] if result["delivery_files"] else None
            result["instructions"] = "Model package:\n" + "\n".join(
                f"  - {f['name']}: {f['url']}" for f in result["delivery_files"]
            )

        elif method == "media_upload":
            # Video, animation — upload media files
            artifacts = []
            for pattern in ["*.mp4", "*.mov", "*.avi", "*.mkv", "*.webm", "*.gif", "*.mp3", "*.wav"]:
                artifacts.extend(project_path.rglob(pattern))

            for artifact in artifacts[:10]:
                url = _upload_file(str(artifact), slug, artifact.name)
                result["delivery_files"].append({"name": artifact.name, "url": url, "size_mb": artifact.stat().st_size / 1024 / 1024})

            result["status"] = "uploaded" if result["delivery_files"] else "no_artifacts"
            result["delivery_url"] = result["delivery_files"][0]["url"] if result["delivery_files"] else None
            result["instructions"] = "Media files:\n" + "\n".join(
                f"  - {f['name']} ({f.get('size_mb', 0):.1f} MB): {f['url']}" for f in result["delivery_files"]
            )

        elif method == "document_send":
            # Documents, reports, audits — find or generate PDFs
            pdfs = list(project_path.rglob("*.pdf"))
            if not pdfs:
                # Look for markdown to convert
                mds = list(project_path.rglob("*.md"))
                if mds:
                    for md in mds[:3]:
                        pdf_name = md.stem + ".pdf"
                        pdf_path = project_path / pdf_name
                        _run(f"pandoc '{md}' -o '{pdf_path}' 2>/dev/null || "
                             f"python3 -c \"from weasyprint import HTML; HTML(filename='{md}').write_pdf('{pdf_path}')\" 2>/dev/null")
                        if pdf_path.exists():
                            pdfs.append(pdf_path)

            for pdf in pdfs[:5]:
                url = _upload_file(str(pdf), slug, pdf.name)
                result["delivery_files"].append({"name": pdf.name, "url": url})

            result["status"] = "uploaded" if result["delivery_files"] else "no_documents"
            result["delivery_url"] = result["delivery_files"][0]["url"] if result["delivery_files"] else None
            result["instructions"] = "Documents:\n" + "\n".join(
                f"  - {f['name']}: {f['url']}" for f in result["delivery_files"]
            ) + "\n\nThese can also be sent as email attachments."

        elif method == "platform_deploy":
            # Shopify, etc — we can't auto-deploy, provide instructions
            result["status"] = "manual_deploy"
            result["instructions"] = (
                "This project requires deployment to the client's platform (Shopify, WordPress, etc.).\n"
                "Provide the client with:\n"
                "1. Access credentials for their platform (they must provide these)\n"
                "2. Step-by-step deployment instructions\n"
                "3. A zip of all customization files\n"
                "4. A screen recording of the deployment process (optional but recommended)"
            )
            # Package files anyway
            _ensure_dirs()
            zip_name = f"{slug}-platform-{now.strftime('%Y%m%d')}"
            zip_path = DELIVERY_DIR / zip_name
            _run(f"cd {project_dir} && zip -r {zip_path}.zip . -x '*.git*' 'node_modules/*' '__pycache__/*' '.venv/*'")
            if Path(f"{zip_path}.zip").exists():
                url = _upload_file(f"{zip_path}.zip", slug, f"{zip_name}.zip")
                result["delivery_files"].append({"name": f"{zip_name}.zip", "url": url})
                result["delivery_url"] = url

        elif method == "infra_handoff":
            # DevOps/infra — provide docs, credentials, access info
            result["status"] = "documentation"
            result["instructions"] = (
                "Infrastructure/DevOps deliverable. Provide the client with:\n"
                "1. Architecture documentation (PDF)\n"
                "2. Access credentials (securely — use encrypted email or password manager link)\n"
                "3. Runbooks for common operations\n"
                "4. Monitoring dashboard URL (if applicable)\n"
                "5. Terraform/Ansible code (packaged zip)\n"
                "6. A handoff call agenda (if client requested one, escalate to human team)"
            )
            # Package IaC code
            _ensure_dirs()
            zip_name = f"{slug}-infra-{now.strftime('%Y%m%d')}"
            zip_path = DELIVERY_DIR / zip_name
            _run(f"cd {project_dir} && zip -r {zip_path}.zip . -x '*.git*' '.terraform/*' '*.tfstate*'")
            if Path(f"{zip_path}.zip").exists():
                url = _upload_file(f"{zip_path}.zip", slug, f"{zip_name}.zip")
                result["delivery_files"].append({"name": f"{zip_name}.zip", "url": url})
                result["delivery_url"] = url

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)

    # Log delivery
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
