from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from backend.app import models
from backend.app.auth import create_access_token, get_current_user, verify_login
from backend.app.config import get_settings
from backend.app.db import create_all, get_db
from backend.app.schemas import (
    ApprovalDecision,
    ApprovalOut,
    AgentApplyRequest,
    AgentPlan,
    AgentRequest,
    AskRequest,
    AskResponse,
    AssetCreate,
    AssetOut,
    AssetUpdate,
    BulkApprovalDecision,
    ImportResult,
    LoginRequest,
    LoginResponse,
)
from backend.app.services.ai_assistant import answer_question
from backend.app.services.ai_db_agent import apply_agent_actions, plan_agent_actions
from backend.app.services.assets import asset_to_dict, create_asset, filter_assets, update_asset
from backend.app.services.excel_importer import import_excel_to_queue
from backend.app.services.exporter import build_export_workbook
from backend.app.services.google_sheets_sync import sync_google_sheets_to_queue
from backend.app.services.source_sync import sync_all_sources, sync_notion_project_sources


app = FastAPI(title="Land and JV Tracker API", version="0.1.0")

ALLOWED_ASSET_FIELDS = set(AssetCreate.model_fields)


async def schedule_sync_loop() -> None:
    """Lightweight asynchronous loop to run sync morning and evening."""
    import asyncio
    settings = get_settings()
    print(f"Background Sync Scheduler active. Morning: {settings.sync_schedule_morning} | Evening: {settings.sync_schedule_evening}")
    
    # Track the last run date+hour to prevent multiple triggers in the same window
    last_trigger_window = None
    
    while True:
        try:
            now = datetime.now()
            # Window key is YYYY-MM-DD-morning or YYYY-MM-DD-evening
            window_key = None
            
            # Check morning window
            m_hour, m_minute = map(int, settings.sync_schedule_morning.split(":"))
            if now.hour == m_hour and now.minute == m_minute:
                window_key = f"{now.strftime('%Y-%m-%d')}-morning"
                
            # Check evening window
            e_hour, e_minute = map(int, settings.sync_schedule_evening.split(":"))
            if now.hour == e_hour and now.minute == e_minute:
                window_key = f"{now.strftime('%Y-%m-%d')}-evening"
                
            if window_key and window_key != last_trigger_window:
                print(f"[{now.isoformat()}] Scheduled sync triggered ({window_key})! Running...")
                from backend.app.db import SessionLocal
                from backend.app.services.source_sync import sync_all_sources
                
                with SessionLocal() as db:
                    result = sync_all_sources(db)
                print(f"[{now.isoformat()}] Scheduled sync finished. Queued: {result.get('queued_count')}, Skipped: {result.get('skipped_count')}")
                last_trigger_window = window_key
                
            await asyncio.sleep(30)
        except Exception as exc:
            print(f"Error in scheduled sync loop: {exc}")
            await asyncio.sleep(60)


@app.on_event("startup")
def startup() -> None:
    create_all()
    import asyncio
    asyncio.create_task(schedule_sync_loop())


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": get_settings().app_name}


