"""SQLAlchemy/GeoAlchemy2 models for the operational store.

The cto and audit_log tables are created/migrated by Alembic
(deploy/migrations/versions/). This module only declares the ORM mapping
the opstore uses to read/write.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID as UUID_type

from geoalchemy2 import Geometry
from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class CtoRow(Base):
    __tablename__ = "cto"

    uid: UUID_type = Column(UUID(as_uuid=True), primary_key=True)
    source_uid: str | None = Column(String(256), nullable=True)
    source_system: str = Column(String(256), nullable=False)
    source_protocol: str = Column(String(32), nullable=False)
    ingest_source: str | None = Column(String(32), nullable=True)

    received_at: datetime = Column(DateTime(timezone=True), nullable=False)
    event_time: datetime = Column(DateTime(timezone=True), nullable=False)
    valid_from: datetime | None = Column(DateTime(timezone=True), nullable=True)
    valid_to: datetime | None = Column(DateTime(timezone=True), nullable=True)

    classification: str = Column(String(32), nullable=False, default="unclassified")
    object_class: str = Column(String(32), nullable=False)

    # PostGIS native geometry - authoritative for spatial query
    geom = Column(Geometry(geometry_type="GEOMETRY", srid=4326), nullable=True)
    # Original JSON geometry preserved for self-description / re-emit
    geometry_json = Column(JSONB, nullable=True)

    symbology = Column(JSONB, nullable=True)
    altitude = Column(JSONB, nullable=True)
    kinematics = Column(JSONB, nullable=True)

    callsign: str | None = Column(String(64), nullable=True)
    label: str | None = Column(String(256), nullable=True)
    remarks: str | None = Column(Text, nullable=True)

    attributes = Column(JSONB, nullable=False, default=dict)

    parent_kmz_uri: str | None = Column(String(512), nullable=True)
    parent_kmz_filename: str | None = Column(String(256), nullable=True)
    parent_kmz_source: str | None = Column(String(32), nullable=True)

    raw_pointer = Column(JSONB, nullable=True)
    provenance = Column(JSONB, nullable=False, default=list)


class AuditLogRow(Base):
    __tablename__ = "audit_log"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    at: datetime = Column(DateTime(timezone=True), nullable=False)
    actor: str = Column(String(256), nullable=False)
    action: str = Column(String(128), nullable=False)
    subject: str | None = Column(String(512), nullable=True)
    subject_uid: UUID_type | None = Column(UUID(as_uuid=True), nullable=True)
    event_type: str | None = Column(String(64), nullable=True)
    details = Column(JSONB, nullable=True)
