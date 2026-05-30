from __future__ import annotations

from typing import Any

from sqlalchemy import Float, Select, cast, func, or_, select
from sqlalchemy.orm import Session, selectinload

from backend.app import models
from backend.app.schemas import AssetCreate, AssetUpdate
from backend.app.services.geocode import geocode_address, google_maps_link


def _asset_code(db: Session) -> str:
    next_id = (db.scalar(select(func.count(models.Asset.id))) or 0) + 1
    return f"LJV-{next_id:05d}"


def enrich_location(data: dict[str, Any]) -> dict[str, Any]:
    raw = data.get("raw_source") or {}
    if not isinstance(raw, dict):
        raw = {}
        
    lat = data.get("latitude")
    lon = data.get("longitude")
    address = data.get("address")
    locality = data.get("locality")
    district = data.get("district")
    
    ingestion_meta = raw.get("_ingestion") if isinstance(raw, dict) else {}
    skip_geocode = isinstance(ingestion_meta, dict) and ingestion_meta.get("skip_geocode")

    if lat and lon:
        data["google_maps_link"] = data.get("google_maps_link") or google_maps_link(lat, lon)
    elif address and not skip_geocode:
        address_parts = [
            address,
            locality,
            data.get("tehsil"),
            district,
            data.get("state"),
        ]
        address_str = ", ".join(str(part) for part in address_parts if part)
        try:
            lat, lon = geocode_address(address_str)
            if lat is not None and lon is not None:
                data["latitude"] = lat
                data["longitude"] = lon
                data["google_maps_link"] = google_maps_link(lat, lon)
        except Exception:
            pass
            
    # Location Scoring
    try:
        if skip_geocode:
            return data
        from backend.app.services.geocode import score_location
        score, reason = score_location(
            address=data.get("address"),
            latitude=data.get("latitude"),
            longitude=data.get("longitude"),
            locality=data.get("locality"),
            district=data.get("district")
        )
        raw["location_score"] = score
        raw["location_score_reason"] = reason
        data["raw_source"] = raw
    except Exception:
        pass
        
    return data


def create_asset(db: Session, payload: AssetCreate | dict[str, Any], *, commit: bool = True) -> models.Asset:
    data = payload.model_dump(exclude_unset=True) if isinstance(payload, AssetCreate) else dict(payload)
    asset_columns = {column.name for column in models.Asset.__table__.columns}
    data = {key: value for key, value in data.items() if key in asset_columns}
    data = enrich_location(data)
    data["asset_code"] = data.get("asset_code") or _asset_code(db)
    asset = models.Asset(**data)
    db.add(asset)
    db.flush()
    if asset.latitude is not None or asset.longitude is not None or asset.address:
        db.add(
            models.AssetLocation(
                asset_id=asset.id,
                label="Primary",
                address=asset.address,
                latitude=asset.latitude,
                longitude=asset.longitude,
                google_maps_link=asset.google_maps_link,
            )
        )
    if commit:
        db.commit()
        db.refresh(asset)
    return asset


def update_asset(db: Session, asset: models.Asset, payload: AssetUpdate) -> models.Asset:
    data = payload.model_dump(exclude_unset=True)
    if "latitude" in data or "longitude" in data or "address" in data:
        data = enrich_location(data | {"address": data.get("address", asset.address)})
    for key, value in data.items():
        setattr(asset, key, value)
    db.commit()
    db.refresh(asset)
    return asset


