"""SQLAlchemy ORM models for persisting CTOs to Postgres.

These mirror the Pydantic CTO model but use PostGIS geometry types
for efficient spatial queries and pgvector for the embedding column
(populated by the RAG pipeline).

Conversion helpers (to_cto / from_cto) live in cto_schema.persistence
to keep the Pydantic and ORM models independently testable.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# These are imported lazily inside functions where needed to avoid
# making the whole package depend on geoalchemy2 just to read models.
# At runtime, the ORM module requires geoalchemy2 to be installed.
from geoalchemy2 import Geometry as GeoColumn

from cto_schema.models import (
    Classification,
    ObjectClass,
    SourceProtocol,
)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class CTORow(Base):
    """A persisted Common Tactical Object.

    Spatial column uses SRID 4326 (WGS84). Indexed via GIST for fast
    bounding-box and proximity queries.
    """

    __tablename__ = "cto"

    # Identity
    uid: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    source_uid: Mapped[str | None] = mapped_column(String(256), index=True)
    source_system: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    source_protocol: Mapped[SourceProtocol] = mapped_column(
        SAEnum(SourceProtocol, name="source_protocol_enum"), nullable=False
    )

    # Temporal
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    event_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Classification
    classification: Mapped[Classification] = mapped_column(
        SAEnum(Classification, name="classification_enum"),
        nullable=False,
        default=Classification.UNCLASSIFIED,
    )
    caveats: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)

    # What & where
    object_class: Mapped[ObjectClass] = mapped_column(
        SAEnum(ObjectClass, name="object_class_enum"), nullable=False, index=True
    )
    # PostGIS geometry — supports all GeoJSON geometry types, SRID 4326.
    # GIST index added below via __table_args__.
    geom: Mapped[Any] = mapped_column(
        GeoColumn(geometry_type="GEOMETRY", srid=4326), nullable=False
    )

    # Altitude (flattened — small enough that a sub-table isn't worth it)
    altitude_m: Mapped[float | None] = mapped_column()
    altitude_source: Mapped[str | None] = mapped_column(String(32))
    altitude_accuracy_m: Mapped[float | None] = mapped_column()

    # Kinematics (flattened)
    course_deg: Mapped[float | None] = mapped_column()
    heading_deg: Mapped[float | None] = mapped_column()
    speed_mps: Mapped[float | None] = mapped_column()
    vertical_rate_mps: Mapped[float | None] = mapped_column()

    # Symbology (flattened)
    sidc_2525d: Mapped[str | None] = mapped_column(String(20), index=True)
    cot_type: Mapped[str | None] = mapped_column(String(64), index=True)
    affiliation: Mapped[str | None] = mapped_column(String(32))
    battle_dimension: Mapped[str | None] = mapped_column(String(32))
    echelon: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[str | None] = mapped_column(String(32))

    # Human-readable
    callsign: Mapped[str | None] = mapped_column(String(128), index=True)
    label: Mapped[str | None] = mapped_column(String(256))
    remarks: Mapped[str | None] = mapped_column(Text)

    # Open extension and provenance — JSONB for indexed querying
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    provenance: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)

    # Raw pointer (flattened)
    raw_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    raw_object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    raw_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_cto_geom_gist", "geom", postgresql_using="gist"),
        Index("ix_cto_attrs_gin", "attributes", postgresql_using="gin"),
        Index("ix_cto_event_class", "event_time", "object_class"),
    )


class AuditLogRow(Base):
    """Lightweight audit row. Sparse for now; structured to grow."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    actor: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    target: Mapped[str | None] = mapped_column(String(512))
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)  # success / failure / denied
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)


class DocumentRow(Base):
    """An ingested document available to the RAG pipeline."""

    __tablename__ = "document"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    source_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    classification: Mapped[Classification] = mapped_column(
        SAEnum(Classification, name="classification_enum"), nullable=False
    )
    caveats: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    page_count: Mapped[int | None] = mapped_column(Integer)
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    chunks: Mapped[list["ChunkRow"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class ChunkRow(Base):
    """A chunk of a document with its embedding for vector search.

    The embedding column type is declared with a fixed dimension matching
    BGE-large-en-v1.5 (1024). Changing models requires a migration.
    """

    __tablename__ = "chunk"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("document.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Anchors back to the source for citation rendering.
    page: Mapped[int | None] = mapped_column(Integer)
    char_start: Mapped[int | None] = mapped_column(Integer)
    char_end: Mapped[int | None] = mapped_column(Integer)
    # Embedding column — type defined via pgvector. Imported lazily so the
    # ORM module can be parsed without pgvector installed; at runtime
    # pgvector.sqlalchemy.Vector is required.
    from pgvector.sqlalchemy import Vector  # noqa: E402  -- intentional local import

    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024))

    document: Mapped["DocumentRow"] = relationship(back_populates="chunks")

    __table_args__ = (
        Index(
            "ix_chunk_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
