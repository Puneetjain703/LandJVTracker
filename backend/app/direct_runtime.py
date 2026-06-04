from __future__ import annotations

import json
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Any

from passlib.context import CryptContext
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from backend.app import models
from backend.app.config import get_settings
from backend.app.db import SessionLocal, create_all
from backend.app.schemas import AssetCreate, AssetUpdate
from backend.app.services.ai_assistant import answer_question
from backend.app.services.ai_db_agent import apply_agent_actions, plan_agent_actions
from backend.app.services.asset_ingestor import create_asset_from_ingested_payload
from backend.app.services.assets import asset_to_dict, create_asset, filter_asset_summaries, filter_assets, update_asset
from backend.app.services.excel_importer import import_excel_to_queue
from backend.app.services.exporter import build_export_workbook
from backend.app.services.google_sheets_sync import sync_google_sheets_to_queue
from backend.app.services.property_copilot import apply_copilot_actions, plan_copilot_message, save_uploads
from backend.app.services.source_sync import sync_all_sources, sync_notion_project_sources


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_schema_ready = False


class DirectRuntimeError(RuntimeError):
    def __init__(self, detail: str, status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class SimpleUpload:
    def __init__(self, filename: str, content: bytes, content_type: str | None = None):
        self.filename = filename
        self.file = BytesIO(content)
        self.content_type = content_type or "application/octet-stream"


def _ensure_schema() -> None:
    global _schema_ready
    if not _schema_ready:
        create_all()
        _schema_ready = True


@contextmanager
def _session() -> Any:
    _ensure_schema()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def verify_direct_login(username: str, password: str) -> bool:
    settings = get_settings()
    if username != settings.app_username:
        return False
    if settings.app_password_hash:
        return pwd_context.verify(password, settings.app_password_hash)
    return password == settings.app_password


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _clean_params(params: dict[str, Any] | None) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in (params or {}).items():
        if value in (None, ""):
            continue
        cleaned[key] = value
    return cleaned


def _stats(db: Session) -> dict[str, int]:
    total_assets = db.scalar(select(func.count(models.Asset.id))) or 0
    active_deals = db.scalar(select(func.count(models.Deal.id)).where(models.Deal.status == "active")) or 0
    pending_approvals = (
        db.scalar(select(func.count(models.ApprovalQueue.id)).where(models.ApprovalQueue.status == "pending")) or 0
    )
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    new_leads = db.scalar(select(func.count(models.Asset.id)).where(models.Asset.created_at >= week_ago)) or 0
    return {
        "total_assets": int(total_assets),
        "active_deals": int(active_deals),
        "pending_approvals": int(pending_approvals),
        "new_leads_this_week": int(new_leads),
    }


def _asset(db: Session, asset_id: int) -> models.Asset:
    asset = db.scalar(
        select(models.Asset)
        .where(models.Asset.id == asset_id)
        .options(
            selectinload(models.Asset.owner),
            selectinload(models.Asset.broker),
            selectinload(models.Asset.contacts).selectinload(models.AssetContact.contact),
            selectinload(models.Asset.documents),
            selectinload(models.Asset.updates),
            selectinload(models.Asset.tags),
            selectinload(models.Asset.locations),
        )
    )
    if not asset:
        raise DirectRuntimeError("Asset not found", 404)
    return asset


def _people(db: Session, params: dict[str, Any]) -> list[dict[str, Any]]:
    stmt = select(models.Contact).options(selectinload(models.Contact.asset_links)).order_by(models.Contact.name)
    query = params.get("query")
    if query:
        like = f"%{query}%"
        stmt = stmt.where(
            models.Contact.name.ilike(like)
            | models.Contact.company.ilike(like)
            | models.Contact.phone.ilike(like)
            | models.Contact.email.ilike(like)
        )
    relationship_type = params.get("relationship_type")
    if relationship_type:
        stmt = stmt.join(models.AssetContact).where(models.AssetContact.relationship_type == relationship_type)
    rows = []
    for contact in db.scalars(stmt).unique().all():
        roles = sorted({link.relationship_type for link in contact.asset_links})
        rows.append(
            {
                "id": contact.id,
                "name": contact.name,
                "company": contact.company,
                "phone": contact.phone,
                "whatsapp": contact.whatsapp,
                "email": contact.email,
                "notes": contact.notes,
                "roles": roles,
                "asset_count": len({link.asset_id for link in contact.asset_links}),
            }
        )
    return rows


def _add_asset_contact(db: Session, asset_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    if not db.get(models.Asset, asset_id):
        raise DirectRuntimeError("Asset not found", 404)
    contact_id = payload.get("contact_id")
    contact = db.get(models.Contact, int(contact_id)) if contact_id else None
    if not contact:
        name = (payload.get("name") or "").strip()
        if not name:
            raise DirectRuntimeError("Contact name or contact_id is required")
        if payload.get("email"):
            contact = db.scalar(select(models.Contact).where(models.Contact.email == payload["email"]))
        if not contact and payload.get("phone"):
            contact = db.scalar(select(models.Contact).where(models.Contact.phone == payload["phone"]))
        if not contact:
            contact = db.scalar(
                select(models.Contact).where(
                    func.lower(models.Contact.name) == name.lower(),
                    func.coalesce(models.Contact.company, "") == (payload.get("company") or ""),
                )
            )
        if not contact:
            contact = models.Contact(
                name=name,
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
    relationship_type = payload.get("relationship_type") or payload.get("role") or "related"
    existing_link = db.scalar(
        select(models.AssetContact).where(
            models.AssetContact.asset_id == asset_id,
            models.AssetContact.contact_id == contact.id,
            models.AssetContact.relationship_type == relationship_type,
        )
    )
    if existing_link:
        if payload.get("relationship_notes"):
            existing_link.notes = payload["relationship_notes"]
        db.commit()
        return {"id": existing_link.id, "contact_id": contact.id, "name": contact.name}
    link = models.AssetContact(
        asset_id=asset_id,
        contact_id=contact.id,
        relationship_type=relationship_type,
        notes=payload.get("relationship_notes"),
    )
    db.add(link)
    db.commit()
    return {"id": link.id, "contact_id": contact.id, "name": contact.name}


def _approve_queue_item(
    db: Session,
    item: models.ApprovalQueue,
    payload: dict[str, Any],
    *,
    user: str,
    notes: str | None = None,
) -> models.Asset:
    if item.status != "pending":
        raise DirectRuntimeError("Approval item is not pending")
    if not payload.get("title"):
        raise DirectRuntimeError("Cannot approve without a title")
    asset = create_asset_from_ingested_payload(
        db,
        payload,
        source=item.source,
        source_uid=item.source_uid,
        source_name=item.created_by_source,
        created_by=user,
    )
    item.status = "approved"
    item.reviewed_by = user
    item.reviewed_at = datetime.now(timezone.utc)
    item.approval_decision = "approved"
    item.decision_notes = notes
    db.commit()
    db.refresh(asset)
    return asset


def _upload_from_files(files: Any, field: str = "file") -> SimpleUpload | None:
    if not files:
        return None
    info = files.get(field) if isinstance(files, dict) else None
    if not info:
        return None
    filename, content, content_type = info[0], info[1], info[2] if len(info) > 2 else None
    return SimpleUpload(filename, content, content_type)


def uploads_from_payload(uploads: list[dict[str, Any]] | None) -> list[SimpleUpload]:
    return [
        SimpleUpload(upload["name"], upload["bytes"], upload.get("type"))
        for upload in uploads or []
        if upload.get("name") and upload.get("bytes") is not None
    ]


def direct_bytes(method: str, path: str, *, user: str | None = None, **_: Any) -> bytes:
    if method.upper() != "GET" or path != "/export/excel":
        raise DirectRuntimeError(f"Unsupported direct download route: {method} {path}", 404)
    with _session() as db:
        return build_export_workbook(db)


def direct_form(path: str, data: dict[str, Any], uploads: list[dict[str, Any]] | None = None, *, user: str | None = None) -> Any:
    upload_files = uploads_from_payload(uploads)
    with _session() as db:
        if path == "/copilot/plan":
            message = data.get("message") or ""
            return _jsonable(plan_copilot_message(db, message, [upload.filename for upload in upload_files], user=user))
        if path == "/copilot/apply":
            try:
                actions = json.loads(data.get("actions_json") or "[]")
            except json.JSONDecodeError as exc:
                raise DirectRuntimeError("actions_json must be valid JSON") from exc
            if not isinstance(actions, list):
                raise DirectRuntimeError("actions_json must be a list of actions")
            saved_uploads = save_uploads(upload_files)
            return _jsonable(apply_copilot_actions(db, message=data.get("message") or "", actions=actions, saved_uploads=saved_uploads, user=user))
        if path == "/copilot/transcribe":
            settings = get_settings()
            if not settings.openai_api_key:
                raise DirectRuntimeError("OPENAI_API_KEY is required for voice transcription")
            audio = next((file for file in upload_files if file.filename), None)
            if not audio:
                raise DirectRuntimeError("Upload a voice note to transcribe")
            try:
                from openai import OpenAI

                client = OpenAI(api_key=settings.openai_api_key)
                transcript = client.audio.transcriptions.create(
                    model=settings.openai_transcription_model,
                    file=(audio.filename or "voice-note.wav", audio.file.read(), audio.content_type or "audio/wav"),
                )
                text = getattr(transcript, "text", None) or transcript.model_dump().get("text")
            except Exception as exc:
                raise DirectRuntimeError(f"Voice transcription failed: {exc}") from exc
            return {"text": text or "", "filename": audio.filename}
    raise DirectRuntimeError(f"Unsupported direct form route: {path}", 404)


def direct_request(method: str, path: str, *, params: dict[str, Any] | None = None, json_payload: dict[str, Any] | None = None, files: Any = None, user: str | None = None) -> Any:
    method = method.upper()
    params = _clean_params(params)
    payload = json_payload or {}

    if path == "/health" and method == "GET":
        return {"status": "ok", "app": get_settings().app_name}

    asset_match = re.fullmatch(r"/assets/(\d+)", path)
    contacts_match = re.fullmatch(r"/assets/(\d+)/contacts", path)
    documents_match = re.fullmatch(r"/assets/(\d+)/documents", path)
    updates_match = re.fullmatch(r"/assets/(\d+)/updates", path)
    approval_approve_match = re.fullmatch(r"/approvals/(\d+)/approve", path)
    approval_reject_match = re.fullmatch(r"/approvals/(\d+)/reject", path)

    with _session() as db:
        if path == "/stats" and method == "GET":
            return _stats(db)

        if path in {"/assets", "/assets/summary"} and method == "GET":
            filters = {key: params.get(key) for key in [
                "asset_type",
                "district",
                "tehsil",
                "locality",
                "source",
                "status",
                "owner_id",
                "broker_id",
                "contact_id",
                "relationship_type",
                "workability_rating",
                "approval_status",
            ]}
            limit = int(params.get("limit") or 500)
            offset = int(params.get("offset") or 0)
            sort = params.get("sort") or "updated_desc"
            search = params.get("search")
            if path == "/assets/summary" or params.get("summary"):
                return _jsonable(filter_asset_summaries(db, filters, limit=limit, offset=offset, search=search, sort=sort))
            assets = filter_assets(db, filters, limit=limit, offset=offset, search=search, sort=sort)
            return _jsonable([asset_to_dict(asset) for asset in assets])

        if path == "/assets" and method == "POST":
            return _jsonable(asset_to_dict(create_asset(db, AssetCreate(**payload))))

        if asset_match:
            asset_id = int(asset_match.group(1))
            if method == "GET":
                return _jsonable(asset_to_dict(_asset(db, asset_id)))
            if method == "PUT":
                asset = db.get(models.Asset, asset_id)
                if not asset:
                    raise DirectRuntimeError("Asset not found", 404)
                return _jsonable(asset_to_dict(update_asset(db, asset, AssetUpdate(**payload))))
            if method == "DELETE":
                asset = db.get(models.Asset, asset_id)
                if not asset:
                    raise DirectRuntimeError("Asset not found", 404)
                result = {"status": "deleted", "asset_id": asset_id, "asset_code": asset.asset_code, "title": asset.title}
                db.delete(asset)
                db.commit()
                return result

        if path == "/assets/bulk-delete" and method == "POST":
            deleted: list[dict[str, Any]] = []
            failed: list[dict[str, Any]] = []
            for asset_id in payload.get("asset_ids") or []:
                asset = db.get(models.Asset, int(asset_id))
                if not asset:
                    failed.append({"asset_id": asset_id, "error": "Asset not found"})
                    continue
                deleted.append({"asset_id": asset.id, "asset_code": asset.asset_code, "title": asset.title})
                db.delete(asset)
            db.commit()
            return {"deleted_count": len(deleted), "failed_count": len(failed), "deleted": deleted, "failed": failed}

        if contacts_match and method == "POST":
            return _jsonable(_add_asset_contact(db, int(contacts_match.group(1)), payload))

        if documents_match and method == "POST":
            asset_id = int(documents_match.group(1))
            if not db.get(models.Asset, asset_id):
                raise DirectRuntimeError("Asset not found", 404)
            document = models.AssetDocument(
                asset_id=asset_id,
                document_name=payload["document_name"],
                document_type=payload.get("document_type"),
                url=payload.get("url"),
                storage_path=payload.get("storage_path"),
                notes=payload.get("notes"),
            )
            db.add(document)
            db.commit()
            db.refresh(document)
            return {"id": document.id, "document_name": document.document_name}

        if updates_match and method == "POST":
            asset_id = int(updates_match.group(1))
            if not db.get(models.Asset, asset_id):
                raise DirectRuntimeError("Asset not found", 404)
            update = models.AssetUpdate(
                asset_id=asset_id,
                update_type=payload.get("update_type") or "note",
                update_text=payload["update_text"],
                created_by=user,
            )
            db.add(update)
            db.commit()
            db.refresh(update)
            return {"id": update.id, "update_text": update.update_text}

        if path == "/people" and method == "GET":
            return _jsonable(_people(db, params))

        if path == "/people" and method == "POST":
            name = (payload.get("name") or "").strip()
            if not name:
                raise DirectRuntimeError("Name is required")
            contact = models.Contact(
                name=name,
                company=payload.get("company"),
                phone=payload.get("phone"),
                whatsapp=payload.get("whatsapp"),
                email=payload.get("email"),
                notes=payload.get("notes"),
            )
            db.add(contact)
            db.commit()
            db.refresh(contact)
            return {"id": contact.id, "name": contact.name}

        if path == "/approvals" and method == "GET":
            queue_status = params.get("status") or "pending"
            stmt = select(models.ApprovalQueue)
            if queue_status != "all":
                stmt = stmt.where(models.ApprovalQueue.status == queue_status)
            stmt = stmt.order_by(models.ApprovalQueue.created_at.desc()).limit(500)
            return _jsonable(
                [
                    {
                        "id": item.id,
                        "source": item.source,
                        "source_uid": item.source_uid,
                        "title": item.title,
                        "payload": item.payload,
                        "edited_payload": item.edited_payload,
                        "status": item.status,
                        "created_by_source": item.created_by_source,
                        "reviewed_by": item.reviewed_by,
                        "reviewed_at": item.reviewed_at,
                        "approval_decision": item.approval_decision,
                        "decision_notes": item.decision_notes,
                        "created_at": item.created_at,
                    }
                    for item in db.scalars(stmt)
                ]
            )

        if path == "/approvals/bulk/approve" and method == "POST":
            approved: list[dict[str, Any]] = []
            failed: list[dict[str, Any]] = []
            for approval_id in payload.get("approval_ids") or []:
                item = db.get(models.ApprovalQueue, int(approval_id))
                if not item:
                    failed.append({"approval_id": approval_id, "error": "Approval item not found"})
                    continue
                try:
                    edited = dict(item.edited_payload or item.payload)
                    if payload.get("asset_type_override"):
                        edited["asset_type"] = payload["asset_type_override"]
                        edited["source_classification"] = "brokerage_opportunity" if payload["asset_type_override"] == "brokerage_listing" else "land_prospect"
                    asset = _approve_queue_item(db, item, edited, user=user or "streamlit", notes=payload.get("notes"))
                    approved.append({"approval_id": approval_id, "asset_id": asset.id, "asset_code": asset.asset_code})
                except Exception as exc:
                    db.rollback()
                    failed.append({"approval_id": approval_id, "error": str(exc)})
            return {"approved_count": len(approved), "failed_count": len(failed), "approved": approved, "failed": failed}

        if path == "/approvals/bulk/reject" and method == "POST":
            rejected = 0
            failed: list[dict[str, Any]] = []
            for approval_id in payload.get("approval_ids") or []:
                item = db.get(models.ApprovalQueue, int(approval_id))
                if not item:
                    failed.append({"approval_id": approval_id, "error": "Approval item not found"})
                    continue
                if item.status != "pending":
                    failed.append({"approval_id": approval_id, "error": "Approval item is not pending"})
                    continue
                item.status = "rejected"
                item.reviewed_by = user
                item.reviewed_at = datetime.now(timezone.utc)
                item.approval_decision = "rejected"
                item.decision_notes = payload.get("notes")
                rejected += 1
            db.commit()
            return {"rejected_count": rejected, "failed_count": len(failed), "failed": failed}

        if approval_approve_match and method == "POST":
            item = db.get(models.ApprovalQueue, int(approval_approve_match.group(1)))
            if not item:
                raise DirectRuntimeError("Approval item not found", 404)
            edited_payload = payload.get("edited_payload") or item.edited_payload or item.payload
            return _jsonable(asset_to_dict(_approve_queue_item(db, item, edited_payload, user=user or "streamlit", notes=payload.get("notes"))))

        if approval_reject_match and method == "POST":
            item = db.get(models.ApprovalQueue, int(approval_reject_match.group(1)))
            if not item:
                raise DirectRuntimeError("Approval item not found", 404)
            if item.status != "pending":
                raise DirectRuntimeError("Approval item is not pending")
            item.status = "rejected"
            item.reviewed_by = user
            item.reviewed_at = datetime.now(timezone.utc)
            item.approval_decision = "rejected"
            item.decision_notes = payload.get("notes")
            db.commit()
            return {"status": "rejected"}

        if path == "/import/excel" and method == "POST":
            upload = _upload_from_files(files)
            if not upload or not upload.filename.lower().endswith((".xlsx", ".xls")):
                raise DirectRuntimeError("Upload an Excel .xlsx or .xls file")
            upload_dir = Path("data/uploads")
            upload_dir.mkdir(parents=True, exist_ok=True)
            destination = upload_dir / upload.filename
            destination.write_bytes(upload.file.read())
            return _jsonable(import_excel_to_queue(db, destination, upload.filename))

        if path == "/export/excel" and method == "GET":
            return build_export_workbook(db)

        if path == "/sync/notion" and method == "POST":
            return _jsonable(sync_notion_project_sources(db))

        if path == "/sync/google-sheets" and method == "POST":
            return _jsonable(sync_google_sheets_to_queue(db))

        if path == "/sync/all" and method == "POST":
            return _jsonable(sync_all_sources(db))

        if path == "/ask" and method == "POST":
            return _jsonable(answer_question(db, payload.get("question") or "", asked_by=user))

        if path == "/agent/plan" and method == "POST":
            return _jsonable(plan_agent_actions(db, payload.get("instruction") or "", asked_by=user))

        if path == "/agent/apply" and method == "POST":
            return _jsonable(apply_agent_actions(db, payload.get("instruction") or "", payload.get("actions") or [], user=user))

        if path == "/copilot/apply-json" and method == "POST":
            return _jsonable(apply_copilot_actions(db, message=payload.get("message") or "", actions=payload.get("actions") or [], user=user))

        if path == "/owners" and method == "GET":
            return _jsonable([{"id": row.id, "name": row.name, "phone": row.phone, "company": row.company} for row in db.scalars(select(models.Owner))])

        if path == "/owners" and method == "POST":
            owner = models.Owner(
                name=payload["name"],
                company=payload.get("company"),
                phone=payload.get("phone"),
                whatsapp=payload.get("whatsapp"),
                email=payload.get("email"),
                notes=payload.get("notes"),
            )
            db.add(owner)
            db.commit()
            db.refresh(owner)
            return {"id": owner.id, "name": owner.name}

        if path == "/brokers" and method == "GET":
            return _jsonable([{"id": row.id, "name": row.name, "phone": row.phone, "company": row.company} for row in db.scalars(select(models.Broker))])

        if path == "/brokers" and method == "POST":
            broker = models.Broker(
                name=payload["name"],
                company=payload.get("company"),
                phone=payload.get("phone"),
                whatsapp=payload.get("whatsapp"),
                email=payload.get("email"),
                notes=payload.get("notes"),
            )
            db.add(broker)
            db.commit()
            db.refresh(broker)
            return {"id": broker.id, "name": broker.name}

    raise DirectRuntimeError(f"Unsupported direct route: {method} {path}", 404)
