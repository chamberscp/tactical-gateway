"""Ingest listeners for CoT in various transports.

Each listener owns a network socket, parses messages off the wire,
hands the raw bytes to the capture writer, then runs the normalizer
to produce a CTO and publishes it to NATS.

Listeners are designed to be cheap to start and stop. The Gateway
process can run several at once (e.g. CoT XML on TCP/8087 plus CoT PB
on TCP/8089).
"""

from __future__ import annotations

import asyncio
import socket
from datetime import datetime, timezone
from typing import Awaitable, Callable
from xml.etree import ElementTree as ET

from cto_schema import CTO

from common import get_logger

from .capture import CaptureWriter
from .normalizers.cot_pb import CoTPbError, try_decode_frame, cot_pb_to_cto
from .normalizers.cot_xml import CoTXmlParseError, cot_xml_to_cto

log = get_logger(__name__)


# A callback the listener invokes for each successfully normalized CTO.
# The gateway main wiring connects this to NATS publishing.
CTOSink = Callable[[CTO], Awaitable[None]]


class ListenerStats:
    """Counters maintained per listener for health and the control GUI."""

    def __init__(self) -> None:
        self.messages_received: int = 0
        self.messages_normalized: int = 0
        self.parse_errors: int = 0
        self.last_message_at: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "messages_received": self.messages_received,
            "messages_normalized": self.messages_normalized,
            "parse_errors": self.parse_errors,
            "last_message_at": self.last_message_at.isoformat() if self.last_message_at else None,
        }


# ---------------------------------------------------------------------------
# CoT XML over TCP
# ---------------------------------------------------------------------------


async def _read_one_xml_event(reader: asyncio.StreamReader) -> bytes | None:
    """Read one complete <event>...</event> from the stream.

    Returns the EXACT raw bytes that arrived on the wire for that event,
    not a re-serialized form. This matters for two reasons:
    (1) Forensic integrity: the SHA-256 hash chain must cover what an
        adversary actually sent, not what our XML library re-emitted.
    (2) Round-trip fidelity: byte-for-byte preservation lets a downstream
        consumer get exactly the upstream's wire format.

    Implementation: we accumulate bytes into a buffer. After each chunk
    arrives, we scan for closing tags '</event>' (with or without
    whitespace before '>'). When we find one, we slice from the start of
    the buffer through the end of that tag, return those bytes, and keep
    the remainder in the buffer (held in StreamReader-style by leaving
    leftover bytes for the next call - we use a small reader-wrapping
    state on the StreamReader itself).
    """
    # We attach a private buffer to the reader so concatenated events
    # work: the second event's leading bytes may have arrived in the
    # same chunk that closed the first event.
    buf: bytearray = getattr(reader, "_xml_buf", None)
    if buf is None:
        buf = bytearray()
        setattr(reader, "_xml_buf", buf)

    CLOSE_TAG = b"</event>"

    while True:
        # Try to find a complete event in the current buffer.
        idx = buf.find(CLOSE_TAG)
        if idx != -1:
            end = idx + len(CLOSE_TAG)
            payload = bytes(buf[:end])
            del buf[:end]
            # Strip leading whitespace between events.
            while buf and buf[:1] in (b" ", b"\t", b"\r", b"\n"):
                del buf[:1]
            # Validate it really is a complete <event> by trying to parse it.
            try:
                root = ET.fromstring(payload)
                if root.tag != "event":
                    raise CoTXmlParseError(f"expected <event>, got <{root.tag}>")
            except ET.ParseError as e:
                raise CoTXmlParseError(f"event parse error: {e}") from e
            return payload

        # Not enough bytes yet, read more.
        chunk = await reader.read(4096)
        if not chunk:
            # EOF. If buffer has leftover bytes, that's truncated data.
            if buf.strip():
                raise CoTXmlParseError("connection closed mid-event")
            return None
        buf.extend(chunk)


