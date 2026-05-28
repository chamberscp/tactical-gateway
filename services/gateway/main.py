"""Gateway service main.

Wires together:
- Listeners (CoT XML TCP, CoT XML UDP, CoT PB TCP)
- Capture writer (MinIO + hash chain)
- Normalizers (called inline by listeners)
- NATS publisher (for downstream consumers)
- Route engine (forwards CTOs to configured destinations)
- HTTP API (health, ready, routes inventory and toggle)
- Phase 2a: KMZ ingest (folder watch + HTTP upload), Query API
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from cto_schema import CTO
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from minio import Minio
from nats.aio.client import Client as NATSClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from common import configure_logging, get_logger, get_settings

from .api_phase2a import build_routes as build_phase2a_routes
from .capture import CaptureWriter
from .kmz_ingest import KmzIngestor
from .folder_watcher import FolderWatcher
from .ovl_ingest import OvlIngestor
from .listeners import (
    CoTPbTcpListener,
    CoTXmlTcpListener,
    CoTXmlUdpListener,
    ListenerStats,
)
from .nats_publisher import NatsPublisher
from .route_engine import RouteEngine
from .routes_model import load_routes

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Application state
# ---------------------------------------------------------------------------

class AppState:
    engine: AsyncEngine | None = None
    minio: Minio | None = None
    nats: NATSClient | None = None
    capture: CaptureWriter | None = None
    publisher: NatsPublisher | None = None
    route_engine: RouteEngine | None = None
    listeners: list = []
    listener_stats: dict[str, ListenerStats] = {}
    # Phase 2a
    kmz_ingestor: "KmzIngestor | None" = None
    folder_watcher: "FolderWatcher | None" = None
    db_dsn: str = ""


state = AppState()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    log.info("starting gateway", version="0.2.0")

    # Postgres
    state.engine = create_async_engine(settings.postgres_url, pool_size=5, max_overflow=5)

    # MinIO
    state.minio = Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )
    state.capture = CaptureWriter(state.minio, settings.minio_bucket_raw)
    await state.capture.ensure_bucket()

    # NATS
    state.nats = NATSClient()
    await state.nats.connect(servers=[settings.nats_url], max_reconnect_attempts=-1)
    state.publisher = NatsPublisher(state.nats)

    # Routes
    routes = load_routes(settings.routes_config_path)
    state.route_engine = RouteEngine(routes)
    log.info("loaded routes", count=len(routes.routes))

    # The sink: each CTO goes both to NATS and through the route engine.
    async def sink(cto: CTO) -> None:
        try:
            await state.publisher.publish_cto(cto)
        except Exception as e:
            log.warning("nats publish failed", error=str(e))
        await state.route_engine.handle_cto(cto)

    # Listeners
    state.listeners = []
    state.listener_stats = {}

    if settings.cot_xml_tcp_port > 0:
        listener = CoTXmlTcpListener(
            host="0.0.0.0",
            port=settings.cot_xml_tcp_port,
            capture=state.capture,
            sink=sink,
        )
        await listener.start()
        state.listeners.append(listener)
        state.listener_stats[f"cot_xml_tcp:{settings.cot_xml_tcp_port}"] = listener.stats

    if settings.cot_xml_udp_port > 0:
        listener = CoTXmlUdpListener(
            host="0.0.0.0",
            port=settings.cot_xml_udp_port,
            capture=state.capture,
            sink=sink,
            multicast_group=settings.cot_xml_udp_group or None,
        )
        await listener.start()
        state.listeners.append(listener)
        state.listener_stats[f"cot_xml_udp:{settings.cot_xml_udp_port}"] = listener.stats

    if settings.cot_pb_tcp_port > 0:
        listener = CoTPbTcpListener(
            host="0.0.0.0",
            port=settings.cot_pb_tcp_port,
            capture=state.capture,
            sink=sink,
        )
        await listener.start()
        state.listeners.append(listener)
        state.listener_stats[f"cot_pb_tcp:{settings.cot_pb_tcp_port}"] = listener.stats

    # ---- Phase 2a additions ----

    # KMZ ingestor
    state.kmz_ingestor = KmzIngestor(
        capture_writer=state.capture,
        publisher=state.publisher,
    )

    # Optional folder watcher
    inbox_path = getattr(settings, "kmz_inbox_path", "") or ""
    if inbox_path:
        state.ovl_ingestor = OvlIngestor(
            capture_writer=state.capture,
            publisher=state.publisher,
        )
        state.folder_watcher = FolderWatcher(
            inbox_path=Path(inbox_path),
            poll_interval_s=getattr(settings, "kmz_inbox_poll_interval_s", 2.0),
        )
        state.folder_watcher.register(".kmz", state.kmz_ingestor)
        state.folder_watcher.register(".ovl", state.ovl_ingestor)
        await state.folder_watcher.start()

    # Mount Phase 2a routes (POST /ingest/kmz, GET /cto/...)
    db_dsn = settings.postgres_url
    db_dsn = db_dsn.replace("postgresql+psycopg://", "postgresql://")
    db_dsn = db_dsn.replace("postgresql+asyncpg://", "postgresql://")
    state.db_dsn = db_dsn
    phase2a_router = build_phase2a_routes(
        kmz_ingestor=state.kmz_ingestor,
        db_dsn=state.db_dsn,
    )
    _app.include_router(phase2a_router)

    log.info("gateway started")
    try:
        yield
    finally:
        log.info("shutting down")
        # Phase 2a shutdown
        if state.folder_watcher is not None:
            try:
                await state.folder_watcher.stop()
            except Exception:
                pass
        for listener in state.listeners:
            try:
                await listener.stop()
            except Exception:
                pass
        if state.route_engine is not None:
            await state.route_engine.close()
        if state.nats is not None and state.nats.is_connected:
            await state.nats.close()
        if state.engine is not None:
            await state.engine.dispose()
        log.info("gateway shut down")


app = FastAPI(
    title="Tactical Gateway",
    version="0.3.0",
    description="Phase 2a: CoT capture/normalize/route + KMZ ingest + query API.",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# HTTP endpoints (Phase 1)
# ---------------------------------------------------------------------------

@app.get("/")
async def root() -> dict:
    return {"service": "tactical-gateway", "version": "0.3.0", "phase": "2a"}


@app.get("/health")
async def health() -> dict:
    pg_ok = True
    minio_ok = True
    nats_ok = state.nats is not None and state.nats.is_connected
    try:
        async with state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        pg_ok = False
    try:
        await asyncio.to_thread(state.minio.bucket_exists, get_settings().minio_bucket_raw)
    except Exception:
        minio_ok = False
    body = {
        "status": "ok" if (pg_ok and minio_ok and nats_ok) else "degraded",
        "postgres": {"ok": pg_ok},
        "minio": {"ok": minio_ok},
        "nats": {"ok": nats_ok},
    }
    if body["status"] != "ok":
        return JSONResponse(status_code=503, content=body)
    return body


@app.get("/ready")
async def ready() -> dict:
    try:
        async with state.engine.connect() as conn:
            result = await conn.execute(text(
                "SELECT EXISTS (SELECT FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'cto')"
            ))
            ok = result.scalar()
        if not ok:
            raise HTTPException(503, "schema not migrated")
        return {"status": "ready"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, str(e))


@app.get("/listeners")
async def listeners_inventory() -> dict:
    return {
        name: stats.to_dict()
        for name, stats in state.listener_stats.items()
    }


@app.get("/routes")
async def routes_inventory() -> dict:
    if state.route_engine is None:
        return {"routes": []}
    return {"routes": state.route_engine.list_routes()}


@app.post("/routes/{route_id}/enable")
async def route_enable(route_id: str) -> dict:
    if state.route_engine is None or not state.route_engine.set_enabled(route_id, True):
        raise HTTPException(404, f"route not found: {route_id}")
    return {"route_id": route_id, "enabled": True}


@app.post("/routes/{route_id}/disable")
async def route_disable(route_id: str) -> dict:
    if state.route_engine is None or not state.route_engine.set_enabled(route_id, False):
        raise HTTPException(404, f"route not found: {route_id}")
    return {"route_id": route_id, "enabled": False}
