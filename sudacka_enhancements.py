#!/usr/bin/env python3
"""Build all 15 Sudačka Mreža enhancements.

Dispatches agents in logical order — some features depend on others.

Group 1 (foundation): Data migration scraper, Croatian search, legal taxonomy
Group 2 (core features): Citation linking, PDF export, RSS/notifications, statistics
Group 3 (user features): Expert verification, annotations, decision bookmarks
Group 4 (integration): Partner API, SEO structured data
Group 5 (advanced): PWA, OCR pipeline, AI summaries
"""
import subprocess
import os
import re
import time
from pathlib import Path
from exceptions import AiElevateError  # TODO: Use specific exception types

env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
PROJECT = "/opt/ai-elevate/gigforge/projects/sudacka-mreza"

def dispatch(agent_id, message, timeout=600):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    session_dir = Path(f"/home/aielevate/.openclaw/agents/{agent_id}/sessions")
    if session_dir.exists():
        for f in session_dir.glob("*.jsonl"):
            f.unlink()
    try:
        proc = subprocess.run(
            ["openclaw", "agent", "--agent", agent_id,
             "--message", message, "--thinking", "low", "--timeout", str(timeout)],
            capture_output=True, text=True, timeout=timeout + 30, env=env,
        )
        output = proc.stdout or ""
        return re.sub(r'\*\[.*?\]\*', '', output, flags=re.DOTALL).strip()
    except subprocess.TimeoutExpired:
        return "TIMEOUT"

def log(msg):
    """TODO: Add docstring — what this function does, why, how. Include Args/Returns/Raises."""
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ============================================================================
# GROUP 1: Foundation
# ============================================================================

log("GROUP 1: Foundation")
log("=" * 60)

# 1. Data Migration Scraper
log("[1/15] Data migration scraper...")
dispatch("gigforge-engineer",
    f"BUILD DATA MIGRATION SCRAPER for Sudačka Mreža\n"
    f"Project dir: {PROJECT}\n"
    f"Old site: http://sudacka-mreza.hr (HTTP only)\n\n"
    f"Create {PROJECT}/cms/src/migration/ directory with these scripts:\n\n"
    f"1. scraper.ts — Playwright/cheerio scraper that crawls the old site:\n"
    f"   - /sudska-praksa.aspx — court decisions (thousands of records)\n"
    f"   - /vjestaci.aspx — expert witnesses (name, speciality, contact, court)\n"
    f"   - /tumaci.aspx — interpreters (name, language pairs, contact)\n"
    f"   - /sudovi.aspx — courts directory (name, type, address, phone)\n"
    f"   - /dorh.aspx — state attorney offices\n"
    f"   - /web-stecaj.aspx — bankruptcy listings\n"
    f"   - /stecaj-ponude.aspx — bankruptcy sales\n"
    f"   - Paginate through all pages, extract structured data\n"
    f"   - Save to JSON files: decisions.json, experts.json, interpreters.json, etc.\n\n"
    f"2. importer.ts — Reads JSON files and imports into Payload CMS:\n"
    f"   - Uses Payload Local API to create records\n"
    f"   - Handles duplicates (check by name/case number)\n"
    f"   - Progress logging\n"
    f"   - Error handling with retry\n\n"
    f"3. migrate.ts — CLI entry point: scrape → import\n"
    f"   - npm run migrate:scrape\n"
    f"   - npm run migrate:import\n"
    f"   - npm run migrate (both)\n\n"
    f"Add cheerio and node-fetch to package.json dependencies.\n"
    f"The old site uses ASP.NET WebForms — parse the HTML tables and forms.\n"
    f"Handle Croatian characters (č, ć, š, đ, ž) correctly (UTF-8).",
    timeout=600)
log("[1/15] done")