@app.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest) -> LoginResponse:
    if not verify_login(payload.username, payload.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    return LoginResponse(access_token=create_access_token(payload.username), username=payload.username)


@app.post("/logout")
def logout(_: str = Depends(get_current_user)) -> dict[str, str]:
    return {"status": "ok"}


@app.get("/stats")
def stats(db: Session = Depends(get_db), _: str = Depends(get_current_user)) -> dict[str, int]:
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


@app.get("/assets", response_model=list[AssetOut])
def get_assets(
    asset_type: str | None = None,
    district: str | None = None,
    tehsil: str | None = None,
    locality: str | None = None,
    source: str | None = None,
    status: str | None = None,
    owner_id: int | None = None,
    broker_id: int | None = None,
    workability_rating: int | None = None,
    approval_status: str | None = None,
    db: Session = Depends(get_db),
    _: str = Depends(get_current_user),
) -> list[dict[str, Any]]:
    filters = {
        "asset_type": asset_type,
        "district": district,
        "tehsil": tehsil,
        "locality": locality,
        "source": source,
        "status": status,
        "owner_id": owner_id,
        "broker_id": broker_id,
        "workability_rating": workability_rating,
        "approval_status": approval_status,
    }
    return [asset_to_dict(asset) for asset in filter_assets(db, filters)]


@app.get("/assets/{asset_id}", response_model=AssetOut)
def get_asset(asset_id: int, db: Session = Depends(get_db), _: str = Depends(get_current_user)) -> dict[str, Any]:
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
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset_to_dict(asset)


@app.post("/assets/{asset_id}/contacts")
def add_asset_contact(
    asset_id: int,
    payload: dict[str, Any],
    db: Session = Depends(get_db),
    _: str = Depends(get_current_user),
) -> dict[str, Any]:
    if not db.get(models.Asset, asset_id):
        raise HTTPException(status_code=404, detail="Asset not found")
    contact = models.Contact(
        name=payload["name"],
        company=payload.get("company"),
        phone=payload.get("phone"),
        whatsapp=payload.get("whatsapp"),
        email=payload.get("email"),
        notes=payload.get("notes"),
    )
    db.add(contact)
    db.flush()
    link = models.AssetContact(
        asset_id=asset_id,
        contact_id=contact.id,
        relationship_type=payload.get("relationship_type") or "related",
        notes=payload.get("relationship_notes"),
    )
    db.add(link)
    db.commit()
    return {"id": link.id, "contact_id": contact.id, "name": contact.name}


@app.post("/assets/{asset_id}/documents")
def add_asset_document(
    asset_id: int,
    payload: dict[str, Any],
    db: Session = Depends(get_db),
    _: str = Depends(get_current_user),
) -> dict[str, Any]:
    if not db.get(models.Asset, asset_id):
        raise HTTPException(status_code=404, detail="Asset not found")
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


@app.post("/assets/{asset_id}/updates")
def add_asset_update(
    asset_id: int,
    payload: dict[str, Any],
    db: Session = Depends(get_db),
    user: str = Depends(get_current_user),
) -> dict[str, Any]:
    if not db.get(models.Asset, asset_id):
        raise HTTPException(status_code=404, detail="Asset not found")
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


@app.post("/assets", response_model=AssetOut)
def post_asset(
    payload: AssetCreate, db: Session = Depends(get_db), _: str = Depends(get_current_user)
) -> dict[str, Any]:
    asset = create_asset(db, payload)
    return asset_to_dict(asset)


@app.put("/assets/{asset_id}", response_model=AssetOut)
def put_asset(
    asset_id: int,
    payload: AssetUpdate,
    db: Session = Depends(get_db),
    _: str = Depends(get_current_user),
) -> dict[str, Any]:
    asset = db.get(models.Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    asset = update_asset(db, asset, payload)
    return asset_to_dict(asset)


@app.get("/approvals", response_model=list[ApprovalOut])
def approvals(
    queue_status: str = Query(default="pending", alias="status"),
    db: Session = Depends(get_db),
    _: str = Depends(get_current_user),
) -> list[models.ApprovalQueue]:
    stmt = select(models.ApprovalQueue)
    if queue_status != "all":
        stmt = stmt.where(models.ApprovalQueue.status == queue_status)
    stmt = stmt.order_by(models.ApprovalQueue.created_at.desc()).limit(500)
    return list(db.scalars(stmt))


def _approve_queue_item(
    db: Session,
    item: models.ApprovalQueue,
    payload: dict[str, Any],
    *,
    user: str,
    notes: str | None = None,
) -> models.Asset:
    if item.status != "pending":
        raise HTTPException(status_code=400, detail="Approval item is not pending")
    asset_payload = {key: value for key, value in payload.items() if key in ALLOWED_ASSET_FIELDS}
    owner_name = payload.get("owner_name")
    broker_name = payload.get("broker_name")
    if owner_name and not asset_payload.get("owner_id"):
        owner = db.scalar(select(models.Owner).where(models.Owner.name == owner_name))
        if not owner:
            owner = models.Owner(name=owner_name)
            db.add(owner)
            db.flush()
        asset_payload["owner_id"] = owner.id
    if broker_name and not asset_payload.get("broker_id"):
        broker = db.scalar(select(models.Broker).where(models.Broker.name == broker_name))
        if not broker:
            broker = models.Broker(name=broker_name)
            db.add(broker)
            db.flush()
        asset_payload["broker_id"] = broker.id
    if not asset_payload.get("title"):
        raise HTTPException(status_code=400, detail="Cannot approve without a title")
    asset_payload["approval_status"] = "approved"
    asset = create_asset(db, asset_payload)
    for document in payload.get("documents", []) or []:
        db.add(
            models.AssetDocument(
                asset_id=asset.id,
                document_name=document.get("document_name") or document.get("url") or "Imported collateral",
                document_type=document.get("document_type"),
                url=document.get("url"),
                storage_path=document.get("storage_path"),
                notes=document.get("notes"),
            )
        )
    if payload.get("bottleneck_notes"):
        db.add(
            models.AssetUpdate(
                asset_id=asset.id,
                update_type="imported_note",
                update_text=payload["bottleneck_notes"],
                created_by=user,
            )
        )
    item.status = "approved"
    item.reviewed_by = user
    item.reviewed_at = datetime.now(timezone.utc)
    item.approval_decision = "approved"
    item.decision_notes = notes
    db.commit()
    db.refresh(asset)
    return asset


@app.post("/approvals/bulk/approve")
def bulk_approve(
    decision: BulkApprovalDecision,
    db: Session = Depends(get_db),
    user: str = Depends(get_current_user),
) -> dict[str, Any]:
    approved: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for approval_id in decision.approval_ids:
        item = db.get(models.ApprovalQueue, approval_id)
        if not item:
            failed.append({"approval_id": approval_id, "error": "Approval item not found"})
            continue
        try:
            payload = dict(item.edited_payload or item.payload)
            if decision.asset_type_override:
                payload["asset_type"] = decision.asset_type_override
                payload["source_classification"] = (
                    "brokerage_opportunity"
                    if decision.asset_type_override == "brokerage_listing"
                    else "land_prospect"
                )
            asset = _approve_queue_item(db, item, payload, user=user, notes=decision.notes)
            approved.append({"approval_id": approval_id, "asset_id": asset.id, "asset_code": asset.asset_code})
        except Exception as exc:
            db.rollback()
            failed.append({"approval_id": approval_id, "error": str(exc)})
    return {"approved_count": len(approved), "failed_count": len(failed), "approved": approved, "failed": failed}


@app.post("/approvals/bulk/reject")
def bulk_reject(
    decision: BulkApprovalDecision,
    db: Session = Depends(get_db),
    user: str = Depends(get_current_user),
) -> dict[str, Any]:
    rejected = 0
    failed: list[dict[str, Any]] = []
    for approval_id in decision.approval_ids:
        item = db.get(models.ApprovalQueue, approval_id)
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
        item.decision_notes = decision.notes
        rejected += 1
    db.commit()
    return {"rejected_count": rejected, "failed_count": len(failed), "failed": failed}


@app.post("/approvals/{approval_id}/approve", response_model=AssetOut)
def approve(
    approval_id: int,
    decision: ApprovalDecision | None = None,
    db: Session = Depends(get_db),
    user: str = Depends(get_current_user),
) -> dict[str, Any]:
    item = db.get(models.ApprovalQueue, approval_id)
    if not item:
        raise HTTPException(status_code=404, detail="Approval item not found")
    payload = decision.edited_payload if decision and decision.edited_payload else item.edited_payload or item.payload
    asset = _approve_queue_item(db, item, payload, user=user, notes=decision.notes if decision else None)
    return asset_to_dict(asset)


@app.post("/approvals/{approval_id}/reject")
def reject(
    approval_id: int,
    decision: ApprovalDecision | None = None,
    db: Session = Depends(get_db),
    user: str = Depends(get_current_user),
) -> dict[str, str]:
    item = db.get(models.ApprovalQueue, approval_id)
    if not item:
        raise HTTPException(status_code=404, detail="Approval item not found")
    if item.status != "pending":
        raise HTTPException(status_code=400, detail="Approval item is not pending")
    item.status = "rejected"
    item.reviewed_by = user
    item.reviewed_at = datetime.now(timezone.utc)
    item.approval_decision = "rejected"
    item.decision_notes = decision.notes if decision else None
    db.commit()
    return {"status": "rejected"}


@app.post("/import/excel", response_model=ImportResult)
def import_excel(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: str = Depends(get_current_user),
) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Upload an Excel .xlsx or .xls file")
    upload_dir = Path("data/uploads")
    upload_dir.mkdir(parents=True, exist_ok=True)
    destination = upload_dir / file.filename
    with destination.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)
    return import_excel_to_queue(db, destination, file.filename)


@app.get("/export/excel")
def export_excel(db: Session = Depends(get_db), _: str = Depends(get_current_user)) -> Response:
    workbook = build_export_workbook(db)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"land_jv_tracker_export_{timestamp}.xlsx"
    return Response(
        content=workbook,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/sync/notion")
def sync_notion(db: Session = Depends(get_db), _: str = Depends(get_current_user)) -> dict[str, Any]:
    return sync_notion_project_sources(db)


@app.post("/sync/google-sheets")
def sync_google_sheets(db: Session = Depends(get_db), _: str = Depends(get_current_user)) -> dict[str, Any]:
    return sync_google_sheets_to_queue(db)


@app.post("/sync/all")
def sync_all(db: Session = Depends(get_db), _: str = Depends(get_current_user)) -> dict[str, Any]:
    return sync_all_sources(db)


@app.post("/ask", response_model=AskResponse)
def ask(payload: AskRequest, db: Session = Depends(get_db), user: str = Depends(get_current_user)) -> dict[str, Any]:
    return answer_question(db, payload.question, asked_by=user)


@app.post("/agent/plan", response_model=AgentPlan)
def agent_plan(payload: AgentRequest, db: Session = Depends(get_db), user: str = Depends(get_current_user)) -> dict[str, Any]:
    return plan_agent_actions(db, payload.instruction, asked_by=user)


@app.post("/agent/apply")
def agent_apply(
    payload: AgentApplyRequest,
    db: Session = Depends(get_db),
    user: str = Depends(get_current_user),
) -> dict[str, Any]:
    return apply_agent_actions(db, payload.instruction, payload.actions, user=user)


@app.get("/owners")
def owners(db: Session = Depends(get_db), _: str = Depends(get_current_user)) -> list[dict[str, Any]]:
    return [{"id": row.id, "name": row.name, "phone": row.phone, "company": row.company} for row in db.scalars(select(models.Owner))]


@app.post("/owners")
def create_owner(payload: dict[str, Any], db: Session = Depends(get_db), _: str = Depends(get_current_user)) -> dict[str, Any]:
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


@app.get("/brokers")
def brokers(db: Session = Depends(get_db), _: str = Depends(get_current_user)) -> list[dict[str, Any]]:
    return [{"id": row.id, "name": row.name, "phone": row.phone, "company": row.company} for row in db.scalars(select(models.Broker))]


@app.post("/brokers")
def create_broker(payload: dict[str, Any], db: Session = Depends(get_db), _: str = Depends(get_current_user)) -> dict[str, Any]:
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
