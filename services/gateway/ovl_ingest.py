"""OVL ingest core - shared between folder watcher and HTTP upload paths.

Mirrors KmzIngestor (services/gateway/kmz_ingest.py) exactly so the generalized
FolderWatcher treats KMZ and OVL identically. The only differences from the KMZ
ingestor are:
  * the validation check (OVL is XML beginning with '<', not a PK zip),
  * protocol="ovl" on capture,
  * ovl_to_ctos(...) instead of kmz_to_ctos(...).

Both converge on the same Phase 1 interfaces:
  CaptureWriter.capture(*, raw_bytes, protocol, captured_at) -> (RawPointer, ChainEntry)
  NatsPublisher.publish_cto(cto)

The result type matches the KMZ contract (.ok/.error/.object_key) but reports
.objects_ingested instead of .features_extracted, since "feature" is KMZ
terminology and "object" (milbobject) is OVL terminology. The FolderWatcher
reads either count generically.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from cto_schema import IngestSource

from common import get_logger

from .capture import CaptureWriter
from .nats_publisher import NatsPublisher
from .ovl.parser import ovl_to_ctos, OvlParseError

log = get_logger(__name__)


@dataclass(frozen=True)
class OvlIngestResult:
    """Summary of an OVL ingest attempt (parallels KmzIngestResult)."""
    ok: bool
    filename: str
    sha256: str | None
    object_key: str | None
    objects_ingested: int
    conflict_count: int = 0
    error: str | None = None


class OvlIngestor:
    """Stateful OVL ingestor that owns capture and publish.

    Constructor deps are identical to KmzIngestor, so main.py builds it the same
    way and registers it on the shared FolderWatcher.
    """

    def __init__(
        self,
        *,
        capture_writer: CaptureWriter,
        publisher: NatsPublisher,
    ):
        self.capture_writer = capture_writer
        self.publisher = publisher

    async def ingest(
        self,
        *,
        data: bytes,
        filename: str,
        ingest_source: IngestSource,
        source_label: str,
    ) -> OvlIngestResult:
        """Ingest one OVL file. `source_label` becomes CTO.source_system,
        e.g. 'ovl-folder:/inbox' or 'ovl-upload:192.0.2.10'."""
        received_at = datetime.now(timezone.utc)

        # Sanity check: real OVLs are XML. Accept an optional BOM / leading
        # whitespace before the '<'.
        head = data[:64].lstrip()
        if len(data) < 16 or not head.startswith(b"<"):
            return OvlIngestResult(
                ok=False, filename=filename, sha256=None, object_key=None,
                objects_ingested=0,
                error="not an XML OVL (expected '<' at start of document)",
            )

        sha256 = hashlib.sha256(data).hexdigest()

        # Capture - identical Phase 1 interface, protocol tag "ovl"
        try:
            raw_pointer, chain_entry = await self.capture_writer.capture(
                raw_bytes=data,
                protocol="ovl",
                captured_at=received_at,
            )
        except Exception as e:
            log.error("capture failed", filename=filename, error=str(e))
            return OvlIngestResult(
                ok=False, filename=filename, sha256=sha256, object_key=None,
                objects_ingested=0, error=f"capture failed: {e}",
            )

        # Parse
        try:
            ctos = ovl_to_ctos(
                ovl_bytes=data,
                filename=filename,
                source_system=source_label,
                received_at=received_at,
                raw_pointer=raw_pointer,
                ingest_source=ingest_source,
            )
        except OvlParseError as e:
            log.error("ovl parse failed", filename=filename, error=str(e))
            return OvlIngestResult(
                ok=False, filename=filename, sha256=sha256,
                object_key=raw_pointer.object_key,
                objects_ingested=0, error=f"parse failed: {e}",
            )

        # Publish - identical Phase 1 publish_cto interface
        published = 0
        conflicts = 0
        for cto in ctos:
            # geometry_conflict lives in attributes; count for the summary
            attrs = getattr(cto, "attributes", {}) or {}
            if attrs.get("geometry_conflict"):
                conflicts += 1
            try:
                await self.publisher.publish_cto(cto)
                published += 1
            except Exception as e:
                log.error("publish failed for cto",
                          uid=str(getattr(cto, "uid", "?")), error=str(e))

        log.info("ovl ingested",
                 filename=filename,
                 sha256=sha256[:16],
                 objects=len(ctos),
                 published=published,
                 conflicts=conflicts,
                 ingest_source=ingest_source.value)

        return OvlIngestResult(
            ok=True,
            filename=filename,
            sha256=sha256,
            object_key=raw_pointer.object_key,
            objects_ingested=len(ctos),
            conflict_count=conflicts,
        )
