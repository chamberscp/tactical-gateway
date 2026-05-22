"""Tests for the KMZ parser.

Coverage:
- Point, LineString, Polygon placemarks each yield correct geometries
- Multi-feature KMZ produces N CTOs with shared parent metadata
- Doctrinal name patterns get tagged
- ExtendedData (including sidc) is captured
- MultiGeometry yields one CTO with a warning
- NetworkLink is logged but not followed
- Bad KMZ (not a zip) raises KmzParseError
- KMZ without any .kml inside raises KmzParseError
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone

import pytest
from cto_schema import IngestSource, ObjectClass, RawPointer

from services.gateway.kmz_parser import (
    KmzParseError,
    _classify_label,
    kmz_to_ctos,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _kmz_from_kml(kml_text: str) -> bytes:
    """Pack a KML document into a KMZ byte string."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("doc.kml", kml_text)
    return buf.getvalue()


def _raw_pointer() -> RawPointer:
    return RawPointer(
        sha256="a" * 64,
        object_key="raw/2026/05/22/kmz/aa/aaaaa",
        size_bytes=42,
        captured_at=datetime.now(timezone.utc),
    )


KML_BASE = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Test</name>
{placemarks}
  </Document>
</kml>"""


def _kmz(placemarks: str) -> bytes:
    return _kmz_from_kml(KML_BASE.format(placemarks=placemarks))


def _ingest(kmz_bytes: bytes, filename: str = "test.kmz"):
    return kmz_to_ctos(
        kmz_bytes=kmz_bytes,
        filename=filename,
        source_system="kmz-folder:/test",
        received_at=datetime.now(timezone.utc),
        raw_pointer=_raw_pointer(),
        ingest_source=IngestSource.FOLDER,
    )


# ---------------------------------------------------------------------------
# Geometry types
# ---------------------------------------------------------------------------


def test_point_placemark_yields_point_cto():
    pm = """
    <Placemark>
      <name>OBJ TARGET</name>
      <Point><coordinates>-77.4,34.5,0</coordinates></Point>
    </Placemark>
    """
    ctos = _ingest(_kmz(pm))
    assert len(ctos) == 1
    assert ctos[0].geometry.type == "Point"
    assert ctos[0].geometry.coordinates == [-77.4, 34.5]
    assert ctos[0].label == "OBJ TARGET"
    assert ctos[0].object_class == ObjectClass.GRAPHIC


def test_linestring_placemark_yields_linestring_cto():
    pm = """
    <Placemark>
      <name>PL ALPHA</name>
      <LineString>
        <coordinates>
          -77.4,34.5 -77.3,34.5 -77.2,34.5
        </coordinates>
      </LineString>
    </Placemark>
    """
    ctos = _ingest(_kmz(pm))
    assert len(ctos) == 1
    assert ctos[0].geometry.type == "LineString"
    assert len(ctos[0].geometry.coordinates) == 3
    assert ctos[0].attributes.get("graphic_kind") == "phase_line"


def test_polygon_placemark_yields_polygon_cto():
    pm = """
    <Placemark>
      <name>NAI 17</name>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>
              -77.5,34.4 -77.3,34.4 -77.3,34.6 -77.5,34.6 -77.5,34.4
            </coordinates>
          </LinearRing>
        </outerBoundaryIs>
      </Polygon>
    </Placemark>
    """
    ctos = _ingest(_kmz(pm))
    assert len(ctos) == 1
    assert ctos[0].geometry.type == "Polygon"
    assert len(ctos[0].geometry.coordinates) == 1
    assert ctos[0].attributes.get("graphic_kind") == "nai"


def test_polygon_with_hole():
    pm = """
    <Placemark>
      <name>ROZ ZULU</name>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>
              -77.5,34.4 -77.3,34.4 -77.3,34.6 -77.5,34.6 -77.5,34.4
            </coordinates>
          </LinearRing>
        </outerBoundaryIs>
        <innerBoundaryIs>
          <LinearRing>
            <coordinates>
              -77.45,34.45 -77.35,34.45 -77.35,34.55 -77.45,34.55 -77.45,34.45
            </coordinates>
          </LinearRing>
        </innerBoundaryIs>
      </Polygon>
    </Placemark>
    """
    ctos = _ingest(_kmz(pm))
    assert len(ctos) == 1
    assert ctos[0].geometry.type == "Polygon"
    assert len(ctos[0].geometry.coordinates) == 2  # outer + 1 inner
    assert ctos[0].attributes.get("graphic_kind") == "roz"


# ---------------------------------------------------------------------------
# Multi-feature handling
# ---------------------------------------------------------------------------


def test_multi_feature_kmz_yields_one_cto_per_placemark():
    placemarks = """
    <Placemark>
      <name>PL ALPHA</name>
      <LineString><coordinates>-77.4,34.5 -77.3,34.5</coordinates></LineString>
    </Placemark>
    <Placemark>
      <name>NAI 7</name>
      <Polygon><outerBoundaryIs><LinearRing>
        <coordinates>-77.5,34.4 -77.3,34.4 -77.3,34.6 -77.5,34.6 -77.5,34.4</coordinates>
      </LinearRing></outerBoundaryIs></Polygon>
    </Placemark>
    <Placemark>
      <name>OBJ ECHO</name>
      <Point><coordinates>-77.4,34.5</coordinates></Point>
    </Placemark>
    """
    ctos = _ingest(_kmz(placemarks), filename="overlay.kmz")
    assert len(ctos) == 3
    types = {c.geometry.type for c in ctos}
    assert types == {"Point", "LineString", "Polygon"}
    # All share parent
    for c in ctos:
        assert c.attributes["parent_kmz_filename"] == "overlay.kmz"
        assert c.attributes["parent_kmz_source"] == "folder"
    # Feature indices are sequential
    indices = sorted(c.attributes["kmz_feature_index"] for c in ctos)
    assert indices == [0, 1, 2]


# ---------------------------------------------------------------------------
# Doctrinal classification
# ---------------------------------------------------------------------------


def test_classify_label_recognizes_doctrinal_patterns():
    cases = [
        ("PL ALPHA", "phase_line"),
        ("PL BRAVO-1", "phase_line"),
        ("FEBA", "feba"),
        ("FLOT", "flot"),
        ("NAI 7", "nai"),
        ("NAI HOTEL", "nai"),
        ("TAI 3", "tai"),
        ("NFA RED", "nfa"),
        ("RFA HOTEL", "rfa"),
        ("ROZ ZULU", "roz"),
        ("FSCL", "fscl"),
        ("CFL", "cfl"),
        ("BOUNDARY 1-2 BN", "boundary"),
        ("BNDRY ALPHA/BRAVO", "boundary"),
        ("OBJ TARGET", "objective"),
        ("EA SWORD", "engagement_area"),
        ("AA TIGER", "assembly_area"),
        ("BP 17", "battle_position"),
        ("Random unrecognized name", None),
    ]
    for label, expected_kind in cases:
        attrs = _classify_label(label)
        if expected_kind is None:
            assert attrs == {}, f"unexpected match for {label!r}: {attrs}"
        else:
            assert attrs.get("graphic_kind") == expected_kind, \
                f"{label!r} expected {expected_kind} got {attrs}"


# ---------------------------------------------------------------------------
# ExtendedData
# ---------------------------------------------------------------------------


def test_extended_data_captures_sidc_and_custom_fields():
    pm = """
    <Placemark>
      <name>PL ALPHA</name>
      <LineString><coordinates>-77.4,34.5 -77.3,34.5</coordinates></LineString>
      <ExtendedData>
        <Data name="sidc"><value>G*GPGLP---****X</value></Data>
        <Data name="author"><value>LCpl Smith</value></Data>
        <Data name="effective_dtg"><value>221200Z MAY 26</value></Data>
      </ExtendedData>
    </Placemark>
    """
    ctos = _ingest(_kmz(pm))
    assert ctos[0].symbology.sidc_2525c == "G*GPGLP---****X"
    ext = ctos[0].attributes["kmz_extended_data"]
    assert ext["author"] == "LCpl Smith"
    assert ext["effective_dtg"] == "221200Z MAY 26"


# ---------------------------------------------------------------------------
# MultiGeometry and NetworkLink edge cases
# ---------------------------------------------------------------------------


def test_multigeometry_extracts_first_geometry_with_warning():
    pm = """
    <Placemark>
      <name>OBJ MULTI</name>
      <MultiGeometry>
        <Point><coordinates>-77.4,34.5</coordinates></Point>
        <LineString><coordinates>-77.3,34.5 -77.2,34.5</coordinates></LineString>
      </MultiGeometry>
    </Placemark>
    """
    ctos = _ingest(_kmz(pm))
    assert len(ctos) == 1
    assert ctos[0].geometry.type == "Point"


def test_networklink_does_not_break_parse_and_is_not_followed():
    pm = """
    <NetworkLink>
      <name>External feed</name>
      <Link><href>http://example.com/feed.kml</href></Link>
    </NetworkLink>
    <Placemark>
      <name>PL ALPHA</name>
      <LineString><coordinates>-77.4,34.5 -77.3,34.5</coordinates></LineString>
    </Placemark>
    """
    ctos = _ingest(_kmz(pm))
    assert len(ctos) == 1
    assert ctos[0].label == "PL ALPHA"


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_not_a_zip_raises():
    with pytest.raises(KmzParseError):
        _ingest(b"this is not a zip")


def test_zip_without_kml_raises():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("README.txt", "hello")
    with pytest.raises(KmzParseError):
        _ingest(buf.getvalue())


def test_placemark_without_geometry_is_skipped():
    pm = """
    <Placemark>
      <name>Just a label</name>
    </Placemark>
    <Placemark>
      <name>OBJ TARGET</name>
      <Point><coordinates>-77.4,34.5</coordinates></Point>
    </Placemark>
    """
    ctos = _ingest(_kmz(pm))
    # Only the one with geometry survives
    assert len(ctos) == 1
    assert ctos[0].label == "OBJ TARGET"
