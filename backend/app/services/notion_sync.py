from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app import models
from backend.app.config import get_settings
from backend.app.services.ingestion import load_dedupe_context, normalize_asset_type, queue_payload
from backend.app.services.notion_processor import process_notion_payload


def _plain_text(items: list[dict[str, Any]] | None) -> str | None:
    if not items:
        return None
    text = "".join(item.get("plain_text", "") for item in items)
    return text.strip() or None


def _property_value(prop: dict[str, Any]) -> Any:
    prop_type = prop.get("type")
    value = prop.get(prop_type)
    if prop_type == "title":
        return _plain_text(value)
    if prop_type == "rich_text":
        return _plain_text(value)
    if prop_type == "select":
        return value.get("name") if value else None
    if prop_type == "multi_select":
        return [item.get("name") for item in value or []]
    if prop_type in {"number", "checkbox", "url", "email", "phone_number"}:
        return value
    if prop_type == "date":
        return value
    if prop_type == "status":
        return value.get("name") if value else None
    if prop_type == "people":
        return [person.get("name") for person in value or []]
    if prop_type == "relation":
        return [item.get("id") for item in value or []]
    if prop_type == "files":
        files = []
        for item in value or []:
            file_value = item.get(item.get("type"), {})
            files.append({"name": item.get("name"), "url": file_value.get("url"), "type": item.get("type")})
        return files
    return value


def _map_page(page: dict[str, Any]) -> dict[str, Any]:
    raw_props = {name: _property_value(prop) for name, prop in page.get("properties", {}).items()}
    lower = {key.strip().lower(): value for key, value in raw_props.items()}
    title = (
        lower.get("title")
        or lower.get("name")
        or lower.get("project")
        or lower.get("property")
        or f"Notion lead {page['id']}"
    )
    return {
        "title": title,
        "asset_type": str(lower.get("asset type") or lower.get("type") or "other").lower().replace(" ", "_"),
        "status": str(lower.get("status") or "lead").lower().replace(" ", "_"),
        "source": "notion",
        "locality": lower.get("locality") or lower.get("location"),
        "area_name": lower.get("area") or lower.get("area name"),
        "tehsil": lower.get("tehsil"),
        "district": lower.get("district") or lower.get("city"),
        "state": lower.get("state") or "Rajasthan",
        "address": lower.get("address") or lower.get("site address"),
        "asking_price": lower.get("asking price") or lower.get("price"),
        "expected_price": lower.get("expected price"),
        "land_area": lower.get("land area") or lower.get("area"),
        "bottleneck_notes": lower.get("notes") or lower.get("remarks"),
        "approval_status": "pending",
        "notion_page_id": page["id"],
        "notion_url": page.get("url"),
        "raw_source": {"notion_properties": raw_props, "page": {"id": page["id"], "url": page.get("url")}},
    }


def _page_id(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"([0-9a-fA-F]{32})", value.replace("-", ""))
    return match.group(1) if match else value


def _title_from_page(page: dict[str, Any]) -> str | None:
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            return _plain_text(prop.get("title")) or None
    return None


def _block_text(client: Any, page_id: str) -> str | None:
    chunks: list[str] = []
    cursor: str | None = None
    while True:
        response = client.blocks.children.list(block_id=page_id, start_cursor=cursor)
        for block in response.get("results", []):
            block_type = block.get("type")
            value = block.get(block_type, {}) if block_type else {}
            text = _plain_text(value.get("rich_text"))
            if text:
                chunks.append(text)
        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")
    return "\n".join(chunks).strip() or None


def _documents_from_props(raw_props: dict[str, Any]) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for name, value in raw_props.items():
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and item.get("url"):
                    documents.append(
                        {
                            "document_name": item.get("name") or name,
                            "document_type": item.get("type") or name,
                            "url": item.get("url"),
                        }
                    )
        elif isinstance(value, str) and value.startswith(("http://", "https://")) and any(
            token in name.lower() for token in ["url", "link", "file", "document", "attachment"]
        ):
            documents.append({"document_name": name, "url": value})
    return documents


