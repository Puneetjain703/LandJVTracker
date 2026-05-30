from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


ASSET_TYPES = [
    "land",
    "jv",
    "resale_unit",
    "commercial",
    "rental",
    "brokerage_listing",
    "other",
]


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str


class AssetBase(BaseModel):
    asset_code: str | None = None
    title: str
    asset_type: str = Field(default="land")
    status: str = "lead"
    source: str | None = None
    locality: str | None = None
    area_name: str | None = None
    tehsil: str | None = None
    district: str | None = None
    state: str | None = "Rajasthan"
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    google_maps_link: str | None = None
    land_area: str | None = None
    built_up_area: str | None = None
    asking_price: Decimal | float | None = None
    expected_price: Decimal | float | None = None
    owner_id: int | None = None
    broker_id: int | None = None
    workability_rating: int | None = None
    bottleneck_rating: int | None = None
    bottleneck_notes: str | None = None
    legal_status: str | None = None
    zoning_status: str | None = None
    location_score: float | None = None
    location_score_reason: str | None = None
    approval_status: str = "approved"
    raw_source: dict[str, Any] | None = None


class AssetCreate(AssetBase):
    pass


class AssetUpdate(BaseModel):
    asset_code: str | None = None
    title: str | None = None
    asset_type: str | None = None
    status: str | None = None
    source: str | None = None
    locality: str | None = None
    area_name: str | None = None
    tehsil: str | None = None
    district: str | None = None
    state: str | None = None
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    google_maps_link: str | None = None
    land_area: str | None = None
    built_up_area: str | None = None
    asking_price: Decimal | float | None = None
    expected_price: Decimal | float | None = None
    owner_id: int | None = None
    broker_id: int | None = None
    workability_rating: int | None = None
    bottleneck_rating: int | None = None
    bottleneck_notes: str | None = None
    legal_status: str | None = None
    zoning_status: str | None = None
    approval_status: str | None = None
    raw_source: dict[str, Any] | None = None


class AssetOut(AssetBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_name: str | None = None
    broker_name: str | None = None
    contacts: list[dict[str, Any]] = Field(default_factory=list)
    people_summary: str | None = None
    documents: list[dict[str, Any]] = Field(default_factory=list)
    updates: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    locations: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    source_uid: str | None
    title: str | None
    payload: dict[str, Any]
    edited_payload: dict[str, Any] | None
    status: str
    created_by_source: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    approval_decision: str | None
    decision_notes: str | None
    created_at: datetime


class ApprovalDecision(BaseModel):
    edited_payload: dict[str, Any] | None = None
    notes: str | None = None


class BulkApprovalDecision(BaseModel):
    approval_ids: list[int]
    asset_type_override: str | None = None
    notes: str | None = None


class BulkAssetDeleteRequest(BaseModel):
    asset_ids: list[int]


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    answer: str
    source_rows: list[dict[str, Any]]


class AgentRequest(BaseModel):
    instruction: str


class AgentPlan(BaseModel):
    summary: str
    actions: list[dict[str, Any]] = Field(default_factory=list)
    matched_assets: list[dict[str, Any]] = Field(default_factory=list)
    answer: str | None = None
    requires_confirmation: bool = True


class AgentApplyRequest(BaseModel):
    instruction: str
    actions: list[dict[str, Any]]


class CopilotApplyRequest(BaseModel):
    message: str
    actions: list[dict[str, Any]]


class ImportResult(BaseModel):
    source: str
    total_rows: int
    queued_count: int
    skipped_count: int
    incomplete_count: int
    log_id: int | None = None


class SyncResult(BaseModel):
    source_name: str
    fetched_count: int
    queued_count: int
    skipped_count: int
    status: str
    log_id: int | None = None
    message: str | None = None