# 2. Croatian Full-Text Search
log("[2/15] Croatian full-text search...")
dispatch("gigforge-dev-backend",
    f"ADD CROATIAN FULL-TEXT SEARCH to Sudačka Mreža\n"
    f"Project dir: {PROJECT}\n\n"
    f"1. Create {PROJECT}/db/init/01-croatian-search.sql:\n"
    f"   - Install pg_trgm extension\n"
    f"   - Create a Croatian text search configuration using 'simple' dictionary\n"
    f"   - Add hunspell dictionary if available, otherwise use unaccent + simple\n"
    f"   - CREATE TEXT SEARCH CONFIGURATION croatian (COPY = simple);\n"
    f"   - ALTER TEXT SEARCH CONFIGURATION croatian ALTER MAPPING FOR word WITH unaccent, simple;\n\n"
    f"2. Create {PROJECT}/cms/src/search/croatianSearch.ts:\n"
    f"   - Global search endpoint that searches across all collections\n"
    f"   - Uses tsvector with croatian config for court decisions\n"
    f"   - Uses pg_trgm (trigram similarity) for fuzzy name matching on experts/interpreters\n"
    f"   - Handles Croatian diacritics: č/ć/š/đ/ž ↔ c/s/d/z\n"
    f"   - Ranked results with ts_rank\n\n"
    f"3. Add search endpoint to Payload: GET /api/search?q=...\n"
    f"   - Returns results grouped by collection type\n"
    f"   - Highlights matching text\n\n"
    f"4. Update docker-compose.yml to run the SQL init script on db startup.",
    timeout=300)
log("[2/15] done")

# 3. Legal Taxonomy
log("[3/15] Legal taxonomy...")
dispatch("gigforge-dev-backend",
    f"ADD LEGAL TAXONOMY to Sudačka Mreža\n"
    f"Project dir: {PROJECT}\n\n"
    f"Create a Payload CMS collection for legal categories:\n\n"
    f"1. {PROJECT}/cms/src/collections/LegalCategories.ts:\n"
    f"   - Fields: name_hr, name_en, slug, parent (self-referencing for hierarchy), icon, description\n"
    f"   - Pre-seed with Croatian legal areas:\n"
    f"     - Kazneno pravo (Criminal Law)\n"
    f"     - Građansko pravo (Civil Law)\n"
    f"     - Trgovačko pravo (Commercial Law)\n"
    f"     - Upravno pravo (Administrative Law)\n"
    f"     - Radno pravo (Labour Law)\n"
    f"     - Obiteljsko pravo (Family Law)\n"
    f"     - Stečajno pravo (Bankruptcy Law)\n"
    f"     - Ustavno pravo (Constitutional Law)\n"
    f"     - Europsko pravo (EU Law)\n"
    f"     - Prekršajno pravo (Misdemeanour Law)\n\n"
    f"2. Add a 'categories' relationship field to the CourtDecisions collection\n"
    f"3. Create a seed script: {PROJECT}/cms/src/migration/seedTaxonomy.ts\n"
    f"4. Register in payload.config.ts",
    timeout=300)
log("[3/15] done")


# ============================================================================
# GROUP 2: Core Features
# ============================================================================

log("\nGROUP 2: Core Features")
log("=" * 60)

# 4. Citation Cross-Linking
log("[4/15] Citation cross-linking...")
dispatch("gigforge-engineer",
    f"ADD LEGAL CITATION CROSS-LINKING to Sudačka Mreža\n"
    f"Project dir: {PROJECT}\n\n"
    f"Court decisions reference other decisions in their text. Auto-detect and hyperlink them.\n\n"
    f"1. {PROJECT}/cms/src/hooks/citationLinker.ts — Payload afterChange hook on CourtDecisions:\n"
    f"   - Parse decision text for citation patterns:\n"
    f"     - 'Rev-123/2024' (revision cases)\n"
    f"     - 'Gž-456/2023' (civil appeals)\n"
    f"     - 'Kž-789/2022' (criminal appeals)\n"
    f"     - 'Pž-012/2021' (commercial appeals)\n"
    f"     - 'Us-I-345/2020' (administrative)\n"
    f"     - 'U-III-678/2019' (constitutional)\n"
    f"   - Match citations against existing decisions in the DB\n"
    f"   - Store linked decision IDs in a 'cited_decisions' relationship field\n"
    f"   - Also build reverse links ('cited_by')\n\n"
    f"2. {PROJECT}/web/src/components/decisions/CitationLink.tsx:\n"
    f"   - Renders citations as clickable links to the referenced decision\n"
    f"   - Tooltip preview on hover\n\n"
    f"3. Add 'cited_decisions' and 'cited_by' fields to CourtDecisions collection",
    timeout=300)
log("[4/15] done")

