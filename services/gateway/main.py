"""Gateway service — Phase 0 placeholder.

This serves only to verify the docker-compose wiring: that Postgres,
MinIO, and NATS are reachable from inside the gateway container. Real
ingest logic lands in Phase 1.

The /health endpoint returns 200 only when all three dependencies
respond. /ready additionally checks the schema has been migrated.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import structlog
from fastapi import FastAPI, HTTPException
from minio import Minio
from nats.aio.client import Client as NATSClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------


def _env(name: str, default: str | None = None, *, required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and not val:
        raise RuntimeError(f"required environment variable not set: {name}")
    return val or ""


PG_HOST = _env("POSTGRES_HOST", "localhost")
PG_PORT = _env("POSTGRES_PORT", "5432")
PG_DB = _env("POSTGRES_DB", "gateway")
PG_USER = _env("POSTGRES_USER", "gateway")
PG_PASSWORD = _env("POSTGRES_PASSWORD", "gateway")

PG_URL = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DB}"

MINIO_ENDPOINT = _env("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = _env("MINIO_ACCESS_KEY", "gateway")
MINIO_SECRET_KEY = _env("MINIO_SECRET_KEY", "gateway-dev-password")
MINIO_BUCKET_RAW = _env("MINIO_BUCKET_RAW", "raw-captures")

NATS_URL = _env("NATS_URL", "nats://localhost:4222")


# ---------------------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------------------


class AppState:
    """Holds long-lived resources for the duration of the process."""

    engine: AsyncEngine | None = None
    minio: Minio | None = None
    nats: NATSClient | None = None


state = AppState()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    log.info("starting", pg_url=PG_URL.replace(PG_PASSWORD, "***"))

    # SQLAlchemy async engine. psycopg3 driver supports async natively.
    state.engine = create_async_engine(PG_URL, pool_size=5, max_overflow=5)

    # MinIO uses HTTP; no startup handshake. We verify in /health.
    state.minio = Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=False,  # set True behind TLS-terminating proxy in production
    )

    state.nats = NATSClient()
    await state.nats.connect(servers=[NATS_URL], max_reconnect_attempts=-1)

    # Ensure the raw bucket exists. Idempotent.
    try:
        if not state.minio.bucket_exists(MINIO_BUCKET_RAW):
            state.minio.make_bucket(MINIO_BUCKET_RAW)
            log.info("created bucket", bucket=MINIO_BUCKET_RAW)
    except Exception as e:
        log.warning("could not verify bucket on startup", error=str(e))

    log.info("started")
    try:
        yield
    finally:
        log.info("shutting down")
        if state.nats and state.nats.is_connected:
            await state.nats.close()
        if state.engine:
            await state.engine.dispose()
        log.info("shut down")


app = FastAPI(
    title="Tactical Gateway",
    version="0.1.0",
    description="Phase 0 placeholder — verifies infrastructure wiring",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Health endpoints
# ---------------------------------------------------------------------------


async def _check_postgres() -> dict[str, Any]:
    if state.engine is None:
        return {"ok": False, "detail": "engine not initialized"}
    try:
        async with state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "detail": str(e)}


def _check_minio() -> dict[str, Any]:
    if state.minio is None:
        return {"ok": False, "detail": "client not initialized"}
    try:
        state.minio.bucket_exists(MINIO_BUCKET_RAW)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "detail": str(e)}


def _check_nats() -> dict[str, Any]:
    if state.nats is None or not state.nats.is_connected:
        return {"ok": False, "detail": "not connected"}
    return {"ok": True}


@app.get("/health")
async def health() -> dict[str, Any]:
    """Liveness + dependency reachability check."""
    pg, minio_, nats_ = await asyncio.gather(
        _check_postgres(),
        asyncio.to_thread(_check_minio),
        asyncio.to_thread(_check_nats),
    )
    all_ok = pg["ok"] and minio_["ok"] and nats_["ok"]
    body = {"status": "ok" if all_ok else "degraded", "postgres": pg, "minio": minio_, "nats": nats_}
    if not all_ok:
        raise HTTPException(status_code=503, detail=body)
    return body


@app.get("/ready")
async def ready() -> dict[str, Any]:
    """Readiness — verifies the schema is migrated by checking for the cto table."""
    if state.engine is None:
        raise HTTPException(status_code=503, detail="engine not initialized")
    try:
        async with state.engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT EXISTS ("
                    "  SELECT FROM information_schema.tables "
                    "  WHERE table_schema = 'public' AND table_name = 'cto'"
                    ")"
                )
            )
            exists = result.scalar()
        if not exists:
            raise HTTPException(status_code=503, detail="schema not migrated; run alembic upgrade head")
        return {"status": "ready"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": "tactical-gateway",
        "version": "0.1.0",
        "phase": "0 — infrastructure wiring",
    }
