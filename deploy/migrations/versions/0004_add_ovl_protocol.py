"""Phase 2b-1 follow-up: add 'ovl' to source_protocol_enum

Revision ID: 0004_add_ovl_protocol
Revises: 0003_add_graphic_enum
Create Date: 2026-05-28

The SourceProtocol.OVL value was introduced in Phase 2b-1 for GCCS-J /
Agile Server overlay (.ovl) ingest, but the source_protocol_enum Postgres
type was never given the corresponding value. Inserts of OVL-sourced CTOs
therefore fail with "invalid input value for enum source_protocol_enum:
'ovl'". This migration corrects that.

ALTER TYPE ... ADD VALUE cannot run inside a transaction in older
Postgres versions, so this migration is wrapped to disable transactional
DDL for the duration. Newer versions (12+) allow it transactionally;
the IF NOT EXISTS guard makes the operation idempotent regardless.
"""

from __future__ import annotations

from alembic import op

revision = "0004_add_ovl_protocol"
down_revision = "0003_add_graphic_enum"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Run outside a transaction for compatibility with older Postgres.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE source_protocol_enum ADD VALUE IF NOT EXISTS 'ovl';"
        )


def downgrade() -> None:
    # Postgres does not support removing enum values without recreating
    # the type. Leaving 'ovl' in place on downgrade is harmless.
    pass