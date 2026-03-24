-- Content workflow tables DDL
CREATE TABLE IF NOT EXISTS blog_posts (
            id SERIAL PRIMARY KEY,
            org TEXT NOT NULL,
            title TEXT NOT NULL,
            slug TEXT NOT NULL,
            content TEXT,
            author_agent TEXT,
            editor_agent TEXT,
            status TEXT DEFAULT 'draft',
            published_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )

CREATE TABLE IF NOT EXISTS newsletters (
            id SERIAL PRIMARY KEY,
            org TEXT NOT NULL,
            week_of TEXT NOT NULL,
            subject TEXT,
            content TEXT,
            posts_included TEXT,
            status TEXT DEFAULT 'draft',
            sent_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )

CREATE TABLE IF NOT EXISTS news_aggregations (
            id SERIAL PRIMARY KEY,
            week_of TEXT NOT NULL,
            title TEXT,
            content TEXT,
            sources TEXT,
            status TEXT DEFAULT 'draft',
            published_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
