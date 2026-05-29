from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from backend.app.db import Base


JsonType = JSON().with_variant(JSONB, "postgresql")


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Organization(TimestampMixin, Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    organization_type: Mapped[str | None] = mapped_column(String(80))
    phone: Mapped[str | None] = mapped_column(String(80))
    email: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)

    contacts: Mapped[list["Contact"]] = relationship(back_populates="organization")


class Contact(TimestampMixin, Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    company: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(80))
    whatsapp: Mapped[str | None] = mapped_column(String(80))
    email: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    organization_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"))

    organization: Mapped[Organization | None] = relationship(back_populates="contacts")
    asset_links: Mapped[list["AssetContact"]] = relationship(back_populates="contact", cascade="all, delete-orphan")


class Broker(TimestampMixin, Base):
    __tablename__ = "brokers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    company: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(80))
    whatsapp: Mapped[str | None] = mapped_column(String(80))
    email: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)

    assets: Mapped[list["Asset"]] = relationship(back_populates="broker")


class Owner(TimestampMixin, Base):
    __tablename__ = "owners"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    company: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(80))
    whatsapp: Mapped[str | None] = mapped_column(String(80))
    email: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)

    assets: Mapped[list["Asset"]] = relationship(back_populates="owner")


class Asset(TimestampMixin, Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_code: Mapped[str | None] = mapped_column(String(80), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(80), default="lead", index=True)
    source: Mapped[str | None] = mapped_column(String(120), index=True)
    locality: Mapped[str | None] = mapped_column(String(255), index=True)
    area_name: Mapped[str | None] = mapped_column(String(255))
    tehsil: Mapped[str | None] = mapped_column(String(255), index=True)
    district: Mapped[str | None] = mapped_column(String(255), index=True)
    state: Mapped[str | None] = mapped_column(String(255), default="Rajasthan")
    address: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    google_maps_link: Mapped[str | None] = mapped_column(Text)
    land_area: Mapped[str | None] = mapped_column(String(120))
    built_up_area: Mapped[str | None] = mapped_column(String(120))
    asking_price: Mapped[float | None] = mapped_column(Numeric(16, 2))
    expected_price: Mapped[float | None] = mapped_column(Numeric(16, 2))
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("owners.id"), index=True)
    broker_id: Mapped[int | None] = mapped_column(ForeignKey("brokers.id"), index=True)
    workability_rating: Mapped[int | None] = mapped_column(Integer)
    bottleneck_rating: Mapped[int | None] = mapped_column(Integer)
    bottleneck_notes: Mapped[str | None] = mapped_column(Text)
    legal_status: Mapped[str | None] = mapped_column(Text)
    zoning_status: Mapped[str | None] = mapped_column(Text)
    approval_status: Mapped[str] = mapped_column(String(80), default="approved", index=True)
    raw_source: Mapped[dict | None] = mapped_column(JsonType)

    owner: Mapped[Owner | None] = relationship(back_populates="assets")
    broker: Mapped[Broker | None] = relationship(back_populates="assets")
    documents: Mapped[list["AssetDocument"]] = relationship(back_populates="asset", cascade="all, delete-orphan")
    locations: Mapped[list["AssetLocation"]] = relationship(back_populates="asset", cascade="all, delete-orphan")
    updates: Mapped[list["AssetUpdate"]] = relationship(back_populates="asset", cascade="all, delete-orphan")
    tags: Mapped[list["AssetTag"]] = relationship(back_populates="asset", cascade="all, delete-orphan")
    contacts: Mapped[list["AssetContact"]] = relationship(back_populates="asset", cascade="all, delete-orphan")
    deals: Mapped[list["Deal"]] = relationship(back_populates="asset", cascade="all, delete-orphan")


class Deal(TimestampMixin, Base):
    __tablename__ = "deals"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    deal_type: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(80), default="active", index=True)
    value: Mapped[float | None] = mapped_column(Numeric(16, 2))
    probability: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)

    asset: Mapped[Asset] = relationship(back_populates="deals")


