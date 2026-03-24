#!/usr/bin/env python3
"""AI Elevate Bid Submitter — Playwright-based form filler for freelance platforms.

What: Automates the tedious process of filling in bid/proposal forms on
      Upwork, Freelancer, and PeoplePerHour. Fills every field, attaches
      portfolio links, takes a confirmation screenshot, then STOPS — the
      human reviews and clicks Submit.

Why:  The biggest bottleneck in the sales pipeline is the human-in-the-loop
      for bid submission. Writing proposals is automated (scout + sales agents),
      but copy-pasting into platform forms takes 5-10 minutes per bid. This
      reduces it to: run script → review screenshot → click submit.

How:  Uses Playwright to:
      1. Log into the platform (stored credentials)
      2. Navigate to the job/project URL
      3. Fill in all form fields (proposal text, bid amount, timeline)
      4. Attach portfolio links where supported
      5. Take a screenshot of the filled form
      6. Send screenshot to Braun via email for review
      7. Optionally wait for manual submit (interactive mode)

Usage:
    # Fill a Freelancer bid
    python3 bid_submitter.py --platform freelancer \\
        --url "https://www.freelancer.com/projects/..." \\
        --proposal-file /opt/ai-elevate/gigforge/memory/proposals/2026-03-21-fl3-news-ai.md \\
        --bid-amount 3500 \\
        --delivery-days 14

    # Fill an Upwork proposal
    python3 bid_submitter.py --platform upwork \\
        --url "https://www.upwork.com/freelance-jobs/..." \\
        --proposal-file /opt/ai-elevate/gigforge/memory/proposals/prospect-1-ibkr.md \\
        --bid-amount 3500

    # Fill a PeoplePerHour proposal
    python3 bid_submitter.py --platform pph \\
        --url "https://www.peopleperhour.com/freelance-jobs/..." \\
        --proposal-file /opt/ai-elevate/gigforge/memory/proposals/prospect-3.md \\
        --bid-amount 4800

    # List available proposals
    python3 bid_submitter.py --list-proposals

    # Interactive mode (keeps browser open for manual submit)
    python3 bid_submitter.py --platform freelancer --url "..." --interactive

Architecture:
    BidSubmitter (base class)
      ├── FreelancerSubmitter
      ├── UpworkSubmitter
      └── PeoplePerHourSubmitter
"""

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

from logging_config import get_logger
from exceptions import AiElevateError

log = get_logger("bid-submitter")

# ============================================================================
# Configuration
# ============================================================================

CREDENTIALS_DIR = Path("/opt/ai-elevate/credentials")
PROPOSALS_DIR = Path("/opt/ai-elevate/gigforge/memory/proposals")
SCREENSHOTS_DIR = Path("/tmp/bid-screenshots")
SCREENSHOTS_DIR.mkdir(exist_ok=True)

PORTFOLIO_LINKS = [
    "https://devops-demo.gigforge.ai",
    "https://billing-demo.gigforge.ai",
    "https://contacts-demo.gigforge.ai",
    "https://gigforge.ai",
]


@dataclass
class BidConfig:
    """Configuration for a single bid submission.

    What: Encapsulates all data needed to fill a bid form on any platform.

    Attributes:
        platform: 'freelancer', 'upwork', or 'pph'
        job_url: Direct URL to the job/project listing
        proposal_text: The proposal body text to paste
        bid_amount: Bid price in the listing's currency
        delivery_days: Delivery timeline in days
        portfolio_links: URLs to include as portfolio/samples
        interactive: If True, keep browser open after filling
    """
    platform: str
    job_url: str
    proposal_text: str = ""
    bid_amount: float = 0
    delivery_days: int = 14
    portfolio_links: list = None
    interactive: bool = False

    def __post_init__(self):
        """Set default portfolio links if not provided."""
        if self.portfolio_links is None:
            self.portfolio_links = PORTFOLIO_LINKS


# ============================================================================
# Proposal Parser
# ============================================================================

