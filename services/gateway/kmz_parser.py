"""KMZ parser: KML inside a zip -> list of CTOs.

A KMZ file is a zip archive containing one or more KML documents and
optional referenced overlay images. We extract only the geographic
features (points, lines, polygons) from the primary KML; image overlays
and styles are noted but not preserved as separate artifacts.

Each placemark in the KML becomes one CTO with object_class=GRAPHIC by
default (operational graphic from a planner). The placemark's label is
run through the doctrinal recognizer (services.gateway.kmz_recognize,
ADR-0012 D1), which produces structured SIDC, affiliation, and
doctrinal kind. Explicit ExtendedData SIDC, when present, overrides the
recognizer's SIDC but does not suppress the recognizer's other outputs
(affiliation, kind, provenance entry).

NetworkLinks are NOT followed (security boundary). Logged with a warning.
Image overlays and ground overlays are ignored.

The XML namespaces handled:
- http://www.opengis.net/kml/2.2  (KML 2.2 standard)
- http://earth.google.com/kml/2.1 (older Google Earth namespace)
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from typing import Iterator
from xml.etree import ElementTree as ET

from cto_schema import (
    CTO,
    Affiliation,
    BattleDimension,
    Geometry,
    IngestSource,
    ObjectClass,
    ProvenanceEntry,
    RawPointer,
    SourceProtocol,
    Symbology,
    uuid7,
)

from common import get_logger

from .kmz_recognize import recognize

log = get_logger(__name__)


# KML namespace - accept either version
_KML_NAMESPACES = [
    "http://www.opengis.net/kml/2.2",
    "http://earth.google.com/kml/2.1",
    "http://earth.google.com/kml/2.0",
]


class KmzParseError(Exception):
    """Raised when KMZ contents cannot be parsed."""


# ---------------------------------------------------------------------------
# XML namespace handling
# ---------------------------------------------------------------------------


def _strip_ns(tag: str) -> str:
    """Strip XML namespace from a tag name. '{ns}foo' -> 'foo'."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _findall_ns(elem: ET.Element, local_name: str) -> list[ET.Element]:
    """Find all child elements with the given local name, ignoring namespace."""
    return [child for child in elem.iter() if _strip_ns(child.tag) == local_name]


def _find_ns(elem: ET.Element, local_name: str) -> ET.Element | None:
    """Find first descendant with the given local name, ignoring namespace."""
    for child in elem.iter():
        if _strip_ns(child.tag) == local_name:
            return child
    return None


def _direct_find_ns(elem: ET.Element, local_name: str) -> ET.Element | None:
    """Find first DIRECT child with the given local name."""
    for child in list(elem):
        if _strip_ns(child.tag) == local_name:
            return child
    return None


def _text_of(elem: ET.Element | None) -> str | None:
    if elem is None or elem.text is None:
        return None
    text = elem.text.strip()
    return text if text else None


# ---------------------------------------------------------------------------
# Coordinate parsing
# ---------------------------------------------------------------------------


def _parse_coordinates(coord_text: str) -> list[list[float]]:
    """Parse KML <coordinates> text into a list of [lon, lat, (alt)] tuples.

    KML coordinate format: 'lon,lat[,alt] lon,lat[,alt] ...' with
    whitespace separation between tuples and commas within. Whitespace
    includes newlines, so we normalize before splitting.
    """
    points: list[list[float]] = []
    for token in coord_text.replace("\n", " ").replace("\t", " ").split():
        token = token.strip().rstrip(",")
        if not token:
            continue
        parts = token.split(",")
        if len(parts) < 2:
            raise KmzParseError(f"invalid coordinate tuple: {token!r}")
        try:
            lon = float(parts[0])
            lat = float(parts[1])
            if len(parts) >= 3 and parts[2]:
                alt = float(parts[2])
                points.append([lon, lat, alt])
            else:
                points.append([lon, lat])
        except ValueError as e:
            raise KmzParseError(f"invalid coordinate values in {token!r}: {e}") from e
    return points


# ---------------------------------------------------------------------------
# Placemark -> CTO
# ---------------------------------------------------------------------------


