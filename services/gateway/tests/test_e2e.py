"""End-to-end integration test for the Phase 1 pipeline.

Uses an in-memory fake for MinIO and NATS, plus a real asyncio TCP
listener as the egress target, to exercise the full flow:

    bytes on wire -> capture -> normalize -> route engine -> bytes on wire

This test does not require docker; it runs in pure Python so it can run
in CI and during development before bringing up the stack.

Run with:
    pytest services/gateway/tests/test_e2e.py -v
"""

from __future__ import annotations

import asyncio
import io
from datetime import datetime, timezone
from typing import Any

import pytest

from cto_schema import (
    CTO,
    ObjectClass,
    RawPointer,
    SourceProtocol,
)

from services.gateway.capture import CaptureWriter
from services.gateway.listeners import CoTXmlTcpListener
from services.gateway.route_engine import RouteEngine
from services.gateway.routes_model import (
    Destination,
    DestinationKind,
    OutputFormat,
    Route,
    RouteMatch,
    RoutesConfig,
)


# ---------------------------------------------------------------------------
# Fake MinIO that satisfies the small interface CaptureWriter uses
# ---------------------------------------------------------------------------


class FakeMinio:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], bytes] = {}
        self.buckets: set[str] = set()

    def bucket_exists(self, bucket: str) -> bool:
        return bucket in self.buckets

    def make_bucket(self, bucket: str) -> None:
        self.buckets.add(bucket)

    def put_object(self, bucket: str, key: str, data, length: int) -> None:
        # Accept BytesIO or bytes.
        if hasattr(data, "read"):
            self.store[(bucket, key)] = data.read()
        else:
            self.store[(bucket, key)] = bytes(data)

    def get_object(self, bucket: str, key: str):
        from minio.error import S3Error
        if (bucket, key) not in self.store:
            # Construct S3Error in a version-tolerant way: different minio
            # client releases reorder the constructor's positional args
            # and which ones are required. We only need the `code`
            # attribute to be 'NoSuchKey' so the CaptureWriter recognises
            # the absence.
            err = S3Error.__new__(S3Error)
            err.code = "NoSuchKey"
            err._code = "NoSuchKey"  # some versions use a private attr
            err.message = "no such key"
            Exception.__init__(err, "NoSuchKey: no such key")
            raise err

        class _Resp:
            def __init__(self, payload: bytes) -> None:
                self._payload = payload

            def read(self) -> bytes:
                return self._payload

            def close(self) -> None:
                pass

            def release_conn(self) -> None:
                pass

        return _Resp(self.store[(bucket, key)])


# ---------------------------------------------------------------------------
# Simple TCP collector to act as the route engine's egress destination
# ---------------------------------------------------------------------------


class TcpCollector:
    """Accepts TCP connections and collects all bytes received."""

    def __init__(self) -> None:
        self.received: bytearray = bytearray()
        self._server: asyncio.AbstractServer | None = None
        self._client_done = asyncio.Event()

    async def start(self, port: int) -> None:
        async def on_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            try:
                while True:
                    chunk = await reader.read(4096)
                    if not chunk:
                        break
                    self.received.extend(chunk)
                    self._client_done.set()
            finally:
                writer.close()
        self._server = await asyncio.start_server(on_client, "127.0.0.1", port)

    async def wait_for_data(self, timeout: float = 5.0) -> None:
        await asyncio.wait_for(self._client_done.wait(), timeout=timeout)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


SAMPLE_XML = b"""<event version="2.0" uid="ITEST-1" type="a-f-G-U-C" time="2026-05-20T12:00:00Z" start="2026-05-20T12:00:00Z" stale="2026-05-20T12:05:00Z" how="m-g"><point lat="34.5054000" lon="-77.4360000" hae="42.00" ce="2.50" le="5.00"/><detail><contact callsign="ITEST-6"/><track course="180.00" speed="2.50"/></detail></event>"""


async def _free_port() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_xml_capture_normalize_route_egress():
    # Spin up the egress collector on a free port.
    egress_port = await _free_port()
    collector = TcpCollector()
    await collector.start(egress_port)

    # Build the capture writer against a fake MinIO.
    fake_minio = FakeMinio()
    capture = CaptureWriter(fake_minio, bucket="raw-captures")
    await capture.ensure_bucket()

    # Build a route engine with one route that forwards every XML message
    # to our collector in CoT XML.
    routes_cfg = RoutesConfig(routes=[
        Route(
            id="test-egress",
            enabled=True,
            match=RouteMatch(source_protocol="cot_xml"),
            destination=Destination(
                kind=DestinationKind.TCP, host="127.0.0.1",
                port=egress_port, format=OutputFormat.COT_XML,
            ),
        ),
    ])
    engine = RouteEngine(routes_cfg)

    # The sink for the listener: hand to the route engine.
    async def sink(cto: CTO) -> None:
        await engine.handle_cto(cto)

    # Spin up the XML TCP listener on another free port.
    listener_port = await _free_port()
    listener = CoTXmlTcpListener(
        host="127.0.0.1", port=listener_port,
        capture=capture, sink=sink,
    )
    await listener.start()

    try:
        # Now connect as a client and send the sample CoT message.
        reader, writer = await asyncio.open_connection("127.0.0.1", listener_port)
        writer.write(SAMPLE_XML)
        await writer.drain()

        # Wait for the egress collector to receive the forwarded message.
        await collector.wait_for_data(timeout=5.0)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

        # Verify capture: a raw object was stored under the right bucket.
        assert len(fake_minio.store) >= 2  # at least the raw + the manifest
        raw_keys = [k for (b, k) in fake_minio.store if k.startswith("raw/") and "manifest" not in k]
        assert len(raw_keys) == 1
        assert fake_minio.store[("raw-captures", raw_keys[0])] == SAMPLE_XML

        # Verify the manifest exists.
        manifest_keys = [k for (b, k) in fake_minio.store if "manifest.jsonl" in k]
        assert len(manifest_keys) == 1

        # Verify the collector received valid CoT XML.
        from xml.etree import ElementTree as ET
        received_str = bytes(collector.received).decode("utf-8")
        # The collector may have received multiple bytes; find an <event>.
        # ET.fromstring will parse the first one.
        out = ET.fromstring(received_str)
        assert out.tag == "event"
        assert out.get("uid") == "ITEST-1"
        assert out.get("type") == "a-f-G-U-C"

        # Verify route stats updated.
        stats = engine.stats["test-egress"]
        assert stats.matched >= 1
        assert stats.sent >= 1
    finally:
        await listener.stop()
        await engine.close()
        await collector.stop()