# 5. PDF Export
log("[5/15] PDF export...")
dispatch("gigforge-dev-backend",
    f"ADD PDF EXPORT to Sudačka Mreža\n"
    f"Project dir: {PROJECT}\n\n"
    f"Judges and lawyers need to download court decisions as formatted PDFs.\n\n"
    f"1. Add @react-pdf/renderer or puppeteer to cms/package.json\n\n"
    f"2. {PROJECT}/cms/src/endpoints/pdfExport.ts — Custom Payload endpoint:\n"
    f"   - GET /api/decisions/:id/pdf\n"
    f"   - Generates a PDF with:\n"
    f"     - Court header (court name, address)\n"
    f"     - Case number prominently displayed\n"
    f"     - Decision date\n"
    f"     - Decision type\n"
    f"     - Full text with proper formatting (paragraphs, numbered lists)\n"
    f"     - Footer: 'Izvor: Sudačka Mreža — sudacka-mreza.hr'\n"
    f"   - Returns PDF as application/pdf with Content-Disposition header\n\n"
    f"3. {PROJECT}/web/src/components/decisions/PdfDownloadButton.tsx:\n"
    f"   - Button on decision detail page\n"
    f"   - Downloads the PDF via the API endpoint",
    timeout=300)
log("[5/15] done")

# 6. RSS/Notifications
log("[6/15] RSS and email notifications...")
dispatch("gigforge-dev-backend",
    f"ADD RSS FEEDS AND EMAIL SUBSCRIPTIONS to Sudačka Mreža\n"
    f"Project dir: {PROJECT}\n\n"
    f"1. {PROJECT}/cms/src/endpoints/rss.ts — RSS feed endpoints:\n"
    f"   - GET /rss/decisions — latest court decisions\n"
    f"   - GET /rss/decisions?court=vts — filtered by court\n"
    f"   - GET /rss/decisions?category=kazneno — filtered by legal category\n"
    f"   - GET /rss/news — latest news posts\n"
    f"   - Standard RSS 2.0 XML with Croatian language tags\n"
    f"   - <link> points to the decision detail page\n\n"
    f"2. {PROJECT}/cms/src/collections/Subscriptions.ts — Payload collection:\n"
    f"   - Fields: email, subscription_type (decisions/news/experts), filters (court, category, keyword)\n"
    f"   - Frequency: daily/weekly digest\n\n"
    f"3. {PROJECT}/cms/src/jobs/notificationDigest.ts — Cron job:\n"
    f"   - Runs daily, checks for new content matching each subscription\n"
    f"   - Sends digest email via Resend\n\n"
    f"4. {PROJECT}/web/src/components/SubscribeForm.tsx — Subscribe widget:\n"
    f"   - Email input + category/court selector\n"
    f"   - Shown on decision search page and court detail pages",
    timeout=300)
log("[6/15] done")

# 7. Statistics Dashboard
log("[7/15] Statistics dashboard...")
dispatch("gigforge-dev-frontend",
    f"ADD STATISTICS DASHBOARD to Sudačka Mreža\n"
    f"Project dir: {PROJECT}\n\n"
    f"Read existing code first. Create a statistics page showing judicial data.\n\n"
    f"1. {PROJECT}/web/src/pages/StatisticsPage.tsx:\n"
    f"   - Decisions per court per year (bar chart)\n"
    f"   - Decisions by legal category (pie/donut chart)\n"
    f"   - Monthly trend of new decisions (line chart)\n"
    f"   - Most active courts ranking\n"
    f"   - Expert witnesses by speciality distribution\n"
    f"   - Total counts: decisions, experts, interpreters, courts\n\n"
    f"2. Use a lightweight chart library — recharts or chart.js\n"
    f"   - Add to web/package.json\n"
    f"   - Import only what's needed (tree-shakeable)\n\n"
    f"3. {PROJECT}/cms/src/endpoints/statistics.ts — API endpoint:\n"
    f"   - GET /api/statistics — aggregated stats from PostgreSQL\n"
    f"   - Uses SQL GROUP BY and COUNT queries\n"
    f"   - Cached for 1 hour (don't hit DB on every request)\n\n"
    f"4. Add 'Statistics' to the navigation in the sidebar and header\n"
    f"5. Match the design system: navy/gold palette, dark mode support",
    timeout=300)
log("[7/15] done")


# ============================================================================
# GROUP 3: User Features
# ============================================================================

