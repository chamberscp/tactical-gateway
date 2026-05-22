"""KMZ folder watcher.

Monitors a configured directory and ingests any .kmz file dropped into it.
Per decision 4a (silent replace), filename collisions are NOT prompted -
the new file is processed and opstore handles same-path supersession of
prior CTOs.

After ingest the file is moved to a `.processed/` subdirectory with a
timestamp prefix so re-ingest (if intended) is possible by moving it back.
Failed files go to `.failed/` with the error appended in a sidecar .err.

Race prevention: a filename is added to the in-flight set BEFORE we start
processing, and only removed AFTER the post-move (success or failure). This
prevents the watcher from picking the same file up twice if a poll fires
mid-processing.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import datetime, timezone
from pathlib import Path

from cto_schema import IngestSource

from common import get_logger

from .kmz_ingest import KmzIngestor

log = get_logger(__name__)


class KmzFolderWatcher:
    """Polls a directory for new .kmz files."""

    def __init__(
        self,
        *,
        inbox_path: Path,
        ingestor: KmzIngestor,
        poll_interval_s: float = 2.0,
    ):
        self.inbox_path = inbox_path
        self.ingestor = ingestor
        self.poll_interval_s = poll_interval_s
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        # Filenames currently being processed (locks out re-pickup
        # while ingest+move is in progress).
        self._in_flight: set[str] = set()

    async def start(self) -> None:
        self.inbox_path.mkdir(parents=True, exist_ok=True)
        (self.inbox_path / ".processed").mkdir(exist_ok=True)
        (self.inbox_path / ".failed").mkdir(exist_ok=True)
        log.info("kmz folder watcher starting",
                 inbox=str(self.inbox_path),
                 poll_interval_s=self.poll_interval_s)
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
            if not entry.name.lower().endswith(".kmz"):
                continue
            if entry.name in self._in_flight:
                continue

            # Stability check: file size must be stable across a short
            # delay (otherwise the file is still being written).
            try:
                size1 = entry.stat().st_size
            except OSError:
                continue
            if size1 == 0:
                continue
            await asyncio.sleep(0.5)
            if entry.name in self._in_flight:
                # Another scan claimed it during our sleep
                continue
            try:
                if not entry.exists():
                    continue
                size2 = entry.stat().st_size
            except OSError:
                continue
            if size1 != size2:
                continue

            # Claim the filename BEFORE any IO that could take time
            self._in_flight.add(entry.name)
            try:
                await self._process(entry)
            finally:
                self._in_flight.discard(entry.name)

    async def _process(self, path: Path) -> None:
        log.info("kmz folder pickup",
                 filename=path.name, size=path.stat().st_size)
        try:
            data = path.read_bytes()
        except OSError as e:
            log.error("failed to read kmz file",
                      filename=path.name, error=str(e))
            return

        source_label = f"kmz-folder:{self.inbox_path}"
        result = await self.ingestor.ingest(
            kmz_bytes=data,
            filename=path.name,
            ingest_source=IngestSource.FOLDER,
            source_label=source_label,
        )

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        if result.ok:
            dest = self.inbox_path / ".processed" / f"{ts}_{path.name}"
            try:
                shutil.move(str(path), str(dest))
                log.info("kmz processed",
                         filename=path.name,
                         moved_to=str(dest),
                         features=result.features_extracted)
            except OSError as e:
                log.error("failed to move processed file",
                          filename=path.name, error=str(e))
        else:
            dest = self.inbox_path / ".failed" / f"{ts}_{path.name}"
            err_path = self.inbox_path / ".failed" / f"{ts}_{path.name}.err"
            try:
                shutil.move(str(path), str(dest))
                err_path.write_text(result.error or "unknown error")
                log.warning("kmz failed",
                            filename=path.name,
                            moved_to=str(dest),
                            error=result.error)
            except OSError as e:
                log.error("failed to move failed file",
                          filename=path.name, error=str(e))