def parse_proposal_file(filepath: Path) -> dict:
    """Parse a proposal .md file to extract text, bid amount, and platform.

    What: Reads a markdown proposal file and extracts the submission-ready
          proposal text, bid amount, platform, and URL.

    Why:  Proposals are stored as markdown with metadata headers. The submitter
          needs just the proposal text and bid amount, not the full document.

    Args:
        filepath: Path to the proposal .md file

    Returns:
        Dict with keys: text, bid_amount, platform, url
    """
    content = filepath.read_text()

    # Extract proposal text (between ## Proposal Text and ## Notes)
    text_match = re.search(r'## Proposal Text\s*\n(.*?)(?=\n## |\Z)', content, re.DOTALL)
    proposal_text = text_match.group(1).strip() if text_match else content

    # Extract bid amount
    bid_match = re.search(r'\*\*Bid:?\*\*:?\s*\$?([\d,]+)', content)
    bid_amount = float(bid_match.group(1).replace(",", "")) if bid_match else 0

    # Extract platform
    platform = "unknown"
    if "freelancer.com" in content.lower():
        platform = "freelancer"
    elif "upwork.com" in content.lower():
        platform = "upwork"
    elif "peopleperhour.com" in content.lower():
        platform = "pph"

    # Extract URL
    url_match = re.search(r'https?://(?:www\.)?(?:freelancer\.com|upwork\.com|peopleperhour\.com)/[^\s\n]+', content)
    url = url_match.group(0) if url_match else ""

    return {
        "text": proposal_text,
        "bid_amount": bid_amount,
        "platform": platform,
        "url": url,
    }


# ============================================================================
# Base Submitter
# ============================================================================

