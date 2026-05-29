from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.services.google_sheets_sync import sync_google_sheets_to_queue
from backend.app.services.notion_sync import sync_notion_project_page_to_queue, sync_notion_to_queue


def _summarize_results(results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "completed" if all(item.get("status") in {"completed", "skipped"} for item in results.values()) else "partial",
        "results": results,
        "queued_count": sum(int(item.get("queued_count") or 0) for item in results.values()),
        "skipped_count": sum(int(item.get("skipped_count") or 0) for item in results.values()),
        "fetched_count": sum(int(item.get("fetched_count") or 0) for item in results.values()),
    }


def sync_notion_project_sources(db: Session) -> dict[str, Any]:
    settings = get_settings()
    results = {
        "pearl_spytech": sync_notion_project_page_to_queue(
            db,
            page_id_or_url=settings.notion_pearl_projects_page_id,
            source_name=settings.notion_source_name,
            source="notion_pearl_spytech_projects",
            default_asset_type="land",
        )
        if settings.notion_pearl_projects_page_id
        else sync_notion_to_queue(db),
        "analyze_lrm": sync_notion_project_page_to_queue(
            db,
            page_id_or_url=settings.notion_analyze_lrm_page_id,
            source_name=settings.notion_analyze_lrm_source_name,
            source="notion_analyze_lrm",
            default_asset_type="land",
        ),
        "brokerage_new_deals": sync_notion_project_page_to_queue(
            db,
            page_id_or_url=settings.notion_brokerage_new_deals_page_id,
            source_name=settings.notion_brokerage_source_name,
            source="notion_brokerage_new_deals",
            default_asset_type="brokerage_listing",
        ),
    }
    return _summarize_results(results)


def sync_all_sources(db: Session) -> dict[str, Any]:
    notion_results = sync_notion_project_sources(db)
    results = {"google_sheets": sync_google_sheets_to_queue(db), **notion_results["results"]}
    return _summarize_results(results)
