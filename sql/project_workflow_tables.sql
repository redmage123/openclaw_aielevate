-- Project workflow tables DDL
CREATE TABLE IF NOT EXISTS revision_cycles (
    id SERIAL PRIMARY KEY,
    customer_email TEXT NOT NULL,
    project_slug TEXT NOT NULL,
    revision_number INTEGER DEFAULT 1,
    change_requests TEXT,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS scope_changes (
    id SERIAL PRIMARY KEY,
    customer_email TEXT NOT NULL,
    project_slug TEXT NOT NULL,
    description TEXT NOT NULL,
    impact_assessment TEXT,
    effort_estimate TEXT,
    price_delta_eur REAL DEFAULT 0,
    status TEXT DEFAULT 'pending',
    approved_by TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS client_onboarding (
    id SERIAL PRIMARY KEY,
    customer_email TEXT NOT NULL,
    project_slug TEXT NOT NULL,
    advocate_intro_sent BOOLEAN DEFAULT FALSE,
    assets_collected TEXT DEFAULT '{}',
    workspace_created BOOLEAN DEFAULT FALSE,
    kickoff_confirmed BOOLEAN DEFAULT FALSE,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS proposals (
    id SERIAL PRIMARY KEY,
    customer_email TEXT,
    project_title TEXT,
    platform TEXT,
    job_url TEXT,
    proposal_text TEXT,
    price_eur REAL,
    status TEXT DEFAULT 'draft',
    approved_by TEXT,
    sent_at TIMESTAMPTZ,
    response TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS payment_milestones (
    id SERIAL PRIMARY KEY,
    customer_email TEXT NOT NULL,
    project_slug TEXT NOT NULL,
    milestone_name TEXT NOT NULL,
    amount_eur REAL NOT NULL,
    invoice_id INTEGER,
    status TEXT DEFAULT 'pending',
    due_date TIMESTAMPTZ,
    paid_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS support_tickets (
    id SERIAL PRIMARY KEY,
    customer_email TEXT NOT NULL,
    project_slug TEXT NOT NULL,
    description TEXT NOT NULL,
    priority TEXT DEFAULT 'normal',
    triage_result TEXT,
    fix_applied TEXT,
    status TEXT DEFAULT 'open',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    within_warranty BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS project_closures (
    id SERIAL PRIMARY KEY,
    customer_email TEXT NOT NULL,
    project_slug TEXT NOT NULL,
    signoff_received BOOLEAN DEFAULT FALSE,
    handover_complete BOOLEAN DEFAULT FALSE,
    production_deployed BOOLEAN DEFAULT FALSE,
    archived BOOLEAN DEFAULT FALSE,
    case_study_generated BOOLEAN DEFAULT FALSE,
    testimonial_requested BOOLEAN DEFAULT FALSE,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);