def _map_related_page(
    client: Any,
    page: dict[str, Any],
    *,
    source: str,
    source_name: str,
    default_asset_type: str,
) -> dict[str, Any]:
    raw_props = {name: _property_value(prop) for name, prop in page.get("properties", {}).items()}
    lower = {key.strip().lower(): value for key, value in raw_props.items()}
    title = (
        _title_from_page(page)
        or lower.get("title")
        or lower.get("name")
        or lower.get("task name")
        or lower.get("property")
        or f"Notion item {page['id']}"
    )
    content = _block_text(client, page["id"])
    notes = "\n\n".join(str(part) for part in [lower.get("notes") or lower.get("remarks"), content] if part)
    base_payload = {
        "title": title,
        "asset_type": normalize_asset_type(lower.get("asset type") or lower.get("type"), default=default_asset_type),
        "status": str(lower.get("status") or "lead").lower().replace(" ", "_"),
        "source": source,
        "locality": lower.get("locality") or lower.get("location") or lower.get("area"),
        "area_name": lower.get("area name") or lower.get("project") or lower.get("property / deal name"),
        "district": lower.get("district") or lower.get("city"),
        "state": lower.get("state") or "Rajasthan",
        "address": lower.get("address") or lower.get("site address"),
        "asking_price": lower.get("asking price") or lower.get("price") or lower.get("commercial terms"),
        "expected_price": lower.get("expected price"),
        "land_area": lower.get("land area") or lower.get("area / size") or lower.get("size"),
        "broker_name": lower.get("broker") or lower.get("referrer"),
        "owner_name": lower.get("owner") or lower.get("seller"),
        "key_people": lower.get("key people") or lower.get("parties"),
        "bottleneck_notes": notes or None,
        "documents": _documents_from_props(raw_props),
        "approval_status": "pending",
        "notion_page_id": page["id"],
        "notion_url": page.get("url"),
        "raw_source": {
            "source_name": source_name,
            "notion_properties": raw_props,
            "content": content,
            "page": {"id": page["id"], "url": page.get("url")},
        },
    }
    return process_notion_payload(
        base_payload=base_payload,
        raw_props=raw_props,
        content=content,
        source_name=source_name,
        default_asset_type=default_asset_type,
    )


def _resolve_database_id(client: Client, configured_id: str | None, source_name: str) -> str | None:
    if configured_id:
        return configured_id
    result = client.search(query=source_name, filter={"property": "object", "value": "database"})
    for item in result.get("results", []):
        title = _plain_text(item.get("title")) if item.get("object") == "database" else None
        if title == source_name:
            return item["id"]
    return None


def _relation_page_ids(
    client: Any,
    *,
    page_id: str,
    source_page: dict[str, Any],
    relation_names: tuple[str, ...],
) -> list[str]:
    relation_name_set = {name.strip().lower() for name in relation_names}
    related_ids: list[str] = []
    for prop_name, prop in source_page.get("properties", {}).items():
        if prop_name.strip().lower() not in relation_name_set or prop.get("type") != "relation":
            continue
        related_ids.extend(item["id"] for item in prop.get("relation") or [] if item.get("id"))
        try:
            cursor: str | None = None
            while True:
                response = client.pages.properties.retrieve(
                    page_id=page_id,
                    property_id=prop["id"],
                    start_cursor=cursor,
                )
                # Handle single property item vs paginated results list
                if isinstance(response, dict) and response.get("object") == "list":
                    for item in response.get("results", []):
                        if item.get("type") == "relation" and item.get("relation", {}).get("id"):
                            related_ids.append(item["relation"]["id"])
                    if not response.get("has_more"):
                        break
                    cursor = response.get("next_cursor")
                    if not cursor:
                        break
                elif isinstance(response, dict) and response.get("type") == "relation":
                    related_ids.extend(item["id"] for item in response.get("relation") or [] if item.get("id"))
                    break
                else:
                    break
        except Exception as exc:
            from notion_client.errors import APIResponseError
            if isinstance(exc, APIResponseError):
                if exc.status == 404 or exc.code == "object_not_found":
                    raise RuntimeError(
                        f"Found the project page, but the Notion integration cannot read the linked '{prop_name}' database. "
                        f"Open the database in Notion, go to Share / Connections, add the integration named 'Land/JV/Brokerage Tracker', and then rerun."
                    )
            raise exc
    return list(dict.fromkeys(related_ids))


def sync_notion_to_queue(db: Session) -> dict[str, Any]:
    from notion_client import Client

    settings = get_settings()
    log = models.NotionSyncLog(
        source_name=settings.notion_source_name,
        notion_database_id=settings.notion_database_id,
        status="running",
    )
    db.add(log)
    db.flush()

    if not settings.notion_api_key:
        log.status = "skipped"
        log.error_message = "NOTION_API_KEY is not configured"
        db.commit()
        return {
            "source_name": settings.notion_source_name,
            "fetched_count": 0,
            "queued_count": 0,
            "skipped_count": 0,
            "status": "skipped",
            "log_id": log.id,
            "message": log.error_message,
        }

    client = Client(auth=settings.notion_api_key)
    database_id = _resolve_database_id(client, settings.notion_database_id, settings.notion_source_name)
    if not database_id:
        log.status = "failed"
        log.error_message = f"Could not find Notion database named {settings.notion_source_name!r}"
        db.commit()
        return {
            "source_name": settings.notion_source_name,
            "fetched_count": 0,
            "queued_count": 0,
            "skipped_count": 0,
            "status": "failed",
            "log_id": log.id,
            "message": log.error_message,
        }

    fetched = queued = skipped = 0
    cursor: str | None = None
    dedupe_context = load_dedupe_context(db)
    while True:
        response = client.databases.query(database_id=database_id, start_cursor=cursor)
        for page in response.get("results", []):
            fetched += 1
            source_uid = page["id"]
            existing = db.scalar(
                select(models.ApprovalQueue).where(
                    models.ApprovalQueue.source == "notion",
                    models.ApprovalQueue.source_uid == source_uid,
                )
            )
            if existing:
                skipped += 1
                continue
            payload = _map_page(page)
            if queue_payload(
                db,
                source="notion",
                source_uid=source_uid,
                title=payload.get("title"),
                payload=payload,
                created_by_source=settings.notion_source_name,
                dedupe_context=dedupe_context,
            ):
                queued += 1
            else:
                skipped += 1
        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")

    log.notion_database_id = database_id
    log.status = "completed"
    log.fetched_count = fetched
    log.queued_count = queued
    log.skipped_count = skipped
    db.commit()
    return {
        "source_name": settings.notion_source_name,
        "fetched_count": fetched,
        "queued_count": queued,
        "skipped_count": skipped,
        "status": "completed",
        "log_id": log.id,
        "message": None,
    }


