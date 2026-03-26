#!/usr/bin/env python3
"""Index all active project details into semantic search."""
import sys; sys.path.insert(0, "/home/aielevate")
from semantic_search import index_document

projects = [
    {
        "doc_id": "project-carehaven-platform",
        "content": "CareHaven is a nursing home management SaaS platform. Built for AI Elevate. "
                   "Backend: Payload CMS on port 4093. Frontend: React on port 4092. "
                   "Admin URL: http://78.47.104.139:4093/admin. Login credentials: admin / admin. "
                   "Client: Jon Brelin (jbrelin@gmail.com). "
                   "Status: Sprint 1 in progress. Platform MVP deployed. "
                   "Jon needs login credentials to access the platform.",
        "metadata": {"project": "CareHaven Platform", "client": "Jon Brelin",
                    "client_email": "jbrelin@gmail.com",
                    "admin_url": "http://78.47.104.139:4093/admin",
                    "credentials": "admin/admin", "status": "sprint-1"},
    },
    {
        "doc_id": "project-carehaven-website",
        "content": "CareHaven AI website. Marketing site for the CareHaven nursing home platform. "
                   "Deployed. Client: Jon Brelin (jbrelin@gmail.com).",
        "metadata": {"project": "CareHaven Website", "client": "Jon Brelin",
                    "client_email": "jbrelin@gmail.com"},
    },
    {
        "doc_id": "project-priority-management",
        "content": "Priority Management Training website rebuild. Client: Willie Bruen (wbruen@prioritymanagementtraining.ie). "
                   "Also involved: David Bruen. "
                   "Current site: prioritymanagementtraining.ie (WordPress, compromised). "
                   "Rebuild: Next.js + Tailwind + headless CMS (Sanity or Contentful). 10-week timeline. "
                   "Status: Site analysis complete, project plan and rebuild proposal sent to Willie. Ticket GF-54.",
        "metadata": {"project": "Priority Management", "client": "Willie Bruen",
                    "client_email": "wbruen@prioritymanagementtraining.ie", "ticket": "GF-54",
                    "current_site": "prioritymanagementtraining.ie", "status": "plan-sent"},
    },
    {
        "doc_id": "project-onlyonecustomer",
        "content": "OnlyOneCustomer (onlyonecustomer.com) — new web application concept from Willie Bruen "
                   "(wbruen@prioritymanagementtraining.ie). "
                   "Concept: aggregator platform with media, accountability, and video ad features. "
                   "Multi-layered: aggregator + media + accountability. Legal/logistical complexity noted. "
                   "Status: Initial concept received, clarifying questions sent to Willie, awaiting response.",
        "metadata": {"project": "OnlyOneCustomer", "client": "Willie Bruen",
                    "client_email": "wbruen@prioritymanagementtraining.ie", "status": "discovery"},
    },
    {
        "doc_id": "project-sudacka-mreza",
        "content": "Sudacka Mreza (Croatian Judiciary Website) rebuild for Drazen Komarica "
                   "(drazen.komarica@gmail.com). React 19 + Payload CMS + PostgreSQL with Croatian full-text search. "
                   "Frontend: port 4092. CMS: port 4093/admin. 15 enhancements including data migration, "
                   "citation linking, PDF export, expert witness directory, AI summaries. "
                   "Status: All milestones complete except Documentation. Data migration from old ASP.NET DB pending. "
                   "Go-live checklist sent. Ticket GF-50.",
        "metadata": {"project": "Sudacka Mreza", "client": "Drazen Komarica",
                    "client_email": "drazen.komarica@gmail.com", "ticket": "GF-50",
                    "status": "documentation-pending"},
    },
    {
        "doc_id": "project-course-creator",
        "content": "TechUni Course Creator platform. AI-powered course creation and learning SaaS. "
                   "Live at cc.techuni.ai. 23 Docker containers. React frontend, FastAPI backend, "
                   "PostgreSQL, Stripe billing (Free/Pro EUR49/Enterprise EUR199). "
                   "Sprint 1 remediation in progress (UX audit, brand audit, frontend fixes). "
                   "Login: admin/admin.",
        "metadata": {"project": "Course Creator", "url": "https://cc.techuni.ai",
                    "credentials": "admin/admin", "status": "sprint-1-remediation"},
    },
]

for p in projects:
    index_document("project", p["content"], doc_id=p["doc_id"],
                  metadata=p["metadata"], org="gigforge")
    name = p["metadata"]["project"]
    print(f"  Indexed: {name}")

print(f"\n{len(projects)} project records indexed")
