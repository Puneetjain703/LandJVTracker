from __future__ import annotations

import re
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select

from backend.app import models
from backend.app.db import SessionLocal, create_all


def clean_name(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value if item)
    text = re.sub(r"\s+", " ", str(value).strip())
    if not text or text.lower() in {"none", "null", "na", "n/a", "-", "0"}:
        return None
    return text[:255]


def raw_value(raw: dict[str, Any], *names: str) -> Any:
    lookup = {str(key).strip().lower(): value for key, value in raw.items()}
    for name in names:
        value = lookup.get(name.lower())
        if value not in (None, ""):
            return value
    return None


def split_people(value: Any) -> list[str]:
    text = clean_name(value)
    if not text:
        return []
    parts = re.split(r"\s*(?:,|/|;|\||\band\b|&)\s*", text, flags=re.I)
    return [part for part in (clean_name(part) for part in parts) if part]


def get_or_create_contact(db, name: str, *, company: str | None = None, notes: str | None = None) -> models.Contact:
    contact = db.scalar(
        select(models.Contact).where(
            func.lower(models.Contact.name) == name.lower(),
            func.coalesce(models.Contact.company, "") == (company or ""),
        )
    )
    if not contact:
        contact = models.Contact(name=name, company=company, notes=notes)
        db.add(contact)
        db.flush()
    elif notes and not contact.notes:
        contact.notes = notes
    return contact


def link_contact(db, asset_id: int, contact_id: int, role: str, notes: str | None = None) -> bool:
    exists = db.scalar(
        select(models.AssetContact).where(
            models.AssetContact.asset_id == asset_id,
            models.AssetContact.contact_id == contact_id,
            models.AssetContact.relationship_type == role,
        )
    )
    if exists:
        return False
    db.add(models.AssetContact(asset_id=asset_id, contact_id=contact_id, relationship_type=role, notes=notes))
    return True


def main() -> None:
    create_all()
    created_links = 0
    with SessionLocal() as db:
        assets = db.scalars(select(models.Asset)).all()
        for asset in assets:
            raw = asset.raw_source if isinstance(asset.raw_source, dict) else {}
            payload = raw.get("source_payload") if isinstance(raw.get("source_payload"), dict) else {}

            candidates: list[tuple[str, str, str | None]] = []
            if asset.owner:
                candidates.append((asset.owner.name, "landowner", "Linked from owner_id"))
            if asset.broker:
                candidates.append((asset.broker.name, "broker", "Linked from broker_id"))

            owner_name = clean_name(payload.get("owner_name") or raw_value(raw, "OWNER", "OWNER ", "Owner", "Seller"))
            broker_name = clean_name(payload.get("broker_name") or raw_value(raw, "BROKER", "Broker", "Reference", "Referrer"))
            if owner_name:
                candidates.append((owner_name, "landowner", "Backfilled from source owner/seller field"))
            if broker_name:
                candidates.append((broker_name, "broker", "Backfilled from source broker/referrer field"))

            for person in split_people(payload.get("key_people") or raw_value(raw, "Key People / Referrer / Owner", "Key People", "Parties")):
                candidates.append((person, "possible_partner", "Backfilled from key people/source parties"))

            bank_name = clean_name(raw_value(raw, "Bank", "Banker", "Bank Name", "Funding Bank") or payload.get("bank_name"))
            financier_name = clean_name(raw_value(raw, "Financier", "Finance", "Funding", "Investor") or payload.get("financier_name"))
            if bank_name:
                candidates.append((bank_name, "bank", "Backfilled from bank/funding source field"))
            if financier_name:
                candidates.append((financier_name, "financier", "Backfilled from finance/investor source field"))

            seen: set[tuple[str, str]] = set()
            for name, role, notes in candidates:
                key = (name.lower(), role)
                if key in seen:
                    continue
                seen.add(key)
                contact = get_or_create_contact(db, name, notes=notes)
                if link_contact(db, asset.id, contact.id, role, notes):
                    created_links += 1
        db.commit()
    print({"assets_scanned": len(assets), "links_created": created_links})


if __name__ == "__main__":
    main()
