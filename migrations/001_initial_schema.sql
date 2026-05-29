CREATE TABLE IF NOT EXISTS organizations (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    organization_type VARCHAR(80),
    phone VARCHAR(80),
    email VARCHAR(255),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS contacts (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    company VARCHAR(255),
    phone VARCHAR(80),
    whatsapp VARCHAR(80),
    email VARCHAR(255),
    notes TEXT,
    organization_id INTEGER REFERENCES organizations(id),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS brokers (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    company VARCHAR(255),
    phone VARCHAR(80),
    whatsapp VARCHAR(80),
    email VARCHAR(255),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS owners (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    company VARCHAR(255),
    phone VARCHAR(80),
    whatsapp VARCHAR(80),
    email VARCHAR(255),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS assets (
    id SERIAL PRIMARY KEY,
    asset_code VARCHAR(80) UNIQUE,
    title VARCHAR(500) NOT NULL,
    asset_type VARCHAR(80) NOT NULL,
    status VARCHAR(80) DEFAULT 'lead',
    source VARCHAR(120),
    locality VARCHAR(255),
    area_name VARCHAR(255),
    tehsil VARCHAR(255),
    district VARCHAR(255),
    state VARCHAR(255) DEFAULT 'Rajasthan',
    address TEXT,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    google_maps_link TEXT,
    land_area VARCHAR(120),
    built_up_area VARCHAR(120),
    asking_price NUMERIC(16,2),
    expected_price NUMERIC(16,2),
    owner_id INTEGER REFERENCES owners(id),
    broker_id INTEGER REFERENCES brokers(id),
    workability_rating INTEGER,
    bottleneck_rating INTEGER,
    bottleneck_notes TEXT,
    legal_status TEXT,
    zoning_status TEXT,
    approval_status VARCHAR(80) DEFAULT 'approved',
    raw_source JSONB,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_assets_asset_type ON assets(asset_type);
CREATE INDEX IF NOT EXISTS ix_assets_status ON assets(status);
CREATE INDEX IF NOT EXISTS ix_assets_source ON assets(source);
CREATE INDEX IF NOT EXISTS ix_assets_district ON assets(district);
CREATE INDEX IF NOT EXISTS ix_assets_tehsil ON assets(tehsil);
CREATE INDEX IF NOT EXISTS ix_assets_locality ON assets(locality);
CREATE INDEX IF NOT EXISTS ix_assets_owner_id ON assets(owner_id);
CREATE INDEX IF NOT EXISTS ix_assets_broker_id ON assets(broker_id);
CREATE INDEX IF NOT EXISTS ix_assets_approval_status ON assets(approval_status);

CREATE TABLE IF NOT EXISTS deals (
    id SERIAL PRIMARY KEY,
    asset_id INTEGER NOT NULL REFERENCES assets(id),
    deal_type VARCHAR(80),
    status VARCHAR(80) DEFAULT 'active',
    value NUMERIC(16,2),
    probability INTEGER,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS asset_contacts (
    id SERIAL PRIMARY KEY,
    asset_id INTEGER NOT NULL REFERENCES assets(id),
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    relationship_type VARCHAR(80) DEFAULT 'related',
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_asset_contact_role UNIQUE(asset_id, contact_id, relationship_type)
);

CREATE TABLE IF NOT EXISTS asset_documents (
    id SERIAL PRIMARY KEY,
    asset_id INTEGER NOT NULL REFERENCES assets(id),
    document_name VARCHAR(255) NOT NULL,
    document_type VARCHAR(120),
    url TEXT,
    storage_path TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS asset_locations (
    id SERIAL PRIMARY KEY,
    asset_id INTEGER NOT NULL REFERENCES assets(id),
    label VARCHAR(120),
    address TEXT,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    google_maps_link TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS asset_updates (
    id SERIAL PRIMARY KEY,
    asset_id INTEGER NOT NULL REFERENCES assets(id),
    update_type VARCHAR(120),
    update_text TEXT NOT NULL,
    created_by VARCHAR(120),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS asset_tags (
    id SERIAL PRIMARY KEY,
    asset_id INTEGER NOT NULL REFERENCES assets(id),
    tag VARCHAR(120) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_asset_tag UNIQUE(asset_id, tag)
);

CREATE TABLE IF NOT EXISTS approval_queue (
    id SERIAL PRIMARY KEY,
    source VARCHAR(120) NOT NULL,
    source_uid VARCHAR(255),
    title VARCHAR(500),
    payload JSONB NOT NULL,
    edited_payload JSONB,
    status VARCHAR(80) DEFAULT 'pending',
    created_by_source VARCHAR(120),
    reviewed_by VARCHAR(120),
    reviewed_at TIMESTAMPTZ,
    approval_decision VARCHAR(80),
    decision_notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_approval_source_uid UNIQUE(source, source_uid)
);

CREATE TABLE IF NOT EXISTS notion_sync_logs (
    id SERIAL PRIMARY KEY,
    source_name VARCHAR(255) NOT NULL,
    notion_database_id VARCHAR(255),
    status VARCHAR(80) NOT NULL,
    fetched_count INTEGER DEFAULT 0,
    queued_count INTEGER DEFAULT 0,
    skipped_count INTEGER DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ingestion_logs (
    id SERIAL PRIMARY KEY,
    source VARCHAR(120) NOT NULL,
    filename VARCHAR(500),
    status VARCHAR(80) NOT NULL,
    total_rows INTEGER DEFAULT 0,
    created_count INTEGER DEFAULT 0,
    review_count INTEGER DEFAULT 0,
    skipped_count INTEGER DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS crm_profiles (
    id SERIAL PRIMARY KEY,
    profile_type VARCHAR(80) DEFAULT 'buyer',
    name VARCHAR(255) NOT NULL,
    phone VARCHAR(80),
    email VARCHAR(255),
    preferred_asset_types JSONB,
    preferred_locations JSONB,
    budget_min NUMERIC(16,2),
    budget_max NUMERIC(16,2),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS asset_match_suggestions (
    id SERIAL PRIMARY KEY,
    asset_id INTEGER NOT NULL REFERENCES assets(id),
    crm_profile_id INTEGER NOT NULL REFERENCES crm_profiles(id),
    score DOUBLE PRECISION,
    rationale TEXT,
    status VARCHAR(80) DEFAULT 'suggested',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ai_query_logs (
    id SERIAL PRIMARY KEY,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    source_rows JSONB,
    asked_by VARCHAR(120),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

