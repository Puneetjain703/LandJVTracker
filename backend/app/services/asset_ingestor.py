from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app import models
from backend.app.config import get_settings
from backend.app.schemas import AssetCreate
from backend.app.services.assets import create_asset
from backend.app.services.ingestion import (
    asset_type_for_classification,
    classify_property,
    dedupe_fingerprint,
    queue_payload,
)


ASSET_FIELD_NAMES = set(AssetCreate.model_fields)


def _clean_name(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value if item)
    text = str(value).strip()
    return text or None


def _get_or_create_owner(db: Session, name: Any) -> models.Owner | None:
    owner_name = _clean_name(name)
    if not owner_name:
        return None
    owner = db.scalar(select(models.Owner).where(func.lower(models.Owner.name) == owner_name.lower()))
    if not owner:
        owner = models.Owner(name=owner_name)
        db.add(owner)
        db.flush()
    return owner


def _get_or_create_broker(db: Session, name: Any) -> models.Broker | None:
    broker_name = _clean_name(name)
    if not broker_name:
        return None
    broker = db.scalar(select(models.Broker).where(func.lower(models.Broker.name) == broker_name.lower()))
    if not broker:
        broker = models.Broker(name=broker_name)
        db.add(broker)
        db.flush()
    return broker


def _get_or_create_contact(db: Session, payload: dict[str, Any]) -> models.Contact | None:
    contact_name = _clean_name(payload.get("name") or payload.get("contact_name"))
    if not contact_name:
        return None
    contact = None
    if payload.get("email"):
        contact = db.scalar(select(models.Contact).where(models.Contact.email == payload["email"]))
    if not contact and payload.get("phone"):
        contact = db.scalar(select(models.Contact).where(models.Contact.phone == payload["phone"]))
    if not contact:
        contact = db.scalar(select(models.Contact).where(func.lower(models.Contact.name) == contact_name.lower()))
    if not contact:
        contact = models.Contact(
            name=contact_name,
            company=payload.get("company"),
            phone=payload.get("phone"),
            whatsapp=payload.get("whatsapp"),
            email=payload.get("email"),
            notes=payload.get("notes"),
        )
        db.add(contact)
        db.flush()
    else:
        for field in ["company", "phone", "whatsapp", "email", "notes"]:
            if payload.get(field) and not getattr(contact, field):
                setattr(contact, field, payload[field])
    return contact


def _asset_duplicate_exists(db: Session, payload: dict[str, Any]) -> bool:
    fingerprint = payload.get("dedupe_fingerprint") or dedupe_fingerprint(payload)
    for asset in db.scalars(select(models.Asset)).all():
        raw_source = asset.raw_source if isinstance(asset.raw_source, dict) else {}
        if raw_source.get("dedupe_fingerprint") == fingerprint:
            return True
        asset_fingerprint = dedupe_fingerprint(
            {
                "title": asset.title,
                "locality": asset.locality,
                "area_name": asset.area_name,
                "district": asset.district,
                "address": asset.address,
                "land_area": asset.land_area,
            }
        )
        if asset_fingerprint == fingerprint:
            return True
    return False


def prepare_asset_payload(
    payload: dict[str, Any],
    *,
    source: str,
    source_uid: str | None = None,
    source_name: str | None = None,
    skip_geocode: bool = True,
) -> dict[str, Any]:
    prepared = dict(payload)
    classification = classify_property(prepared, source_name=source_name)
    prepared["source_classification"] = classification
    prepared["asset_type"] = asset_type_for_classification(classification)
    prepared["approval_status"] = "approved"
    prepared["source"] = prepared.get("source") or source
    prepared["dedupe_fingerprint"] = prepared.get("dedupe_fingerprint") or dedupe_fingerprint(prepared)

    raw_source = prepared.get("raw_source") if isinstance(prepared.get("raw_source"), dict) else {}
    prepared["raw_source"] = {
        **raw_source,
        "dedupe_fingerprint": prepared["dedupe_fingerprint"],
        "source_classification": classification,
        "_ingestion": {
            "source": source,
            "source_uid": source_uid,
            "source_name": source_name,
            "auto_published": True,
            "skip_geocode": skip_geocode,
        },
    }
    return prepared


