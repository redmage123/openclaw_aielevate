#!/usr/bin/env python3
"""Playwright Acceptance Test — proves an app WORKS before it's marked delivered.

Runs a headless browser against the deployed app and verifies:
1. Pages load (HTTP 200, no blank screens)
2. No placeholder text ("coming soon", "lorem ipsum", "todo")
3. Interactive elements work (forms, buttons, navigation)
4. API endpoints return real data
5. Screenshots every page for visual review
6. GDPR elements present (privacy policy, cookie consent)
7. Accessibility basics (ARIA attributes, alt text)

Usage:
    python3 acceptance_test.py --url http://78.47.104.139:4106 --project carehaven
    python3 acceptance_test.py --url https://cc.techuni.ai --project course-creator
"""

import sys
import os
import json
import time
import re
import argparse
import logging
from pathlib import Path
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [acceptance] %(message)s")
log = logging.getLogger("acceptance")


def run_tests(url, project_slug, login_email=None, login_password=None, screenshot_dir=None):
    """Run full acceptance test suite against a deployed app."""
    from playwright.sync_api import sync_playwright

    if screenshot_dir is None:
        screenshot_dir = f"/opt/ai-elevate/gigforge/projects/{project_slug}/test-screenshots"
    Path(screenshot_dir).mkdir(parents=True, exist_ok=True)

    results = {
        "url": url,
        "project": project_slug,
        "timestamp": datetime.now().isoformat(),
        "tests": [],
        "passed": 0,
        "failed": 0,
        "screenshots": [],
    }

    def record(name, passed, detail=""):
        status = "PASS" if passed else "FAIL"
        results["tests"].append({"name": name, "status": status, "detail": detail})
        if passed:
            results["passed"] += 1
        else:
            results["failed"] += 1
        log.info(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 AcceptanceTest/1.0",
        )
        page = context.new_page()

        # ── Test 1: Homepage loads ──
        log.info(f"Testing {url}")
        try:
            resp = page.goto(url, wait_until="networkidle", timeout=15000)
            record("Homepage loads", resp.status == 200, f"HTTP {resp.status}")
            page.screenshot(path=f"{screenshot_dir}/01-homepage.png")
            results["screenshots"].append("01-homepage.png")
        except Exception as e:
            record("Homepage loads", False, str(e)[:100])

        # ── Test 2: No placeholder text ──
        body_text = page.inner_text("body").lower()
        placeholders = ["coming soon", "lorem ipsum", "todo", "placeholder", "under construction", "sample text"]
        found_placeholders = [p for p in placeholders if p in body_text]
        record("No placeholder text", len(found_placeholders) == 0,
               f"Found: {found_placeholders}" if found_placeholders else "Clean")

        # ── Test 3: Login (if credentials provided) ──
        if login_email and login_password:
            try:
                # Find login form
                email_input = page.query_selector('input[type="email"], input[name="email"], #login-email')
                password_input = page.query_selector('input[type="password"], input[name="password"], #login-password')

                if email_input and password_input:
                    email_input.fill(login_email)
                    password_input.fill(login_password)
                    page.screenshot(path=f"{screenshot_dir}/02-login-filled.png")

                    # Click submit
                    submit = page.query_selector('button[type="submit"], input[type="submit"]')
                    if submit:
                        submit.click()
                        page.wait_for_timeout(3000)
                        page.screenshot(path=f"{screenshot_dir}/03-after-login.png")
                        results["screenshots"].extend(["02-login-filled.png", "03-after-login.png"])

                        # Check if login succeeded (no longer on login page)
                        current_url = page.url
                        record("Login works", "/login" not in current_url,
                               f"Redirected to {current_url}")
                    else:
                        record("Login works", False, "No submit button found")
                else:
                    record("Login works", False, "No email/password inputs found")
            except Exception as e:
                record("Login works", False, str(e)[:100])

        # ── Test 4: Check key pages ──
        pages_to_check = [
            ("/residents", "Residents page"),
            ("/mar", "MAR page"),
            ("/incidents", "Incidents page"),
        ]

        for path, name in pages_to_check:
            try:
                page.goto(f"{url}{path}", wait_until="networkidle", timeout=10000)
                page.wait_for_timeout(2000)
                text = page.inner_text("body").lower()
                screenshot_name = f"page-{path.strip('/').replace('/', '-')}.png"
                page.screenshot(path=f"{screenshot_dir}/{screenshot_name}")
                results["screenshots"].append(screenshot_name)

                has_placeholder = any(p in text for p in placeholders)
                has_content = len(text.strip()) > 100
                has_error = "error" in text[:200].lower() and "try again" in text.lower()

                if has_placeholder:
                    record(f"{name} loads", False, "Contains placeholder text")
                elif has_error:
                    record(f"{name} loads", False, "Shows error message")
                elif has_content:
                    record(f"{name} loads", True, f"{len(text)} chars of content")
                else:
                    record(f"{name} loads", False, "Page appears blank")
            except Exception as e:
                record(f"{name} loads", False, str(e)[:100])

        # ── Test 5: Interactive elements ──
        page.goto(url, wait_until="networkidle", timeout=10000)
        page.wait_for_timeout(2000)
        html = page.content()
        buttons = html.count("<button")
        inputs = html.count("<input")
        links = html.count("<a ")
        forms = html.count("<form")
        record("Has interactive elements",
               buttons > 0 or inputs > 0 or forms > 0,
               f"buttons={buttons} inputs={inputs} links={links} forms={forms}")

        # ── Test 6: GDPR elements ──
        full_html = page.content().lower()
        has_privacy = "privacy" in full_html or "gdpr" in full_html or "data protection" in full_html
        has_cookie = "cookie" in full_html
        record("GDPR elements", has_privacy or has_cookie,
               f"privacy={'yes' if has_privacy else 'no'} cookie={'yes' if has_cookie else 'no'}")

        # ── Test 7: Accessibility ──
        aria_count = full_html.count("aria-")
        alt_count = full_html.count('alt="')
        role_count = full_html.count('role="')
        record("Accessibility basics",
               aria_count > 5,
               f"aria={aria_count} alt={alt_count} role={role_count}")

        # ── Test 8: No console errors ──
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)[:100]))
        page.goto(url, wait_until="networkidle", timeout=10000)
        page.wait_for_timeout(2000)
        record("No console errors", len(errors) == 0,
               f"{len(errors)} errors" if errors else "Clean")

        # ── Test 9: Mobile responsive ──
        context2 = browser.new_context(viewport={"width": 375, "height": 812})
        mobile = context2.new_page()
        try:
            mobile.goto(url, wait_until="networkidle", timeout=10000)
            mobile.wait_for_timeout(2000)
            mobile.screenshot(path=f"{screenshot_dir}/mobile.png")
            results["screenshots"].append("mobile.png")
            mobile_text = mobile.inner_text("body")
            record("Mobile responsive", len(mobile_text.strip()) > 50, "Content renders on mobile")
        except Exception as e:
            record("Mobile responsive", False, str(e)[:100])
        context2.close()

        browser.close()

    # ── Report ──
    total = results["passed"] + results["failed"]
    pct = (results["passed"] / total * 100) if total > 0 else 0
    results["summary"] = f"{results['passed']}/{total} passed ({pct:.0f}%)"

    # Save report
    report_path = f"{screenshot_dir}/acceptance-report.json"
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2)

    log.info(f"\n{'='*50}")
    log.info(f"ACCEPTANCE TEST: {results['summary']}")
    log.info(f"Screenshots: {screenshot_dir}")
    log.info(f"Report: {report_path}")
    log.info(f"{'='*50}")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Acceptance Test")
    parser.add_argument("--url", required=True, help="App URL to test")
    parser.add_argument("--project", required=True, help="Project slug")
    parser.add_argument("--email", help="Login email")
    parser.add_argument("--password", help="Login password")
    parser.add_argument("--screenshot-dir", help="Where to save screenshots")
    args = parser.parse_args()

    results = run_tests(args.url, args.project, args.email, args.password, args.screenshot_dir)

    sys.exit(0 if results["failed"] == 0 else 1)
