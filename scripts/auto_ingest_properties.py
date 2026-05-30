from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select

from backend.app import models
from backend.app.db import SessionLocal, create_all
from backend.app.services.asset_ingestor import promote_approval_item_to_asset
from backend.app.services.ingestion import dedupe_fingerprint
from backend.app.services.source_sync import sync_all_sources


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto-ingest pending approval queue items as classified assets.")
    parser.add_argument("--sync-first", action="store_true", help="Fetch configured Google Sheets and Notion sources before promotion.")
    args = parser.parse_args()

    create_all()
    with SessionLocal() as db:
        sync_result = sync_all_sources(db) if args.sync_first else None
        items = list(
            db.scalars(
                select(models.ApprovalQueue)
                .where(models.ApprovalQueue.status == "pending")
                .order_by(models.ApprovalQueue.created_at.asc())
            )
        )
        existing_fingerprints: set[str] = set()
        for asset in db.scalars(select(models.Asset)):
            raw_source = asset.raw_source if isinstance(asset.raw_source, dict) else {}
            if raw_source.get("dedupe_fingerprint"):
                existing_fingerprints.add(raw_source["dedupe_fingerprint"])
            existing_fingerprints.add(
                dedupe_fingerprint(
                    {
                        "title": asset.title,
                        "locality": asset.locality,
                        "area_name": asset.area_name,
                        "district": asset.district,
                        "address": asset.address,
                        "land_area": asset.land_area,
                    }
                )
            )
        created = skipped_duplicates = failed = 0
        classifications: Counter[str] = Counter()
        failures: list[dict[str, str]] = []
        next_asset_number = (db.scalar(select(func.count(models.Asset.id))) or 0) + 1
        processed_since_commit = 0
        batch_size = 50
        for item in items:
            try:
                asset = promote_approval_item_to_asset(
                    db,
                    item,
                    reviewed_by="auto_ingest",
                    notes="Auto-published because approval gating is disabled for this app build.",
                    existing_fingerprints=existing_fingerprints,
                    asset_code=f"LJV-{next_asset_number:05d}",
                    commit=False,
                )
                if asset:
                    created += 1
                    next_asset_number += 1
                    classifications[asset.asset_type] += 1
                else:
                    skipped_duplicates += 1
                processed_since_commit += 1
                if processed_since_commit >= batch_size:
                    db.commit()
                    processed_since_commit = 0
            except Exception as exc:
                db.rollback()
                processed_since_commit = 0
                failed += 1
                failures.append({"approval_id": str(item.id), "title": item.title or "", "error": str(exc)})
        if processed_since_commit:
            db.commit()

        print(
            {
                "sync_first": sync_result,
                "pending_items_found": len(items),
                "assets_created": created,
                "duplicate_items_marked_approved": skipped_duplicates,
                "failed": failed,
                "classification_counts": dict(classifications),
                "failures": failures[:25],
            }
        )


if __name__ == "__main__":
    main()
