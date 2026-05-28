"""Folder watcher with per-extension dispatch.

Generalizes the Phase 2a KmzFolderWatcher (Option A, ADR-0011 follow-on). The
polling, file-stability check, race prevention, and processed/failed handling
are unchanged from the KMZ watcher; the only difference is that the extension
and the ingest call are now looked up from a handler registry instead of being
hard-coded to .kmz.

A handler is any object exposing:

    async def ingest(*, data: bytes, filename: str,
                     ingest_source: IngestSource, source_label: str) -> Result

where Result has .ok (bool), .error (str | None), and an optional integer count
(.features_extracted for KMZ, .objects_ingested for OVL). The watcher only reads
.ok / .error for control flow and logs the count generically.

Migration from KmzFolderWatcher:
    watcher = FolderWatcher(inbox_path=..., poll_interval_s=...)
    watcher.register(".kmz", kmz_ingestor)
    watcher.register(".ovl", ovl_ingestor)
    await watcher.start()

Behavior for .kmz is identical to the old watcher.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Protocol

from cto_schema import IngestSource

from common import get_logger

log = get_logger(__name__)


class Ingestor(Protocol):
    async def ingest(
        self,
        *,
        data: bytes,
        filename: str,
        ingest_source: IngestSource,
        source_label: str,
    ): ...


class FolderWatcher:
    """Polls a directory and dispatches new files to per-extension ingestors."""

    def __init__(
        self,
        *,
        inbox_path: Path,
        poll_interval_s: float = 2.0,
    ):
        self.inbox_path = inbox_path
        self.poll_interval_s = poll_interval_s
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._in_flight: set[str] = set()
        # extension (lowercase, with dot) -> ingestor
        self._handlers: Dict[str, Ingestor] = {}

    def register(self, extension: str, ingestor: Ingestor) -> None:
        """Register an ingestor for a file extension (e.g. '.kmz', '.ovl')."""
        ext = extension.lower()
        if not ext.startswith("."):
            ext = "." + ext
        self._handlers[ext] = ingestor
        log.info("folder watcher handler registered", extension=ext)

    def _match_handler(self, filename: str) -> Ingestor | None:
        lname = filename.lower()
        for ext, handler in self._handlers.items():
            if lname.endswith(ext):
                return handler
        return None

    async def start(self) -> None:
        self.inbox_path.mkdir(parents=True, exist_ok=True)
        (self.inbox_path / ".processed").mkdir(exist_ok=True)
        (self.inbox_path / ".failed").mkdir(exist_ok=True)
        log.info(
            "folder watcher starting",
            inbox=str(self.inbox_path),
            poll_interval_s=self.poll_interval_s,
            extensions=sorted(self._handlers.keys()),
        )
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._scan_once()
            except Exception as e:
                log.error("folder watcher scan failed", error=str(e))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval_s)
                break  # stop was set
            except asyncio.TimeoutError:
                pass

    async def _scan_once(self) -> None:
        for entry in sorted(self.inbox_path.iterdir()):
            if entry.is_dir():
                continue
            handler = self._match_handler(entry.name)
            if handler is None:
                continue  # extension not registered; ignore
            if entry.name in self._in_flight:
                continue

            # Stability check: size must be stable across a short delay.
            try:
                size1 = entry.stat().st_size
            except OSError:
                continue
            if size1 == 0:
                continue
            await asyncio.sleep(0.5)
            if entry.name in self._in_flight:
                continue
            try:
                if not entry.exists():
                    continue
                size2 = entry.stat().st_size
            except OSError:
                continue
            if size1 != size2:
                continue

            self._in_flight.add(entry.name)
            try:
                await self._process(entry, handler)
            finally:
                self._in_flight.discard(entry.name)

    async def _process(self, path: Path, handler: Ingestor) -> None:
        log.info("folder pickup", filename=path.name, size=path.stat().st_size)
        try:
            data = path.read_bytes()
        except OSError as e:
            log.error("failed to read file", filename=path.name, error=str(e))
            return

        source_label = f"folder:{self.inbox_path}"
        result = await handler.ingest(
            data=data,
            filename=path.name,
            ingest_source=IngestSource.FOLDER,
            source_label=source_label,
        )

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        if result.ok:
            dest = self.inbox_path / ".processed" / f"{ts}_{path.name}"
            try:
                shutil.move(str(path), str(dest))
                # count field differs by format; log whichever is present
                count = getattr(result, "features_extracted", None)
                if count is None:
                    count = getattr(result, "objects_ingested", None)
                log.info("file processed",
                         filename=path.name, moved_to=str(dest), count=count)
            except OSError as e:
                log.error("failed to move processed file",
                          filename=path.name, error=str(e))
        else:
            dest = self.inbox_path / ".failed" / f"{ts}_{path.name}"
            err_path = self.inbox_path / ".failed" / f"{ts}_{path.name}.err"
            try:
                shutil.move(str(path), str(dest))
                err_path.write_text(result.error or "unknown error")
                log.warning("file failed",
                            filename=path.name, moved_to=str(dest),
                            error=result.error)
            except OSError as e:
                log.error("failed to move failed file",
                          filename=path.name, error=str(e))
