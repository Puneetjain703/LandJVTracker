from __future__ import annotations

import json
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Use deterministic extraction for catch-up syncs; OpenAI enrichment can be run later per record.
os.environ["OPENAI_API_KEY"] = ""

from sqlalchemy import text

from backend.app.config import get_settings
from backend.app.db import SessionLocal, create_all
from backend.app.services.notion_sync import sync_notion_project_page_to_queue
from scripts.fast_backfill_asset_people import SQL as PEOPLE_BACKFILL_SQL


class TimeoutError(Exception):
    pass


def _timeout_handler(_signum: int, _frame: Any) -> None:
    raise TimeoutError("source timed out")


def sync_with_timeout(db, *, timeout_seconds: int, **kwargs: Any) -> dict[str, Any]:
    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(timeout_seconds)
    try:
        return sync_notion_project_page_to_queue(db, **kwargs)
    except TimeoutError as exc:
        return {
            "source_name": kwargs["source_name"],
            "fetched_count": 0,
            "queued_count": 0,
            "skipped_count": 0,
            "status": "failed",
            "log_id": None,
            "message": str(exc),
        }
    finally:
        signal.alarm(0)


def finalize_assets(db) -> dict[str, int]:
    score_result = db.execute(
        text(
            """
            WITH scored AS (
                SELECT
                    id,
                    CASE
                        WHEN txt = '' THEN 3.0
                        WHEN txt LIKE '%c-scheme%' OR txt LIKE '%c scheme%' THEN 9.5
                        WHEN txt LIKE '%civil lines%' THEN 9.2
                        WHEN txt LIKE '%jln marg%' THEN 9.0
                        WHEN txt LIKE '%bani park%' THEN 8.8
                        WHEN txt LIKE '%vaishali nagar%' THEN 8.7
                        WHEN txt LIKE '%malviya nagar%' THEN 8.6
                        WHEN txt LIKE '%bapu nagar%' THEN 8.5
                        WHEN txt LIKE '%raja park%' THEN 8.3
                        WHEN txt LIKE '%tonk road%' THEN 8.2
                        WHEN txt LIKE '%mansarovar%' THEN 8.0
                        ELSE LEAST(
                            8.0,
                            3.0
                            + CASE WHEN district IS NOT NULL AND trim(district) <> '' THEN 1.5 ELSE 0 END
                            + CASE WHEN locality IS NOT NULL AND trim(locality) <> '' THEN 1.5 ELSE 0 END
                            + CASE WHEN address IS NOT NULL AND length(address) > 15 THEN 1.0 ELSE 0 END
                            + CASE WHEN latitude IS NOT NULL AND longitude IS NOT NULL THEN 1.0 ELSE 0 END
                        )
                    END::numeric AS score,
                    CASE
                        WHEN txt = '' THEN 'Minimal location data.'
                        WHEN txt LIKE '%c-scheme%' OR txt LIKE '%c scheme%' THEN 'Premium zone match: C-Scheme.'
                        WHEN txt LIKE '%civil lines%' THEN 'Premium zone match: Civil Lines.'
                        WHEN txt LIKE '%jln marg%' THEN 'Premium zone match: JLN Marg.'
                        WHEN txt LIKE '%bani park%' THEN 'Premium zone match: Bani Park.'
                        WHEN txt LIKE '%vaishali nagar%' THEN 'Premium zone match: Vaishali Nagar.'
                        WHEN txt LIKE '%malviya nagar%' THEN 'Premium zone match: Malviya Nagar.'
                        WHEN txt LIKE '%bapu nagar%' THEN 'Premium zone match: Bapu Nagar.'
                        WHEN txt LIKE '%raja park%' THEN 'Premium zone match: Raja Park.'
                        WHEN txt LIKE '%tonk road%' THEN 'Premium zone match: Tonk Road.'
                        WHEN txt LIKE '%mansarovar%' THEN 'Premium zone match: Mansarovar.'
                        ELSE 'Rule-based score from location detail and coordinates.'
                    END AS reason
                FROM (
                    SELECT
                        id,
                        address,
                        locality,
                        district,
                        latitude,
                        longitude,
                        lower(trim(concat_ws(' ', address, locality, district))) AS txt
                    FROM assets
                ) base
            )
            UPDATE assets a
            SET
                google_maps_link = CASE
                    WHEN a.latitude IS NOT NULL AND a.longitude IS NOT NULL
                        THEN 'https://www.google.com/maps?q=' || a.latitude::text || ',' || a.longitude::text
                    ELSE a.google_maps_link
                END,
                raw_source = jsonb_set(
                    jsonb_set(
                        jsonb_set(
                            coalesce(a.raw_source, '{}'::jsonb),
                            '{_ingestion}',
                            coalesce(a.raw_source->'_ingestion', '{}'::jsonb) || '{"skip_geocode": false}'::jsonb,
                            true
                        ),
                        '{location_score}',
                        to_jsonb(scored.score),
                        true
                    ),
                    '{location_score_reason}',
                    to_jsonb(scored.reason),
                    true
                ),
                updated_at = now()
            FROM scored
            WHERE a.id = scored.id
            """
        )
    )
    updated_locations = db.execute(
        text(
            """
            UPDATE asset_locations l
            SET address = a.address,
                latitude = a.latitude,
                longitude = a.longitude,
                google_maps_link = a.google_maps_link,
                updated_at = now()
            FROM assets a
            WHERE l.asset_id = a.id AND l.label = 'Primary'
            """
        )
    )
    inserted_locations = db.execute(
        text(
            """
            INSERT INTO asset_locations (asset_id, label, address, latitude, longitude, google_maps_link)
            SELECT a.id, 'Primary', a.address, a.latitude, a.longitude, a.google_maps_link
            FROM assets a
            WHERE NOT EXISTS (
                SELECT 1 FROM asset_locations l WHERE l.asset_id = a.id AND l.label = 'Primary'
            )
            """
        )
    )
    db.commit()
    return {
        "scored": int(score_result.rowcount or 0),
        "locations_updated": int(updated_locations.rowcount or 0),
        "locations_inserted": int(inserted_locations.rowcount or 0),
    }


