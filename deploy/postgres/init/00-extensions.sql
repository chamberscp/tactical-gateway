-- Runs once when the Postgres data directory is first initialized.
-- Alembic migrations also create these (idempotent), but having them here
-- means the database is immediately usable for ad-hoc tools that don't
-- run migrations first.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;