def create_asset_from_ingested_payload(
    db: Session,
    payload: dict[str, Any],
    *,
    source: str,
    source_uid: str | None = None,
    source_name: str | None = None,
    created_by: str = "auto_ingest",
    commit: bool = True,
    skip_geocode: bool = True,
) -> models.Asset:
    prepared = prepare_asset_payload(
        payload,
        source=source,
        source_uid=source_uid,
        source_name=source_name,
        skip_geocode=skip_geocode,
    )
    asset_payload = {key: value for key, value in prepared.items() if key in ASSET_FIELD_NAMES}

    owner = _get_or_create_owner(db, prepared.get("owner_name"))
    if owner and not asset_payload.get("owner_id"):
        asset_payload["owner_id"] = owner.id
    broker = _get_or_create_broker(db, prepared.get("broker_name"))
    if broker and not asset_payload.get("broker_id"):
        asset_payload["broker_id"] = broker.id

    if not asset_payload.get("title"):
        raise ValueError("Cannot ingest an asset without a title")

    asset = create_asset(db, asset_payload, commit=False)
    for document in prepared.get("documents", []) or []:
        if not isinstance(document, dict):
            continue
        db.add(
            models.AssetDocument(
                asset_id=asset.id,
                document_name=document.get("document_name") or document.get("name") or document.get("url") or "Imported collateral",
                document_type=document.get("document_type") or document.get("type"),
                url=document.get("url"),
                storage_path=document.get("storage_path"),
                notes=document.get("notes"),
            )
        )
    if prepared.get("bottleneck_notes"):
        db.add(
            models.AssetUpdate(
                asset_id=asset.id,
                update_type="imported_note",
                update_text=str(prepared["bottleneck_notes"]),
                created_by=created_by,
            )
        )
    people: list[dict[str, Any]] = []
    if prepared.get("owner_name"):
        people.append({"name": prepared["owner_name"], "relationship_type": "landowner"})
    if prepared.get("broker_name"):
        people.append({"name": prepared["broker_name"], "relationship_type": "broker"})
    for key in ["contacts", "people", "pending_contacts"]:
        for item in prepared.get(key, []) or []:
            if isinstance(item, dict):
                people.append(item)
    seen_people: set[tuple[str, str]] = set()
    for person in people:
        contact = _get_or_create_contact(db, person)
        if not contact:
            continue
        relationship_type = person.get("relationship_type") or person.get("role") or "related"
        relationship_key = (contact.name.lower(), relationship_type)
        if relationship_key in seen_people:
            continue
        seen_people.add(relationship_key)
        db.add(
            models.AssetContact(
                asset_id=asset.id,
                contact_id=contact.id,
                relationship_type=relationship_type,
                notes=person.get("relationship_notes") or person.get("notes"),
            )
        )
    for key in ["updates", "asset_updates", "pending_updates"]:
        for item in prepared.get(key, []) or []:
            if isinstance(item, dict) and item.get("update_text"):
                db.add(
                    models.AssetUpdate(
                        asset_id=asset.id,
                        update_type=item.get("update_type") or "note",
                        update_text=str(item["update_text"]),
                        created_by=item.get("created_by") or created_by,
                    )
                )
            elif isinstance(item, str) and item.strip():
                db.add(
                    models.AssetUpdate(
                        asset_id=asset.id,
                        update_type="note",
                        update_text=item.strip(),
                        created_by=created_by,
                    )
                )
    if commit:
        db.commit()
        db.refresh(asset)
    else:
        db.flush()
    return asset


def ingest_or_queue_payload(
    db: Session,
    *,
    source: str,
    source_uid: str,
    title: str | None,
    payload: dict[str, Any],
    created_by_source: str,
    dedupe_context: dict[str, set[Any]] | None = None,
) -> tuple[str, models.Asset | None]:
    settings = get_settings()
    if not settings.auto_publish_ingested_assets:
        queued = queue_payload(
            db,
            source=source,
            source_uid=source_uid,
            title=title,
            payload=payload,
            created_by_source=created_by_source,
            dedupe_context=dedupe_context,
        )
        return ("queued" if queued else "skipped", None)

    prepared = prepare_asset_payload(
        payload,
        source=source,
        source_uid=source_uid,
        source_name=created_by_source,
    )
    source_key = (source, source_uid)
    if dedupe_context is not None:
        if source_key in dedupe_context["source_uids"] or prepared["dedupe_fingerprint"] in dedupe_context["fingerprints"]:
            return "skipped", None
    elif _asset_duplicate_exists(db, prepared):
        return "skipped", None

    asset = create_asset_from_ingested_payload(
        db,
        prepared,
        source=source,
        source_uid=source_uid,
        source_name=created_by_source,
    )
    if dedupe_context is not None:
        dedupe_context["source_uids"].add(source_key)
        dedupe_context["fingerprints"].add(prepared["dedupe_fingerprint"])
    return "created", asset


def promote_approval_item_to_asset(
    db: Session,
    item: models.ApprovalQueue,
    *,
    reviewed_by: str = "auto_ingest",
    notes: str | None = None,
    existing_fingerprints: set[str] | None = None,
    asset_code: str | None = None,
    commit: bool = True,
) -> models.Asset | None:
    payload = dict(item.edited_payload or item.payload or {})
    prepared = prepare_asset_payload(
        payload,
        source=item.source,
        source_uid=item.source_uid,
        source_name=item.created_by_source,
    )
    if asset_code and not prepared.get("asset_code"):
        prepared["asset_code"] = asset_code
    is_duplicate = (
        prepared["dedupe_fingerprint"] in existing_fingerprints
        if existing_fingerprints is not None
        else _asset_duplicate_exists(db, prepared)
    )
    if is_duplicate:
        item.status = "approved"
        item.reviewed_by = reviewed_by
        item.reviewed_at = datetime.now(timezone.utc)
        item.approval_decision = "approved_duplicate_skipped"
        item.decision_notes = notes or "Auto-ingest skipped asset creation because an existing asset matched this lead."
        if commit:
            db.commit()
        return None
    asset = create_asset_from_ingested_payload(
        db,
        prepared,
        source=item.source,
        source_uid=item.source_uid,
        source_name=item.created_by_source,
        created_by=reviewed_by,
        commit=False,
    )
    item.status = "approved"
    item.reviewed_by = reviewed_by
    item.reviewed_at = datetime.now(timezone.utc)
    item.approval_decision = "approved"
    item.decision_notes = notes or "Auto-ingested directly from the approval queue."
    if commit:
        db.commit()
    if existing_fingerprints is not None:
        existing_fingerprints.add(prepared["dedupe_fingerprint"])
    return asset
