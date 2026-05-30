from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from backend.app import models
from backend.app.config import get_settings
from backend.app.schemas import AssetUpdate
from backend.app.services.assets import update_asset
from backend.app.services.ingestion import (
    asset_type_for_classification,
    classify_property,
    normalize_asset_type,
    queue_payload,
)


ASSET_FIELDS = {
    "title",
    "asset_type",
    "status",
    "source",
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
    "workability_rating",
    "bottleneck_rating",
    "bottleneck_notes",
    "legal_status",
    "zoning_status",
    "approval_status",
    "raw_source",
}
UPDATE_FIELDS = set(AssetUpdate.model_fields)
CONTACT_ROLES = {
    "broker",
    "landowner",
    "possible_partner",
    "financier",
    "bank",
    "buyer",
    "seller",
    "developer",
    "legal_advisor",
    "architect",
    "government_contact",
    "referrer",
    "related",
}
ACTION_TYPES = {
    "answer",
    "ask_followup",
    "create_asset",
    "update_asset",
    "add_update",
    "add_contact",
    "add_document",
    "missing_info_report",
}
IMPORTANT_FIELDS = [
    "title",
    "asset_type",
    "locality",
    "district",
    "address",
    "land_area",
    "asking_price",
    "owner_name",
    "broker_name",
    "workability_rating",
    "bottleneck_notes",
    "legal_status",
]


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _stringify_values(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _json_safe(value) for key, value in row.items()}


def _parse_money(text: str, unit: str | None = None) -> float | None:
    try:
        amount = float(str(text).replace(",", ""))
    except (TypeError, ValueError):
        return None
    unit_value = (unit or "").lower()
    if unit_value in {"k", "thousand"}:
        amount *= 1_000
    elif unit_value in {"lac", "lakh", "lacs", "lakhs"}:
        amount *= 100_000
    elif unit_value in {"cr", "crore", "crores"}:
        amount *= 10_000_000
    return amount


def _area_to_sqyd(area_text: str | None) -> tuple[float | None, str | None]:
    if not area_text:
        return None, None
    match = re.search(
        r"(\d+(?:,\d+)*(?:\.\d+)?)\s*(bigha|acre|acres|sq\.?\s*ft|sqft|square\s*feet|sqm|sq\.?\s*m|square\s*meter|square\s*metre|gaj|sq\.?\s*yd|sqyd|yard|yards)",
        area_text,
        flags=re.I,
    )
    if not match:
        return None, None
    value = float(match.group(1).replace(",", ""))
    unit = re.sub(r"\s+", " ", match.group(2).lower().replace(".", "")).strip()
    multipliers = {
        "gaj": 1.0,
        "sq yd": 1.0,
        "sqyd": 1.0,
        "yard": 1.0,
        "yards": 1.0,
        "sq ft": 1 / 9,
        "sqft": 1 / 9,
        "square feet": 1 / 9,
        "sqm": 1.19599,
        "sq m": 1.19599,
        "square meter": 1.19599,
        "square metre": 1.19599,
        "acre": 4840.0,
        "acres": 4840.0,
        "bigha": 3025.0,
    }
    multiplier = multipliers.get(unit)
    if not multiplier:
        return None, None
    return round(value * multiplier, 2), f"{value:g} {unit}"


def _unit_rate_to_sqyd(rate_text: str) -> tuple[float | None, str | None]:
    match = re.search(
        r"(?:(?:rate|price|ask|asking)\D{0,20})?(\d+(?:,\d+)*(?:\.\d+)?)\s*(k|thousand|lac|lakh|lacs|lakhs|cr|crore|crores)?\s*(?:/|per)\s*(bigha|acre|acres|sq\.?\s*ft|sqft|square\s*feet|sqm|sq\.?\s*m|square\s*meter|square\s*metre|gaj|sq\.?\s*yd|sqyd|yard|yards)",
        rate_text,
        flags=re.I,
    )
    if not match:
        return None, None
    amount = _parse_money(match.group(1), match.group(2))
    if amount is None:
        return None, None
    unit_text = f"1 {match.group(3)}"
    unit_sqyd, normalized_unit = _area_to_sqyd(unit_text)
    if not unit_sqyd:
        return None, None
    return round(amount / unit_sqyd, 2), f"{amount:g} per {normalized_unit or match.group(3)}"


