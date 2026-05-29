from __future__ import annotations

import json
from typing import Any

from backend.app.config import get_settings
from backend.app.services.ingestion import clean_cell, normalize_asset_type


STRUCTURED_FIELDS = {
    "title",
    "asset_type",
    "status",
    "locality",
    "area_name",
    "tehsil",
    "district",
    "state",
    "address",
    "latitude",
    "longitude",
    "google_maps_link",
    "land_area",
    "built_up_area",
    "asking_price",
    "expected_price",
    "owner_name",
    "broker_name",
    "key_people",
    "workability_rating",
    "bottleneck_rating",
    "bottleneck_notes",
    "legal_status",
    "zoning_status",
    "documents",
}


def _compact_raw(raw_props: dict[str, Any], content: str | None) -> str:
    raw = {
        "notion_properties": raw_props,
        "page_content": content,
    }
    text = json.dumps(raw, ensure_ascii=False, default=str)
    return text[:14000]


def _fallback_payload(
    *,
    base_payload: dict[str, Any],
    raw_props: dict[str, Any],
    content: str | None,
    default_asset_type: str,
) -> dict[str, Any]:
    lower = {key.strip().lower(): clean_cell(value) for key, value in raw_props.items()}
    title = (
        base_payload.get("title")
        or lower.get("title")
        or lower.get("name")
        or lower.get("task name")
        or lower.get("property")
    )
    notes = "\n\n".join(str(part) for part in [base_payload.get("bottleneck_notes"), content] if part)
    return {
        "title": title,
        "asset_type": normalize_asset_type(
            base_payload.get("asset_type") or lower.get("asset type") or lower.get("type"),
            default=default_asset_type,
        ),
        "status": str(base_payload.get("status") or lower.get("status") or "lead").lower().replace(" ", "_"),
        "locality": base_payload.get("locality") or lower.get("locality") or lower.get("location") or lower.get("area"),
        "area_name": base_payload.get("area_name") or lower.get("area name") or lower.get("project"),
        "district": base_payload.get("district") or lower.get("district") or lower.get("city"),
        "state": base_payload.get("state") or lower.get("state") or "Rajasthan",
        "address": base_payload.get("address") or lower.get("address") or lower.get("site address"),
        "asking_price": base_payload.get("asking_price") or lower.get("asking price") or lower.get("price"),
        "expected_price": base_payload.get("expected_price") or lower.get("expected price"),
        "land_area": base_payload.get("land_area") or lower.get("land area") or lower.get("area / size") or lower.get("size"),
        "broker_name": base_payload.get("broker_name") or lower.get("broker") or lower.get("referrer"),
        "owner_name": base_payload.get("owner_name") or lower.get("owner") or lower.get("seller"),
        "key_people": base_payload.get("key_people") or lower.get("key people") or lower.get("parties"),
        "bottleneck_notes": notes or None,
    }


def _openai_payload(
    *,
    base_payload: dict[str, Any],
    raw_props: dict[str, Any],
    content: str | None,
    source_name: str,
    default_asset_type: str,
) -> dict[str, Any] | None:
    settings = get_settings()
    if not settings.openai_api_key:
        return None
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key)
    prompt = f"""
Extract one real-estate property/deal lead from this Notion task or note.

Return ONLY a JSON object. Use null when unknown. Do not invent facts.
If the source is a brokerage lead, prefer asset_type "brokerage_listing".
Otherwise use one of: land, jv, resale_unit, commercial, rental, brokerage_listing, other.

Required JSON keys:
{sorted(STRUCTURED_FIELDS)}

Context:
- source_name: {source_name}
- default_asset_type: {default_asset_type}
- current_payload: {json.dumps(base_payload, ensure_ascii=False, default=str)}

Raw Notion input:
{_compact_raw(raw_props, content)}
"""
    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {
                "role": "system",
                "content": "You extract structured real-estate intelligence for an internal approval queue.",
            },
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    text = response.choices[0].message.content or "{}"
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return {key: value for key, value in data.items() if key in STRUCTURED_FIELDS and value not in (None, "", [])}


def process_notion_payload(
    *,
    base_payload: dict[str, Any],
    raw_props: dict[str, Any],
    content: str | None,
    source_name: str,
    default_asset_type: str,
) -> dict[str, Any]:
    fallback = _fallback_payload(
        base_payload=base_payload,
        raw_props=raw_props,
        content=content,
        default_asset_type=default_asset_type,
    )
    processed = dict(base_payload)
    processed.update({key: value for key, value in fallback.items() if value not in (None, "", [])})
    ai_payload = _openai_payload(
        base_payload=processed,
        raw_props=raw_props,
        content=content,
        source_name=source_name,
        default_asset_type=default_asset_type,
    )
    if ai_payload:
        processed.update(ai_payload)
        processed["notion_processor"] = "openai"
    else:
        processed["notion_processor"] = "fallback"
    processed["asset_type"] = normalize_asset_type(processed.get("asset_type"), default=default_asset_type)
    processed["approval_status"] = "pending"
    return processed