class CoTXmlTcpListener:
    """Accepts CoT XML over TCP. One client at a time per connection."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        capture: CaptureWriter,
        sink: CTOSink,
        source_system_prefix: str = "cot-xml-tcp",
    ) -> None:
        self._host = host
        self._port = port
        self._capture = capture
        self._sink = sink
        self._source_prefix = source_system_prefix
        self._server: asyncio.AbstractServer | None = None
        self.stats = ListenerStats()

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, self._host, self._port)
        log.info("listening", proto="cot_xml_tcp", host=self._host, port=self._port)

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        log.info("stopped", proto="cot_xml_tcp", host=self._host, port=self._port)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        peer_str = f"{peer[0]}:{peer[1]}" if peer else "unknown"
        source_system = f"{self._source_prefix}:{peer_str}"
        log.info("client connected", proto="cot_xml_tcp", peer=peer_str)
        try:
            while True:
                try:
                    payload = await _read_one_xml_event(reader)
                except CoTXmlParseError as e:
                    self.stats.parse_errors += 1
                    log.warning("xml parse error, closing client", peer=peer_str, error=str(e))
                    break
                if payload is None:
                    break  # EOF
                await self._handle_message(payload, source_system)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            log.info("client disconnected", proto="cot_xml_tcp", peer=peer_str)

    async def _handle_message(self, payload: bytes, source_system: str) -> None:
        received_at = datetime.now(timezone.utc)
        self.stats.messages_received += 1
        self.stats.last_message_at = received_at

        pointer, _entry = await self._capture.capture(
            raw_bytes=payload, protocol="cot_xml", captured_at=received_at
        )
        try:
            cto = cot_xml_to_cto(
                xml_bytes=payload,
                source_system=source_system,
                received_at=received_at,
                raw_pointer=pointer,
            )
        except CoTXmlParseError as e:
            self.stats.parse_errors += 1
            log.warning("xml normalize error", source=source_system, error=str(e))
            return
        await self._sink(cto)
        self.stats.messages_normalized += 1


# ---------------------------------------------------------------------------
# CoT XML over UDP
# ---------------------------------------------------------------------------


class _CoTXmlUdpProtocol(asyncio.DatagramProtocol):
    """Async UDP receiver. Each datagram is one CoT XML event."""

    def __init__(
        self,
        *,
        capture: CaptureWriter,
        sink: CTOSink,
        source_prefix: str,
        stats: ListenerStats,
    ) -> None:
        self._capture = capture
        self._sink = sink
        self._source_prefix = source_prefix
        self._stats = stats
        self._loop = asyncio.get_event_loop()

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        # Schedule the actual work in an asyncio task to keep this method fast.
        peer = f"{addr[0]}:{addr[1]}"
        self._loop.create_task(self._process(data, peer))

    async def _process(self, data: bytes, peer: str) -> None:
        received_at = datetime.now(timezone.utc)
        self._stats.messages_received += 1
        self._stats.last_message_at = received_at
        source_system = f"{self._source_prefix}:{peer}"
        try:
            pointer, _ = await self._capture.capture(
                raw_bytes=data, protocol="cot_xml", captured_at=received_at
            )
            cto = cot_xml_to_cto(
                xml_bytes=data,
                source_system=source_system,
                received_at=received_at,
                raw_pointer=pointer,
            )
        except CoTXmlParseError as e:
            self._stats.parse_errors += 1
            log.warning("udp xml parse/normalize error", peer=peer, error=str(e))
            return
        await self._sink(cto)
        self._stats.messages_normalized += 1


class CoTXmlUdpListener:
    """UDP receiver for CoT XML; supports unicast and multicast.

    For multicast, pass `multicast_group` (e.g. "239.2.3.1") to join that
    group on all interfaces. Leave None for plain unicast bind.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        capture: CaptureWriter,
        sink: CTOSink,
        multicast_group: str | None = None,
        source_system_prefix: str = "cot-xml-udp",
    ) -> None:
        self._host = host
        self._port = port
        self._capture = capture
        self._sink = sink
        self._mcast = multicast_group
        self._source_prefix = source_system_prefix
        self._transport: asyncio.BaseTransport | None = None
        self.stats = ListenerStats()

    async def start(self) -> None:
        # For multicast we need to control the socket so we can join groups.
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        bind_host = "0.0.0.0" if self._mcast else self._host
        sock.bind((bind_host, self._port))
        if self._mcast:
            mreq = socket.inet_aton(self._mcast) + socket.inet_aton("0.0.0.0")
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            log.info("joined multicast group", group=self._mcast, port=self._port)

        loop = asyncio.get_event_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _CoTXmlUdpProtocol(
                capture=self._capture,
                sink=self._sink,
                source_prefix=self._source_prefix,
                stats=self.stats,
            ),
            sock=sock,
        )
        log.info("listening", proto="cot_xml_udp", host=bind_host, port=self._port)

    async def stop(self) -> None:
        if self._transport is not None:
            self._transport.close()


# ---------------------------------------------------------------------------
# CoT Protobuf over TCP
# ---------------------------------------------------------------------------


class CoTPbTcpListener:
    """Accepts TAK-protocol protobuf framed CoT messages over TCP."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        capture: CaptureWriter,
        sink: CTOSink,
        source_system_prefix: str = "cot-pb-tcp",
    ) -> None:
        self._host = host
        self._port = port
        self._capture = capture
        self._sink = sink
        self._source_prefix = source_system_prefix
        self._server: asyncio.AbstractServer | None = None
        self.stats = ListenerStats()

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, self._host, self._port)
        log.info("listening", proto="cot_pb_tcp", host=self._host, port=self._port)

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        log.info("stopped", proto="cot_pb_tcp", host=self._host, port=self._port)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        peer_str = f"{peer[0]}:{peer[1]}" if peer else "unknown"
        source_system = f"{self._source_prefix}:{peer_str}"
        log.info("client connected", proto="cot_pb_tcp", peer=peer_str)
        buf = bytearray()
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    break
                buf.extend(chunk)
                # Decode as many complete frames as are available.
                while True:
                    try:
                        payload, consumed = try_decode_frame(buf)
                    except CoTPbError as e:
                        self.stats.parse_errors += 1
                        log.warning("pb framing error, closing client", peer=peer_str, error=str(e))
                        return
                    if payload is None:
                        break  # incomplete; wait for more bytes
                    del buf[:consumed]
                    await self._handle_message(payload, source_system)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            log.info("client disconnected", proto="cot_pb_tcp", peer=peer_str)

    async def _handle_message(self, payload: bytes, source_system: str) -> None:
        received_at = datetime.now(timezone.utc)
        self.stats.messages_received += 1
        self.stats.last_message_at = received_at

        # We capture the FRAMED bytes - that's what arrived on the wire.
        # The payload-only bytes are reconstructable from the frame, but
        # storing the frame is the more honest capture of "what we received".
        # However, for the normalizer to work we need the payload only.
        # Compromise: capture the payload (which is what's meaningfully
        # the message) and tag the protocol accordingly. We can revisit
        # to add a "raw_framed" pointer if forensics ever needs it.
        pointer, _entry = await self._capture.capture(
            raw_bytes=payload, protocol="cot_pb", captured_at=received_at
        )
        try:
            cto = cot_pb_to_cto(
                pb_bytes=payload,
                source_system=source_system,
                received_at=received_at,
                raw_pointer=pointer,
            )
        except CoTPbError as e:
            self.stats.parse_errors += 1
            log.warning("pb normalize error", source=source_system, error=str(e))
            return
        await self._sink(cto)
        self.stats.messages_normalized += 1