def _pricing_calculation(message: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    area_sqyd, area_basis = _area_to_sqyd(str(fields.get("land_area") or fields.get("built_up_area") or message))
    if not area_sqyd:
        area_sqyd, area_basis = _area_to_sqyd(message)
    rate_sqyd, rate_basis = _unit_rate_to_sqyd(message)
    if not area_sqyd or not rate_sqyd:
        return None
    total = round(area_sqyd * rate_sqyd, 2)
    return {
        "area_sqyd": area_sqyd,
        "area_basis": area_basis,
        "rate_per_sqyd": rate_sqyd,
        "rate_basis": rate_basis,
        "computed_total_price": total,
        "calculation": f"{area_sqyd:g} sqyd x {rate_sqyd:g}/sqyd = {total:g}",
        "unit_assumptions": "1 bigha assumed as 3025 sqyd; 1 acre as 4840 sqyd; 1 sqyd/gaj as 9 sqft.",
    }


def _brokerage_economics(message: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    if normalize_asset_type(fields.get("asset_type"), default="land") != "brokerage_listing":
        return None
    price = _parse_money(str(fields.get("asking_price") or fields.get("expected_price") or ""), None)
    if price is None:
        price = fields.get("asking_price") or fields.get("expected_price")
    try:
        deal_value = float(price) if price else 0.0
    except (TypeError, ValueError):
        deal_value = 0.0
    percent_match = re.search(r"(?:brokerage|commission|margin)\D{0,20}(\d+(?:\.\d+)?)\s*(?:%|percent)", message, flags=re.I)
    percent = float(percent_match.group(1)) if percent_match else 1.0
    if percent > 15:
        percent = 1.0
    margin_match = re.search(r"(?:my\s+)?(?:margin|spread|net)\D{0,20}(\d+(?:\.\d+)?)\s*(cr|crore|lac|lakh)?", message, flags=re.I)
    explicit_margin = _parse_money(margin_match.group(1), margin_match.group(2)) if margin_match else None
    if not deal_value and explicit_margin is None:
        return None
    return {
        "deal_value": deal_value or None,
        "brokerage_percent": percent if deal_value else None,
        "estimated_brokerage": round(deal_value * percent / 100, 2) if deal_value else None,
        "explicit_margin": explicit_margin,
        "notes": "Estimated from chatbot text; verify mandate, commission split, and receivable party before relying on it.",
    }


def _copilot_raw_source(message: str, fields: dict[str, Any]) -> dict[str, Any]:
    pricing = _pricing_calculation(message, fields)
    if pricing and not fields.get("asking_price"):
        fields["asking_price"] = pricing["computed_total_price"]
    brokerage = _brokerage_economics(message, fields)
    features = {
        key: fields.get(key)
        for key in [
            "asset_type",
            "locality",
            "district",
            "address",
            "land_area",
            "built_up_area",
            "asking_price",
            "expected_price",
            "owner_name",
            "broker_name",
            "legal_status",
            "zoning_status",
            "workability_rating",
            "bottleneck_rating",
        ]
        if fields.get(key) not in (None, "", [])
    }
    return {
        "copilot": {
            "original_prompt": message,
            "extracted_features": features,
            "pricing_calculation": pricing,
            "brokerage_economics": brokerage,
            "extracted_at": datetime.now(timezone.utc).isoformat(),
        }
    }


def _append_to_pending_payload(item: models.ApprovalQueue, key: str, value: dict[str, Any]) -> None:
    payload = dict(item.edited_payload or item.payload or {})
    values = list(payload.get(key) or [])
    values.append(value)
    payload[key] = values
    item.edited_payload = payload


def _compact_asset(asset: models.Asset) -> dict[str, Any]:
    raw = asset.raw_source if isinstance(asset.raw_source, dict) else {}
    contacts = [
        f"{link.contact.name} ({link.relationship_type})"
        for link in asset.contacts[:5]
        if link.contact and link.contact.name
    ]
    recent_updates = [
        update.update_text[:260]
        for update in sorted(asset.updates, key=lambda item: item.created_at or datetime.min, reverse=True)[:2]
        if update.update_text
    ]
    return _stringify_values(
        {
            "id": asset.id,
            "asset_code": asset.asset_code,
            "title": asset.title,
            "asset_type": asset.asset_type,
            "status": asset.status,
            "source": asset.source,
            "locality": asset.locality,
            "area_name": asset.area_name,
            "tehsil": asset.tehsil,
            "district": asset.district,
            "state": asset.state,
            "address": asset.address,
            "latitude": asset.latitude,
            "longitude": asset.longitude,
            "google_maps_link": asset.google_maps_link,
            "land_area": asset.land_area,
            "built_up_area": asset.built_up_area,
            "asking_price": asset.asking_price,
            "expected_price": asset.expected_price,
            "owner_name": asset.owner.name if asset.owner else None,
            "broker_name": asset.broker.name if asset.broker else None,
            "people_summary": ", ".join(contacts),
            "document_count": len(asset.documents),
            "recent_updates": recent_updates,
            "workability_rating": asset.workability_rating,
            "bottleneck_rating": asset.bottleneck_rating,
            "bottleneck_notes": (asset.bottleneck_notes or "")[:500] or None,
            "legal_status": asset.legal_status,
            "zoning_status": asset.zoning_status,
            "location_score": raw.get("location_score"),
            "location_score_reason": raw.get("location_score_reason"),
            "approval_status": asset.approval_status,
            "created_at": asset.created_at,
            "updated_at": asset.updated_at,
        }
    )


def _candidate_assets(db: Session, message: str) -> list[dict[str, Any]]:
    id_matches = [int(match) for match in re.findall(r"\b(?:asset|id)\s*#?(\d+)\b", message.lower())]
    code_matches = re.findall(r"\bLJV-\d+\b", message, flags=re.I)
    tokens = [token for token in re.findall(r"[A-Za-z0-9_/-]+", message.lower()) if len(token) > 2]
    stmt = select(models.Asset).options(
        selectinload(models.Asset.owner),
        selectinload(models.Asset.broker),
        selectinload(models.Asset.contacts).selectinload(models.AssetContact.contact),
        selectinload(models.Asset.documents),
        selectinload(models.Asset.updates),
        selectinload(models.Asset.locations),
        selectinload(models.Asset.tags),
    )
    if id_matches or code_matches:
        clauses = []
        if id_matches:
            clauses.append(models.Asset.id.in_(id_matches))
        if code_matches:
            clauses.append(models.Asset.asset_code.in_([code.upper() for code in code_matches]))
        stmt = stmt.where(or_(*clauses))
    elif tokens:
        clauses = []
        for token in tokens[:12]:
            like = f"%{token}%"
            clauses.extend(
                [
                    models.Asset.asset_code.ilike(like),
                    models.Asset.title.ilike(like),
                    models.Asset.locality.ilike(like),
                    models.Asset.area_name.ilike(like),
                    models.Asset.district.ilike(like),
                    models.Asset.tehsil.ilike(like),
                    models.Asset.address.ilike(like),
                    models.Asset.bottleneck_notes.ilike(like),
                    models.Asset.source.ilike(like),
                ]
            )
        stmt = stmt.where(or_(*clauses))
    stmt = stmt.order_by(models.Asset.updated_at.desc()).limit(12)
    return [_compact_asset(asset) for asset in db.scalars(stmt)]


def _fallback_extract(message: str) -> dict[str, Any]:
    text = message.strip()
    payload: dict[str, Any] = {}
    title_match = re.search(r"(?:property|plot|land|asset|deal)\s+(?:at|in|near)?\s*([^.,\n]+)", text, flags=re.I)
    if title_match:
        payload["title"] = title_match.group(1).strip(" -")
    elif len(text) < 140 and any(word in text.lower() for word in ["land", "plot", "jv", "brokerage", "deal"]):
        payload["title"] = text[:120]
    locality_match = re.search(r"(?:at|in|near)\s+([A-Za-z0-9 /-]{3,60})(?:,|\.|\n|$)", text, flags=re.I)
    if locality_match:
        payload["locality"] = locality_match.group(1).strip()
    area_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:bigha|acre|sq\.?\s*ft|sqft|sqm|gaj|yard)", text, flags=re.I)
    if area_match:
        payload["land_area"] = area_match.group(0)
    price_match = re.search(r"(?:ask|asking|price|rate)\D{0,20}(\d+(?:\.\d+)?)\s*(cr|crore|lac|lakh)?", text, flags=re.I)
    if price_match:
        amount = float(price_match.group(1))
        unit = (price_match.group(2) or "").lower()
        if unit in {"cr", "crore"}:
            amount *= 10_000_000
        elif unit in {"lac", "lakh"}:
            amount *= 100_000
        payload["asking_price"] = amount
    pricing = _pricing_calculation(text, payload)
    if pricing:
        payload["asking_price"] = pricing["computed_total_price"]
    owner_match = re.search(r"owner(?:\s+is|\s*:)?\s+([A-Za-z .'-]{3,80})", text, flags=re.I)
    if owner_match:
        payload["owner_name"] = owner_match.group(1).strip(" .,-")
    broker_match = re.search(r"broker(?:\s+is|\s*:)?\s+([A-Za-z .'-]{3,80})", text, flags=re.I)
    if broker_match:
        payload["broker_name"] = broker_match.group(1).strip(" .,-")
    classification = classify_property(payload | {"bottleneck_notes": text})
    payload["asset_type"] = asset_type_for_classification(classification)
    payload["status"] = "lead"
    payload["source"] = "chatbot"
    payload["bottleneck_notes"] = text
    payload["raw_source"] = _copilot_raw_source(text, payload)
    return {key: value for key, value in payload.items() if value not in (None, "", [])}


def _missing_fields(payload: dict[str, Any]) -> list[str]:
    missing = []
    for field in IMPORTANT_FIELDS:
        if payload.get(field) in (None, "", []):
            missing.append(field)
    return missing


def _fallback_plan(message: str, candidates: list[dict[str, Any]], upload_names: list[str]) -> dict[str, Any]:
    lower = message.lower()
    actions: list[dict[str, Any]] = []
    answer = None
    if any(word in lower for word in ["missing", "incomplete", "attention", "need", "clean up"]):
        actions.append({"action": "missing_info_report", "reason": "User asked for data quality or attention needed."})
    target = candidates[0] if len(candidates) == 1 else None
    wants_create = any(word in lower for word in ["add new", "create", "new property", "new deal", "ingest"])
    wants_update = any(word in lower for word in ["update", "revise", "sold", "changed", "add note", "follow up", "spoke", "called", "met", "conversation"])
    if target and wants_update:
        fields: dict[str, Any] = {}
        status = re.search(r"status(?:\s+to|\s+as|\s+is)?\s+([A-Za-z_ -]+)", message, flags=re.I)
        workability = re.search(r"workability(?:\s+rating|\s+score)?\D+(\d{1,2})", message, flags=re.I)
        bottleneck = re.search(r"bottleneck(?:\s+rating|\s+score)?\D+(\d{1,2})", message, flags=re.I)
        if status:
            fields["status"] = status.group(1).strip().lower().replace(" ", "_")[:80]
        if workability:
            fields["workability_rating"] = max(0, min(10, int(workability.group(1))))
        if bottleneck:
            fields["bottleneck_rating"] = max(0, min(10, int(bottleneck.group(1))))
        if fields:
            actions.append({"action": "update_asset", "asset_id": target["id"], "fields": fields, "reason": "Parsed from update request."})
        update_type = "broker_conversation" if any(word in lower for word in ["spoke", "called", "met", "conversation"]) else "note"
        actions.append({"action": "add_update", "asset_id": target["id"], "update_type": update_type, "update_text": message, "reason": "Keep this instruction in the property timeline."})
        person_match = re.search(r"(?:spoke|called|met|conversation)\s+(?:to|with)?\s*([A-Za-z .'-]{3,80})", message, flags=re.I)
        if person_match:
            actions.append(
                {
                    "action": "add_contact",
                    "asset_id": target["id"],
                    "relationship_type": "related",
                    "contact": {"name": person_match.group(1).strip()},
                    "relationship_notes": "Mentioned in chatbot conversation update.",
                    "reason": "Capture person discussed in broker/dealer conversation.",
                }
            )
        for name in upload_names:
            actions.append({"action": "add_document", "asset_id": target["id"], "file_name": name, "document_name": name, "document_type": "uploaded", "reason": "Attach uploaded file to matched property."})
    elif wants_create or (upload_names and not target):
        payload = _fallback_extract(message)
        missing = _missing_fields(payload)
        if not payload.get("title"):
            actions.append({"action": "ask_followup", "questions": ["What should this property/deal be called?", "Where is it located?", "Is it land purchase, JV, or brokerage?"]})
        else:
            action = {"action": "create_asset", "fields": payload, "missing_fields": missing, "reason": "Create a new structured property from your message."}
            actions.append(action)
    elif candidates:
        lines = ["I found these matching property files:"]
        for row in candidates[:5]:
            lines.append(f"{row.get('asset_code') or row.get('id')}: {row.get('title')} | {row.get('asset_type')} | {row.get('locality') or '-'}, {row.get('district') or '-'}")
        answer = "\n".join(lines)
    else:
        answer = "I could not confidently match or create a property yet. Tell me the location/name and whether this is land purchase, JV, or brokerage."
    return {
        "summary": "Prepared a safe copilot plan.",
        "answer": answer,
        "actions": actions,
        "matched_assets": candidates[:8],
        "requires_confirmation": True,
    }


def _previous_create_fields(message: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for match in re.findall(r"Current proposed actions JSON:\s*(\[.*?\])(?:\n|$)", message, flags=re.S):
        try:
            actions = json.loads(match)
        except json.JSONDecodeError:
            continue
        for action in actions:
            if isinstance(action, dict) and action.get("action") == "create_asset" and isinstance(action.get("fields"), dict):
                fields.update({key: value for key, value in action["fields"].items() if value not in (None, "", [])})
    return fields


def _openai_plan(message: str, candidates: list[dict[str, Any]], upload_names: list[str]) -> dict[str, Any] | None:
    settings = get_settings()
    if not settings.openai_api_key:
        return None
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key)
    schema_hint = {
        "summary": "short description of what you understood",
        "answer": "concise answer for read-only questions, or null",
        "actions": [
            {
                "action": "create_asset | update_asset | add_update | add_contact | add_document | ask_followup | missing_info_report | answer",
                "asset_id": 123,
                "fields": {"title": "Property name", "asset_type": "land"},
                "relationship_type": "broker",
                "contact": {"name": "Person", "phone": "optional", "company": "optional", "notes": "optional"},
                "update_type": "note",
                "update_text": "timeline note",
                "file_name": "uploaded filename if attaching",
                "document_name": "friendly document name",
                "document_type": "image | title_doc | map | brochure | other",
                "questions": ["missing question"],
                "missing_fields": ["field"],
                "reason": "why",
            }
        ],
        "matched_assets": [],
        "requires_confirmation": True,
    }
    system = (
        "You are Property Copilot for an internal Indian real-estate database. "
        "Turn unstructured user input into safe proposed database actions. "
        "Classify each property into asset_type land, jv, brokerage_listing, commercial, resale_unit, rental, or other. "
        "Prefer asset_type land for investment/land purchase, jv for joint venture/development share, brokerage_listing for brokerage opportunities. "
        "If the user gives a per-unit rate and area, calculate total asking_price and include the calculation in raw_source.copilot.pricing_calculation. "
        "For brokerage listings, estimate brokerage/margin when enough price or percentage information is present. "
        "If the user says they spoke/called/met a broker/dealer/person about a property, propose add_update with update_type broker_conversation and add_contact if a person is named. "
        "Never delete. Never invent missing facts. If a new property lacks title/location/category, ask follow-up questions. "
        "Use only provided candidate asset ids for updates/documents. Return only JSON."
    )
    user = (
        f"User message:\n{message}\n\n"
        f"Uploaded file names: {upload_names}\n\n"
        f"Candidate assets:\n{json.dumps(candidates[:8], ensure_ascii=False, default=str)}\n\n"
        f"Allowed asset fields: {sorted(ASSET_FIELDS)}\n"
        f"Important fields to collect: {IMPORTANT_FIELDS}\n"
        f"Allowed contact roles: {sorted(CONTACT_ROLES)}\n"
        f"Return JSON shaped like: {json.dumps(schema_hint, ensure_ascii=False)}"
    )
    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format={"type": "json_object"},
        temperature=0,
    )
    try:
        return json.loads(response.choices[0].message.content or "{}")
    except json.JSONDecodeError:
        return None