def close_stale_logs(db) -> int:
    result = db.execute(
        text(
            """
            update notion_sync_logs
            set status='failed',
                error_message='Interrupted by controlled completion run; source is being retried.',
                updated_at=now()
            where status='running'
            """
        )
    )
    db.commit()
    return int(result.rowcount or 0)


def main() -> None:
    create_all()
    settings = get_settings()
    results: dict[str, Any] = {}
    with SessionLocal() as db:
        stale_closed = close_stale_logs(db)
        sources = [
            {
                "key": "pearl_spytech",
                "page_id_or_url": settings.notion_pearl_projects_page_id,
                "source_name": settings.notion_source_name,
                "source": "notion_pearl_spytech_projects",
                "default_asset_type": "land",
                "timeout_seconds": 180,
            },
            {
                "key": "analyze_lrm",
                "page_id_or_url": settings.notion_analyze_lrm_page_id,
                "source_name": settings.notion_analyze_lrm_source_name,
                "source": "notion_analyze_lrm",
                "default_asset_type": "land",
                "timeout_seconds": 180,
            },
            {
                "key": "brokerage_new_deals",
                "page_id_or_url": settings.notion_brokerage_new_deals_page_id,
                "source_name": settings.notion_brokerage_source_name,
                "source": "notion_brokerage_new_deals",
                "default_asset_type": "brokerage_listing",
                "timeout_seconds": 180,
            },
        ]
        for source_config in sources:
            timeout_seconds = source_config.pop("timeout_seconds")
            key = source_config.pop("key")
            results[key] = sync_with_timeout(db, timeout_seconds=timeout_seconds, **source_config)
        finalize = finalize_assets(db)
        db.execute(text(PEOPLE_BACKFILL_SQL))
        db.commit()
        counts = {
            "assets": db.execute(text("select count(*) from assets")).scalar(),
            "coordinates": db.execute(text("select count(*) from assets where latitude is not null and longitude is not null")).scalar(),
            "maps": db.execute(text("select count(*) from assets where google_maps_link is not null")).scalar(),
            "locations": db.execute(text("select count(*) from asset_locations")).scalar(),
            "location_scores": db.execute(text("select count(*) from assets where raw_source ? 'location_score'")).scalar(),
            "people": db.execute(text("select count(*) from contacts")).scalar(),
            "people_links": db.execute(text("select count(*) from asset_contacts")).scalar(),
            "pending_approvals": db.execute(text("select count(*) from approval_queue where status='pending'")).scalar(),
        }
    print(json.dumps({"stale_logs_closed": stale_closed, "notion": results, "finalize": finalize, "counts": counts}, indent=2, default=str))


if __name__ == "__main__":
    main()
