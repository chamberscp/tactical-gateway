#!/usr/bin/env python3
"""CoT traffic generator.

Simulates N tracks moving randomly within a geofenced area and sends
CoT XML messages to a target TCP host:port at a configurable rate.

Usage:
    python -m tools.cot_generator --host 127.0.0.1 --port 8087 \
        --tracks 50 --rate 10 --duration 60 \
        --center 34.5054,-77.4360 --radius-km 10

    # Send a single message and exit (for quick smoke tests):
    python -m tools.cot_generator --host 127.0.0.1 --port 8087 --once

This intentionally only sends CoT XML. For protobuf, see
tools.cot_pb_generator (next file).
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

DEFAULT_AFFILIATIONS = ["f", "n", "u", "h"]
DEFAULT_DIMENSIONS = ["G", "A", "S"]
CALLSIGN_PREFIXES = ["HAMMER", "ANVIL", "SLEDGE", "RAZOR", "GHOST", "VIPER"]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(dt: datetime) -> str:
    """ISO-8601 with explicit Z suffix (CoT convention)."""
    s = dt.astimezone(timezone.utc).isoformat()
    return s.replace("+00:00", "Z")


class Track:
    """One simulated entity with smooth-ish movement."""

    def __init__(self, *, center_lat: float, center_lon: float, radius_km: float, idx: int):
        self.uid = f"GEN-{idx:04d}-{int(time.time())}"
        self.callsign = f"{random.choice(CALLSIGN_PREFIXES)}-{idx}"
        self.affil = random.choice(DEFAULT_AFFILIATIONS)
        self.dim = random.choice(DEFAULT_DIMENSIONS)
        self.cot_type = f"a-{self.affil}-{self.dim}"
        # Initial position inside the circle.
        bearing = random.uniform(0, 2 * math.pi)
        r = random.uniform(0, radius_km)
        self.lat, self.lon = self._offset(center_lat, center_lon, r, bearing)
        self.center_lat = center_lat
        self.center_lon = center_lon
        self.radius_km = radius_km
        # Movement state.
        self.course_deg = random.uniform(0, 360)
        self.speed_mps = random.uniform(1.0, 30.0)
        self.hae = random.uniform(10.0, 500.0) if self.dim == "A" else random.uniform(0.0, 20.0)

    @staticmethod
    def _offset(lat: float, lon: float, r_km: float, bearing_rad: float) -> tuple[float, float]:
        """Move a point by r_km along the given bearing (radians)."""
        R = 6371.0  # earth radius km
        lat1 = math.radians(lat)
        lon1 = math.radians(lon)
        lat2 = math.asin(
            math.sin(lat1) * math.cos(r_km / R)
            + math.cos(lat1) * math.sin(r_km / R) * math.cos(bearing_rad)
        )
        lon2 = lon1 + math.atan2(
            math.sin(bearing_rad) * math.sin(r_km / R) * math.cos(lat1),
            math.cos(r_km / R) - math.sin(lat1) * math.sin(lat2),
        )
        return math.degrees(lat2), math.degrees(lon2)

    @staticmethod
    def _bearing_back_to(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Initial bearing from (lat1,lon1) to (lat2,lon2), radians."""
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dl = math.radians(lon2 - lon1)
        x = math.sin(dl) * math.cos(phi2)
        y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dl)
        return math.atan2(x, y)

    @staticmethod
    def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        R = 6371.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dl = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2) ** 2
        return 2 * R * math.asin(math.sqrt(a))

    def step(self, dt_s: float) -> None:
        # Advance position; occasionally jitter course; turn back toward
        # center if we drift outside the radius.
        if random.random() < 0.05:
            self.course_deg = (self.course_deg + random.uniform(-30, 30)) % 360
        r_km = (self.speed_mps * dt_s) / 1000.0
        self.lat, self.lon = self._offset(
            self.lat, self.lon, r_km, math.radians(self.course_deg)
        )
        if self._haversine_km(self.lat, self.lon, self.center_lat, self.center_lon) > self.radius_km:
            # Steer back toward center.
            back = self._bearing_back_to(self.lat, self.lon, self.center_lat, self.center_lon)
            self.course_deg = math.degrees(back) % 360

    def to_cot_xml(self) -> bytes:
        now = now_utc()
        stale = now + timedelta(minutes=5)
        body = (
            f'<event version="2.0" uid="{escape(self.uid)}" type="{self.cot_type}" '
            f'time="{iso_z(now)}" start="{iso_z(now)}" stale="{iso_z(stale)}" how="m-g">'
            f'<point lat="{self.lat:.7f}" lon="{self.lon:.7f}" '
            f'hae="{self.hae:.2f}" ce="2.5" le="5.0"/>'
            f'<detail>'
            f'<contact callsign="{escape(self.callsign)}"/>'
            f'<track course="{self.course_deg:.2f}" speed="{self.speed_mps:.2f}"/>'
            f'<__group name="Blue" role="Team Member"/>'
            f'</detail>'
            f'</event>'
        )
        return body.encode("utf-8")