def asset_to_dict(asset: models.Asset) -> dict[str, Any]:
    contacts = [
        {
            "id": link.id,
            "contact_id": link.contact_id,
            "name": link.contact.name if link.contact else None,
            "company": link.contact.company if link.contact else None,
            "phone": link.contact.phone if link.contact else None,
            "whatsapp": link.contact.whatsapp if link.contact else None,
            "email": link.contact.email if link.contact else None,
            "relationship_type": link.relationship_type,
            "notes": link.notes,
        }
        for link in asset.contacts
    ]
    return {
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
        "owner_id": asset.owner_id,
        "broker_id": asset.broker_id,
        "owner_name": asset.owner.name if asset.owner else None,
        "broker_name": asset.broker.name if asset.broker else None,
        "contacts": contacts,
        "people_summary": ", ".join(
            f"{contact.get('name')} ({str(contact.get('relationship_type') or 'related').replace('_', ' ')})"
            for contact in contacts[:5]
            if contact.get("name")
        ),
        "documents": [
            {
                "id": document.id,
                "document_name": document.document_name,
                "document_type": document.document_type,
                "url": document.url,
                "storage_path": document.storage_path,
                "notes": document.notes,
            }
            for document in asset.documents
        ],
        "updates": [
            {
                "id": update.id,
                "update_type": update.update_type,
                "update_text": update.update_text,
                "created_by": update.created_by,
                "created_at": update.created_at.isoformat() if update.created_at else None,
            }
            for update in asset.updates
        ],
        "tags": [tag.tag for tag in asset.tags],
        "locations": [
            {
                "id": location.id,
                "label": location.label,
                "address": location.address,
                "latitude": location.latitude,
                "longitude": location.longitude,
                "google_maps_link": location.google_maps_link,
            }
            for location in asset.locations
        ],
        "workability_rating": asset.workability_rating,
        "bottleneck_rating": asset.bottleneck_rating,
        "bottleneck_notes": asset.bottleneck_notes,
        "legal_status": asset.legal_status,
        "zoning_status": asset.zoning_status,
        "location_score": (asset.raw_source or {}).get("location_score") if isinstance(asset.raw_source, dict) else None,
        "location_score_reason": (asset.raw_source or {}).get("location_score_reason") if isinstance(asset.raw_source, dict) else None,
        "approval_status": asset.approval_status,
        "raw_source": asset.raw_source,
        "created_at": asset.created_at,
        "updated_at": asset.updated_at,
    }


def asset_to_summary_dict(asset: models.Asset) -> dict[str, Any]:
    raw_source = asset.raw_source if isinstance(asset.raw_source, dict) else {}
    people_summary = " | ".join(
        part
        for part in [
            f"Owner: {asset.owner.name}" if asset.owner else "",
            f"Broker: {asset.broker.name}" if asset.broker else "",
        ]
        if part
    )
    return {
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
        "owner_id": asset.owner_id,
        "broker_id": asset.broker_id,
        "owner_name": asset.owner.name if asset.owner else None,
        "broker_name": asset.broker.name if asset.broker else None,
        "contacts": [],
        "people_summary": people_summary,
        "documents": [],
        "updates": [],
        "tags": [],
        "locations": [],
        "workability_rating": asset.workability_rating,
        "bottleneck_rating": asset.bottleneck_rating,
        "bottleneck_notes": asset.bottleneck_notes,
        "legal_status": asset.legal_status,
        "zoning_status": asset.zoning_status,
        "location_score": raw_source.get("location_score"),
        "location_score_reason": raw_source.get("location_score_reason"),
        "approval_status": asset.approval_status,
        "raw_source": None,
        "created_at": asset.created_at,
        "updated_at": asset.updated_at,
    }


def filter_assets(
    db: Session,
    filters: dict[str, Any],
    *,
    summary: bool = False,
    limit: int = 500,
    offset: int = 0,
    search: str | None = None,
    sort: str = "updated_desc",
) -> list[models.Asset]:
    options = [selectinload(models.Asset.owner), selectinload(models.Asset.broker)]
    if not summary:
        options.extend(
            [
                selectinload(models.Asset.contacts).selectinload(models.AssetContact.contact),
                selectinload(models.Asset.documents),
                selectinload(models.Asset.updates),
                selectinload(models.Asset.tags),
                selectinload(models.Asset.locations),
            ]
        )
    stmt: Select = select(models.Asset).options(*options)
    for field in [
        "asset_type",
        "district",
        "tehsil",
        "locality",
        "source",
        "status",
        "owner_id",
        "broker_id",
        "approval_status",
    ]:
        value = filters.get(field)
        if value not in (None, ""):
            stmt = stmt.where(getattr(models.Asset, field) == value)
    workability_rating = filters.get("workability_rating")
    if workability_rating not in (None, ""):
        stmt = stmt.where(models.Asset.workability_rating >= int(workability_rating))
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            or_(
                models.Asset.asset_code.ilike(like),
                models.Asset.title.ilike(like),
                models.Asset.district.ilike(like),
                models.Asset.tehsil.ilike(like),
                models.Asset.locality.ilike(like),
                models.Asset.area_name.ilike(like),
                models.Asset.source.ilike(like),
            )
        )
    contact_id = filters.get("contact_id")
    relationship_type = filters.get("relationship_type")
    if contact_id not in (None, "") or relationship_type not in (None, ""):
        stmt = stmt.join(models.AssetContact)
        if contact_id not in (None, ""):
            stmt = stmt.where(models.AssetContact.contact_id == int(contact_id))
        if relationship_type not in (None, ""):
            stmt = stmt.where(models.AssetContact.relationship_type == relationship_type)
    if sort == "workability_desc":
        stmt = stmt.order_by(models.Asset.workability_rating.desc().nullslast(), models.Asset.updated_at.desc())
    elif sort == "price_desc":
        stmt = stmt.order_by(models.Asset.asking_price.desc().nullslast(), models.Asset.updated_at.desc())
    elif sort == "title_asc":
        stmt = stmt.order_by(models.Asset.title.asc())
    else:
        stmt = stmt.order_by(models.Asset.updated_at.desc())
    stmt = stmt.offset(max(0, offset)).limit(max(1, min(limit, 700)))
    return list(db.scalars(stmt))


