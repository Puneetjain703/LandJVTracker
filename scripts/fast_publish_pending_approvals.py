from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from backend.app.db import SessionLocal, create_all


SQL = """
WITH base AS (
    SELECT COALESCE(max((regexp_match(asset_code, 'LJV-([0-9]+)'))[1]::int), 0) AS n
    FROM assets
    WHERE asset_code ~ '^LJV-[0-9]+$'
),
pending AS (
    SELECT
        q.id AS approval_id,
        q.source,
        q.source_uid,
        q.created_by_source,
        q.title AS queue_title,
        COALESCE(q.edited_payload, q.payload)::jsonb AS payload,
        row_number() OVER (ORDER BY q.created_at, q.id) AS rn
    FROM approval_queue q
    WHERE q.status = 'pending'
),
classified AS (
    SELECT
        p.*,
        lower(concat_ws(
            ' ',
            p.created_by_source,
            p.source,
            p.queue_title,
            p.payload->>'title',
            p.payload->>'asset_type',
            p.payload->>'status',
            p.payload->>'locality',
            p.payload->>'area_name',
            p.payload->>'address',
            p.payload->>'bottleneck_notes',
            p.payload::text
        )) AS haystack
    FROM pending p
),
prepared AS (
    SELECT
        c.*,
        CASE
            WHEN haystack LIKE '%brokerage new deals%' OR haystack LIKE '%brokerage%' OR haystack LIKE '%mandate%' OR haystack LIKE '%commission%' OR haystack LIKE '%listing%' OR haystack LIKE '%resale%' THEN 'brokerage'
            WHEN haystack LIKE '%joint venture%' OR haystack LIKE '% jv %' OR haystack LIKE '%revenue share%' OR haystack LIKE '%development agreement%' OR haystack LIKE '%landowner%' THEN 'joint_venture'
            ELSE 'land_purchase'
        END AS classification,
        COALESCE(NULLIF(c.payload->>'dedupe_fingerprint', ''), 'approval-' || c.approval_id::text) AS fingerprint
    FROM classified c
),
to_insert AS (
    SELECT prepared.*
    FROM prepared
    WHERE NOT EXISTS (
        SELECT 1
        FROM assets a
        WHERE (a.source = prepared.source AND a.raw_source #>> '{_ingestion,source_uid}' = prepared.source_uid)
           OR (a.raw_source->>'dedupe_fingerprint' = prepared.fingerprint)
    )
),
inserted AS (
    INSERT INTO assets (
        asset_code,
        title,
        asset_type,
        status,
        source,
        locality,
        area_name,
        tehsil,
        district,
        state,
        address,
        latitude,
        longitude,
        google_maps_link,
        land_area,
        built_up_area,
        asking_price,
        expected_price,
        workability_rating,
        bottleneck_rating,
        bottleneck_notes,
        legal_status,
        zoning_status,
        approval_status,
        raw_source
    )
    SELECT
        'LJV-' || lpad((base.n + to_insert.rn)::text, 5, '0'),
        COALESCE(NULLIF(to_insert.payload->>'title', ''), to_insert.queue_title, 'Imported lead ' || to_insert.approval_id::text),
        CASE
            WHEN to_insert.classification = 'brokerage' THEN 'brokerage_listing'
            WHEN to_insert.classification = 'joint_venture' THEN 'jv'
            ELSE 'land'
        END,
        COALESCE(NULLIF(to_insert.payload->>'status', ''), 'lead'),
        COALESCE(NULLIF(to_insert.payload->>'source', ''), to_insert.source),
        NULLIF(to_insert.payload->>'locality', ''),
        NULLIF(to_insert.payload->>'area_name', ''),
        NULLIF(to_insert.payload->>'tehsil', ''),
        COALESCE(NULLIF(to_insert.payload->>'district', ''), NULLIF(to_insert.payload->>'city', '')),
        COALESCE(NULLIF(to_insert.payload->>'state', ''), 'Rajasthan'),
        NULLIF(to_insert.payload->>'address', ''),
        CASE WHEN (to_insert.payload->>'latitude') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (to_insert.payload->>'latitude')::double precision ELSE NULL END,
        CASE WHEN (to_insert.payload->>'longitude') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (to_insert.payload->>'longitude')::double precision ELSE NULL END,
        NULLIF(to_insert.payload->>'google_maps_link', ''),
        NULLIF(to_insert.payload->>'land_area', ''),
        NULLIF(to_insert.payload->>'built_up_area', ''),
        CASE WHEN NULLIF(regexp_replace(COALESCE(to_insert.payload->>'asking_price', ''), '[^0-9.]', '', 'g'), '') IS NOT NULL THEN NULLIF(regexp_replace(COALESCE(to_insert.payload->>'asking_price', ''), '[^0-9.]', '', 'g'), '')::numeric ELSE NULL END,
        CASE WHEN NULLIF(regexp_replace(COALESCE(to_insert.payload->>'expected_price', ''), '[^0-9.]', '', 'g'), '') IS NOT NULL THEN NULLIF(regexp_replace(COALESCE(to_insert.payload->>'expected_price', ''), '[^0-9.]', '', 'g'), '')::numeric ELSE NULL END,
        CASE WHEN (to_insert.payload->>'workability_rating') ~ '^[0-9]+$' THEN (to_insert.payload->>'workability_rating')::int ELSE NULL END,
        CASE WHEN (to_insert.payload->>'bottleneck_rating') ~ '^[0-9]+$' THEN (to_insert.payload->>'bottleneck_rating')::int ELSE NULL END,
        NULLIF(to_insert.payload->>'bottleneck_notes', ''),
        NULLIF(to_insert.payload->>'legal_status', ''),
        NULLIF(to_insert.payload->>'zoning_status', ''),
        'approved',
        (
            CASE
                WHEN jsonb_typeof(to_insert.payload->'raw_source') = 'object' THEN to_insert.payload->'raw_source'
                ELSE '{}'::jsonb
            END
            || jsonb_build_object(
                'dedupe_fingerprint', to_insert.fingerprint,
                'source_classification', to_insert.classification,
                'source_payload', to_insert.payload,
                '_ingestion', jsonb_build_object(
                    'source', to_insert.source,
                    'source_uid', to_insert.source_uid,
                    'source_name', to_insert.created_by_source,
                    'auto_published', true,
                    'fast_sql_backfill', true,
                    'skip_geocode', true
                )
            )
        )
    FROM to_insert, base
    RETURNING id, raw_source
),
marked AS (
    UPDATE approval_queue q
    SET
        status = 'approved',
        reviewed_by = 'auto_ingest',
        reviewed_at = now(),
        approval_decision = CASE
            WHEN EXISTS (
                SELECT 1
                FROM inserted i
                WHERE i.raw_source #>> '{_ingestion,source_uid}' = q.source_uid
                  AND i.raw_source #>> '{_ingestion,source}' = q.source
            )
            THEN 'approved'
            ELSE 'approved_duplicate_skipped'
        END,
        decision_notes = 'Auto-published by fast SQL backfill after approval gating was disabled.'
    WHERE q.status = 'pending'
    RETURNING q.id
)
SELECT
    (SELECT count(*) FROM pending) AS pending_seen,
    (SELECT count(*) FROM inserted) AS assets_inserted,
    (SELECT count(*) FROM marked) AS approvals_marked;
"""


def main() -> None:
    create_all()
    with SessionLocal() as db:
        result = db.execute(text(SQL)).mappings().one()
        db.commit()
        print(dict(result))


if __name__ == "__main__":
    main()
