#!/usr/bin/env python3
"""Customer Preview Deployment — spins up a Docker container and configures nginx.

Usage:
  # Deploy a project for customer preview
  python3 preview_deploy.py --project-dir /opt/ai-elevate/gigforge/projects/khhs-website \
      --slug khhs --org gigforge --customer peter.munro@ai-elevate.ai

  # List active previews
  python3 preview_deploy.py --list

  # Tear down a preview
  python3 preview_deploy.py --teardown khhs

Library usage:
  from preview_deploy import deploy_preview, teardown_preview, list_previews
  result = deploy_preview(project_dir="/path/to/project", slug="khhs", org="gigforge", customer_email="x@y.com")
  # result = {"url": "https://khhs.gigforge.ai", "port": 4100, "container": "preview-khhs", "status": "live"}
"""

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from exceptions import AiElevateError  # TODO: Use specific exception types

PREVIEW_DB = Path("/opt/ai-elevate/devops/previews.json")
NGINX_DIR = Path("/etc/nginx/sites-enabled")
PORT_START = 4100
PORT_END = 4199
SERVER_IP = "78.47.104.139"

def _load_previews() -> dict:
    PREVIEW_DB.parent.mkdir(parents=True, exist_ok=True)
    if PREVIEW_DB.exists():
        return json.loads(PREVIEW_DB.read_text())
    return {}

def _save_previews(previews: dict):
    PREVIEW_DB.parent.mkdir(parents=True, exist_ok=True)
    PREVIEW_DB.write_text(json.dumps(previews, indent=2))

def _next_port(previews: dict) -> int:
    """Find next free port — checks both preview DB and actual system ports."""
    used = {p["port"] for p in previews.values() if "port" in p}
    result = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True)
    system_ports = result.stdout
    for port in range(PORT_START, PORT_END + 1):
        if port not in used and f":{port} " not in system_ports:
            # Double-check with socket bind
            import socket
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.bind(("", port))
                s.close()
                return port
            except OSError:
                continue
    # Expand range if all 4100-4199 are taken
    for port in range(4200, 4300):
        if f":{port} " not in system_ports:
            import socket
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.bind(("", port))
                s.close()
                return port
            except OSError:
                continue
    raise RuntimeError("No free ports in range 4100-4299")

def _run(cmd: str, timeout: int = 120) -> tuple:
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return result.stdout.strip(), result.stderr.strip(), result.returncode

def deploy_preview(project_dir: str, slug: str, org: str = "gigforge", customer_email: str = "", production_domain: str = "") -> dict:
    """Deploy a project as a Docker preview container with nginx proxy."""

    previews = _load_previews()
    project_path = Path(project_dir)
    container_name = f"preview-{slug}"

    # Check for Dockerfile
    dockerfile = project_path / "Dockerfile"
    compose_file = project_path / "docker-compose.yml"

    if not dockerfile.exists() and not compose_file.exists():
        return {"status": "error", "error": f"No Dockerfile or docker-compose.yml in {project_dir}"}

    port = _next_port(previews)
    subdomain = f"{slug}.gigforge.ai"

    # Build and run container
    if compose_file.exists():
        # Use docker compose but override the port
        stdout, stderr, rc = _run(
            f"cd {project_dir} && docker compose -p preview-{slug} build 2>&1",
            timeout=300
        )
        if rc != 0:
            return {"status": "error", "error": f"Build failed: {stderr or stdout}"}

        stdout, stderr, rc = _run(
            f"cd {project_dir} && PORT={port} docker compose -p preview-{slug} up -d 2>&1"
        )
    else:
        # Build from Dockerfile
        stdout, stderr, rc = _run(
            f"docker build -t {container_name} {project_dir} 2>&1",
            timeout=300
        )
        if rc != 0:
            return {"status": "error", "error": f"Build failed: {stderr or stdout}"}

        stdout, stderr, rc = _run(
            f"docker run -d --name {container_name} --restart unless-stopped -p {port}:3000 {container_name} 2>&1"
        )

    if rc != 0:
        return {"status": "error", "error": f"Container start failed: {stderr or stdout}"}

    # Wait for container to be healthy
    time.sleep(5)
    stdout, stderr, rc = _run(f"curl -s -o /dev/null -w '%{{http_code}}' http://localhost:{port}/")
    if stdout != "200":
        # Try common alternate ports
        for internal_port in [8080, 4000, 5000, 80]:
            _run(f"docker rm -f {container_name} 2>/dev/null")
            stdout2, stderr2, rc2 = _run(
                f"docker run -d --name {container_name} --restart unless-stopped -p {port}:{internal_port} {container_name} 2>&1"
            )
            time.sleep(3)
            stdout, stderr, rc = _run(f"curl -s -o /dev/null -w '%{{http_code}}' http://localhost:{port}/")
            if stdout == "200":
                break

    # Configure nginx reverse proxy
    nginx_conf = f"""server {{
    listen 80;
    server_name {subdomain};

    location / {{
        proxy_pass http://127.0.0.1:{port};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}
}}
"""
    nginx_path = NGINX_DIR / f"preview-{slug}.conf"
    try:
        nginx_path.write_text(nginx_conf)
        _run("sudo nginx -t && sudo nginx -s reload")
    except PermissionError:
        # Try with sudo
        _run(f"echo '{nginx_conf}' | sudo tee {nginx_path}")
        _run("sudo nginx -t && sudo nginx -s reload")

    # Record preview
    previews[slug] = {
        "slug": slug,
        "org": org,
        "project_dir": str(project_dir),
        "container": container_name,
        "port": port,
        "subdomain": subdomain,
        "url": f"http://{subdomain}",
        "direct_url": f"http://{SERVER_IP}:{port}",
        "customer_email": customer_email,
        "production_domain": production_domain,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "live",
    }
    _save_previews(previews)

    return previews[slug]


