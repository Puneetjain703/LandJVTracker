from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from backend.app import models
from backend.app.config import get_settings
from backend.app.schemas import AssetUpdate
from backend.app.services.assets import asset_to_dict, update_asset


ALLOWED_UPDATE_FIELDS = {
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
    "workability_rating",
    "bottleneck_rating",
    "bottleneck_notes",
    "legal_status",
    "zoning_status",
    "approval_status",
}
ALLOWED_ACTIONS = {"update_asset", "add_update", "answer"}


def _serialize_asset(asset: models.Asset) -> dict[str, Any]:
    row = asset_to_dict(asset)
    return {key: str(value) if value is not None and not isinstance(value, (int, float, str, bool, list, dict)) else value for key, value in row.items()}


def _candidate_assets(db: Session, instruction: str) -> list[dict[str, Any]]:
    id_matches = [int(match) for match in re.findall(r"\basset\s+#?(\d+)\b|\bid\s+#?(\d+)\b", instruction.lower()) for match in match if match]
    code_matches = re.findall(r"\bLJV-\d+\b", instruction, flags=re.I)
    tokens = [token for token in re.findall(r"[A-Za-z0-9_]+", instruction.lower()) if len(token) > 2]
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
        exact_clauses = []
        if id_matches:
            exact_clauses.append(models.Asset.id.in_(id_matches))
        if code_matches:
            exact_clauses.append(models.Asset.asset_code.in_([code.upper() for code in code_matches]))
        stmt = stmt.where(or_(*exact_clauses)).order_by(models.Asset.updated_at.desc()).limit(25)
        return [_serialize_asset(asset) for asset in db.scalars(stmt)]
    clauses = []
    for token in tokens[:10]:
        like = f"%{token}%"
        clauses.extend(
            [
                models.Asset.title.ilike(like),
                models.Asset.asset_code.ilike(like),
                models.Asset.locality.ilike(like),
                models.Asset.area_name.ilike(like),
                models.Asset.district.ilike(like),
                models.Asset.status.ilike(like),
                models.Asset.bottleneck_notes.ilike(like),
            ]
        )
    if clauses:
        stmt = stmt.where(or_(*clauses))
    stmt = stmt.order_by(models.Asset.updated_at.desc()).limit(25)
    return [_serialize_asset(asset) for asset in db.scalars(stmt)]


def _fallback_plan(instruction: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    target = candidates[0] if len(candidates) == 1 else None
    text = instruction.strip()
    if target:
        fields: dict[str, Any] = {}
        workability = re.search(r"workability(?: rating| score)?\D+(\d{1,2})", instruction, flags=re.I)
        bottleneck = re.search(r"bottleneck(?: rating| score)?\D+(\d{1,2})", instruction, flags=re.I)
        price = re.search(r"(?:asking price|price)\D+([0-9]+(?:\.[0-9]+)?)", instruction, flags=re.I)
        status = re.search(r"status(?: to| as| is)?\s+([A-Za-z_ -]+)", instruction, flags=re.I)
        if workability:
            fields["workability_rating"] = max(0, min(10, int(workability.group(1))))
        if bottleneck:
            fields["bottleneck_rating"] = max(0, min(10, int(bottleneck.group(1))))
        if price:
            fields["asking_price"] = float(price.group(1))
        if status:
            fields["status"] = status.group(1).strip().lower().replace(" ", "_")[:80]
        if fields:
            actions.append({"action": "update_asset", "asset_id": target["id"], "fields": fields, "reason": "Parsed from instruction."})
        if "update" in instruction.lower() or "note" in instruction.lower():
            actions.append({"action": "add_update", "asset_id": target["id"], "update_type": "note", "update_text": text, "reason": "Store instruction as timeline update."})
    answer = "I found matching assets. Choose one more specifically if you want me to edit it." if candidates else "I could not find a matching asset."
    return {"summary": "Fallback parser prepared a plan.", "actions": actions, "matched_assets": candidates[:10], "answer": answer, "requires_confirmation": True}


def _openai_plan(instruction: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    settings = get_settings()
    if not settings.openai_api_key:
        return None
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key)
    schema_hint = {
        "summary": "short plan summary",
        "answer": "direct answer if user asked to pull details; null if not needed",
        "actions": [
            {
                "action": "update_asset | add_update | answer",
                "asset_id": 123,
                "fields": {"workability_rating": 8},
                "update_type": "note",
                "update_text": "timeline note",
                "reason": "why this action is appropriate",
            }
        ],
    }
    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a careful internal database agent for a real-estate asset tracker. "
                    "Return only JSON. You may propose read answers, asset field updates, or timeline updates. "
                    "Never propose delete actions. Only use asset ids from the provided candidate rows. "
                    f"Allowed update fields: {sorted(ALLOWED_UPDATE_FIELDS)}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Instruction: {instruction}\n\nCandidate assets JSON:\n"
                    f"{json.dumps(candidates[:15], ensure_ascii=False, default=str)}\n\n"
                    f"Return JSON shaped like: {json.dumps(schema_hint)}"
                ),
            },
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    try:
        data = json.loads(response.choices[0].message.content or "{}")
    except json.JSONDecodeError:
        return None
    return data