def _validate_plan(plan: dict[str, Any], candidates: list[dict[str, Any]], upload_names: list[str]) -> dict[str, Any]:
    candidate_ids = {int(row["id"]) for row in candidates if row.get("id") is not None}
    uploads = set(upload_names)
    actions: list[dict[str, Any]] = []
    for raw in plan.get("actions") or []:
        action = raw.get("action")
        if action not in ACTION_TYPES:
            continue
        clean = {"action": action, "reason": raw.get("reason")}
        if action in {"answer", "ask_followup", "missing_info_report"}:
            if raw.get("text"):
                clean["text"] = raw["text"]
            if raw.get("questions"):
                clean["questions"] = [str(item) for item in raw["questions"][:6]]
            actions.append({key: value for key, value in clean.items() if value not in (None, "", [])})
            continue
        asset_id = raw.get("asset_id")
        if action != "create_asset":
            if not isinstance(asset_id, int) or (candidate_ids and asset_id not in candidate_ids):
                continue
            clean["asset_id"] = asset_id
        if action in {"create_asset", "update_asset"}:
            fields = {key: value for key, value in (raw.get("fields") or {}).items() if key in ASSET_FIELDS and value not in (None, "", [])}
            if fields.get("asset_type"):
                fields["asset_type"] = normalize_asset_type(fields["asset_type"], default="land")
            if action == "update_asset":
                fields = {key: value for key, value in fields.items() if key in UPDATE_FIELDS}
            if not fields:
                continue
            if action == "create_asset":
                fields["source"] = fields.get("source") or "chatbot"
                fields["status"] = fields.get("status") or "lead"
                clean["missing_fields"] = raw.get("missing_fields") or _missing_fields(fields)
            clean["fields"] = fields
        elif action == "add_update":
            if not raw.get("update_text"):
                continue
            clean["update_type"] = raw.get("update_type") or "note"
            clean["update_text"] = str(raw["update_text"])
        elif action == "add_contact":
            contact = raw.get("contact") or raw.get("fields") or {}
            name = str(contact.get("name") or raw.get("name") or "").strip()
            if not name:
                continue
            clean["contact"] = {key: value for key, value in contact.items() if value not in (None, "", [])}
            clean["contact"]["name"] = name
            role = raw.get("relationship_type") or raw.get("role") or "related"
            clean["relationship_type"] = role if role in CONTACT_ROLES else "related"
            if raw.get("relationship_notes"):
                clean["relationship_notes"] = raw["relationship_notes"]
        elif action == "add_document":
            file_name = raw.get("file_name")
            if upload_names and file_name not in uploads:
                file_name = upload_names[0]
            if not file_name:
                continue
            clean["file_name"] = file_name
            clean["document_name"] = raw.get("document_name") or file_name
            clean["document_type"] = raw.get("document_type") or "uploaded"
            if raw.get("notes"):
                clean["notes"] = raw["notes"]
        actions.append({key: value for key, value in clean.items() if value not in (None, "", [], {})})
    return {
        "summary": plan.get("summary") or "Prepared copilot actions.",
        "answer": plan.get("answer"),
        "actions": actions,
        "matched_assets": candidates[:8],
        "requires_confirmation": True,
        "upload_names": upload_names,
    }