class AssetContact(TimestampMixin, Base):
    __tablename__ = "asset_contacts"
    __table_args__ = (UniqueConstraint("asset_id", "contact_id", "relationship_type", name="uq_asset_contact_role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), index=True)
    relationship_type: Mapped[str] = mapped_column(String(80), default="related")
    notes: Mapped[str | None] = mapped_column(Text)

    asset: Mapped[Asset] = relationship(back_populates="contacts")
    contact: Mapped[Contact] = relationship(back_populates="asset_links")


class AssetDocument(TimestampMixin, Base):
    __tablename__ = "asset_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    document_name: Mapped[str] = mapped_column(String(255), nullable=False)
    document_type: Mapped[str | None] = mapped_column(String(120))
    url: Mapped[str | None] = mapped_column(Text)
    storage_path: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    asset: Mapped[Asset] = relationship(back_populates="documents")


class AssetLocation(TimestampMixin, Base):
    __tablename__ = "asset_locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    label: Mapped[str | None] = mapped_column(String(120))
    address: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    google_maps_link: Mapped[str | None] = mapped_column(Text)

    asset: Mapped[Asset] = relationship(back_populates="locations")


class AssetUpdate(TimestampMixin, Base):
    __tablename__ = "asset_updates"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    update_type: Mapped[str | None] = mapped_column(String(120))
    update_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(120))

    asset: Mapped[Asset] = relationship(back_populates="updates")


class AssetTag(TimestampMixin, Base):
    __tablename__ = "asset_tags"
    __table_args__ = (UniqueConstraint("asset_id", "tag", name="uq_asset_tag"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    tag: Mapped[str] = mapped_column(String(120), index=True)

    asset: Mapped[Asset] = relationship(back_populates="tags")


class ApprovalQueue(TimestampMixin, Base):
    __tablename__ = "approval_queue"
    __table_args__ = (UniqueConstraint("source", "source_uid", name="uq_approval_source_uid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(120), index=True)
    source_uid: Mapped[str | None] = mapped_column(String(255), index=True)
    title: Mapped[str | None] = mapped_column(String(500))
    payload: Mapped[dict] = mapped_column(JsonType, nullable=False)
    edited_payload: Mapped[dict | None] = mapped_column(JsonType)
    status: Mapped[str] = mapped_column(String(80), default="pending", index=True)
    created_by_source: Mapped[str | None] = mapped_column(String(120))
    reviewed_by: Mapped[str | None] = mapped_column(String(120))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_decision: Mapped[str | None] = mapped_column(String(80))
    decision_notes: Mapped[str | None] = mapped_column(Text)


class NotionSyncLog(TimestampMixin, Base):
    __tablename__ = "notion_sync_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_name: Mapped[str] = mapped_column(String(255))
    notion_database_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(80))
    fetched_count: Mapped[int] = mapped_column(Integer, default=0)
    queued_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)


class IngestionLog(TimestampMixin, Base):
    __tablename__ = "ingestion_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(120), index=True)
    filename: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(80))
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    created_count: Mapped[int] = mapped_column(Integer, default=0)
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)


class CrmProfile(TimestampMixin, Base):
    __tablename__ = "crm_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_type: Mapped[str] = mapped_column(String(80), default="buyer")
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(80))
    email: Mapped[str | None] = mapped_column(String(255))
    preferred_asset_types: Mapped[list | None] = mapped_column(JsonType)
    preferred_locations: Mapped[list | None] = mapped_column(JsonType)
    budget_min: Mapped[float | None] = mapped_column(Numeric(16, 2))
    budget_max: Mapped[float | None] = mapped_column(Numeric(16, 2))
    notes: Mapped[str | None] = mapped_column(Text)


class AssetMatchSuggestion(TimestampMixin, Base):
    __tablename__ = "asset_match_suggestions"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    crm_profile_id: Mapped[int] = mapped_column(ForeignKey("crm_profiles.id"), index=True)
    score: Mapped[float | None] = mapped_column(Float)
    rationale: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(80), default="suggested")


class AiQueryLog(TimestampMixin, Base):
    __tablename__ = "ai_query_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    source_rows: Mapped[list | None] = mapped_column(JsonType)
    asked_by: Mapped[str | None] = mapped_column(String(120))

