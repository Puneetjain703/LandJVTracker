from __future__ import annotations

import json
from typing import Any

import requests
from sqlalchemy.orm import Session

from backend.app import models
from backend.app.config import get_settings
from backend.app.services.excel_importer import COLUMN_ALIASES
from backend.app.services.ingestion import (
    clean_cell,
    is_blank_row,
    load_dedupe_context,
    normalize_asset_type,
    normalize_text,
    parse_coordinates,
    queue_payload,
    row_uid,
)


def _headers() -> dict[str, str]:
    settings = get_settings()
    if not settings.google_service_account_file and not settings.google_service_account_json:
        raise RuntimeError("Google Sheets sync needs GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_SERVICE_ACCOUNT_JSON")
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request

    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    if settings.google_service_account_json:
        info = json.loads(settings.google_service_account_json)
        credentials = service_account.Credentials.from_service_account_info(info, scopes=scopes)
    else:
        credentials = service_account.Credentials.from_service_account_file(
            settings.google_service_account_file,
            scopes=scopes,
        )
    credentials.refresh(Request())
    return {"Authorization": f"Bearer {credentials.token}"}


def _normalize_columns(columns: list[str]) -> dict[str, str]:
    lookup = {normalize_text(col): col for col in columns}
    mapping: dict[str, str] = {}
    for target, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lookup:
                mapping[target] = lookup[alias]
                break
    return mapping


def _values_for_tab(sheet_id: str, tab_name: str) -> list[list[Any]]:
    range_name = requests.utils.quote(f"{tab_name}!A:ZZ", safe="")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{range_name}"
    response = requests.get(url, headers=_headers(), timeout=60)
    if response.status_code >= 400:
        raise RuntimeError(f"Google Sheets fetch failed for {tab_name}: {response.status_code} {response.text[:500]}")
    return response.json().get("values", [])


def _row_payload(raw: dict[str, Any], mapping: dict[str, str], source: str) -> dict[str, Any]:
    payload = {target: clean_cell(raw.get(source_col)) for target, source_col in mapping.items()}
    if not payload.get("title"):
        payload["title"] = (
            payload.get("locality")
            or payload.get("area_name")
            or payload.get("address")
            or raw.get("Property")
            or raw.get("Property Name")
            or raw.get("Name")
        )
    latitude, longitude = parse_coordinates(raw)
    if latitude is not None and longitude is not None:
        payload["latitude"] = latitude
        payload["longitude"] = longitude
        payload["google_maps_link"] = f"https://www.google.com/maps?q={latitude},{longitude}"
    owner_name = raw.get("OWNER ") or raw.get("OWNER") or raw.get("Owner") or raw.get("Seller")
    broker_name = raw.get("BROKER") or raw.get("Broker") or raw.get("Reference") or raw.get("Referrer")
    if owner_name:
        payload["owner_name"] = owner_name
    if broker_name:
        payload["broker_name"] = broker_name
    if raw.get("Area Unit") and payload.get("land_area"):
        payload["land_area"] = f"{payload['land_area']} {raw['Area Unit']}"
    if raw.get("LAST UPDATE") and raw.get("REFERENCE"):
        payload["bottleneck_notes"] = f"{raw['LAST UPDATE']}\n\nHistory: {raw['REFERENCE']}"
    payload["asset_type"] = normalize_asset_type(payload.get("asset_type"), default="land")
    payload["source"] = source
    payload["approval_status"] = "pending"
    payload["raw_source"] = raw
    if not (payload.get("address") or payload.get("locality") or payload.get("area_name")):
        payload["needs_manual_review"] = True
        payload["review_reason"] = "Missing location fields"
    return payload


def sync_google_sheets_to_queue(db: Session) -> dict[str, Any]:
    settings = get_settings()
    source = "google_sheets"
    log = models.IngestionLog(source=source, filename=settings.google_sheet_id, status="running")
    db.add(log)
    db.flush()

    if not settings.google_sheet_id:
        log.status = "skipped"
        log.error_message = "GOOGLE_SHEET_ID is not configured"
        db.commit()
        return {"source_name": source, "fetched_count": 0, "queued_count": 0, "skipped_count": 0, "status": "skipped", "log_id": log.id, "message": log.error_message}
    if not settings.google_service_account_file and not settings.google_service_account_json:
        log.status = "skipped"
        log.error_message = "Google Sheets sync needs GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_SERVICE_ACCOUNT_JSON"
        db.commit()
        return {"source_name": source, "fetched_count": 0, "queued_count": 0, "skipped_count": 0, "status": "skipped", "log_id": log.id, "message": log.error_message}

    fetched = queued = skipped = 0
    try:
        dedupe_context = load_dedupe_context(db)
        for tab_name in [tab.strip() for tab in settings.google_sheet_tabs.split(",") if tab.strip()]:
            values = _values_for_tab(settings.google_sheet_id, tab_name)
            if not values:
                continue
            columns = [str(value).strip() if value not in (None, "") else f"Column {index + 1}" for index, value in enumerate(values[0])]
            mapping = _normalize_columns(columns)
            for row_number, row in enumerate(values[1:], start=2):
                raw = {
                    columns[index] if index < len(columns) else f"Column {index + 1}": clean_cell(value)
                    for index, value in enumerate(row)
                }
                if is_blank_row(raw):
                    skipped += 1
                    continue
                fetched += 1
                payload = _row_payload(raw, mapping, source)
                source_uid = row_uid(source, f"{settings.google_sheet_id}:{tab_name}", row_number, raw)
                if queue_payload(
                    db,
                    source=source,
                    source_uid=source_uid,
                    title=payload.get("title") or f"{tab_name} row {row_number}",
                    payload=payload,
                    created_by_source=f"Google Sheet: {tab_name}",
                    dedupe_context=dedupe_context,
                ):
                    queued += 1
                else:
                    skipped += 1
        log.status = "completed"
    except Exception as exc:
        log.status = "failed"
        log.error_message = str(exc)
        db.commit()
        return {"source_name": source, "fetched_count": fetched, "queued_count": queued, "skipped_count": skipped, "status": "failed", "log_id": log.id, "message": str(exc)}

    log.total_rows = fetched
    log.review_count = queued
    log.skipped_count = skipped
    db.commit()
    return {"source_name": source, "fetched_count": fetched, "queued_count": queued, "skipped_count": skipped, "status": "completed", "log_id": log.id, "message": None}