log("\nGROUP 3: User Features")
log("=" * 60)

# 8. Expert Verification
log("[8/15] Expert verification system...")
dispatch("gigforge-dev-backend",
    f"ADD EXPERT VERIFICATION SYSTEM to Sudačka Mreža\n"
    f"Project dir: {PROJECT}\n\n"
    f"1. Add fields to ExpertWitnesses collection:\n"
    f"   - verified: boolean (default false)\n"
    f"   - verified_at: date\n"
    f"   - verified_by: relationship to users\n"
    f"   - last_confirmed: date\n"
    f"   - flag_reports: array of {{ reporter: user, reason: text, date: date }}\n\n"
    f"2. {PROJECT}/cms/src/endpoints/expertFlag.ts:\n"
    f"   - POST /api/experts/:id/flag — logged-in users can flag inaccurate profiles\n"
    f"   - POST /api/experts/:id/verify — admin/editor can verify\n\n"
    f"3. {PROJECT}/web/src/components/experts/VerificationBadge.tsx:\n"
    f"   - Green checkmark for verified experts\n"
    f"   - 'Last verified: date' tooltip\n"
    f"   - 'Report inaccuracy' button for logged-in users\n\n"
    f"4. Same for Interpreters collection",
    timeout=300)
log("[8/15] done")

# 9. Decision Annotations
log("[9/15] Decision annotations and bookmarks...")
dispatch("gigforge-dev-backend",
    f"ADD DECISION ANNOTATIONS AND BOOKMARKS to Sudačka Mreža\n"
    f"Project dir: {PROJECT}\n\n"
    f"Logged-in judges can highlight text, add notes, and bookmark decisions.\n\n"
    f"1. {PROJECT}/cms/src/collections/Annotations.ts:\n"
    f"   - Fields: user (relationship), decision (relationship), text_selection (start/end offsets),\n"
    f"     highlight_color, note, created_at\n"
    f"   - Access: only the annotation owner can read/write\n\n"
    f"2. {PROJECT}/cms/src/collections/Bookmarks.ts:\n"
    f"   - Fields: user, decision, folder (string, e.g. 'Criminal cases'), created_at\n"
    f"   - Access: owner only\n\n"
    f"3. {PROJECT}/web/src/components/decisions/AnnotationLayer.tsx:\n"
    f"   - Text selection → create highlight with optional note\n"
    f"   - Color picker (yellow, green, blue, pink)\n"
    f"   - Highlights rendered inline in decision text\n\n"
    f"4. {PROJECT}/web/src/components/decisions/BookmarkButton.tsx:\n"
    f"   - Toggle bookmark on decision detail page\n"
    f"   - Folder selector dropdown\n\n"
    f"5. {PROJECT}/web/src/pages/MyLibraryPage.tsx:\n"
    f"   - List bookmarked decisions grouped by folder\n"
    f"   - List annotated decisions with note previews\n"
    f"   - Requires login",
    timeout=300)
log("[9/15] done")


# ============================================================================
# GROUP 4: Integration
# ============================================================================

log("\nGROUP 4: Integration")
log("=" * 60)

# 10. Partner API
log("[10/15] Public REST API for partners...")
dispatch("gigforge-dev-backend",
    f"ADD PUBLIC REST API for Sudačka Mreža\n"
    f"Project dir: {PROJECT}\n\n"
    f"Partner institutions (Serbia, Macedonia) need API access to Croatian case law.\n\n"
    f"1. {PROJECT}/cms/src/endpoints/publicApi.ts:\n"
    f"   - GET /api/v1/decisions — paginated, filterable by court/date/category\n"
    f"   - GET /api/v1/decisions/:id — single decision with full text\n"
    f"   - GET /api/v1/experts — paginated expert directory\n"
    f"   - GET /api/v1/interpreters — paginated interpreter directory\n"
    f"   - GET /api/v1/courts — courts directory\n"
    f"   - GET /api/v1/statistics — aggregate stats\n"
    f"   - Rate limited: 100 req/min per IP\n"
    f"   - API key optional (higher rate limits)\n"
    f"   - JSON responses with pagination metadata\n"
    f"   - CORS: allow all origins (public API)\n\n"
    f"2. {PROJECT}/cms/src/collections/ApiKeys.ts:\n"
    f"   - Fields: key (auto-generated), name, organization, email, rate_limit, active\n\n"
    f"3. {PROJECT}/web/src/pages/ApiDocsPage.tsx:\n"
    f"   - Interactive API documentation page\n"
    f"   - Shows all endpoints with example requests/responses\n"
    f"   - API key request form",
    timeout=300)