def sync_notion_project_page_to_queue(
    db: Session,
    *,
    page_id_or_url: str | None,
    source_name: str,
    source: str,
    default_asset_type: str = "land",
    relation_names: tuple[str, ...] = ("Tasks", "Notes"),
) -> dict[str, Any]:
    from notion_client import Client

    log = models.NotionSyncLog(source_name=source_name, notion_database_id=_page_id(page_id_or_url), status="running")
    db.add(log)
    db.flush()

    settings = get_settings()
    if not settings.notion_api_key:
        log.status = "skipped"
        log.error_message = "NOTION_API_KEY is not configured"
        db.commit()
        return {"source_name": source_name, "fetched_count": 0, "queued_count": 0, "skipped_count": 0, "status": "skipped", "log_id": log.id, "message": log.error_message}

    page_id = _page_id(page_id_or_url)
    client = Client(auth=settings.notion_api_key)
    if not page_id:
        search = client.search(query=source_name, filter={"property": "object", "value": "page"})
        for result in search.get("results", []):
            if _title_from_page(result) == source_name:
                page_id = result["id"]
                break
    if not page_id:
        log.status = "failed"
        log.error_message = f"Could not find Notion page named {source_name!r}"
        db.commit()
        return {"source_name": source_name, "fetched_count": 0, "queued_count": 0, "skipped_count": 0, "status": "failed", "log_id": log.id, "message": log.error_message}

    try:
        source_page = client.pages.retrieve(page_id=page_id)
        related_ids = _relation_page_ids(
            client,
            page_id=page_id,
            source_page=source_page,
            relation_names=relation_names,
        )

        fetched = queued = skipped = 0
        dedupe_context = load_dedupe_context(db)
        seen_ids: set[str] = set()
        for related_id in dict.fromkeys(related_ids):
            seen_ids.add(related_id)
            fetched += 1
            page = client.pages.retrieve(page_id=related_id)
            payload = _map_related_page(
                client,
                page,
                source=source,
                source_name=source_name,
                default_asset_type=default_asset_type,
            )
            if queue_payload(
                db,
                source=source,
                source_uid=related_id,
                title=payload.get("title"),
                payload=payload,
                created_by_source=source_name,
                dedupe_context=dedupe_context,
            ):
                queued += 1
            else:
                skipped += 1
        if fetched == 0:
            log.status = "failed"
            log.notion_database_id = page_id
            log.error_message = (
                f"Found the Notion project page {source_name!r}, but its Tasks/Notes relation entries "
                "are hidden from the integration. This listener reads only that project page and its "
                "Tasks/Notes relation properties; it does not query the global Tasks or Notes database. "
                "In Notion, share this project page and the related task/note pages visible inside its "
                "Tasks and Notes sections with the integration named Land/JV/Brokerage Tracker, then rerun."
            )
            db.commit()
            return {
                "source_name": source_name,
                "fetched_count": 0,
                "queued_count": 0,
                "skipped_count": 0,
                "status": "failed",
                "log_id": log.id,
                "message": log.error_message,
            }
        log.status = "completed"
        log.notion_database_id = page_id
        log.fetched_count = fetched
        log.queued_count = queued
        log.skipped_count = skipped
        db.commit()
        return {"source_name": source_name, "fetched_count": fetched, "queued_count": queued, "skipped_count": skipped, "status": "completed", "log_id": log.id, "message": None}
    except Exception as exc:
        log.status = "failed"
        log.error_message = str(exc)
        db.commit()
        return {"source_name": source_name, "fetched_count": 0, "queued_count": 0, "skipped_count": 0, "status": "failed", "log_id": log.id, "message": str(exc)}
