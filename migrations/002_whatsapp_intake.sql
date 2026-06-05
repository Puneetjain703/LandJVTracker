CREATE TABLE IF NOT EXISTS whatsapp_messages (
    id SERIAL PRIMARY KEY,
    wamid VARCHAR(255) NOT NULL,
    from_number VARCHAR(80),
    phone_number_id VARCHAR(120),
    message_type VARCHAR(80),
    body_text TEXT,
    transcription_text TEXT,
    media_id VARCHAR(255),
    media_mime_type VARCHAR(255),
    media_sha256 VARCHAR(255),
    media_filename VARCHAR(500),
    media_caption TEXT,
    raw_payload JSONB,
    intent VARCHAR(80),
    processing_status VARCHAR(80) DEFAULT 'received',
    approval_queue_id INTEGER REFERENCES approval_queue(id),
    asset_id INTEGER REFERENCES assets(id),
    response_text TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_whatsapp_wamid UNIQUE(wamid)
);

CREATE INDEX IF NOT EXISTS ix_whatsapp_messages_wamid ON whatsapp_messages(wamid);
CREATE INDEX IF NOT EXISTS ix_whatsapp_messages_from_number ON whatsapp_messages(from_number);
CREATE INDEX IF NOT EXISTS ix_whatsapp_messages_phone_number_id ON whatsapp_messages(phone_number_id);
CREATE INDEX IF NOT EXISTS ix_whatsapp_messages_processing_status ON whatsapp_messages(processing_status);
CREATE INDEX IF NOT EXISTS ix_whatsapp_messages_asset_id ON whatsapp_messages(asset_id);
CREATE INDEX IF NOT EXISTS ix_whatsapp_messages_approval_queue_id ON whatsapp_messages(approval_queue_id);