log("[10/15] done")

# 11. SEO Structured Data
log("[11/15] SEO structured data...")
dispatch("gigforge-dev-frontend",
    f"ADD SEO STRUCTURED DATA to Sudačka Mreža\n"
    f"Project dir: {PROJECT}\n\n"
    f"Read existing pages and add proper SEO markup.\n\n"
    f"1. {PROJECT}/web/src/components/seo/StructuredData.tsx:\n"
    f"   - Renders JSON-LD <script> tags in <head>\n"
    f"   - schema.org/Organization for the homepage\n"
    f"   - schema.org/LegalService for court pages\n"
    f"   - schema.org/Article for news posts\n"
    f"   - schema.org/Person for expert witnesses\n\n"
    f"2. {PROJECT}/web/src/components/seo/MetaTags.tsx:\n"
    f"   - Dynamic <title>, <meta description>, Open Graph tags per page\n"
    f"   - Croatian as default, English variant\n"
    f"   - og:image using court coat of arms or logo\n\n"
    f"3. {PROJECT}/cms/src/endpoints/sitemap.ts:\n"
    f"   - GET /sitemap.xml — auto-generated from all CMS content\n"
    f"   - Includes all decisions, experts, interpreters, courts, news\n"
    f"   - lastmod from updated_at\n\n"
    f"4. {PROJECT}/cms/src/endpoints/robots.ts:\n"
    f"   - GET /robots.txt — allow all, point to sitemap\n\n"
    f"5. Update web/index.html with base meta tags",
    timeout=300)
log("[11/15] done")


# ============================================================================
# GROUP 5: Advanced
# ============================================================================

log("\nGROUP 5: Advanced")
log("=" * 60)

# 12. PWA / Offline
log("[12/15] PWA with offline support...")
dispatch("gigforge-dev-frontend",
    f"ADD PWA / OFFLINE SUPPORT to Sudačka Mreža\n"
    f"Project dir: {PROJECT}\n\n"
    f"1. {PROJECT}/web/public/manifest.json:\n"
    f"   - name: Sudačka Mreža\n"
    f"   - short_name: SM\n"
    f"   - theme_color: #0D2B55\n"
    f"   - background_color: #F8F9FA\n"
    f"   - display: standalone\n"
    f"   - icons: use existing favicons\n\n"
    f"2. {PROJECT}/web/src/sw.ts — Service worker:\n"
    f"   - Cache-first for static assets (JS, CSS, fonts, images)\n"
    f"   - Network-first for API calls (fall back to cache if offline)\n"
    f"   - Cache recent court decisions for offline reading\n"
    f"   - Background sync for bookmarks/annotations made offline\n\n"
    f"3. Register service worker in main.tsx\n"
    f"4. Add <link rel='manifest'> to index.html\n"
    f"5. Add install prompt banner component\n"
    f"6. Use vite-plugin-pwa if available, otherwise manual SW",
    timeout=300)
log("[12/15] done")

# 13. OCR Pipeline
log("[13/15] OCR pipeline for scanned documents...")
dispatch("gigforge-engineer",
    f"ADD OCR PIPELINE to Sudačka Mreža\n"
    f"Project dir: {PROJECT}\n\n"
    f"Older court decisions may be scanned PDFs. Need OCR for searchability.\n\n"
    f"1. {PROJECT}/cms/src/ocr/ocrProcessor.ts:\n"
    f"   - Uses tesseract.js (runs in Node.js, no native deps)\n"
    f"   - Process uploaded PDF → extract images → OCR each page\n"
    f"   - Language: 'hrv' (Croatian) + 'eng' (English fallback)\n"
    f"   - Returns extracted text\n\n"
    f"2. {PROJECT}/cms/src/hooks/ocrOnUpload.ts — Payload hook:\n"
    f"   - Runs on CourtDecisions afterChange when a PDF attachment is uploaded\n"
    f"   - If the decision has no full_text but has a PDF, run OCR\n"
    f"   - Save extracted text to the full_text field\n"
    f"   - Mark as ocr_processed: true\n\n"
    f"3. Add tesseract.js to cms/package.json\n"
    f"4. Add 'ocr_processed' boolean field to CourtDecisions collection",
    timeout=300)
