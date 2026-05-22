#!/usr/bin/env python3
"""TCP listener for inspecting gateway egress.

Binds to a TCP port and prints each received message to stdout. Three modes:

    --mode xml          Treat the stream as concatenated CoT XML events
                        and print them one at a time with separators.
    --mode pb           Decode TAK protocol framed protobuf messages and
                        print a summary plus a hex preview.
    --mode raw          Print raw bytes as they arrive (hex + ASCII).

Usage:
    python -m tools.tcp_listener --port 9999 --mode xml
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from xml.etree import ElementTree as ET


def ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]


async def handle_xml(reader: asyncio.StreamReader, peer: str) -> None:
    """Read CoT XML events from a stream that contains many back-to-back docs.

    Python's XMLPullParser fails with 'junk after document element' once it has
    seen one complete <event>...</event> and then encounters the next event's
    opening tag. CoT-over-TCP has no wrapping root element, so we cannot use a
    single parser across the whole stream. Instead, scan bytes for the </event>
    closing tag, then parse each event as its own document.
    """
    received = 0
    buf = bytearray()
    CLOSE = b"</event>"
    while True:
        chunk = await reader.read(4096)
        if not chunk:
            break
        buf.extend(chunk)
        while True:
            idx = buf.find(CLOSE)
            if idx == -1:
                break
            end = idx + len(CLOSE)
            payload = bytes(buf[:end])
            del buf[:end]
            # Strip whitespace between events
            while buf and buf[:1] in (b" ", b"\t", b"\r", b"\n"):
                del buf[:1]
            try:
                elem = ET.fromstring(payload)
            except ET.ParseError as e:
                print(f"[{ts()}] {peer} PARSE ERROR: {e}", file=sys.stderr)
                continue
            received += 1
            summary = _summarize_cot_event(elem)
            print(f"[{ts()}] {peer} #{received}  {summary}")
    print(f"[{ts()}] {peer} disconnected after {received} messages", file=sys.stderr)


def _summarize_cot_event(elem: ET.Element) -> str:
    uid = elem.get("uid", "?")
    cot_type = elem.get("type", "?")
    point = elem.find("point")
    lat = point.get("lat") if point is not None else "?"
    lon = point.get("lon") if point is not None else "?"
    callsign = "?"
    contact = elem.find("detail/contact") if elem.find("detail") is not None else None
    if contact is not None:
        callsign = contact.get("callsign", "?")
    return f"type={cot_type} uid={uid} cs={callsign} pos=({lat},{lon})"


async def handle_pb(reader: asyncio.StreamReader, peer: str) -> None:
    """Decode TAK-framed messages and print a summary."""
    received = 0
    buf = bytearray()
    while True:
        chunk = await reader.read(4096)
        if not chunk:
            break
        buf.extend(chunk)
        while True:
            payload, consumed = _try_decode_frame(buf)
            if payload is None:
                break
            del buf[:consumed]
            received += 1
            preview = payload[:32].hex()
            print(f"[{ts()}] {peer} #{received}  pb_payload_size={len(payload)} prefix={preview}...")
    print(f"[{ts()}] {peer} disconnected after {received} messages", file=sys.stderr)


MAGIC = 0xBF


def _try_decode_frame(buf: bytearray) -> tuple[bytes | None, int]:
    if len(buf) < 1:
        return None, 0
    if buf[0] != MAGIC:
        for j in range(1, len(buf)):
            if buf[j] == MAGIC:
                del buf[:j]
                break
        else:
            buf.clear()
        return None, 0
    length = 0
    shift = 0
    i = 1
    while True:
        if i >= len(buf):
            return None, 0
        b = buf[i]
        i += 1
        length |= (b & 0x7F) << shift
        if (b & 0x80) == 0:
            break
        shift += 7
        if shift > 35:
            return None, 0
    if i >= len(buf) or buf[i] != MAGIC:
        return None, 0
    i += 1
    if len(buf) < i + length:
        return None, 0
    return bytes(buf[i : i + length]), i + length


async def handle_raw(reader: asyncio.StreamReader, peer: str) -> None:
    bytes_total = 0
    while True:
        chunk = await reader.read(4096)
        if not chunk:
            break
        bytes_total += len(chunk)
        hexs = chunk.hex()
        ascii_repr = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"[{ts()}] {peer} +{len(chunk)}B  hex={hexs[:96]}  ascii={ascii_repr[:48]}")
    print(f"[{ts()}] {peer} disconnected (total {bytes_total} bytes)", file=sys.stderr)


async def run(args: argparse.Namespace) -> int:
    handlers = {"xml": handle_xml, "pb": handle_pb, "raw": handle_raw}
    handler = handlers[args.mode]

    async def on_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        peer_str = f"{peer[0]}:{peer[1]}" if peer else "unknown"
        print(f"[{ts()}] {peer_str} connected", file=sys.stderr)
        try:
            await handler(reader, peer_str)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    server = await asyncio.start_server(on_client, args.host, args.port)
    addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
    print(f"listening on {addrs} (mode={args.mode})", file=sys.stderr)
    async with server:
        await server.serve_forever()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="TCP listener for inspecting gateway egress")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=9999)
    ap.add_argument("--mode", choices=["xml", "pb", "raw"], default="xml")
    args = ap.parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())