class BidSubmitter:
    """Base class for platform-specific bid form fillers.

    What: Provides common Playwright browser management and screenshot/notification
          functionality. Subclasses implement platform-specific form filling.

    Why:  Each platform has different form layouts, field names, and submission
          flows. The base class handles the common parts (browser launch, login
          state, screenshots, email notifications).

    How:  Uses Playwright in non-headless mode for interactive use, or headless
          for automated fills. Stores login state in browser context to avoid
          re-authentication on every run.
    """

    platform_name = "unknown"
    login_url = ""
    state_file = ""

    def __init__(self, headless: bool = True):
        """Initialize the submitter.

        Args:
            headless: Run browser in headless mode (False for interactive)
        """
        self.headless = headless
        self.browser = None
        self.page = None

    def launch(self):
        """Launch the browser with stored login state.

        What: Starts a Chromium browser and loads any saved authentication state
              (cookies, localStorage) so the user doesn't have to log in every time.

        How:  Uses Playwright's persistent context with a storage state file.
              If no state exists, the browser opens to the login page.
        """
        from playwright.sync_api import sync_playwright

        self.pw = sync_playwright().start()
        state_path = CREDENTIALS_DIR / f"{self.platform_name}-playwright-state.json"

        launch_args = {
            "headless": self.headless,
            "args": ["--no-sandbox"],
        }

        if state_path.exists():
            self.context = self.pw.chromium.launch_persistent_context(
                user_data_dir=str(CREDENTIALS_DIR / f"{self.platform_name}-browser-data"),
                storage_state=str(state_path),
                **launch_args,
            )
            log.info("Browser launched with saved state", extra={"platform": self.platform_name})
        else:
            self.browser = self.pw.chromium.launch(**launch_args)
            self.context = self.browser.new_context()
            log.info("Browser launched (no saved state — may need login)", extra={"platform": self.platform_name})

        self.page = self.context.new_page()
        self.page.set_viewport_size({"width": 1280, "height": 900})

    def save_state(self):
        """Save browser authentication state for future runs.

        What: Dumps cookies and localStorage to a JSON file so the next
              run doesn't need to log in again.
        """
        state_path = CREDENTIALS_DIR / f"{self.platform_name}-playwright-state.json"
        self.context.storage_state(path=str(state_path))
        log.info("Browser state saved", extra={"platform": self.platform_name})

    def screenshot(self, name: str = "bid-form") -> Path:
        """Take a screenshot of the current page.

        Args:
            name: Screenshot filename prefix

        Returns:
            Path to the saved screenshot file
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        path = SCREENSHOTS_DIR / f"{name}-{self.platform_name}-{timestamp}.png"
        self.page.screenshot(path=str(path))
        log.info("Screenshot saved", extra={"path": str(path)})
        return path

    def notify(self, screenshot_path: Path, config: BidConfig):
        """Send the filled form screenshot to Braun for review.

        What: Emails the screenshot so Braun can review the filled form
              before manually clicking Submit.

        Args:
            screenshot_path: Path to the screenshot image
            config: The bid configuration used
        """
        try:
            import requests
            key = open("/opt/ai-elevate/credentials/mailgun-api-key.txt").read().strip()
            requests.post(
                "https://api.mailgun.net/v3/gigforge.ai/messages",
                auth=("api", key),
                files=[("attachment", (screenshot_path.name, open(screenshot_path, "rb")))],
                data={
                    "from": "GigForge Scout <gigforge-scout@gigforge.ai>",
                    "to": "braun.brelin@ai-elevate.ai",
                    "subject": f"Bid ready for review — {config.platform} ${config.bid_amount}",
                    "text": (
                        f"Platform: {config.platform}\n"
                        f"Job: {config.job_url}\n"
                        f"Bid: ${config.bid_amount}\n"
                        f"Delivery: {config.delivery_days} days\n\n"
                        f"The form is filled and ready. Log in to {config.platform} and click Submit.\n\n"
                        f"Screenshot attached."
                    ),
                },
            )
            log.info("Review notification sent", extra={"platform": config.platform})
        except Exception as e:
            log.warning("Failed to send notification", extra={"error": str(e)})

    def fill(self, config: BidConfig):
        """Fill the bid form. Override in subclasses.

        Args:
            config: Bid configuration with all form data
        """
        raise NotImplementedError("Subclasses must implement fill()")

    def close(self):
        """Close the browser."""
        if self.context:
            self.context.close()
        if self.browser:
            self.browser.close()
        if self.pw:
            self.pw.stop()

    def run(self, config: BidConfig) -> Path:
        """Full workflow: launch → navigate → fill → screenshot → notify.

        Args:
            config: Bid configuration

        Returns:
            Path to the confirmation screenshot
        """
        try:
            self.launch()
            self.fill(config)
            ss = self.screenshot("bid-filled")
            self.save_state()
            self.notify(ss, config)

            if config.interactive:
                log.info("Interactive mode — browser stays open. Press Enter when done.")
                input("Press Enter to close browser...")

            return ss
        finally:
            if not config.interactive:
                self.close()


# ============================================================================
# Freelancer
# ============================================================================

class FreelancerSubmitter(BidSubmitter):
    """Fill bid forms on Freelancer.com.

    What: Navigates to a Freelancer project, clicks 'Place Bid', fills in
          the bid amount, delivery period, and proposal description.

    How:  Uses Playwright selectors for Freelancer's bid form. The form
          structure is: bid amount → delivery days → description textarea.
    """

    platform_name = "freelancer"
    login_url = "https://www.freelancer.com/login"

    def fill(self, config: BidConfig):
        """Fill the Freelancer bid form.

        Args:
            config: Bid configuration with amount, days, and proposal text
        """
        log.info("Navigating to job", extra={"url": config.job_url})
        self.page.goto(config.job_url, wait_until="networkidle", timeout=30000)
        self.page.wait_for_timeout(3000)

        # Click Place Bid button
        try:
            self.page.click("text=Place Bid", timeout=5000)
            self.page.wait_for_timeout(2000)
        except Exception:
            log.info("No 'Place Bid' button — may already be on bid form")

        # Fill bid amount
        try:
            amount_input = self.page.query_selector('input[name="bidAmount"], input[data-testid="bid-amount"], input[placeholder*="amount" i]')
            if amount_input:
                amount_input.clear()
                amount_input.type(str(int(config.bid_amount)))
                log.info("Bid amount filled", extra={"amount": config.bid_amount})
        except Exception as e:
            log.warning("Could not fill bid amount", extra={"error": str(e)})

        # Fill delivery period
        try:
            days_input = self.page.query_selector('input[name="period"], input[data-testid="delivery-period"], input[placeholder*="day" i]')
            if days_input:
                days_input.clear()
                days_input.type(str(config.delivery_days))
                log.info("Delivery days filled", extra={"days": config.delivery_days})
        except Exception as e:
            log.warning("Could not fill delivery days", extra={"error": str(e)})

        # Fill proposal description
        try:
            desc_area = self.page.query_selector('textarea[name="descr"], textarea[data-testid="proposal-description"], textarea[placeholder*="describe" i], textarea')
            if desc_area:
                desc_area.clear()
                desc_area.type(config.proposal_text, delay=5)
                log.info("Proposal text filled", extra={"chars": len(config.proposal_text)})
        except Exception as e:
            log.warning("Could not fill proposal text", extra={"error": str(e)})

        self.page.wait_for_timeout(1000)
        log.info("Freelancer bid form filled — ready for review")


# ============================================================================
# Upwork
# ============================================================================

class UpworkSubmitter(BidSubmitter):
    """Fill proposal forms on Upwork.

    What: Navigates to an Upwork job, clicks 'Submit a Proposal', fills in
          the cover letter and bid terms.

    How:  Upwork has a multi-step proposal form. This fills the cover letter,
          proposed rate/amount, and any additional questions.
    """

    platform_name = "upwork"
    login_url = "https://www.upwork.com/ab/account-security/login"

    def fill(self, config: BidConfig):
        """Fill the Upwork proposal form.

        Args:
            config: Bid configuration with proposal text and bid amount
        """
        log.info("Navigating to job", extra={"url": config.job_url})
        self.page.goto(config.job_url, wait_until="networkidle", timeout=30000)
        self.page.wait_for_timeout(3000)

        # Click Submit a Proposal / Apply Now
        try:
            self.page.click("text=Submit a Proposal", timeout=5000)
            self.page.wait_for_timeout(3000)
        except Exception:
            try:
                self.page.click("text=Apply Now", timeout=5000)
                self.page.wait_for_timeout(3000)
            except Exception:
                log.info("No submit button — may already be on proposal form")

        # Fill cover letter
        try:
            cover_letter = self.page.query_selector('textarea[data-testid="cover-letter"], textarea[name="coverLetter"], textarea[placeholder*="cover letter" i], textarea')
            if cover_letter:
                cover_letter.clear()
                cover_letter.type(config.proposal_text, delay=5)
                log.info("Cover letter filled", extra={"chars": len(config.proposal_text)})
        except Exception as e:
            log.warning("Could not fill cover letter", extra={"error": str(e)})

        # Fill bid amount (if fixed price)
        if config.bid_amount > 0:
            try:
                bid_input = self.page.query_selector('input[data-testid="bid-input"], input[name="amount"], input[aria-label*="bid" i]')
                if bid_input:
                    bid_input.clear()
                    bid_input.type(str(int(config.bid_amount)))
                    log.info("Bid amount filled", extra={"amount": config.bid_amount})
            except Exception as e:
                log.warning("Could not fill bid amount", extra={"error": str(e)})

        self.page.wait_for_timeout(1000)
        log.info("Upwork proposal form filled — ready for review")


# ============================================================================
# PeoplePerHour
# ============================================================================

class PeoplePerHourSubmitter(BidSubmitter):
    """Fill proposal forms on PeoplePerHour.

    What: Navigates to a PPH project, fills in the proposal text,
          price, and delivery timeline.
    """

    platform_name = "pph"
    login_url = "https://www.peopleperhour.com/login"

    def fill(self, config: BidConfig):
        """Fill the PeoplePerHour proposal form.

        Args:
            config: Bid configuration with proposal text, price, and timeline
        """
        log.info("Navigating to job", extra={"url": config.job_url})
        self.page.goto(config.job_url, wait_until="networkidle", timeout=30000)
        self.page.wait_for_timeout(3000)

        # Click Send Proposal / Apply
        try:
            self.page.click("text=Send Proposal", timeout=5000)
            self.page.wait_for_timeout(2000)
        except Exception:
            try:
                self.page.click("text=Apply", timeout=5000)
                self.page.wait_for_timeout(2000)
            except Exception:
                log.info("No apply button — may already be on form")

        # Fill proposal text
        try:
            proposal_area = self.page.query_selector('textarea[name="proposal"], textarea[placeholder*="proposal" i], textarea')
            if proposal_area:
                proposal_area.clear()
                proposal_area.type(config.proposal_text, delay=5)
                log.info("Proposal text filled", extra={"chars": len(config.proposal_text)})
        except Exception as e:
            log.warning("Could not fill proposal", extra={"error": str(e)})

        # Fill price
        if config.bid_amount > 0:
            try:
                price_input = self.page.query_selector('input[name="price"], input[placeholder*="price" i], input[type="number"]')
                if price_input:
                    price_input.clear()
                    price_input.type(str(int(config.bid_amount)))
                    log.info("Price filled", extra={"amount": config.bid_amount})
            except Exception as e:
                log.warning("Could not fill price", extra={"error": str(e)})

        # Fill delivery days
        try:
            days_input = self.page.query_selector('input[name="deliveryDays"], input[placeholder*="day" i], select[name="delivery"]')
            if days_input:
                days_input.clear()
                days_input.type(str(config.delivery_days))
                log.info("Delivery filled", extra={"days": config.delivery_days})
        except Exception as e:
            log.warning("Could not fill delivery", extra={"error": str(e)})

        self.page.wait_for_timeout(1000)
        log.info("PPH proposal form filled — ready for review")


# ============================================================================
# Factory
# ============================================================================

SUBMITTERS = {
    "freelancer": FreelancerSubmitter,
    "upwork": UpworkSubmitter,
    "pph": PeoplePerHourSubmitter,
    "peopleperhour": PeoplePerHourSubmitter,
}


def get_submitter(platform: str, headless: bool = True) -> BidSubmitter:
    """Get the appropriate submitter for a platform.

    Args:
        platform: Platform name ('freelancer', 'upwork', 'pph')
        headless: Run headless (True) or with visible browser (False)

    Returns:
        Platform-specific BidSubmitter instance

    Raises:
        ValueError: If platform is not supported
    """
    cls = SUBMITTERS.get(platform.lower())
    if not cls:
        raise ValueError(f"Unsupported platform: {platform}. Use: {list(SUBMITTERS.keys())}")
    return cls(headless=headless)


# ============================================================================
# CLI
# ============================================================================

def list_proposals():
    """List all available proposals with metadata."""
    if not PROPOSALS_DIR.exists():
        print("No proposals directory found")
        return

    for f in sorted(PROPOSALS_DIR.glob("*.md")):
        try:
            data = parse_proposal_file(f)
            platform = data["platform"]
            amount = data["bid_amount"]
            url = data["url"][:60] if data["url"] else "no URL"
            print(f"  {f.name}")
            print(f"    Platform: {platform} | Bid: ${amount:.0f} | {url}")
        except Exception:
            print(f"  {f.name} (parse error)")


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Bid Submitter — fill freelance platform bid forms with Playwright",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --platform freelancer --url "https://freelancer.com/projects/..." --proposal-file proposal.md --bid-amount 3500
  %(prog)s --platform upwork --url "https://upwork.com/..." --proposal-file proposal.md --bid-amount 3500
  %(prog)s --platform pph --url "https://peopleperhour.com/..." --proposal-file proposal.md
  %(prog)s --list-proposals
  %(prog)s --platform freelancer --url "..." --interactive   # keeps browser open

Supported platforms: freelancer, upwork, pph (PeoplePerHour)
        """,
    )
    parser.add_argument("--platform", choices=["freelancer", "upwork", "pph"], help="Target platform")
    parser.add_argument("--url", help="Job/project URL on the platform")
    parser.add_argument("--proposal-file", help="Path to proposal .md file")
    parser.add_argument("--proposal-text", help="Proposal text (alternative to --proposal-file)")
    parser.add_argument("--bid-amount", type=float, default=0, help="Bid amount in USD/GBP/EUR")
    parser.add_argument("--delivery-days", type=int, default=14, help="Delivery timeline in days")
    parser.add_argument("--interactive", action="store_true", help="Keep browser open after filling")
    parser.add_argument("--headless", action="store_true", default=True, help="Run browser headless")
    parser.add_argument("--no-headless", action="store_true", help="Show browser window")
    parser.add_argument("--list-proposals", action="store_true", help="List available proposals")
    parser.add_argument("--login-only", action="store_true", help="Just open login page and save state")
    return parser


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()

    if args.list_proposals:
        list_proposals()
        sys.exit(0)

    if args.login_only and args.platform:
        submitter = get_submitter(args.platform, headless=False)
        submitter.launch()
        submitter.page.goto(submitter.login_url)
        print(f"Log in to {args.platform}, then press Enter to save state...")
        input()
        submitter.save_state()
        submitter.close()
        print("Login state saved.")
        sys.exit(0)

    if not args.platform or not args.url:
        parser.print_help()
        sys.exit(1)

    # Get proposal text
    proposal_text = args.proposal_text or ""
    if args.proposal_file:
        pf = Path(args.proposal_file)
        if pf.exists():
            data = parse_proposal_file(pf)
            proposal_text = data["text"]
            if args.bid_amount == 0 and data["bid_amount"] > 0:
                args.bid_amount = data["bid_amount"]

    config = BidConfig(
        platform=args.platform,
        job_url=args.url,
        proposal_text=proposal_text,
        bid_amount=args.bid_amount,
        delivery_days=args.delivery_days,
        interactive=args.interactive,
    )

    headless = not args.no_headless and not args.interactive
    submitter = get_submitter(args.platform, headless=headless)
    screenshot = submitter.run(config)
    print(f"Done. Screenshot: {screenshot}")