def teardown_preview(slug: str) -> dict:
    """Stop and remove a preview container and nginx config."""
    previews = _load_previews()

    if slug not in previews:
        return {"status": "error", "error": f"Preview '{slug}' not found"}

    preview = previews[slug]
    container = preview.get("container", f"preview-{slug}")

    # Stop container
    _run(f"docker rm -f {container} 2>/dev/null")
    # Also try compose teardown
    project_dir = preview.get("project_dir", "")
    if project_dir:
        _run(f"cd {project_dir} && docker compose -p preview-{slug} down 2>/dev/null")

    # Remove nginx config
    nginx_path = NGINX_DIR / f"preview-{slug}.conf"
    if nginx_path.exists():
        try:
            nginx_path.unlink()
        except PermissionError:
            _run(f"sudo rm -f {nginx_path}")
        _run("sudo nginx -s reload")

    preview["status"] = "torn_down"
    preview["torn_down_at"] = datetime.now(timezone.utc).isoformat()
    previews[slug] = preview
    _save_previews(previews)

    return {"status": "torn_down", "slug": slug}


def promote_to_production(slug: str, production_domain: str = "") -> dict:
    """Promote a preview to production — update nginx to serve the production domain."""
    previews = _load_previews()
    if slug not in previews:
        return {"status": "error", "error": f"Preview '{slug}' not found"}

    preview = previews[slug]
    domain = production_domain or preview.get("production_domain", "")
    if not domain:
        return {"status": "error", "error": "No production domain specified. Pass production_domain or set it during deploy_preview()."}

    port = preview["port"]

    # Add production domain to nginx config (keep preview subdomain too)
    nginx_conf = f"""server {{
    listen 80;
    server_name {domain} {preview['subdomain']};

    location / {{
        proxy_pass http://127.0.0.1:{port};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}
}}
"""
    nginx_path = NGINX_DIR / f"preview-{slug}.conf"
    try:
        nginx_path.write_text(nginx_conf)
        _run("sudo nginx -t && sudo nginx -s reload")
    except PermissionError:
        _run(f"echo '{nginx_conf}' | sudo tee {nginx_path}")
        _run("sudo nginx -t && sudo nginx -s reload")

    preview["production_domain"] = domain
    preview["status"] = "production"
    preview["promoted_at"] = datetime.now(timezone.utc).isoformat()
    previews[slug] = preview
    _save_previews(previews)

    return {
        "status": "production",
        "domain": domain,
        "url": f"http://{domain}",
        "port": port,
        "note": f"DNS for {domain} must point to {SERVER_IP} (A record or Cloudflare proxy)",
    }


def list_previews() -> list:
    """List all active previews."""
    previews = _load_previews()
    return [p for p in previews.values() if p.get("status") == "live"]


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Customer Preview Deployment")
    parser.add_argument("--project-dir", help="Path to project directory")
    parser.add_argument("--slug", help="Short name for the preview (e.g. khhs)")
    parser.add_argument("--org", default="gigforge", help="Organization")
    parser.add_argument("--customer", default="", help="Customer email")
    parser.add_argument("--list", action="store_true", help="List active previews")
    parser.add_argument("--teardown", help="Tear down a preview by slug")

    args = parser.parse_args()

    if args.list:
        for p in list_previews():
            print(f"  {p['slug']} | {p['url']} | port {p['port']} | {p.get('customer_email', '?')}")
    elif args.teardown:
        result = teardown_preview(args.teardown)
        print(json.dumps(result, indent=2))
    elif args.project_dir and args.slug:
        result = deploy_preview(args.project_dir, args.slug, args.org, args.customer)
        print(json.dumps(result, indent=2))
    else:
        parser.print_help()
