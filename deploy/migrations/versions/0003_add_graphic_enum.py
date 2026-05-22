"""Phase 2a follow-up: add 'graphic' to object_class_enum

Revision ID: 0003_add_graphic_enum
Revises: 0002_phase2a
Create Date: 2026-05-22

The ObjectClass.GRAPHIC value was introduced in Phase 2a for KMZ-sourced
operational graphics, but the original 0002 migration overlooked adding
the value to the Postgres enum type. This migration corrects that.

ALTER TYPE ... ADD VALUE cannot run inside a transaction in older
Postgres versions, so this migration is wrapped to disable transactional
DDL for the duration. Newer versions (12+) allow it transactionally;
the IF NOT EXISTS guard makes the operation idempotent regardless.
"""

from __future__ import annotations

from alembic import op

revision = "0003_add_graphic_enum"
down_revision = "0002_phase2a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Run outside a transaction for compatibility with older Postgres.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE object_class_enum ADD VALUE IF NOT EXISTS 'graphic';"
        )


def downgrade() -> None:
    # Postgres does not support removing enum values without recreating
    # the type. Leaving 'graphic' in place on downgrade is harmless.
    pass
