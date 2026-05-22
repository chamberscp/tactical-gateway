"""Route engine and egress senders.

The route engine receives every normalized CTO, checks each enabled
route's match conditions, and for matching routes invokes the
appropriate translator and egress sender.

Egress senders maintain persistent connections where possible (TCP) and
are designed to never block the route-matching path: if a send is slow
or a connection is down, messages are dropped (and counted) rather than
queued indefinitely. For Phase 1 this is the right trade; if we ever
need at-least-once egress, we'll wire NATS JetStream in front of the
egress senders.
"""

from __future__ import annotations

import asyncio
import fnmatch
import socket
from datetime import datetime
from typing import Awaitable, Callable

from cto_schema import CTO

from common import get_logger

from .normalizers.cot_pb import cto_to_cot_pb, encode_frame
from .normalizers.cot_xml import cto_to_cot_xml
from .routes_model import (
    Destination,
    DestinationKind,
    OutputFormat,
    Route,
    RouteMatch,
    RoutesConfig,
)

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Translation dispatch
# ---------------------------------------------------------------------------

def _translate(cto: CTO, fmt: OutputFormat) -> tuple[bytes, list[str]]:
    if fmt == OutputFormat.COT_XML:
        return cto_to_cot_xml(cto)
    if fmt == OutputFormat.COT_PROTOBUF:
        payload, lossy = cto_to_cot_pb(cto)
        return encode_frame(payload), lossy
    raise ValueError(f"unsupported output format: {fmt}")


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _matches(cto: CTO, m: RouteMatch) -> bool:
    if m.source_system_glob is not None:
        if not fnmatch.fnmatchcase(cto.source_system, m.source_system_glob):
            return False
    if m.source_protocol is not None:
        if cto.source_protocol.value != m.source_protocol:
            return False
    if m.object_class is not None:
        if cto.object_class.value != m.object_class:
            return False
    return True


# ---------------------------------------------------------------------------
# Senders
# ---------------------------------------------------------------------------

class RouteStats:
    """Per-route counters."""
    def __init__(self) -> None:
        self.matched: int = 0
        self.sent: int = 0
        self.send_errors: int = 0
        self.last_send_at: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "matched": self.matched,
            "sent": self.sent,
            "send_errors": self.send_errors,
            "last_send_at": self.last_send_at.isoformat() if self.last_send_at else None,
        }


class _TcpSender:
    """Maintains a persistent TCP connection to one destination.

    On connection failure or send error, the sender drops the message,
    increments the error counter, and attempts to reconnect on the next
    send. We deliberately do not retry the dropped message: at-least-once
    semantics on the lossy egress side require a queue, which is Phase 5+.
    """

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

    async def send(self, data: bytes) -> None:
        async with self._lock:
            if self._writer is None or self._writer.is_closing():
                _, self._writer = await asyncio.open_connection(self._host, self._port)
            self._writer.write(data)
            await self._writer.drain()

    async def close(self) -> None:
        async with self._lock:
            if self._writer is not None:
                self._writer.close()
                try:
                    await self._writer.wait_closed()
                except Exception:
                    pass
                self._writer = None


class _UdpSender:
    """Stateless UDP sender. Datagrams are fire-and-forget."""

    def __init__(self, host: str, port: int) -> None:
        self._addr = (host, port)
        self._sock: socket.socket | None = None

    def _ensure_socket(self) -> socket.socket:
        if self._sock is None:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        return self._sock

    async def send(self, data: bytes) -> None:
        loop = asyncio.get_event_loop()
        sock = self._ensure_socket()
        await loop.sock_sendto(sock, data, self._addr)

    async def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class RouteEngine:
    """Receives CTOs and forwards them per the configured routes."""

    def __init__(self, config: RoutesConfig) -> None:
        self._config = config
        self._senders: dict[str, _TcpSender | _UdpSender] = {}
        self._stats: dict[str, RouteStats] = {r.id: RouteStats() for r in config.routes}

    @property
    def stats(self) -> dict[str, RouteStats]:
        return self._stats

    def _sender_for(self, dest: Destination) -> _TcpSender | _UdpSender:
        key = f"{dest.kind.value}://{dest.host}:{dest.port}"
        if key not in self._senders:
            if dest.kind == DestinationKind.TCP:
                self._senders[key] = _TcpSender(dest.host, dest.port)
            elif dest.kind == DestinationKind.UDP:
                self._senders[key] = _UdpSender(dest.host, dest.port)
            else:
                raise ValueError(f"unsupported destination kind: {dest.kind}")
        return self._senders[key]

    def list_routes(self) -> list[dict]:
        """Return a JSON-serializable view of routes plus stats."""
        out = []
        for r in self._config.routes:
            entry = r.model_dump(mode="json")
            entry["stats"] = self._stats[r.id].to_dict()
            out.append(entry)
        return out

    def set_enabled(self, route_id: str, enabled: bool) -> bool:
        """Toggle a route on or off in memory. Returns True if changed."""
        for r in self._config.routes:
            if r.id == route_id:
                r.enabled = enabled
                log.info("route toggled", route_id=route_id, enabled=enabled)
                return True
        return False

    async def handle_cto(self, cto: CTO) -> None:
        """Dispatch one CTO to all matching routes."""
        for route in self._config.routes:
            if not route.enabled:
                continue
            if not _matches(cto, route.match):
                continue
            stats = self._stats[route.id]
            stats.matched += 1
            try:
                data, lossy = _translate(cto, route.destination.format)
                if lossy:
                    log.debug(
                        "lossy egress",
                        route_id=route.id,
                        cto_uid=str(cto.uid),
                        lossy=lossy,
                    )
                sender = self._sender_for(route.destination)
                await sender.send(data)
                stats.sent += 1
                stats.last_send_at = datetime.now(cto.received_at.tzinfo)
            except Exception as e:
                stats.send_errors += 1
                log.warning(
                    "route send error",
                    route_id=route.id,
                    error=str(e),
                    error_type=type(e).__name__,
                )

    async def close(self) -> None:
        for sender in self._senders.values():
            await sender.close()