def _attention_report(db: Session) -> str:
    assets = db.scalars(
        select(models.Asset)
        .options(selectinload(models.Asset.owner), selectinload(models.Asset.broker))
        .order_by(models.Asset.updated_at.desc())
        .limit(250)
    ).all()
    issues: list[str] = []
    for asset in assets:
        missing: list[str] = []
        if not asset.locality and not asset.address:
            missing.append("location")
        if asset.latitude is None or asset.longitude is None:
            missing.append("map pin")
        if not asset.asking_price and not asset.expected_price:
            missing.append("price")
        if not asset.owner:
            missing.append("owner")
        if not asset.broker:
            missing.append("broker")
        if not asset.land_area and asset.asset_type in {"land", "jv"}:
            missing.append("area")
        if not asset.workability_rating:
            missing.append("workability")
        if not asset.bottleneck_notes:
            missing.append("bottleneck notes")
        if missing:
            issues.append(f"{asset.asset_code or asset.id}: {asset.title} needs {', '.join(missing[:5])}.")
        if len(issues) >= 12:
            break
    if not issues:
        return "I did not find obvious missing-data issues in the latest checked assets."
    return "Priority cleanup list:\n" + "\n".join(f"- {line}" for line in issues)


def plan_copilot_message(db: Session, message: str, upload_names: list[str], user: str | None = None) -> dict[str, Any]:
    candidates = _candidate_assets(db, message)
    plan = _openai_plan(message, candidates, upload_names) or _fallback_plan(message, candidates, upload_names)
    validated = _validate_plan(plan, candidates, upload_names)
    previous_fields = _previous_create_fields(message)
    if previous_fields:
        for action in validated["actions"]:
            if action.get("action") == "create_asset":
                action["fields"] = {**previous_fields, **(action.get("fields") or {})}
                action["missing_fields"] = _missing_fields(action["fields"])
    compact_actions: list[dict[str, Any]] = []
    missing_report_added = False
    for action in validated["actions"]:
        if action.get("action") == "missing_info_report":
            if missing_report_added:
                continue
            missing_report_added = True
        compact_actions.append(action)
    validated["actions"] = compact_actions
    wants_create = any(token in message.lower() for token in ["create", "add new", "new property", "new deal", "ingest"])
    fallback_create_fields = _fallback_extract(message) if wants_create else {}
    if wants_create and not any(action.get("action") == "create_asset" for action in validated["actions"]):
        extracted = fallback_create_fields
        if extracted.get("title"):
            validated["actions"].insert(
                0,
                {
                    "action": "create_asset",
                    "fields": extracted,
                    "missing_fields": _missing_fields(extracted),
                    "reason": "User explicitly asked to create a new property; extracted structured fields from the message.",
                },
            )
    for action in validated["actions"]:
        if action.get("action") != "create_asset":
            continue
        fields = {**fallback_create_fields, **(action.get("fields") or {})}
        raw_source = fields.get("raw_source") if isinstance(fields.get("raw_source"), dict) else {}
        copilot_source = _copilot_raw_source(message, fields)
        fields["raw_source"] = {**raw_source, **copilot_source}
        action["fields"] = fields
        action["missing_fields"] = _missing_fields(fields)
    if any(action.get("action") == "missing_info_report" for action in validated["actions"]):
        validated["answer"] = _attention_report(db)
    if upload_names and not any(action.get("action") == "add_document" for action in validated["actions"]):
        target_id = None
        for action in validated["actions"]:
            if action.get("asset_id"):
                target_id = action["asset_id"]
                break
        if target_id or any(action.get("action") == "create_asset" for action in validated["actions"]):
            for name in upload_names:
                document_action = {
                    "action": "add_document",
                    "file_name": name,
                    "document_name": name,
                    "document_type": "uploaded",
                    "reason": "Attach uploaded file through Property Copilot.",
                }
                if target_id:
                    document_action["asset_id"] = target_id
                validated["actions"].append(document_action)
    db.add(models.AiQueryLog(question=f"COPILOT PLAN: {message}", answer=validated["summary"], source_rows=validated.get("matched_assets", []), asked_by=user))
    db.commit()
    return validated