def _validate_actions(actions: list[dict[str, Any]], candidate_ids: set[int]) -> list[dict[str, Any]]:
    validated: list[dict[str, Any]] = []
    for action in actions:
        action_name = action.get("action")
        if action_name not in ALLOWED_ACTIONS:
            continue
        if action_name == "answer":
            validated.append({"action": "answer", "text": str(action.get("text") or action.get("reason") or "")})
            continue
        asset_id = action.get("asset_id")
        if not isinstance(asset_id, int) or asset_id not in candidate_ids:
            continue
        if action_name == "update_asset":
            fields = {key: value for key, value in (action.get("fields") or {}).items() if key in ALLOWED_UPDATE_FIELDS and value is not None}
            if fields:
                validated.append({"action": "update_asset", "asset_id": asset_id, "fields": fields, "reason": action.get("reason")})
        if action_name == "add_update" and action.get("update_text"):
            validated.append(
                {
                    "action": "add_update",
                    "asset_id": asset_id,
                    "update_type": action.get("update_type") or "note",
                    "update_text": str(action["update_text"]),
                    "reason": action.get("reason"),
                }
            )
    return validated


def plan_agent_actions(db: Session, instruction: str, asked_by: str | None = None) -> dict[str, Any]:
    candidates = _candidate_assets(db, instruction)
    raw_plan = _openai_plan(instruction, candidates) or _fallback_plan(instruction, candidates)
    candidate_ids = {int(row["id"]) for row in candidates}
    actions = _validate_actions(raw_plan.get("actions") or [], candidate_ids)
    answer = raw_plan.get("answer")
    summary = raw_plan.get("summary") or ("Prepared a database action plan." if actions else "No editable action found.")
    db.add(models.AiQueryLog(question=instruction, answer=summary, source_rows=candidates[:10], asked_by=asked_by))
    db.commit()
    return {
        "summary": summary,
        "actions": actions,
        "matched_assets": candidates[:10],
        "answer": answer,
        "requires_confirmation": True,
    }


def apply_agent_actions(db: Session, instruction: str, actions: list[dict[str, Any]], user: str | None = None) -> dict[str, Any]:
    applied: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for action in actions:
        try:
            asset_id = int(action.get("asset_id"))
            asset = db.get(models.Asset, asset_id)
            if not asset:
                failed.append({"action": action, "error": "Asset not found"})
                continue
            if action.get("action") == "update_asset":
                fields = {key: value for key, value in (action.get("fields") or {}).items() if key in ALLOWED_UPDATE_FIELDS}
                updated = update_asset(db, asset, AssetUpdate(**fields))
                applied.append({"action": "update_asset", "asset_id": updated.id, "fields": fields})
            elif action.get("action") == "add_update":
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
        except Exception as exc:
            db.rollback()
            failed.append({"action": action, "error": str(exc)})
    answer = f"Applied {len(applied)} action(s). Failed {len(failed)}."
    db.add(models.AiQueryLog(question=f"APPLY: {instruction}", answer=answer, source_rows=applied + failed, asked_by=user))
    db.commit()
    return {"applied_count": len(applied), "failed_count": len(failed), "applied": applied, "failed": failed}
