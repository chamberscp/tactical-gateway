"""CoT XML <-> CTO conversion.

CoT (Cursor on Target) is the de facto tactical track exchange format
used by TAK and many DoD systems. Each message is a single XML element:

    <event version="2.0"
           uid="ANDROID-12345"
           type="a-f-G-U-C"
           time="2026-05-20T12:00:00Z"
           start="2026-05-20T12:00:00Z"
           stale="2026-05-20T12:05:00Z"
           how="m-g">
      <point lat="34.5054" lon="-77.4360" hae="42.0" ce="2.5" le="9999999"/>
      <detail>
        <contact callsign="HAMMER-6" endpoint="*:-1:stcp"/>
        <__group name="Blue" role="Team Member"/>
        <track course="180.0" speed="2.5"/>
        <remarks>any free-form text</remarks>
      </detail>
    </event>

Reference: MITRE TR 04-1582 (CoT Schema v2), TAK ICD.

The `type` attribute is the CoT type string. It encodes affiliation
and battle dimension:
    a-{affiliation}-{dimension}-...
where affiliation is f/h/n/u/p/s/a/j (friend/hostile/neutral/unknown/
pending/suspect/assumed-friend/joker) and dimension is G/A/S/U/P/F
(ground/air/sea-surface/sea-subsurface/space/SOF).

We translate this into our typed CTO fields where we recognize the
pattern, and preserve the raw type string in symbology.cot_type for
round-trip fidelity. Anything we don't recognize lands in the CTO's
attributes dict so ingest is lossless.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from xml.etree import ElementTree as ET

from cto_schema import (
    CTO,
    Affiliation,
    Altitude,
    AltitudeSource,
    BattleDimension,
    Geometry,
    Kinematics,
    ObjectClass,
    ProvenanceEntry,
    RawPointer,
    SourceProtocol,
    Symbology,
    uuid7,
)

# ---------------------------------------------------------------------------
# CoT type-string mapping
# ---------------------------------------------------------------------------

_AFFILIATION_MAP: dict[str, Affiliation] = {
    "f": Affiliation.FRIEND,
    "h": Affiliation.HOSTILE,
    "n": Affiliation.NEUTRAL,
    "u": Affiliation.UNKNOWN,
    "p": Affiliation.PENDING,
    "s": Affiliation.SUSPECT,
    "a": Affiliation.ASSUMED_FRIEND,
    "j": Affiliation.FRIEND,  # joker - friend exercising as hostile
    "k": Affiliation.HOSTILE,  # faker - hostile exercising as friend
}

_DIMENSION_MAP: dict[str, BattleDimension] = {
    "G": BattleDimension.LAND,
    "A": BattleDimension.AIR,
    "S": BattleDimension.SEA_SURFACE,
    "U": BattleDimension.SEA_SUBSURFACE,
    "P": BattleDimension.SPACE,
    "F": BattleDimension.SOF,
}


def parse_cot_type(cot_type: str) -> tuple[Affiliation | None, BattleDimension | None]:
    """Return (affiliation, battle_dimension) from a CoT type string.

    Handles common variations including atomic types ("a-f-G-U-C"),
    bare points ("b-m-p-s-m"), and unknown patterns (returns None, None).
    """
    parts = cot_type.split("-")
    if not parts or parts[0] != "a":
        return None, None
    affil: Affiliation | None = None
    dim: BattleDimension | None = None
    if len(parts) >= 2:
        affil = _AFFILIATION_MAP.get(parts[1].lower())
    if len(parts) >= 3:
        dim = _DIMENSION_MAP.get(parts[2].upper())
    return affil, dim


# ---------------------------------------------------------------------------
# Parse XML -> CTO
# ---------------------------------------------------------------------------


class CoTXmlParseError(Exception):
    """Raised when CoT XML cannot be parsed into a CTO."""


def _require_attr(elem: ET.Element, name: str) -> str:
    val = elem.get(name)
    if val is None:
        raise CoTXmlParseError(f"missing required attribute '{name}' on <{elem.tag}>")
    return val


def _parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp. CoT uses 'Z' suffix for UTC."""
    # Python <3.11 doesn't parse 'Z'; 3.11+ does. We replace defensively.
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _detail_to_attributes(detail: ET.Element) -> dict[str, Any]:
    """Capture the entire <detail> subtree as a nested dict so nothing is lost.

    We do NOT attempt to interpret every sub-element type; we just preserve
    structure. Sub-elements known to be commonly used (contact, track,
    __group, remarks) get parsed into named fields by the caller; the rest
    sit in attributes for later use.
    """
    result: dict[str, Any] = {}
    for child in detail:
        key = child.tag
        # Each element becomes a dict of its attributes plus its text.
        node: dict[str, Any] = {}
        if child.attrib:
            node.update(child.attrib)
        if child.text and child.text.strip():
            node["_text"] = child.text.strip()
        if list(child):
            node["_children"] = _detail_to_attributes(child)
        # Multiple children with same tag - keep as list.
        if key in result:
            if not isinstance(result[key], list):
                result[key] = [result[key]]
            result[key].append(node)
        else:
            result[key] = node
    return result


