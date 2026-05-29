from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app import models


EXPORT_MODELS: list[tuple[str, type]] = [
    ("assets", models.Asset),
    ("deals", models.Deal),
    ("owners", models.Owner),
    ("brokers", models.Broker),
    ("organizations", models.Organization),
    ("contacts", models.Contact),
    ("asset_contacts", models.AssetContact),
    ("asset_documents", models.AssetDocument),
    ("asset_locations", models.AssetLocation),
    ("asset_updates", models.AssetUpdate),
    ("asset_tags", models.AssetTag),
    ("approval_queue", models.ApprovalQueue),
    ("ingestion_logs", models.IngestionLog),
    ("notion_sync_logs", models.NotionSyncLog),
    ("crm_profiles", models.CrmProfile),
    ("match_suggestions", models.AssetMatchSuggestion),
    ("ai_query_logs", models.AiQueryLog),
]


def _safe_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _model_rows(db: Session, model: type) -> list[dict[str, Any]]:
    rows = db.scalars(select(model)).all()
    columns = model.__table__.columns
    return [
        {column.name: _safe_value(getattr(row, column.name)) for column in columns}
        for row in rows
    ]


def _asset_snapshot(db: Session) -> list[dict[str, Any]]:
    rows = db.scalars(select(models.Asset)).all()
    snapshot: list[dict[str, Any]] = []
    for asset in rows:
        snapshot.append(
            {
                "id": asset.id,
                "asset_code": asset.asset_code,
                "title": asset.title,
                "asset_type": asset.asset_type,
                "status": asset.status,
                "source": asset.source,
                "district": asset.district,
                "tehsil": asset.tehsil,
                "locality": asset.locality,
                "area_name": asset.area_name,
                "address": asset.address,
                "latitude": asset.latitude,
                "longitude": asset.longitude,
                "google_maps_link": asset.google_maps_link,
                "land_area": asset.land_area,
                "built_up_area": asset.built_up_area,
                "asking_price": _safe_value(asset.asking_price),
                "expected_price": _safe_value(asset.expected_price),
                "owner": asset.owner.name if asset.owner else None,
                "broker": asset.broker.name if asset.broker else None,
                "workability_rating": asset.workability_rating,
                "bottleneck_rating": asset.bottleneck_rating,
                "bottleneck_notes": asset.bottleneck_notes,
                "legal_status": asset.legal_status,
                "zoning_status": asset.zoning_status,
                "approval_status": asset.approval_status,
                "documents_count": len(asset.documents),
                "updates_count": len(asset.updates),
                "contacts_count": len(asset.contacts),
                "created_at": _safe_value(asset.created_at),
                "updated_at": _safe_value(asset.updated_at),
            }
        )
    return snapshot


def build_export_workbook(db: Session) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(_asset_snapshot(db)).to_excel(writer, sheet_name="asset_snapshot", index=False)
        for sheet_name, model in EXPORT_MODELS:
            rows = _model_rows(db, model)
            pd.DataFrame(rows).to_excel(writer, sheet_name=sheet_name[:31], index=False)

        workbook = writer.book
        for worksheet in workbook.worksheets:
            worksheet.freeze_panes = "A2"
            if worksheet.max_row > 1 and worksheet.max_column > 0:
                worksheet.auto_filter.ref = worksheet.dimensions
            for column_cells in worksheet.columns:
                header = column_cells[0].value
                width = min(max(len(str(header or "")) + 4, 12), 42)
                worksheet.column_dimensions[column_cells[0].column_letter].width = width

    output.seek(0)
    return output.read()

