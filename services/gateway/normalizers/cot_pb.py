"""CoT Protobuf (TAK protocol) <-> CTO conversion.

TAK Protocol uses protobuf-encoded messages over TCP with this framing:
    [0xbf] [varint length] [0xbf] [protobuf bytes]

Generated bindings come from cotevent.proto. The build runs `protoc`
to produce cotevent_pb2.py alongside this module.

If the generated module isn't present (e.g. during initial test runs
before protoc has been invoked), this module's parse/build functions
raise a clear ImportError pointing to the build step. We import lazily
to make this happen at use time rather than module-load time, so the
gateway can start and serve XML even if protobuf bindings are missing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree as ET

from cto_schema import (
    CTO,
    Altitude,
    AltitudeSource,
    Geometry,
    Kinematics,
    ObjectClass,
    ProvenanceEntry,
    RawPointer,
    SourceProtocol,
    Symbology,
    uuid7,
)

from .cot_xml import (
    CoTXmlParseError,
    _detail_to_attributes,
    _format_iso,
    parse_cot_type,
)

# TAK protocol framing magic byte.
MAGIC = 0xBF


class CoTPbError(Exception):
    """Raised on protobuf framing or schema errors."""


def _load_pb() -> Any:
    """Import the generated bindings module on demand."""
    try:
        from . import cotevent_pb2  # type: ignore[attr-defined]
    except ImportError as e:
        raise CoTPbError(
            "cotevent_pb2 not found; run `protoc --python_out=. cotevent.proto` "
            "from services/gateway/normalizers/ to generate bindings"
        ) from e
    return cotevent_pb2


# ---------------------------------------------------------------------------
# Wire framing
# ---------------------------------------------------------------------------


def encode_frame(payload: bytes) -> bytes:
    """Wrap a protobuf payload in the TAK protocol framing."""
    length = len(payload)
    out = bytearray([MAGIC])
    # varint encoding of length
    n = length
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    out.append(MAGIC)
    out.extend(payload)
    return bytes(out)


def try_decode_frame(buf: bytearray) -> tuple[bytes | None, int]:
    """Try to decode one frame from buf.

    Returns (payload_or_None, bytes_consumed). If a complete frame is not
    yet present, returns (None, 0). The caller should retain buf and call
    again when more data arrives.

    Raises CoTPbError on malformed framing.
    """
    if len(buf) < 1:
        return None, 0
    if buf[0] != MAGIC:
        raise CoTPbError(f"expected leading magic 0x{MAGIC:02x}, got 0x{buf[0]:02x}")

    # Decode varint starting at index 1.
    length = 0
    shift = 0
    i = 1
    while True:
        if i >= len(buf):
            return None, 0  # incomplete varint
        b = buf[i]
        i += 1
        length |= (b & 0x7F) << shift
        if (b & 0x80) == 0:
            break
        shift += 7
        if shift > 35:
            raise CoTPbError("varint too long")
    if i >= len(buf):
        return None, 0
    if buf[i] != MAGIC:
        raise CoTPbError(f"expected second magic 0x{MAGIC:02x}, got 0x{buf[i]:02x}")
    i += 1
    if len(buf) < i + length:
        return None, 0
    payload = bytes(buf[i : i + length])
    return payload, i + length


# ---------------------------------------------------------------------------
# Protobuf -> CTO
# ---------------------------------------------------------------------------


def _from_millis(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def cot_pb_to_cto(
    *,
    pb_bytes: bytes,
    source_system: str,
    received_at: datetime,
    raw_pointer: RawPointer,
) -> CTO:
    """Parse a TakMessage protobuf payload into a CTO."""
    pb = _load_pb()
    msg = pb.TakMessage()
    msg.ParseFromString(pb_bytes)
    if not msg.HasField("cotEvent"):
        raise CoTPbError("TakMessage has no cotEvent")
    ev = msg.cotEvent

    affil, dim = parse_cot_type(ev.type)

    geometry = Geometry(type="Point", coordinates=[ev.lon, ev.lat])

    altitude: Altitude | None = None
    if ev.hae != 0.0 or ev.le != 0.0:
        altitude = Altitude(
            value_m=ev.hae,
            source=AltitudeSource.UNKNOWN,
            accuracy_m=ev.le if ev.le and ev.le < 9999999 else None,
        )

    callsign: str | None = None
    kinematics: Kinematics | None = None
    attributes: dict[str, Any] = {}

    if ev.HasField("detail"):
        d = ev.detail
        if d.HasField("contact") and d.contact.callsign:
            callsign = d.contact.callsign
        if d.HasField("track"):
            kinematics = Kinematics(
                course_deg=d.track.course if d.track.course != 0.0 else None,
                speed_mps=d.track.speed if d.track.speed != 0.0 else None,
            )
        # Embedded XML detail subtree: parse if present, preserve verbatim too.
        if d.xmlDetail:
            attributes["cot_xml_detail_raw"] = d.xmlDetail
            try:
                wrapped = f"<detail>{d.xmlDetail}</detail>"
                attributes["cot_detail"] = _detail_to_attributes(ET.fromstring(wrapped))
            except ET.ParseError:
                pass
        # Other detail fields - preserve as a plain dict for round-trip.
        if d.HasField("group"):
            attributes.setdefault("cot_detail", {}).setdefault("__group", {
                "name": d.group.name, "role": d.group.role,
            })
        if d.HasField("status") and d.status.battery:
            attributes.setdefault("cot_detail", {}).setdefault("status", {
                "battery": d.status.battery,
            })
        if d.HasField("takv"):
            attributes.setdefault("cot_detail", {}).setdefault("takv", {
                "device": d.takv.device,
                "platform": d.takv.platform,
                "os": d.takv.os,
                "version": d.takv.version,
            })
        if d.HasField("precisionLocation"):
            pl = d.precisionLocation
            attributes.setdefault("cot_detail", {}).setdefault("precisionlocation", {
                "geopointsrc": pl.geopointsrc, "altsrc": pl.altsrc,
            })

    if ev.how:
        attributes["cot_how"] = ev.how
    if ev.ce and ev.ce < 9999999:
        attributes["cot_horizontal_accuracy_m"] = ev.ce

    return CTO(
        uid=uuid7(),
        source_uid=ev.uid,
        source_system=source_system,
        source_protocol=SourceProtocol.COT_PROTOBUF,
        received_at=received_at,
        event_time=_from_millis(ev.sendTime),
        valid_from=_from_millis(ev.startTime),
        valid_to=_from_millis(ev.staleTime),
        object_class=ObjectClass.TRACK,
        geometry=geometry,
        altitude=altitude,
        kinematics=kinematics,
        symbology=Symbology(
            cot_type=ev.type,
            affiliation=affil,
            battle_dimension=dim,
        ),
        callsign=callsign,
        attributes=attributes,
        raw_pointer=raw_pointer,
        provenance=[ProvenanceEntry(
            step="cot_pb_to_cto",
            actor="gateway.normalizers.cot_pb",
            at=received_at,
            notes=None,
            lossy_fields=[],
        )],
    )


# ---------------------------------------------------------------------------
# CTO -> protobuf
# ---------------------------------------------------------------------------


def _to_millis(dt: datetime) -> int:
    return int(dt.astimezone(timezone.utc).timestamp() * 1000)


def cto_to_cot_pb(cto: CTO) -> tuple[bytes, list[str]]:
    """Convert a CTO to a TakMessage protobuf payload (no framing).

    Returns (payload_bytes, lossy_field_list). The caller usually wraps
    the payload with encode_frame() before sending on the wire.
    """
    pb = _load_pb()
    lossy: list[str] = []

    msg = pb.TakMessage()
    ev = msg.cotEvent

    cot_type = cto.symbology.cot_type or "a-u-G"
    if not cto.symbology.cot_type:
        lossy.append("cot_type_synthesized")

    ev.type = cot_type
    ev.uid = cto.source_uid or str(cto.uid)
    ev.sendTime = _to_millis(cto.event_time)
    ev.startTime = _to_millis(cto.valid_from or cto.event_time)
    ev.staleTime = _to_millis(cto.valid_to or cto.event_time)

    how = cto.attributes.get("cot_how")
    if isinstance(how, str):
        ev.how = how

    if cto.geometry.type != "Point":
        lossy.append("geometry_not_point")
    coords = cto.geometry.coordinates
    ev.lon = float(coords[0])
    ev.lat = float(coords[1])

    if cto.altitude is not None:
        ev.hae = float(cto.altitude.value_m)
        if cto.altitude.accuracy_m is not None:
            ev.le = float(cto.altitude.accuracy_m)
    ce = cto.attributes.get("cot_horizontal_accuracy_m")
    if isinstance(ce, (int, float)):
        ev.ce = float(ce)

    # Detail
    if cto.callsign:
        ev.detail.contact.callsign = cto.callsign
    if cto.kinematics is not None:
        if cto.kinematics.course_deg is not None:
            ev.detail.track.course = float(cto.kinematics.course_deg)
        if cto.kinematics.speed_mps is not None:
            ev.detail.track.speed = float(cto.kinematics.speed_mps)

    # Round-trip the embedded XML detail subtree where we have it.
    stored = cto.attributes.get("cot_detail")
    if isinstance(stored, dict):
        # Build a compact <detail>-children XML string, excluding fields
        # we already emitted via typed protobuf fields.
        already = {"contact", "track"}
        root = ET.Element("detail")
        for tag, node in stored.items():
            if tag in already:
                continue
            _emit_detail_for_pb(root, tag, node)
        # ET.tostring returns the full <detail>...</detail>; we want only
        # the inner XML to match the protobuf field semantics.
        inner = "".join(
            ET.tostring(child, encoding="unicode") for child in root
        )
        if inner:
            ev.detail.xmlDetail = inner

    return msg.SerializeToString(), lossy


def _emit_detail_for_pb(parent: ET.Element, tag: str, node: Any) -> None:
    """Variant of detail emitter for the protobuf xmlDetail field."""
    if isinstance(node, list):
        for n in node:
            _emit_detail_for_pb(parent, tag, n)
        return
    if not isinstance(node, dict):
        return
    attrs = {k: v for k, v in node.items() if not k.startswith("_") and isinstance(v, str)}
    elem = ET.SubElement(parent, tag, attrs)
    text = node.get("_text")
    if isinstance(text, str):
        elem.text = text
    children = node.get("_children")
    if isinstance(children, dict):
        for child_tag, child_node in children.items():
            _emit_detail_for_pb(elem, child_tag, child_node)
