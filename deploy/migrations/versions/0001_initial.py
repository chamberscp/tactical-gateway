"""Initial schema with PostGIS and pgvector.

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-20

Creates extensions (postgis, vector), enum types, and all tables.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry as GeoColumn
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Extensions must exist before tables that depend on them.
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Enums
    source_protocol = postgresql.ENUM(
        "cot_xml",
        "cot_protobuf",
        "oth_gold",
        "kml",
        "kmz",
        "internal",
        name="source_protocol_enum",
        create_type=True,
    )
    source_protocol.create(op.get_bind(), checkfirst=True)

    object_class = postgresql.ENUM(
        "track",
        "point",
        "area",
        "route",
        "symbol",
        "overlay",
        "text_annotation",
        name="object_class_enum",
        create_type=True,
    )
    object_class.create(op.get_bind(), checkfirst=True)

    classification = postgresql.ENUM(
        "U", "CUI", "S", name="classification_enum", create_type=True
    )
    classification.create(op.get_bind(), checkfirst=True)

    # cto table
    op.create_table(
        "cto",
        sa.Column("uid", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("source_uid", sa.String(256), index=True),
        sa.Column("source_system", sa.String(128), nullable=False, index=True),
        sa.Column(
            "source_protocol",
            postgresql.ENUM(name="source_protocol_enum", create_type=False),
            nullable=False,
        ),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("valid_from", sa.DateTime(timezone=True)),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.Column(
            "classification",
            postgresql.ENUM(name="classification_enum", create_type=False),
            nullable=False,
            server_default="U",
        ),
        sa.Column(
            "caveats",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "object_class",
            postgresql.ENUM(name="object_class_enum", create_type=False),
            nullable=False,
            index=True,
        ),
        sa.Column("geom", GeoColumn(geometry_type="GEOMETRY", srid=4326), nullable=False),
        sa.Column("altitude_m", sa.Float()),
        sa.Column("altitude_source", sa.String(32)),
        sa.Column("altitude_accuracy_m", sa.Float()),
        sa.Column("course_deg", sa.Float()),
        sa.Column("heading_deg", sa.Float()),
        sa.Column("speed_mps", sa.Float()),
        sa.Column("vertical_rate_mps", sa.Float()),
        sa.Column("sidc_2525d", sa.String(20), index=True),
        sa.Column("cot_type", sa.String(64), index=True),
        sa.Column("affiliation", sa.String(32)),
        sa.Column("battle_dimension", sa.String(32)),
        sa.Column("echelon", sa.String(8)),
        sa.Column("status", sa.String(32)),
        sa.Column("callsign", sa.String(128), index=True),
        sa.Column("label", sa.String(256)),
        sa.Column("remarks", sa.Text()),
        sa.Column("attributes", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("provenance", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("raw_sha256", sa.String(64), nullable=False, index=True),
        sa.Column("raw_object_key", sa.String(512), nullable=False),
        sa.Column("raw_size_bytes", sa.Integer(), nullable=False),
        sa.Column("raw_captured_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_cto_geom_gist", "cto", ["geom"], postgresql_using="gist")
    op.create_index("ix_cto_attrs_gin", "cto", ["attributes"], postgresql_using="gin")
    op.create_index("ix_cto_event_class", "cto", ["event_time", "object_class"])

    # audit_log
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("actor", sa.String(128), nullable=False, index=True),
        sa.Column("action", sa.String(128), nullable=False, index=True),
        sa.Column("target", sa.String(512)),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default="{}"),
    )

    # document
    op.create_table(
        "document",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("title", sa.String(512), nullable=False, index=True),
        sa.Column("source_path", sa.String(1024), nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=False),
        sa.Column(
            "classification",
            postgresql.ENUM(name="classification_enum", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "caveats",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("page_count", sa.Integer()),
        sa.Column("extra", postgresql.JSONB(), nullable=False, server_default="{}"),
    )

    # chunk
    op.create_table(
        "chunk",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("document.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("page", sa.Integer()),
        sa.Column("char_start", sa.Integer()),
        sa.Column("char_end", sa.Integer()),
        sa.Column("embedding", Vector(1024)),
    )
    # HNSW index for fast cosine similarity search.
    op.execute(
        "CREATE INDEX ix_chunk_embedding_hnsw ON chunk "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )
    # Full-text search index on chunk text for hybrid retrieval (BM25-ish).
    op.execute(
        "CREATE INDEX ix_chunk_text_fts ON chunk "
        "USING gin (to_tsvector('english', text))"
    )


def downgrade() -> None:
    op.drop_table("chunk")
    op.drop_table("document")
    op.drop_table("audit_log")
    op.drop_index("ix_cto_event_class", table_name="cto")
    op.drop_index("ix_cto_attrs_gin", table_name="cto")
    op.drop_index("ix_cto_geom_gist", table_name="cto")
    op.drop_table("cto")
    op.execute("DROP TYPE IF EXISTS classification_enum")
    op.execute("DROP TYPE IF EXISTS object_class_enum")
    op.execute("DROP TYPE IF EXISTS source_protocol_enum")
    # PostGIS and vector extensions are left in place — other databases
    # in the cluster might use them. Drop manually if needed.