async def run(args: argparse.Namespace) -> int:
    center_lat_str, center_lon_str = args.center.split(",")
    center_lat = float(center_lat_str)
    center_lon = float(center_lon_str)

    tracks = [
        Track(center_lat=center_lat, center_lon=center_lon,
              radius_km=args.radius_km, idx=i)
        for i in range(args.tracks)
    ]

    print(f"connecting to {args.host}:{args.port}...", file=sys.stderr)
    reader, writer = await asyncio.open_connection(args.host, args.port)
    print("connected", file=sys.stderr)

    if args.once:
        # Send one message from track 0 and exit.
        payload = tracks[0].to_cot_xml()
        writer.write(payload)
        await writer.drain()
        print(f"sent 1 message ({len(payload)} bytes)", file=sys.stderr)
        writer.close()
        await writer.wait_closed()
        return 0

    period = 1.0 / args.rate
    sent = 0
    start = time.monotonic()
    deadline = start + args.duration if args.duration > 0 else float("inf")

    try:
        # Pace using monotonic clock; each tick sends one track update,
        # round-robining through tracks so each track ticks at rate/N Hz.
        idx = 0
        last_tick = time.monotonic()
        while time.monotonic() < deadline:
            tick_start = time.monotonic()
            dt_s = tick_start - last_tick
            last_tick = tick_start

            t = tracks[idx % len(tracks)]
            t.step(dt_s)
            payload = t.to_cot_xml()
            writer.write(payload)
            sent += 1
            if sent % 100 == 0:
                await writer.drain()
                elapsed = time.monotonic() - start
                print(
                    f"sent={sent}  elapsed={elapsed:.1f}s  "
                    f"rate={sent/elapsed:.1f} msg/s",
                    file=sys.stderr,
                )
            idx += 1

            sleep_for = period - (time.monotonic() - tick_start)
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
    finally:
        await writer.drain()
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

    elapsed = time.monotonic() - start
    print(f"done. sent={sent} in {elapsed:.1f}s ({sent/max(elapsed,0.001):.1f} msg/s)", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="CoT XML traffic generator")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8087)
    ap.add_argument("--tracks", type=int, default=20, help="number of simulated tracks")
    ap.add_argument("--rate", type=float, default=10.0, help="total messages per second across all tracks")
    ap.add_argument("--duration", type=float, default=30.0, help="seconds to run; 0 = forever")
    ap.add_argument("--center", default="34.5054,-77.4360", help="AO center lat,lon (Camp Lejeune by default)")
    ap.add_argument("--radius-km", type=float, default=5.0)
    ap.add_argument("--once", action="store_true", help="send exactly one message and exit")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    if args.seed is not None:
        random.seed(args.seed)
    try:
        return asyncio.run(run(args))
    except ConnectionRefusedError:
        print(f"connection refused: is the gateway listening on {args.host}:{args.port}?", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