log("[13/15] done")

# 14. AI Case Law Summary
log("[14/15] AI case law summaries...")
dispatch("gigforge-engineer",
    f"ADD AI CASE LAW SUMMARIES to Sudačka Mreža\n"
    f"Project dir: {PROJECT}\n\n"
    f"Generate plain-language summaries of court decisions in Croatian and English.\n\n"
    f"1. {PROJECT}/cms/src/ai/summarizer.ts:\n"
    f"   - Takes decision full_text\n"
    f"   - Calls OpenAI API (gpt-4o-mini) with prompt:\n"
    f"     'Summarize this Croatian court decision in 3-4 sentences.\n"
    f"      Write in plain language a non-lawyer can understand.\n"
    f"      Include: what the case was about, the court's decision, and why.'\n"
    f"   - Returns summary in Croatian + English\n"
    f"   - Handles missing API key gracefully (skip, don't crash)\n\n"
    f"2. {PROJECT}/cms/src/hooks/autoSummarize.ts — Payload hook:\n"
    f"   - afterChange on CourtDecisions\n"
    f"   - If full_text exists but summary_hr is empty, generate summary\n"
    f"   - Async — don't block the save\n\n"
    f"3. Add fields to CourtDecisions: summary_hr, summary_en, summary_generated_at\n\n"
    f"4. {PROJECT}/web/src/components/decisions/AiSummary.tsx:\n"
    f"   - Collapsible summary box at top of decision detail page\n"
    f"   - Label: 'AI-generated summary — may contain errors'\n"
    f"   - Toggle between Croatian and English",
    timeout=300)
log("[14/15] done")

# 15. eSPIS Integration Stub
log("[15/15] eSPIS integration stub...")
dispatch("gigforge-dev-backend",
    f"ADD eSPIS INTEGRATION STUB to Sudačka Mreža\n"
    f"Project dir: {PROJECT}\n\n"
    f"eSPIS is Croatia's official court information system. We don't have API access yet\n"
    f"but we should build the integration layer so it's ready when we do.\n\n"
    f"1. {PROJECT}/cms/src/integrations/espis.ts:\n"
    f"   - Interface: EspisClient with methods:\n"
    f"     - fetchNewDecisions(court: string, since: Date): Decision[]\n"
    f"     - fetchCaseStatus(caseNumber: string): CaseStatus\n"
    f"     - searchCases(query: string): CaseResult[]\n"
    f"   - Mock implementation that returns sample data\n"
    f"   - Real implementation placeholder (commented out, for when API access is granted)\n"
    f"   - Config: ESPIS_API_URL, ESPIS_API_KEY env vars\n\n"
    f"2. {PROJECT}/cms/src/jobs/espisSync.ts:\n"
    f"   - Cron job stub that would poll eSPIS for new decisions\n"
    f"   - Import into Payload CMS collections\n"
    f"   - Currently runs in mock mode (logs what it would do)\n\n"
    f"3. Add ESPIS_API_URL and ESPIS_API_KEY to .env.example\n"
    f"4. Document in README.md under 'eSPIS Integration'",
    timeout=300)
log("[15/15] done")


# ============================================================================
# Rebuild and deploy
# ============================================================================

log("\nRebuilding and deploying...")
log("=" * 60)

# Rebuild
dispatch("gigforge-devops",
    f"REBUILD AND REDEPLOY Sudačka Mreža with all new enhancements\n"
    f"Project dir: {PROJECT}\n\n"
    f"15 new features have been added. Rebuild everything.\n"
    f"1. cd {PROJECT}\n"
    f"2. Install new deps: cd cms && npm install && cd ../web && npm install\n"
    f"3. Build: cd {PROJECT}/web && npm run build\n"
    f"4. Deploy: cd {PROJECT} && docker compose up -d --build --force-recreate\n"
    f"5. Wait for all services healthy\n"
    f"6. Verify: curl http://localhost:4092/\n"
    f"7. If build fails, read error, fix, retry\n"
    f"8. Report status",
    timeout=600)

log("DEPLOY done")
log("=" * 60)
log("ALL 15 ENHANCEMENTS BUILT AND DEPLOYED")
