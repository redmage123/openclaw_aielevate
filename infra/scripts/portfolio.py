#!/usr/bin/env python3
"""Portfolio Registry — live demos and case studies for proposals.

When writing a proposal, agents call match_portfolio(job_description) to get
relevant portfolio items with live URLs to include in the proposal.

Usage:
  from portfolio import match_portfolio, get_all

  # Get portfolio items matching a job description
  matches = match_portfolio("React dashboard with charts and Stripe integration")
  for m in matches:
      print(f"{m['name']} — {m['url']} — {m['relevance']}")
"""

PORTFOLIO = [
    {
        "name": "GigForge Agency Website",
        "url": "https://gigforge.ai",
        "tech": ["next.js", "react", "tailwind", "docker", "cloudflare"],
        "category": "website",
        "description": "AI-powered freelance agency website with portfolio, services, contact forms, blog, dark mode, responsive design, i18n support.",
        "tags": ["website", "react", "responsive", "dark mode", "cms", "seo"],
    },
    {
        "name": "TechUni AI Platform",
        "url": "https://techuni.ai",
        "tech": ["next.js", "react", "tailwind", "docker"],
        "category": "saas",
        "description": "Corporate training platform — AI-powered course creation at scale. SaaS with subscription billing, user management, course builder.",
        "tags": ["saas", "ai", "education", "platform", "subscription", "react"],
    },
    {
        "name": "Course Creator Platform",
        "url": "https://courses.techuni.ai",
        "tech": ["react", "python", "fastapi", "postgresql", "docker", "stripe"],
        "category": "saas",
        "description": "Full SaaS platform — 391 courses, user management, payment integration, multi-tenant architecture. 22 Docker containers.",
        "tags": ["saas", "platform", "stripe", "multi-tenant", "docker", "postgresql"],
    },
    {
        "name": "CryptoAdvisor Dashboard",
        "url": "http://78.47.104.139:8050",
        "tech": ["python", "fastapi", "react", "bokeh", "sqlite", "docker"],
        "category": "dashboard",
        "description": "Multi-chain cryptocurrency analytics dashboard. Real-time data from 7 blockchains, technical indicators, portfolio tracking, risk metrics.",
        "tags": ["dashboard", "crypto", "analytics", "charts", "real-time", "python", "react"],
    },
    {
        "name": "CRM System",
        "url": "http://78.47.104.139:3090",
        "tech": ["react", "python", "fastapi", "postgresql", "docker", "jwt"],
        "category": "webapp",
        "description": "Full CRM with contact management, lead pipeline, deal tracking, JWT auth, role-based access control.",
        "tags": ["crm", "webapp", "auth", "rbac", "postgresql", "react", "fastapi"],
    },
    {
        "name": "Job Board Platform",
        "url": None,
        "tech": ["react", "typescript", "vite", "fastapi", "postgresql"],
        "category": "webapp",
        "description": "Job posting and application platform with search, filtering, admin panel, user registration.",
        "tags": ["webapp", "marketplace", "search", "react", "typescript"],
    },
    {
        "name": "SaaS Billing API",
        "url": None,
        "tech": ["node.js", "nestjs", "postgresql", "stripe", "docker"],
        "category": "api",
        "description": "Complete billing API with invoicing, subscription management, payment processing via Stripe.",
        "tags": ["api", "billing", "stripe", "subscription", "nestjs"],
    },
    {
        "name": "AI Chat Widget",
        "url": None,
        "tech": ["react", "typescript", "python", "fastapi", "llm"],
        "category": "ai",
        "description": "Embeddable AI chat widget for websites. Connects to knowledge bases, handles customer queries autonomously.",
        "tags": ["ai", "chatbot", "widget", "llm", "react"],
    },
    {
        "name": "Video Creator Platform",
        "url": None,
        "tech": ["python", "docker", "chromadb", "fastapi"],
        "category": "ai",
        "description": "Agentic video production platform. 7 specialized AI agents (writer, director, editor, sound, assets, QA, orchestrator) collaborate to produce videos.",
        "tags": ["ai", "video", "agents", "automation", "docker"],
    },
    {
        "name": "DevOps Starter Kit",
        "url": None,
        "tech": ["docker", "nginx", "github-actions", "terraform"],
        "category": "devops",
        "description": "Production-ready DevOps template: Docker Compose, CI/CD, monitoring, SSL, automated deployments.",
        "tags": ["devops", "docker", "ci/cd", "terraform", "automation"],
    },
    {
        "name": "98-Agent AI Company",
        "url": "https://ai-elevate.ai",
        "tech": ["python", "typescript", "openclaw", "docker", "postgresql", "ai"],
        "category": "ai",
        "description": "Fully autonomous AI company with 98 agents across 3 organizations. Sales, engineering, legal, marketing, support, finance — all AI-driven with human oversight.",
        "tags": ["ai", "agents", "automation", "enterprise", "orchestration"],
    },
]


def match_portfolio(text: str, limit: int = 3) -> list:
    """Find the most relevant portfolio items for a job description."""
    text_lower = text.lower()
    scored = []

    for item in PORTFOLIO:
        score = 0
        # Tech match
        for tech in item["tech"]:
            if tech.lower() in text_lower:
                score += 10
        # Tag match
        for tag in item["tags"]:
            if tag in text_lower:
                score += 5
        # Category match
        if item["category"] in text_lower:
            score += 8
        # Description keyword overlap
        desc_words = set(item["description"].lower().split())
        text_words = set(text_lower.split())
        overlap = len(desc_words & text_words)
        score += overlap * 2

        if score > 0:
            scored.append({
                "name": item["name"],
                "url": item["url"],
                "tech": item["tech"],
                "description": item["description"],
                "relevance": score,
                "category": item["category"],
            })

    scored.sort(key=lambda x: x["relevance"], reverse=True)
    return scored[:limit]


def get_all() -> list:
    """Get all portfolio items."""
    return PORTFOLIO


def portfolio_summary(text: str) -> str:
    """Generate a portfolio section for a proposal."""
    matches = match_portfolio(text, limit=3)
    if not matches:
        return "We have delivered similar projects — happy to share relevant examples."

    lines = ["**Relevant work from our portfolio:**\n"]
    for m in matches:
        url_str = f" — Live: {m['url']}" if m['url'] else ""
        lines.append(f"- **{m['name']}**{url_str}")
        lines.append(f"  {m['description'][:150]}")
        lines.append(f"  Tech: {', '.join(m['tech'][:5])}")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        print(portfolio_summary(query))
    else:
        for p in PORTFOLIO:
            url = p['url'] or 'no live demo'
            print(f"  {p['name']:35s} [{p['category']:10s}] {url}")