def save_uploads(files: list[Any] | None) -> dict[str, dict[str, str]]:
    saved: dict[str, dict[str, str]] = {}
    upload_dir = Path("data/copilot_uploads") / datetime.now(timezone.utc).strftime("%Y%m%d")
    upload_dir.mkdir(parents=True, exist_ok=True)
    for upload in files or []:
        if not upload.filename:
            continue
        safe_name = re.sub(r"[^A-Za-z0-9._ -]+", "_", upload.filename).strip() or "upload"
        path = upload_dir / f"{uuid4().hex}_{safe_name}"
        with path.open("wb") as handle:
            handle.write(upload.file.read())
        saved[upload.filename] = {"storage_path": str(path), "document_name": upload.filename, "content_type": upload.content_type or ""}
    return saved


def apply_copilot_actions(
    db: Session,
    *,
    message: str,
    actions: list[dict[str, Any]],
    saved_uploads: dict[str, dict[str, str]] | None = None,
    user: str | None = None,
) -> dict[str, Any]:
    saved_uploads = saved_uploads or {}
    applied: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    created_asset_id: int | None = None
    created_approval_id: int | None = None
    for action in actions:
        try:
            name = action.get("action")
            if name == "create_asset":
                fields = dict(action.get("fields") or {})
                if not fields.get("title"):
                    raise ValueError("Create action needs a title.")
                fields["approval_status"] = "pending_review"
                source_uid = f"chatbot:{uuid4().hex}"
                queued = queue_payload(
                    db,
                    source="chatbot",
                    source_uid=source_uid,
                    title=fields.get("title"),
                    payload=fields,
                    created_by_source="Property Copilot",
                )
                if not queued:
                    db.rollback()
                    applied.append({"action": "queue_create_asset", "status": "skipped_duplicate", "title": fields.get("title")})
                    continue
                db.commit()
                queued_item = db.scalar(
                    select(models.ApprovalQueue).where(
                        models.ApprovalQueue.source == "chatbot",
                        models.ApprovalQueue.source_uid == source_uid,
                    )
                )
                created_approval_id = queued_item.id if queued_item else None
                applied.append(
                    {
                        "action": "queue_create_asset",
                        "approval_id": created_approval_id,
                        "title": fields.get("title"),
                        "status": "pending_approval",
                    }
                )
            elif name == "update_asset":
                asset = db.get(models.Asset, int(action["asset_id"]))
                if not asset:
                    raise ValueError("Asset not found.")
                fields = {key: value for key, value in (action.get("fields") or {}).items() if key in UPDATE_FIELDS}
                updated = update_asset(db, asset, AssetUpdate(**fields))
                applied.append({"action": "update_asset", "asset_id": updated.id, "fields": fields})
            elif name == "add_update":
                asset_id = int(action.get("asset_id") or created_asset_id or 0)
                if not asset_id and created_approval_id:
                    item = db.get(models.ApprovalQueue, created_approval_id)
                    if not item:
                        raise ValueError("Approval item not found for queued update.")
                    _append_to_pending_payload(
                        item,
                        "pending_updates",
                        {
                            "update_type": action.get("update_type") or "note",
                            "update_text": action["update_text"],
                            "created_by": user,
                        },
                    )
                    db.commit()
                    applied.append({"action": "queue_update", "approval_id": created_approval_id})
                    continue
                if not db.get(models.Asset, asset_id):
                    raise ValueError("Asset not found for update.")
                update = models.AssetUpdate(
                    asset_id=asset_id,
                    update_type=action.get("update_type") or "note",
                    update_text=action["update_text"],
                    created_by=user,
                )
                db.add(update)
                db.commit()
                db.refresh(update)
                applied.append({"action": "add_update", "asset_id": asset_id, "update_id": update.id})
            elif name == "add_contact":
                asset_id = int(action.get("asset_id") or created_asset_id or 0)
                contact_payload = action.get("contact") or {}
                contact_name = (contact_payload.get("name") or "").strip()
                if not asset_id and created_approval_id and contact_name:
                    item = db.get(models.ApprovalQueue, created_approval_id)
                    if not item:
                        raise ValueError("Approval item not found for queued contact.")
                    _append_to_pending_payload(
                        item,
                        "pending_contacts",
                        {
                            **contact_payload,
                            "relationship_type": action.get("relationship_type") or contact_payload.get("relationship_type") or "related",
                            "relationship_notes": action.get("relationship_notes"),
                        },
                    )
                    db.commit()
                    applied.append({"action": "queue_contact", "approval_id": created_approval_id, "name": contact_name})
                    continue
                if not db.get(models.Asset, asset_id) or not contact_name:
                    raise ValueError("Asset and contact name are required.")
                contact = db.scalar(select(models.Contact).where(models.Contact.name.ilike(contact_name)))
                if not contact:
                    contact = models.Contact(
                        name=contact_name,
                        company=contact_payload.get("company"),
                        phone=contact_payload.get("phone"),
                        whatsapp=contact_payload.get("whatsapp"),
                        email=contact_payload.get("email"),
                        notes=contact_payload.get("notes"),
                    )
                    db.add(contact)
                    db.flush()
                relationship_type = action.get("relationship_type") or "related"
                link = db.scalar(
                    select(models.AssetContact).where(
                        models.AssetContact.asset_id == asset_id,
                        models.AssetContact.contact_id == contact.id,
                        models.AssetContact.relationship_type == relationship_type,
                    )
                )
                if not link:
                    link = models.AssetContact(
                        asset_id=asset_id,
                        contact_id=contact.id,
                        relationship_type=relationship_type,
                        notes=action.get("relationship_notes"),
                    )
                    db.add(link)
                elif action.get("relationship_notes"):
                    link.notes = action["relationship_notes"]
                db.commit()
                applied.append({"action": "add_contact", "asset_id": asset_id, "contact_id": contact.id})
            elif name == "add_document":
                asset_id = int(action.get("asset_id") or created_asset_id or 0)
                file_meta = saved_uploads.get(action.get("file_name"), {})
                document_payload = {
                    "document_name": action.get("document_name") or file_meta.get("document_name") or action.get("file_name") or "Copilot upload",
                    "document_type": action.get("document_type") or "uploaded",
                    "storage_path": file_meta.get("storage_path"),
                    "notes": action.get("notes"),
                }
                if not asset_id and created_approval_id:
                    item = db.get(models.ApprovalQueue, created_approval_id)
                    if not item:
                        raise ValueError("Approval item not found for queued document.")
                    _append_to_pending_payload(item, "documents", document_payload)
                    db.commit()
                    applied.append({"action": "queue_document", "approval_id": created_approval_id, "document_name": document_payload["document_name"]})
                    continue
                if not db.get(models.Asset, asset_id):
                    raise ValueError("Asset not found for document.")
                document = models.AssetDocument(
                    asset_id=asset_id,
                    document_name=document_payload["document_name"],
                    document_type=document_payload["document_type"],
                    storage_path=document_payload["storage_path"],
                    notes=document_payload["notes"],
                )
                db.add(document)
                db.commit()
                db.refresh(document)
                applied.append({"action": "add_document", "asset_id": asset_id, "document_id": document.id})
        except Exception as exc:
            db.rollback()
            failed.append({"action": action, "error": str(exc)})
    answer = f"Applied {len(applied)} copilot action(s). Failed {len(failed)}."
    db.add(models.AiQueryLog(question=f"COPILOT APPLY: {message}", answer=answer, source_rows=applied + failed, asked_by=user))
    db.commit()
    return {"applied_count": len(applied), "failed_count": len(failed), "applied": applied, "failed": failed}