def cot_xml_to_cto(
    *,
    xml_bytes: bytes,
    source_system: str,
    received_at: datetime,
    raw_pointer: RawPointer,
) -> CTO:
    """Parse a CoT XML message and produce a CTO.

    Raises CoTXmlParseError on malformed input.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise CoTXmlParseError(f"invalid XML: {e}") from e

    if root.tag != "event":
        raise CoTXmlParseError(f"expected <event> root, got <{root.tag}>")

    cot_type = _require_attr(root, "type")
    source_uid = _require_attr(root, "uid")
    event_time = _parse_iso(_require_attr(root, "time"))
    start = _parse_iso(_require_attr(root, "start"))
    stale = _parse_iso(_require_attr(root, "stale"))

    point = root.find("point")
    if point is None:
        raise CoTXmlParseError("missing required <point> element")
    lat = float(_require_attr(point, "lat"))
    lon = float(_require_attr(point, "lon"))
    hae_str = point.get("hae")
    ce_str = point.get("ce")  # circular error (horizontal accuracy, meters)
    le_str = point.get("le")  # linear error (vertical accuracy, meters)

    geometry = Geometry(type="Point", coordinates=[lon, lat])

    altitude: Altitude | None = None
    if hae_str is not None:
        try:
            altitude = Altitude(
                value_m=float(hae_str),
                source=AltitudeSource.UNKNOWN,
                # CoT uses 9999999 as a sentinel for "no data".
                accuracy_m=(
                    float(le_str)
                    if le_str is not None and float(le_str) < 9999999
                    else None
                ),
            )
        except ValueError:
            pass

    affil, dim = parse_cot_type(cot_type)

    # Pull common detail fields if present.
    callsign: str | None = None
    kinematics: Kinematics | None = None
    remarks: str | None = None
    attributes: dict[str, Any] = {}

    detail = root.find("detail")
    if detail is not None:
        contact = detail.find("contact")
        if contact is not None:
            callsign = contact.get("callsign")

        track = detail.find("track")
        if track is not None:
            try:
                kinematics = Kinematics(
                    course_deg=(
                        float(track.get("course")) if track.get("course") else None
                    ),
                    speed_mps=(
                        float(track.get("speed")) if track.get("speed") else None
                    ),
                )
            except ValueError:
                kinematics = None

        rem_elem = detail.find("remarks")
        if rem_elem is not None and rem_elem.text:
            remarks = rem_elem.text.strip()

        # Stash the full detail tree for round-trip and unknown-element preservation.
        attributes["cot_detail"] = _detail_to_attributes(detail)

    # Preserve any unrecognized attributes on the <event> element.
    known_event_attrs = {"version", "uid", "type", "time", "start", "stale", "how"}
    extras = {k: v for k, v in root.attrib.items() if k not in known_event_attrs}
    if extras:
        attributes["cot_event_extras"] = extras
    # 'how' is interesting enough to keep accessible.
    if "how" in root.attrib:
        attributes["cot_how"] = root.attrib["how"]

    # Horizontal accuracy goes alongside the geometry.
    if ce_str is not None:
        try:
            ce = float(ce_str)
            if ce < 9999999:
                attributes["cot_horizontal_accuracy_m"] = ce
        except ValueError:
            pass

    return CTO(
        uid=uuid7(),
        source_uid=source_uid,
        source_system=source_system,
        source_protocol=SourceProtocol.COT_XML,
        received_at=received_at,
        event_time=event_time,
        valid_from=start,
        valid_to=stale,
        object_class=ObjectClass.TRACK,
        geometry=geometry,
        altitude=altitude,
        kinematics=kinematics,
        symbology=Symbology(
            cot_type=cot_type,
            affiliation=affil,
            battle_dimension=dim,
        ),
        callsign=callsign,
        remarks=remarks,
        attributes=attributes,
        raw_pointer=raw_pointer,
        provenance=[ProvenanceEntry(
            step="cot_xml_to_cto",
            actor="gateway.normalizers.cot_xml",
            at=received_at,
            notes=None,
            lossy_fields=[],
        )],
    )


# ---------------------------------------------------------------------------
# Generate XML <- CTO
# ---------------------------------------------------------------------------


def _format_iso(dt: datetime) -> str:
    """Format datetime as CoT-style ISO with 'Z' suffix."""
    s = dt.astimezone().isoformat()
    if s.endswith("+00:00"):
        s = s[:-6] + "Z"
    return s


def cto_to_cot_xml(cto: CTO) -> tuple[bytes, list[str]]:
    """Convert a CTO back to CoT XML bytes.

    Returns (xml_bytes, lossy_field_list). The lossy list records any
    CTO fields that couldn't be represented faithfully in CoT XML.
    """
    lossy: list[str] = []

    # CoT requires a type string. We prefer the original; failing that we
    # synthesize from affiliation+dimension; failing that we use a-u-G (atom unknown ground).
    cot_type = cto.symbology.cot_type
    if not cot_type:
        af = "u"
        for k, v in _AFFILIATION_MAP.items():
            if v == cto.symbology.affiliation:
                af = k
                break
        dm = "G"
        for k2, v2 in _DIMENSION_MAP.items():
            if v2 == cto.symbology.battle_dimension:
                dm = k2
                break
        cot_type = f"a-{af}-{dm}"
        lossy.append("cot_type_synthesized")

    event_attrs: dict[str, str] = {
        "version": "2.0",
        "uid": cto.source_uid or str(cto.uid),
        "type": cot_type,
        "time": _format_iso(cto.event_time),
        "start": _format_iso(cto.valid_from or cto.event_time),
        "stale": _format_iso(cto.valid_to or cto.event_time),
    }
    cot_how = cto.attributes.get("cot_how")
    if isinstance(cot_how, str):
        event_attrs["how"] = cot_how

    # Restore unknown event-level attrs.
    extras = cto.attributes.get("cot_event_extras")
    if isinstance(extras, dict):
        for k, v in extras.items():
            if isinstance(v, str) and k not in event_attrs:
                event_attrs[k] = v

    event = ET.Element("event", event_attrs)

    # Point - CoT geometry is always a single point in <point>.
    if cto.geometry.type != "Point":
        lossy.append("geometry_not_point")
    coords = cto.geometry.coordinates
    lon, lat = float(coords[0]), float(coords[1])

    point_attrs: dict[str, str] = {
        "lat": f"{lat:.7f}",
        "lon": f"{lon:.7f}",
    }
    if cto.altitude is not None:
        point_attrs["hae"] = f"{cto.altitude.value_m:.2f}"
        point_attrs["le"] = (
            f"{cto.altitude.accuracy_m:.2f}"
            if cto.altitude.accuracy_m is not None
            else "9999999"
        )
    else:
        point_attrs["hae"] = "9999999"
        point_attrs["le"] = "9999999"

    ce = cto.attributes.get("cot_horizontal_accuracy_m")
    point_attrs["ce"] = f"{float(ce):.2f}" if isinstance(ce, (int, float)) else "9999999"
    ET.SubElement(event, "point", point_attrs)

    # Detail
    detail = ET.SubElement(event, "detail")
    if cto.callsign:
        ET.SubElement(detail, "contact", {"callsign": cto.callsign})
    if cto.kinematics is not None:
        track_attrs: dict[str, str] = {}
        if cto.kinematics.course_deg is not None:
            track_attrs["course"] = f"{cto.kinematics.course_deg:.2f}"
        if cto.kinematics.speed_mps is not None:
            track_attrs["speed"] = f"{cto.kinematics.speed_mps:.2f}"
        if track_attrs:
            ET.SubElement(detail, "track", track_attrs)
    if cto.remarks:
        rem = ET.SubElement(detail, "remarks")
        rem.text = cto.remarks

    # Restore the rest of the original <detail> tree if we have it,
    # being careful not to overwrite elements we already added.
    stored_detail = cto.attributes.get("cot_detail")
    if isinstance(stored_detail, dict):
        already = {child.tag for child in detail}
        for tag, node in stored_detail.items():
            if tag in already:
                continue
            _emit_detail_node(detail, tag, node)

    xml_bytes = ET.tostring(event, encoding="utf-8", xml_declaration=False)
    return xml_bytes, lossy


def _emit_detail_node(parent: ET.Element, tag: str, node: Any) -> None:
    """Re-emit a node captured by _detail_to_attributes."""
    if isinstance(node, list):
        for n in node:
            _emit_detail_node(parent, tag, n)
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
            _emit_detail_node(elem, child_tag, child_node)