def filter_asset_summaries(
    db: Session,
    filters: dict[str, Any],
    *,
    limit: int = 500,
    offset: int = 0,
    search: str | None = None,
    sort: str = "updated_desc",
) -> list[dict[str, Any]]:
    location_score = cast(models.Asset.raw_source.op("->>")("location_score"), Float).label("location_score")
    stmt = (
        select(
            models.Asset.id,
            models.Asset.asset_code,
            models.Asset.title,
            models.Asset.asset_type,
            models.Asset.status,
            models.Asset.source,
            models.Asset.locality,
            models.Asset.area_name,
            models.Asset.tehsil,
            models.Asset.district,
            models.Asset.latitude,
            models.Asset.longitude,
            models.Asset.google_maps_link,
            models.Asset.asking_price,
            models.Asset.expected_price,
            models.Asset.owner_id,
            models.Asset.broker_id,
            models.Asset.workability_rating,
            models.Asset.bottleneck_rating,
            models.Asset.approval_status,
            models.Asset.created_at,
            models.Asset.updated_at,
            models.Owner.name.label("owner_name"),
            models.Broker.name.label("broker_name"),
            location_score,
        )
        .select_from(models.Asset)
        .outerjoin(models.Owner, models.Owner.id == models.Asset.owner_id)
        .outerjoin(models.Broker, models.Broker.id == models.Asset.broker_id)
    )
    for field in [
        "asset_type",
        "district",
        "tehsil",
        "locality",
        "source",
        "status",
        "owner_id",
        "broker_id",
        "approval_status",
    ]:
        value = filters.get(field)
        if value not in (None, ""):
            stmt = stmt.where(getattr(models.Asset, field) == value)
    workability_rating = filters.get("workability_rating")
    if workability_rating not in (None, ""):
        stmt = stmt.where(models.Asset.workability_rating >= int(workability_rating))
    contact_id = filters.get("contact_id")
    relationship_type = filters.get("relationship_type")
    if contact_id not in (None, "") or relationship_type not in (None, ""):
        stmt = stmt.join(models.AssetContact, models.AssetContact.asset_id == models.Asset.id)
        if contact_id not in (None, ""):
            stmt = stmt.where(models.AssetContact.contact_id == int(contact_id))
        if relationship_type not in (None, ""):
            stmt = stmt.where(models.AssetContact.relationship_type == relationship_type)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            or_(
                models.Asset.asset_code.ilike(like),
                models.Asset.title.ilike(like),
                models.Asset.district.ilike(like),
                models.Asset.tehsil.ilike(like),
                models.Asset.locality.ilike(like),
                models.Asset.area_name.ilike(like),
                models.Asset.source.ilike(like),
            )
        )
    if sort == "workability_desc":
        stmt = stmt.order_by(models.Asset.workability_rating.desc().nullslast(), models.Asset.updated_at.desc())
    elif sort == "price_desc":
        stmt = stmt.order_by(models.Asset.asking_price.desc().nullslast(), models.Asset.updated_at.desc())
    elif sort == "title_asc":
        stmt = stmt.order_by(models.Asset.title.asc())
    else:
        stmt = stmt.order_by(models.Asset.updated_at.desc())
    rows = db.execute(stmt.offset(max(0, offset)).limit(max(1, min(limit, 700)))).mappings().all()
    summaries: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["people_summary"] = " | ".join(
            part
            for part in [
                f"Owner: {item['owner_name']}" if item.get("owner_name") else "",
                f"Broker: {item['broker_name']}" if item.get("broker_name") else "",
            ]
            if part
        )
        summaries.append(item)
    return summaries
