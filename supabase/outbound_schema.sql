CREATE TABLE IF NOT EXISTS outbound_runs (
    id            BIGSERIAL PRIMARY KEY,
    target_company TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'queued',
    competitors   JSONB DEFAULT '[]',
    config        JSONB DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS outbound_contacts (
    id            BIGSERIAL PRIMARY KEY,
    run_id        BIGINT REFERENCES outbound_runs(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    first_name    TEXT,
    last_name     TEXT,
    company       TEXT,
    domain        TEXT,
    role          TEXT,
    seniority     TEXT,
    email         TEXT,
    email_verified BOOLEAN DEFAULT FALSE,
    email_source  TEXT,
    linkedin_url  TEXT,
    persona_segment TEXT,
    opener_line   TEXT,
    source_competitor TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS outbound_campaigns (
    id            BIGSERIAL PRIMARY KEY,
    run_id        BIGINT REFERENCES outbound_runs(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    persona       TEXT NOT NULL,
    template_subject TEXT,
    template_body TEXT,
    contact_count INTEGER DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'draft',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS outbound_stages (
    id         BIGSERIAL PRIMARY KEY,
    run_id     BIGINT REFERENCES outbound_runs(id) ON DELETE CASCADE,
    stage      TEXT NOT NULL,
    status     TEXT NOT NULL,
    detail     TEXT,
    payload    JSONB DEFAULT '{}',
    elapsed_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
