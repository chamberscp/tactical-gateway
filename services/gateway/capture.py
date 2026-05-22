"""Raw capture writer.

The first stop for every byte received by the gateway. Writes the raw
bytes to MinIO under a deterministic key, computes the SHA-256 and the
hash chain entry, and appends to the daily manifest.

Design notes:
- Single asyncio task serializes appends to the daily manifest so the
  chain is always consistent (no two messages compete to be "next").
- The prior chain tip is read from MinIO on startup (or the start of
  each new day) so chains survive process restarts.
- The chain manifest is append-only: each call to capture() emits one
  JSONL line to a per-day manifest object. We use S3 "append" via
  upload-and-replace because MinIO does not support native append.
  For higher throughput we could shard by hour; per-day is fine for
  Phase 1 volumes.
- All MinIO calls run in a thread executor because the `minio` library
  is synchronous. This is acceptable: capture rates are bounded by
  network I/O, not CPU, and the executor keeps the async loop responsive.
"""

from __future__ import annotations

import asyncio
import io
from datetime import datetime, timezone
from typing import Any

from cto_schema import (
    GENESIS_PREV_HASH,
    ChainEntry,
    RawPointer,
    make_entry,
)
from minio import Minio
from minio.error import S3Error

from common import get_logger

log = get_logger(__name__)


def _object_key_for(
    *, captured_at: datetime, protocol: str, sha256: str
) -> str:
    """Deterministic key layout: raw/yyyy/mm/dd/protocol/<2>/<sha>."""
    return (
        f"raw/{captured_at.year:04d}/{captured_at.month:02d}/"
        f"{captured_at.day:02d}/{protocol}/{sha256[:2]}/{sha256}"
    )


def _manifest_key_for(captured_at: datetime) -> str:
    return (
        f"raw/{captured_at.year:04d}/{captured_at.month:02d}/"
        f"{captured_at.day:02d}/manifest.jsonl"
    )


class CaptureWriter:
    """Writes raw captured bytes plus the hash chain manifest to MinIO.

    Not safe for direct concurrent use from multiple tasks; instead,
    create one writer per ingest service and have all listeners call
    capture() on it. Internal locking serializes manifest writes.
    """

    def __init__(self, minio_client: Minio, bucket: str) -> None:
        self._client = minio_client
        self._bucket = bucket
        # Per-day chain state. Resets when the day rolls over.
        self._chain_day: str | None = None       # "yyyy-mm-dd"
        self._chain_tip: str = GENESIS_PREV_HASH
        self._chain_manifest_lines: list[str] = []
        self._lock = asyncio.Lock()

    async def ensure_bucket(self) -> None:
        """Create the configured bucket if it doesn't exist. Idempotent."""
        def _do() -> None:
            try:
                if not self._client.bucket_exists(self._bucket):
                    self._client.make_bucket(self._bucket)
                    log.info("created bucket", bucket=self._bucket)
            except S3Error as e:
                log.error("bucket ensure failed", bucket=self._bucket, error=str(e))
                raise
        await asyncio.to_thread(_do)

    async def capture(
        self,
        *,
        raw_bytes: bytes,
        protocol: str,
        captured_at: datetime | None = None,
    ) -> tuple[RawPointer, ChainEntry]:
        """Persist raw bytes and produce a RawPointer + ChainEntry.

        Returns the pointer the normalizer should attach to the resulting
        CTO and the chain entry that was appended (useful for tests and
        audit logs).
        """
        if captured_at is None:
            captured_at = datetime.now(timezone.utc)

        async with self._lock:
            await self._roll_chain_if_needed(captured_at)

            # Build the chain entry deterministically from the bytes and
            # the current tip.
            object_key_placeholder = "PLACEHOLDER"  # replaced once we know sha
            entry = make_entry(
                prev_hash=self._chain_tip,
                raw_bytes=raw_bytes,
                captured_at=captured_at,
                object_key=object_key_placeholder,
                protocol=protocol,
            )
            object_key = _object_key_for(
                captured_at=captured_at, protocol=protocol, sha256=entry.sha256
            )
            # Recreate the entry with the real object_key (it does not
            # affect entry_hash by design - only sha, prev, ts go in).
            entry = ChainEntry(
                sha256=entry.sha256,
                prev_hash=entry.prev_hash,
                entry_hash=entry.entry_hash,
                captured_at=entry.captured_at,
                object_key=object_key,
                size_bytes=entry.size_bytes,
                protocol=protocol,
            )

            await self._write_object(object_key, raw_bytes)
            await self._append_manifest_line(captured_at, entry.to_jsonl())

            self._chain_tip = entry.entry_hash

        pointer = RawPointer(
            sha256=entry.sha256,
            object_key=entry.object_key,
            size_bytes=entry.size_bytes,
            captured_at=captured_at,
        )
        log.debug(
            "captured",
            protocol=protocol,
            sha256=entry.sha256[:16],
            size_bytes=entry.size_bytes,
            key=object_key,
        )
        return pointer, entry

    # --- internal helpers --------------------------------------------

    async def _roll_chain_if_needed(self, captured_at: datetime) -> None:
        """If the day has changed, load (or initialize) that day's chain."""
        day_key = f"{captured_at.year:04d}-{captured_at.month:02d}-{captured_at.day:02d}"
        if day_key == self._chain_day:
            return
        # New day - read the existing manifest if present so we continue the chain.
        manifest_key = _manifest_key_for(captured_at)
        existing = await self._read_object_or_none(manifest_key)
        if existing is None:
            self._chain_tip = GENESIS_PREV_HASH
            self._chain_manifest_lines = []
            log.info("starting new chain for day", day=day_key)
        else:
            lines = existing.decode("utf-8").splitlines()
            self._chain_manifest_lines = lines
            if lines:
                last = ChainEntry.from_jsonl(lines[-1])
                self._chain_tip = last.entry_hash
                log.info(
                    "resumed chain from manifest",
                    day=day_key,
                    entries=len(lines),
                    tip=self._chain_tip[:16],
                )
            else:
                self._chain_tip = GENESIS_PREV_HASH
        self._chain_day = day_key

    async def _read_object_or_none(self, key: str) -> bytes | None:
        def _do() -> bytes | None:
            try:
                resp = self._client.get_object(self._bucket, key)
                try:
                    return resp.read()
                finally:
                    resp.close()
                    resp.release_conn()
            except S3Error as e:
                if e.code in ("NoSuchKey", "NoSuchBucket"):
                    return None
                raise
        return await asyncio.to_thread(_do)

    async def _write_object(self, key: str, data: bytes) -> None:
        def _do() -> None:
            self._client.put_object(
                self._bucket,
                key,
                io.BytesIO(data),
                length=len(data),
            )
        await asyncio.to_thread(_do)

    async def _append_manifest_line(self, captured_at: datetime, line: str) -> None:
        """Append a line to the daily manifest.

        MinIO doesn't support native append, so we keep the day's lines
        in memory and rewrite the manifest object on each capture. For
        Phase 1 volumes this is fine; if we exceed ~10K entries per day
        we'll move to chunked manifest files.
        """
        self._chain_manifest_lines.append(line)
        body = ("\n".join(self._chain_manifest_lines) + "\n").encode("utf-8")
        await self._write_object(_manifest_key_for(captured_at), body)
