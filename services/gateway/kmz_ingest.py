"""KMZ ingest core - shared between folder watcher and HTTP upload paths.

Both paths converge here:
1. Validate the bytes look like a KMZ.
2. CaptureWriter persists the bytes to MinIO with a hash chain entry.
3. KMZ parser produces one CTO per placemark.
4. NatsPublisher publishes each CTO (subject derived from object_class).
5. Return a summary for the caller.

Uses the real Phase 1 interfaces:
  CaptureWriter.capture(*, raw_bytes, protocol, captured_at) -> (RawPointer, ChainEntry)
  NatsPublisher.publish_cto(cto)
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from cto_schema import IngestSource

from common import get_logger

from .capture import CaptureWriter
from .kmz_parser import KmzParseError, kmz_to_ctos
from .nats_publisher import NatsPublisher

log = get_logger(__name__)


@dataclass(frozen=True)
class KmzIngestResult:
    """Summary of an ingest attempt."""
    ok: bool
    filename: str
    sha256: str | None
    object_key: str | None
    features_extracted: int
    error: str | None = None


class KmzIngestor:
    """Stateful KMZ ingestor that owns capture and publish."""

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
        kmz_bytes: bytes,
        filename: str,
        ingest_source: IngestSource,
        source_label: str,
    ) -> KmzIngestResult:
        """Ingest one KMZ file. `source_label` becomes CTO.source_system,
        e.g. 'kmz-folder:/inbox/kmz' or 'kmz-upload:192.0.2.10'."""
        received_at = datetime.now(timezone.utc)

        # Quick sanity check (KMZ is a zip, all zips start with PK)
        if len(kmz_bytes) < 4 or kmz_bytes[:2] != b"PK":
            return KmzIngestResult(
                ok=False, filename=filename, sha256=None, object_key=None,
                features_extracted=0,
                error="not a zip file (KMZ must begin with PK signature)",
            )

        sha256 = hashlib.sha256(kmz_bytes).hexdigest()

        # Capture - uses the real Phase 1 interface
        try:
            raw_pointer, chain_entry = await self.capture_writer.capture(
                raw_bytes=kmz_bytes,
                protocol="kmz",
                captured_at=received_at,
            )
        except Exception as e:
            log.error("capture failed", filename=filename, error=str(e))
            return KmzIngestResult(
                ok=False, filename=filename, sha256=sha256, object_key=None,
                features_extracted=0, error=f"capture failed: {e}",
            )

        # Parse
        try:
            ctos = kmz_to_ctos(
                kmz_bytes=kmz_bytes,
                filename=filename,
                source_system=source_label,
                received_at=received_at,
                raw_pointer=raw_pointer,
                ingest_source=ingest_source,
            )
        except KmzParseError as e:
            log.error("kmz parse failed", filename=filename, error=str(e))
            return KmzIngestResult(
                ok=False, filename=filename, sha256=sha256,
                object_key=raw_pointer.object_key,
                features_extracted=0, error=f"parse failed: {e}",
            )

        # Publish - uses the real Phase 1 publish_cto interface
        published = 0
        for cto in ctos:
            try:
                await self.publisher.publish_cto(cto)
                published += 1
            except Exception as e:
                log.error("publish failed for cto",
                          uid=str(cto.uid), error=str(e))

        log.info("kmz ingested",
                 filename=filename,
                 sha256=sha256[:16],
                 features=len(ctos),
                 published=published,
                 ingest_source=ingest_source.value)

        return KmzIngestResult(
            ok=True,
            filename=filename,
            sha256=sha256,
            object_key=raw_pointer.object_key,
            features_extracted=len(ctos),
        )
