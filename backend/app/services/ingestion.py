from __future__ import annotations

import hashlib
import re
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.app import models


def clean_cell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip().lower())


def normalize_asset_type(value: Any, default: str = "land") -> str:
    if not value:
        return default
    cleaned = normalize_text(value)
    if "jv" in cleaned or "joint venture" in cleaned:
        return "jv"
    if "resale" in cleaned:
        return "resale_unit"
    if "rent" in cleaned or "lease" in cleaned:
        return "rental"
    if "commercial" in cleaned:
        return "commercial"
    if "brokerage" in cleaned or "sale" in cleaned:
        return "brokerage_listing"
    if any(token in cleaned for token in ["plot", "farm", "project", "land"]):
        return "land"
    return cleaned.replace(" ", "_")


def classify_property(payload: dict[str, Any], *, source_name: str | None = None) -> str:
    """Classify an ingested lead into the three operating buckets."""
    source_text = normalize_text(source_name or payload.get("created_by_source") or payload.get("source"))
    text_parts = [
        source_text,
        payload.get("title"),
        payload.get("asset_type"),
        payload.get("status"),
        payload.get("locality"),
        payload.get("area_name"),
        payload.get("address"),
        payload.get("bottleneck_notes"),
        payload.get("legal_status"),
        payload.get("zoning_status"),
        payload.get("key_people"),
        payload.get("raw_source"),
    ]
    text = " ".join(normalize_text(part) for part in text_parts if part is not None)

    if "brokerage new deals" in text or "brokerage" in text:
        return "brokerage"
    if any(token in text for token in ["mandate", "commission", "listing", "brokerage opportunity"]):
        return "brokerage"
    if any(token in text for token in ["resale", "sale opportunity", "seller", "buyer requirement"]):
        return "brokerage"
    if any(token in text for token in ["joint venture", "jv", "revenue share", "development agreement"]):
        return "joint_venture"
    if any(token in text for token in ["landowner", "developer share", "built up share", "area share"]):
        return "joint_venture"
    return "land_purchase"


def asset_type_for_classification(classification: str) -> str:
    if classification == "brokerage":
        return "brokerage_listing"
    if classification == "joint_venture":
        return "jv"
    return "land"


def is_blank_row(raw: dict[str, Any]) -> bool:
    return not any(clean_cell(value) not in (None, 0, 0.0) for value in raw.values())


def parse_coordinates(raw: dict[str, Any]) -> tuple[float | None, float | None]:
    for key, value in raw.items():
        key_lower = normalize_text(key)
        if not value or not any(token in key_lower for token in ["coordinate", "lat", "long", "location pin"]):
            continue
        text = str(value).replace(" ", "")
        if "," not in text:
            continue
        left, right = text.split(",", 1)
        try:
            return float(left), float(right)
        except ValueError:
            continue
    return None, None


def dedupe_fingerprint(payload: dict[str, Any]) -> str:
    parts = [
        payload.get("title"),
        payload.get("locality"),
        payload.get("area_name"),
        payload.get("district"),
        payload.get("address"),
        payload.get("land_area"),
    ]
    normalized = "|".join(normalize_text(part) for part in parts if normalize_text(part))
    if not normalized:
        normalized = normalize_text(payload)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def row_uid(source: str, source_label: str, row_number: int, raw: dict[str, Any]) -> str:
    stable_keys = ["id", "property id", "asset code", "title", "property", "property name", "location", "address"]
    lower = {normalize_text(key): value for key, value in raw.items()}
    stable = {key: clean_cell(lower.get(key)) for key in stable_keys if lower.get(key)}
    basis = stable or raw
    digest = hashlib.sha256(f"{source}:{source_label}:{row_number}:{basis}".encode("utf-8")).hexdigest()[:24]
    return f"{source}:{digest}"


def candidate_exists(db: Session, source: str, source_uid: str | None, payload: dict[str, Any]) -> bool:
    if source_uid:
        existing = db.scalar(
            select(models.ApprovalQueue.id).where(
                models.ApprovalQueue.source == source,
                models.ApprovalQueue.source_uid == source_uid,
            )
        )
        if existing:
            return True

    fingerprint = payload.get("dedupe_fingerprint") or dedupe_fingerprint(payload)
    existing_queue = db.scalars(select(models.ApprovalQueue)).all()
    for item in existing_queue:
        queued_payload = item.edited_payload or item.payload or {}
        if queued_payload.get("dedupe_fingerprint") == fingerprint:
            return True

    title = normalize_text(payload.get("title"))
    if not title:
        return False
    locality = normalize_text(payload.get("locality") or payload.get("area_name") or payload.get("address"))
    stmt = select(models.Asset).where(func.lower(models.Asset.title) == title)
    if locality:
        stmt = stmt.where(
            or_(
                func.lower(func.coalesce(models.Asset.locality, "")) == locality,
                func.lower(func.coalesce(models.Asset.area_name, "")) == locality,
                func.lower(func.coalesce(models.Asset.address, "")) == locality,
            )
        )
    return db.scalar(stmt) is not None


def load_dedupe_context(db: Session) -> dict[str, set[Any]]:
    context: dict[str, set[Any]] = {
        "source_uids": set(),
        "fingerprints": set(),
    }
    for item in db.scalars(select(models.ApprovalQueue)).all():
        if item.source_uid:
            context["source_uids"].add((item.source, item.source_uid))
        queued_payload = item.edited_payload or item.payload or {}
        if queued_payload.get("dedupe_fingerprint"):
            context["fingerprints"].add(queued_payload["dedupe_fingerprint"])
        else:
            context["fingerprints"].add(dedupe_fingerprint(queued_payload))
    for asset in db.scalars(select(models.Asset)).all():
        raw_source = asset.raw_source if isinstance(asset.raw_source, dict) else {}
        ingestion_meta = raw_source.get("_ingestion") if isinstance(raw_source, dict) else {}
        if isinstance(ingestion_meta, dict) and ingestion_meta.get("source_uid"):
            context["source_uids"].add((asset.source, ingestion_meta["source_uid"]))
        if isinstance(raw_source, dict) and raw_source.get("dedupe_fingerprint"):
            context["fingerprints"].add(raw_source["dedupe_fingerprint"])
        context["fingerprints"].add(
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
    return context


def queue_payload(
    db: Session,
    *,
    source: str,
    source_uid: str,
    title: str | None,
    payload: dict[str, Any],
    created_by_source: str,
    dedupe_context: dict[str, set[Any]] | None = None,
) -> bool:
    payload["dedupe_fingerprint"] = payload.get("dedupe_fingerprint") or dedupe_fingerprint(payload)
    source_key = (source, source_uid)
    if dedupe_context is not None:
        if source_key in dedupe_context["source_uids"] or payload["dedupe_fingerprint"] in dedupe_context["fingerprints"]:
            return False
    elif candidate_exists(db, source, source_uid, payload):
        return False
    db.add(
        models.ApprovalQueue(
            source=source,
            source_uid=source_uid,
            title=title or payload.get("title"),
            payload=payload,
            status="pending",
            created_by_source=created_by_source,
        )
    )
    if dedupe_context is not None:
        dedupe_context["source_uids"].add(source_key)
        dedupe_context["fingerprints"].add(payload["dedupe_fingerprint"])
    return True