def _placemark_geometry(placemark: ET.Element) -> Geometry | None:
    """Extract a Geometry from a Placemark's first recognized geometry child."""
    # Try in order: Point, LineString, Polygon. We do not handle
    # MultiGeometry (would yield multiple CTOs from one placemark, which
    # is doable but adds complexity); log a warning instead.

    point = _direct_find_ns(placemark, "Point")
    if point is not None:
        coords_elem = _direct_find_ns(point, "coordinates")
        if coords_elem is not None and coords_elem.text:
            pts = _parse_coordinates(coords_elem.text)
            if pts:
                return Geometry(type="Point", coordinates=pts[0][:2])

    line = _direct_find_ns(placemark, "LineString")
    if line is not None:
        coords_elem = _direct_find_ns(line, "coordinates")
        if coords_elem is not None and coords_elem.text:
            pts = _parse_coordinates(coords_elem.text)
            if len(pts) >= 2:
                return Geometry(
                    type="LineString",
                    coordinates=[[p[0], p[1]] for p in pts],
                )

    polygon = _direct_find_ns(placemark, "Polygon")
    if polygon is not None:
        rings = []
        outer = _find_ns(polygon, "outerBoundaryIs")
        if outer is not None:
            ring_elem = _find_ns(outer, "LinearRing")
            if ring_elem is not None:
                coords_elem = _direct_find_ns(ring_elem, "coordinates")
                if coords_elem is not None and coords_elem.text:
                    pts = _parse_coordinates(coords_elem.text)
                    if len(pts) >= 3:
                        ring = [[p[0], p[1]] for p in pts]
                        # KML doesn't require closed rings; close if needed
                        if ring[0] != ring[-1]:
                            ring.append(ring[0])
                        rings.append(ring)
        # Inner rings (holes)
        for inner in _findall_ns(polygon, "innerBoundaryIs"):
            ring_elem = _find_ns(inner, "LinearRing")
            if ring_elem is not None:
                coords_elem = _direct_find_ns(ring_elem, "coordinates")
                if coords_elem is not None and coords_elem.text:
                    pts = _parse_coordinates(coords_elem.text)
                    if len(pts) >= 3:
                        ring = [[p[0], p[1]] for p in pts]
                        if ring[0] != ring[-1]:
                            ring.append(ring[0])
                        rings.append(ring)
        if rings:
            return Geometry(type="Polygon", coordinates=rings)

    multi = _direct_find_ns(placemark, "MultiGeometry")
    if multi is not None:
        log.warning("kmz placemark has MultiGeometry; only first child extracted",
                    placemark_name=_text_of(_direct_find_ns(placemark, "name")))
        # Recurse into the multi
        for child in list(multi):
            tag = _strip_ns(child.tag)
            if tag in ("Point", "LineString", "Polygon"):
                # Build a synthetic placemark holding this single geometry
                fake = ET.Element("Placemark")
                fake.append(child)
                return _placemark_geometry(fake)

    return None


def _placemark_extended_data(placemark: ET.Element) -> dict:
    """Extract all <ExtendedData><Data> name/value pairs."""
    data = {}
    ext = _direct_find_ns(placemark, "ExtendedData")
    if ext is None:
        return data
    for item in _findall_ns(ext, "Data"):
        name = item.get("name")
        value_elem = _direct_find_ns(item, "value")
        value = value_elem.text if value_elem is not None else None
        if name and value:
            data[name] = value.strip()
    return data


