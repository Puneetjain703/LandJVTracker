from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from backend.app.db import SessionLocal, create_all


SQL = """
WITH extracted AS (
    SELECT
        a.id AS asset_id,
        'landowner'::text AS role,
        NULLIF(trim(COALESCE(
            a.raw_source #>> '{source_payload,owner_name}',
            a.raw_source->>'OWNER',
            a.raw_source->>'OWNER ',
            a.raw_source->>'Owner',
            a.raw_source->>'Seller'
        )), '') AS name
    FROM assets a
    UNION ALL
    SELECT
        a.id AS asset_id,
        'broker'::text AS role,
        NULLIF(trim(COALESCE(
            a.raw_source #>> '{source_payload,broker_name}',
            a.raw_source->>'BROKER',
            a.raw_source->>'Broker',
            a.raw_source->>'Reference',
            a.raw_source->>'Referrer'
        )), '') AS name
    FROM assets a
    UNION ALL
    SELECT
        a.id AS asset_id,
        'possible_partner'::text AS role,
        NULLIF(trim(a.raw_source #>> '{source_payload,key_people}'), '') AS name
    FROM assets a
    UNION ALL
    SELECT
        a.id AS asset_id,
        'bank'::text AS role,
        NULLIF(trim(COALESCE(
            a.raw_source #>> '{source_payload,bank_name}',
            a.raw_source->>'Bank',
            a.raw_source->>'Banker',
            a.raw_source->>'Bank Name',
            a.raw_source->>'Funding Bank'
        )), '') AS name
    FROM assets a
    UNION ALL
    SELECT
        a.id AS asset_id,
        'financier'::text AS role,
        NULLIF(trim(COALESCE(
            a.raw_source #>> '{source_payload,financier_name}',
            a.raw_source->>'Financier',
            a.raw_source->>'Finance',
            a.raw_source->>'Funding',
            a.raw_source->>'Investor'
        )), '') AS name
    FROM assets a
),
cleaned AS (
    SELECT DISTINCT
        asset_id,
        role,
        left(regexp_replace(name, '\\s+', ' ', 'g'), 255) AS name
    FROM extracted
    WHERE name IS NOT NULL
      AND lower(name) NOT IN ('none', 'null', 'na', 'n/a', '-', '0')
),
new_contacts AS (
    INSERT INTO contacts (name, notes)
    SELECT DISTINCT c.name, 'Backfilled from imported asset source fields'
    FROM cleaned c
    WHERE NOT EXISTS (
        SELECT 1
        FROM contacts existing
        WHERE lower(existing.name) = lower(c.name)
          AND coalesce(existing.company, '') = ''
    )
    RETURNING id, name
),
matched_contacts AS (
    SELECT DISTINCT ON (lower(name))
        id,
        name
    FROM (
        SELECT c.id, c.name
        FROM contacts c
        WHERE EXISTS (SELECT 1 FROM cleaned x WHERE lower(x.name) = lower(c.name))
        UNION ALL
        SELECT id, name
        FROM new_contacts
    ) all_contacts
    ORDER BY lower(name), id
),
new_links AS (
    INSERT INTO asset_contacts (asset_id, contact_id, relationship_type, notes)
    SELECT
        c.asset_id,
        mc.id,
        c.role,
        'Backfilled from imported source data'
    FROM cleaned c
    JOIN matched_contacts mc ON lower(mc.name) = lower(c.name)
    WHERE NOT EXISTS (
        SELECT 1
        FROM asset_contacts ac
        WHERE ac.asset_id = c.asset_id
          AND ac.contact_id = mc.id
          AND ac.relationship_type = c.role
    )
    RETURNING id
)
SELECT
    (SELECT count(*) FROM cleaned) AS extracted_people,
    (SELECT count(*) FROM new_contacts) AS contacts_created,
    (SELECT count(*) FROM new_links) AS links_created;
"""


def main() -> None:
    create_all()
    with SessionLocal() as db:
        result = db.execute(text(SQL)).mappings().one()
        db.commit()
        print(dict(result))


if __name__ == "__main__":
    main()
