"""Phase 2a: PostGIS geometry, KMZ ingest support, supersession fields

Revision ID: 0002_phase2a
Revises: 0001_initial
Create Date: 2026-05-22

Idempotent: every column / index uses IF NOT EXISTS so re-running against
a partially-migrated DB is safe. This matters in dev where the Phase 0
init scripts may have already created some columns the Alembic chain
later catches up to.
"""

from __future__ import annotations

from alembic import op

revision = "0002_phase2a"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostGIS extension (no-op if already enabled)
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis;")

    # New columns on cto (each guarded individually)
    op.execute("""
        ALTER TABLE cto
        ADD COLUMN IF NOT EXISTS geom geometry(GEOMETRY, 4326);
    """)
    op.execute("""
        ALTER TABLE cto
        ADD COLUMN IF NOT EXISTS ingest_source VARCHAR(32);
    """)
    op.execute("""
        ALTER TABLE cto
        ADD COLUMN IF NOT EXISTS parent_kmz_uri VARCHAR(512);
    """)
    op.execute("""
        ALTER TABLE cto
        ADD COLUMN IF NOT EXISTS parent_kmz_filename VARCHAR(256);
    """)
    op.execute("""
        ALTER TABLE cto
        ADD COLUMN IF NOT EXISTS parent_kmz_source VARCHAR(32);
    """)

    # Rename old JSON geometry column to geometry_json (if present and
    # not already renamed)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='cto' AND column_name='geometry'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='cto' AND column_name='geometry_json'
            ) THEN
                ALTER TABLE cto RENAME COLUMN geometry TO geometry_json;
            END IF;
        END$$;
    """)

    # Indexes for query API
    op.execute("CREATE INDEX IF NOT EXISTS ix_cto_geom ON cto USING GIST (geom);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_cto_event_time ON cto (event_time);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_cto_source_system ON cto (source_system);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_cto_object_class ON cto (object_class);")
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_cto_parent_kmz_current
        ON cto (parent_kmz_filename, parent_kmz_source)
        WHERE valid_to IS NULL;
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_cto_valid_to ON cto (valid_to);")

    # Audit log extensions
    op.execute("""
        ALTER TABLE audit_log
        ADD COLUMN IF NOT EXISTS event_type VARCHAR(64);
    """)
    op.execute("""
        ALTER TABLE audit_log
        ADD COLUMN IF NOT EXISTS subject_uid UUID;
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_log_event_type ON audit_log (event_type);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_log_subject_uid ON audit_log (subject_uid);")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_cto_geom;")
    op.execute("DROP INDEX IF EXISTS ix_cto_event_time;")
    op.execute("DROP INDEX IF EXISTS ix_cto_source_system;")
    op.execute("DROP INDEX IF EXISTS ix_cto_object_class;")
    op.execute("DROP INDEX IF EXISTS ix_cto_parent_kmz_current;")
    op.execute("DROP INDEX IF EXISTS ix_cto_valid_to;")
    op.execute("DROP INDEX IF EXISTS ix_audit_log_event_type;")
    op.execute("DROP INDEX IF EXISTS ix_audit_log_subject_uid;")
    op.execute("ALTER TABLE cto DROP COLUMN IF EXISTS parent_kmz_source;")
    op.execute("ALTER TABLE cto DROP COLUMN IF EXISTS parent_kmz_filename;")
    op.execute("ALTER TABLE cto DROP COLUMN IF EXISTS parent_kmz_uri;")
    op.execute("ALTER TABLE cto DROP COLUMN IF EXISTS ingest_source;")
    op.execute("ALTER TABLE cto DROP COLUMN IF EXISTS geom;")