def _placemark_to_cto(
    placemark: ET.Element,
    *,
    feature_index: int,
    source_system: str,
    received_at: datetime,
    raw_pointer: RawPointer,
    ingest_source: IngestSource,
    parent_kmz_uri: str,
    parent_kmz_filename: str,
) -> CTO | None:
    """Convert a single KML <Placemark> into a CTO, or None to skip."""
    name = _text_of(_direct_find_ns(placemark, "name"))
    description = _text_of(_direct_find_ns(placemark, "description"))
    geometry = _placemark_geometry(placemark)
    if geometry is None:
        log.warning("placemark has no recognized geometry; skipping",
                    placemark_name=name, feature_index=feature_index)
        return None

    # ExtendedData fields. An explicit ExtendedData sidc, when present,
    # wins over the recognizer's inferred SIDC because it is authoritative.
    ext_data = _placemark_extended_data(placemark)
    explicit_sidc = ext_data.get("sidc") or ext_data.get("SIDC")

    # Run the doctrinal recognizer (ADR-0012 D1). Always returns a
    # result; never None. We use its outputs for affiliation, kind, and
    # provenance regardless of whether the SIDC was set explicitly.
    recognition = recognize(
        label=name,
        description=description,
        geometry_type=geometry.type,
    )
    effective_sidc = explicit_sidc or recognition.sidc

    attributes: dict = {
        "kmz_feature_index": feature_index,
        "parent_kmz_uri": parent_kmz_uri,
        "parent_kmz_filename": parent_kmz_filename,
        "parent_kmz_source": ingest_source.value,
    }
    if ext_data:
        attributes["kmz_extended_data"] = ext_data
    # Preserve graphic_kind for backward compatibility with Phase 2a
    # CTOs already in the database. The recognizer's doctrinal_kind
    # uses the same vocabulary as the old _classify_label() did.
    if recognition.doctrinal_kind:
        attributes["graphic_kind"] = recognition.doctrinal_kind
    if description:
        attributes["kmz_description"] = description

    placemark_id = placemark.get("id")
    if placemark_id:
        attributes["kmz_placemark_id"] = placemark_id

    # Build the recognizer provenance step. lossy_fields lists the
    # fields that were inferred rather than read directly from the
    # source, so audit queries can filter for fidelity:
    #   - SIDC is lossy unless ExtendedData carried an explicit value
    #   - affiliation is always lossy for KMZ (no native field)
    # For clean recognizer matches the SIDC is the canonical doctrinal
    # value for the recognized kind; we still record it as inferred
    # because the source KMZ did not literally carry that SIDC string.
    recognition_lossy: list[str] = []
    if not explicit_sidc:
        recognition_lossy.append("sidc_2525c")
    recognition_lossy.append("affiliation")

    recognition_notes = json.dumps({
        "matched_layer": recognition.matched_layer,
        "status": recognition.status,
        "doctrinal_kind": recognition.doctrinal_kind,
        "suspected_modifier": recognition.suspected_modifier,
        "reasons": list(recognition.reasons),
        "explicit_sidc_used": explicit_sidc is not None,
    })

    return CTO(
        uid=uuid7(),
        source_uid=placemark_id,
        source_system=source_system,
        source_protocol=SourceProtocol.KMZ,
        ingest_source=ingest_source,
        received_at=received_at,
        event_time=received_at,  # KML has no per-feature timestamp by default
        object_class=ObjectClass.GRAPHIC,
        geometry=geometry,
        symbology=Symbology(
            sidc_2525c=effective_sidc,
            affiliation=Affiliation(recognition.affiliation),
        ),
        label=name,
        remarks=description,
        attributes=attributes,
        raw_pointer=raw_pointer,
        provenance=[
            ProvenanceEntry(
                step="kmz_to_cto",
                actor="gateway.kmz_parser",
                at=received_at,
                notes=None,
                lossy_fields=[],
            ),
            ProvenanceEntry(
                step="kmz_label_recognized",
                actor="gateway.kmz_recognizer",
                at=received_at,
                notes=recognition_notes,
                lossy_fields=recognition_lossy,
            ),
        ],
    )


# ---------------------------------------------------------------------------
# KMZ extraction
# ---------------------------------------------------------------------------


def _read_primary_kml(kmz_bytes: bytes) -> bytes:
    """Find and return the primary KML document inside a KMZ archive.

    The OGC standard says doc.kml is the default but isn't required.
    If doc.kml is not present, use the first .kml found.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(kmz_bytes))
    except zipfile.BadZipFile as e:
        raise KmzParseError(f"invalid KMZ (not a zip): {e}") from e

    names = zf.namelist()
    if "doc.kml" in names:
        return zf.read("doc.kml")
    for name in names:
        if name.lower().endswith(".kml"):
            return zf.read(name)
    raise KmzParseError("KMZ contains no .kml file")


def _iter_placemarks(kml_root: ET.Element) -> Iterator[ET.Element]:
    """Yield all Placemark elements in document order."""
    for elem in kml_root.iter():
        if _strip_ns(elem.tag) == "Placemark":
            yield elem


def kmz_to_ctos(
    *,
    kmz_bytes: bytes,
    filename: str,
    source_system: str,
    received_at: datetime,
    raw_pointer: RawPointer,
    ingest_source: IngestSource,
) -> list[CTO]:
    """Parse a KMZ file and produce one CTO per placemark.

    `filename` is the original filename (used to set parent_kmz_filename
    on each CTO).
    `raw_pointer` points to the captured KMZ bytes in MinIO.
    """
    kml_bytes = _read_primary_kml(kmz_bytes)
    try:
        kml_root = ET.fromstring(kml_bytes)
    except ET.ParseError as e:
        raise KmzParseError(f"invalid KML inside KMZ: {e}") from e

    # Count and warn about NetworkLinks
    networklink_count = sum(1 for elem in kml_root.iter()
                           if _strip_ns(elem.tag) == "NetworkLink")
    if networklink_count > 0:
        log.warning("kmz contains NetworkLinks; not followed (security boundary)",
                    filename=filename, count=networklink_count)

    ctos: list[CTO] = []
    parent_uri = f"{raw_pointer.object_key}"
    for idx, placemark in enumerate(_iter_placemarks(kml_root)):
        cto = _placemark_to_cto(
            placemark,
            feature_index=idx,
            source_system=source_system,
            received_at=received_at,
            raw_pointer=raw_pointer,
            ingest_source=ingest_source,
            parent_kmz_uri=parent_uri,
            parent_kmz_filename=filename,
        )
        if cto is not None:
            ctos.append(cto)

    log.info("kmz parsed",
             filename=filename, features=len(ctos),
             ingest_source=ingest_source.value)
    return ctos
