"""tg-opstore: NATS subscriber that writes CTOs to PostGIS.

Uses raw psycopg connections to write against the Phase 0 flat schema.
The CTO Pydantic model is structured (nested altitude, symbology, etc.)
but the DB columns are flat; the writer module handles the mapping.
"""

from __future__ import annotations

import asyncio
import json
import signal
import sys
from collections import defaultdict
from datetime import datetime, timezone

import nats
import psycopg
from cto_schema import CTO

from common import configure_logging, get_logger, get_settings

from .writer import write_ctos_with_supersession

log = get_logger(__name__)

BATCH_WINDOW_MS = 200


class Opstore:
    def __init__(self, settings):
        self.settings = settings
        # Convert SQLAlchemy-style DSN to psycopg-style by stripping the
        # +psycopg suffix. The Phase 0 postgres_url property may include it.
        dsn = settings.postgres_url
        self.dsn = dsn.replace("postgresql+psycopg://", "postgresql://")
        self._stop = asyncio.Event()
        self._messages_received = 0
        self._messages_written = 0
        self._messages_failed = 0
        self._buffer: dict[str, list[CTO]] = defaultdict(list)
        self._buffer_started_at: dict[str, float] = {}

    async def run(self) -> int:
        nc = await nats.connect(
            self.settings.nats_url,
            name="tg-opstore",
            max_reconnect_attempts=-1,
        )
        log.info("connected to nats", url=self.settings.nats_url)

        subject = self.settings.opstore_subject
        sub = await nc.subscribe(subject, cb=self._on_message)
        log.info("subscribed", subject=subject)

        flusher = asyncio.create_task(self._flush_loop())

        await self._stop.wait()

        log.info("stopping; draining")
        flusher.cancel()
        try:
            await flusher
        except asyncio.CancelledError:
            pass
        await self._flush_all()

        await sub.drain()
        await nc.drain()
        log.info(
            "opstore stopped",
            received=self._messages_received,
            written=self._messages_written,
            failed=self._messages_failed,
        )
        return 0

    async def _on_message(self, msg) -> None:
        self._messages_received += 1
        try:
            data = json.loads(msg.data.decode("utf-8"))
            cto = CTO.model_validate(data)
        except Exception as e:
            self._messages_failed += 1
            log.error(
                "invalid CTO message on bus",
                subject=msg.subject,
                error=str(e),
            )
            return

        attrs = cto.attributes or {}
        key = (
            f"kmz:{attrs.get('parent_kmz_filename')}:{attrs.get('parent_kmz_source')}"
            if attrs.get("parent_kmz_filename")
            else f"stream:{cto.source_system}"
        )
        if key not in self._buffer or not self._buffer[key]:
            self._buffer_started_at[key] = asyncio.get_event_loop().time()
        self._buffer[key].append(cto)

    async def _flush_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(BATCH_WINDOW_MS / 1000.0)
            now = asyncio.get_event_loop().time()
            ready_keys = [
                key
                for key, started in self._buffer_started_at.items()
                if (now - started) * 1000.0 >= BATCH_WINDOW_MS
                and self._buffer.get(key)
            ]
            for key in ready_keys:
                await self._flush_one(key)

    async def _flush_all(self) -> None:
        keys = list(self._buffer.keys())
        for key in keys:
            await self._flush_one(key)

    async def _flush_one(self, key: str) -> None:
        ctos = self._buffer.pop(key, [])
        self._buffer_started_at.pop(key, None)
        if not ctos:
            return
        try:
            await asyncio.to_thread(self._write_batch, ctos)
            self._messages_written += len(ctos)
        except Exception as e:
            self._messages_failed += len(ctos)
            log.error(
                "batch write failed",
                key=key,
                batch_size=len(ctos),
                error=str(e),
            )

    def _write_batch(self, ctos: list[CTO]) -> None:
        with psycopg.connect(self.dsn) as conn:
            with conn.transaction():
                inserted, superseded = write_ctos_with_supersession(conn, ctos)
            log.info(
                "batch written",
                inserted=inserted,
                superseded=superseded,
                batch_size=len(ctos),
            )

    def request_stop(self) -> None:
        self._stop.set()


def main() -> int:
    configure_logging()
    settings = get_settings()

    opstore = Opstore(settings)

    def _signal_handler(*_):
        log.info("signal received, stopping")
        opstore.request_stop()

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, opstore.request_stop)
            except NotImplementedError:
                signal.signal(sig, _signal_handler)
        return loop.run_until_complete(opstore.run())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
