"""OVL ingest handler.

Ties the OVL parser into the existing Phase 1/2a pipeline:

  raw .ovl bytes
    -> capture to MinIO with SHA-256 hash chain   (reuse common.capture)
    -> parse to OvlModel                          (ovl.parser)
    -> convert each milbobject to a graphic CTO   (ovl.parser)
    -> publish on NATS subject cto.normalized.ovl (reuse common.bus)
       -> opstore subscribes and writes to Postgres (Phase 2a, unchanged)

The capture and bus interfaces mirror the KMZ ingest path added in Phase 2a, so
the opstore, audit log, and hash-chain verification all work without change.

This module depends on the shared gateway interfaces (common.capture,
common.bus, common.audit). Those are provided by the integrated repo; here we
declare the expected protocol so the handler is reviewable and unit-shaped.
"""
from __future__ import annotations

import os
from typing import Protocol, List

from ovl.parser import parse_ovl_bytes, ovl_to_cto_dicts


NATS_SUBJECT = "cto.normalized.ovl"


class CaptureStore(Protocol):
    """Phase 1 capture interface (MinIO + SHA-256 hash chain)."""
    def put(self, data: bytes, *, source: str, content_type: str) -> str:
        """Store raw bytes, extend the hash chain, return the capture URI."""
        ...


class Bus(Protocol):
    """NATS publish interface."""
    async def publish(self, subject: str, payload: dict) -> None: ...


class AuditLog(Protocol):
    """Phase 0 audit interface."""
    async def record(self, event: str, detail: dict) -> None: ...


async def ingest_ovl_bytes(
    data: bytes,
    *,
    filename: str,
    capture: CaptureStore,
    bus: Bus,
    audit: AuditLog,
) -> dict:
    """Ingest a single OVL file end to end.

    Returns a summary dict: capture_uri, overlay_name, object_count,
    conflict_count.
    """
    # 1. Capture raw bytes first (tamper-evident), same path as KMZ.
    capture_uri = capture.put(
        data, source=f"ovl:{filename}", content_type="application/xml"
    )

    # 2. Parse and convert to CTOs.
    model = parse_ovl_bytes(data)
    ctos = ovl_to_cto_dicts(model, parent_ovl_uri=capture_uri)

    # 3. Publish each CTO on NATS; opstore writes them (supersession-aware).
    conflict_count = 0
    for cto in ctos:
        # tag lineage so all graphics from this overlay are traceable
        cto["attributes"]["overlay_name"] = model.name
        cto["attributes"]["source_filename"] = filename
        if cto["attributes"].get("geometry_conflict"):
            conflict_count += 1
        await bus.publish(NATS_SUBJECT, cto)

    # 4. Audit the ingest event (sparse now, enriched in Phase 6).
    await audit.record(
        "ovl_ingested",
        {
            "filename": filename,
            "capture_uri": capture_uri,
            "overlay_name": model.name,
            "object_count": model.object_count,
            "conflict_count": conflict_count,
        },
    )

    return {
        "capture_uri": capture_uri,
        "overlay_name": model.name,
        "object_count": model.object_count,
        "conflict_count": conflict_count,
    }


async def ingest_ovl_path(
    path: str, *, capture: CaptureStore, bus: Bus, audit: AuditLog
) -> dict:
    with open(path, "rb") as f:
        data = f.read()
    return await ingest_ovl_bytes(
        data,
        filename=os.path.basename(path),
        capture=capture,
        bus=bus,
        audit=audit,
    )
