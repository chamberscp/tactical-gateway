"""Unit tests for the parser ↔ recognizer integration.

These tests build tiny synthetic KMZs in-memory and assert that the
KMZ parser correctly:
  1. Runs the recognizer on each placemark's label.
  2. Populates Symbology(sidc_2525c, affiliation) from the recognition.
  3. Preserves the doctrinal_kind in attributes['graphic_kind'] for
     Phase 2a backward compatibility.
  4. Records a `kmz_label_recognized` ProvenanceEntry with the right
     lossy_fields and a parseable JSON notes payload.
  5. Lets an explicit <ExtendedData sidc=...> override the recognizer's
     SIDC while still using the recognizer for other fields.

No I/O, no infrastructure; fast.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone

import pytest

from cto_schema import Affiliation, IngestSource, ObjectClass, RawPointer

from services.gateway.kmz_parser import kmz_to_ctos


# ---------- helpers ------------------------------------------------------


def _make_kmz(placemarks_kml: str) -> bytes:
    """Wrap raw <Placemark> KML in a minimal KMZ and return the zip bytes."""
    kml = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    {placemarks_kml}
  </Document>
</kml>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("doc.kml", kml)
    return buf.getvalue()


def _fake_raw_pointer() -> RawPointer:
    return RawPointer(
        sha256="0" * 64,
        object_key="raw/2026/06/02/kmz/00/test",
        size_bytes=1,
        captured_at=datetime.now(timezone.utc),
    )


def _parse(kmz_bytes: bytes):
    """Run the parser with the standard test fixtures."""
    return kmz_to_ctos(
        kmz_bytes=kmz_bytes,
        filename="test.kmz",
        source_system="test",
        received_at=datetime.now(timezone.utc),
        raw_pointer=_fake_raw_pointer(),
        ingest_source=IngestSource.FOLDER,
    )


def _recognition_step(cto):
    """Find the kmz_label_recognized provenance entry on a CTO."""
    for entry in cto.provenance:
        if entry.step == "kmz_label_recognized":
            return entry
    raise AssertionError("CTO has no kmz_label_recognized provenance step")


# ---------- tests --------------------------------------------------------


class TestRecognitionFlowsIntoCto:
    """The recognizer's outputs land on the CTO in the expected fields."""

    def test_pl_alpha_populates_symbology(self):
        kmz = _make_kmz("""
          <Placemark>
            <name>PL ALPHA</name>
            <LineString>
              <coordinates>-77.4,34.5,0 -77.3,34.5,0</coordinates>
            </LineString>
          </Placemark>
        """)
        ctos = _parse(kmz)
        assert len(ctos) == 1
        c = ctos[0]
        # Recognizer produced a SIDC; parser stored it.
        assert c.symbology.sidc_2525c is not None
        assert len(c.symbology.sidc_2525c) == 15
        # Affiliation is unknown (no description hint).
        assert c.symbology.affiliation == Affiliation.UNKNOWN
        # graphic_kind preserved for backward compatibility.
        assert c.attributes["graphic_kind"] == "phase_line"

    def test_obj_target_with_enemy_description_yields_hostile(self):
        kmz = _make_kmz("""
          <Placemark>
            <name>OBJ TARGET</name>
            <description>enemy stronghold</description>
            <Point><coordinates>-77.4,34.5,0</coordinates></Point>
          </Placemark>
        """)
        ctos = _parse(kmz)
        c = ctos[0]
        assert c.symbology.affiliation == Affiliation.HOSTILE
        # SIDC position 2 should be 'H'.
        assert c.symbology.sidc_2525c[1] == "H"

    def test_suspected_prefix_yields_suspect(self):
        kmz = _make_kmz("""
          <Placemark>
            <name>Suspected EA 1</name>
            <Polygon><outerBoundaryIs><LinearRing><coordinates>
              -77.5,34.5,0 -77.4,34.5,0 -77.4,34.6,0 -77.5,34.5,0
            </coordinates></LinearRing></outerBoundaryIs></Polygon>
          </Placemark>
        """)
        ctos = _parse(kmz)
        c = ctos[0]
        assert c.symbology.affiliation == Affiliation.SUSPECT
        # graphic_kind is from the underlying EA prefix.
        assert c.attributes["graphic_kind"] == "engagement_area"

    def test_explicit_extendeddata_sidc_wins(self):
        """An explicit <ExtendedData sidc=...> should override the
        recognizer's SIDC, but other recognizer outputs still apply."""
        kmz = _make_kmz("""
          <Placemark>
            <name>NAI 7</name>
            <ExtendedData>
              <Data name="sidc"><value>GFGPGAA-------X</value></Data>
            </ExtendedData>
            <Polygon><outerBoundaryIs><LinearRing><coordinates>
              -77.5,34.5,0 -77.4,34.5,0 -77.4,34.6,0 -77.5,34.5,0
            </coordinates></LinearRing></outerBoundaryIs></Polygon>
          </Placemark>
        """)
        ctos = _parse(kmz)
        c = ctos[0]
        # Explicit SIDC kept verbatim.
        assert c.symbology.sidc_2525c == "GFGPGAA-------X"
        # But graphic_kind still came from the recognizer (NAI prefix).
        assert c.attributes["graphic_kind"] == "named_area_of_interest"
        # The provenance step records that explicit SIDC was used.
        prov = _recognition_step(c)
        payload = json.loads(prov.notes)
        assert payload["explicit_sidc_used"] is True
        # sidc_2525c should NOT be in lossy_fields when explicit.
        assert "sidc_2525c" not in prov.lossy_fields


class TestRecognitionProvenance:
    """The kmz_label_recognized provenance step carries the right data."""

    def test_provenance_step_is_appended(self):
        kmz = _make_kmz("""
          <Placemark>
            <name>NAI 7</name>
            <Point><coordinates>-77.4,34.5,0</coordinates></Point>
          </Placemark>
        """)
        ctos = _parse(kmz)
        c = ctos[0]
        steps = [p.step for p in c.provenance]
        assert steps == ["kmz_to_cto", "kmz_label_recognized"]

    def test_provenance_notes_are_json_parseable(self):
        kmz = _make_kmz("""
          <Placemark>
            <name>PL ALPHA</name>
            <LineString>
              <coordinates>-77.4,34.5,0 -77.3,34.5,0</coordinates>
            </LineString>
          </Placemark>
        """)
        ctos = _parse(kmz)
        prov = _recognition_step(ctos[0])
        payload = json.loads(prov.notes)
        assert payload["matched_layer"] == "prefix"
        assert payload["doctrinal_kind"] == "phase_line"
        assert "reasons" in payload
        assert isinstance(payload["reasons"], list)

    def test_best_effort_match_marks_lossy_fields(self):
        """An unrecognized label should populate lossy_fields with both
        sidc_2525c and affiliation, since both were inferred."""
        kmz = _make_kmz("""
          <Placemark>
            <name>Chuck's house</name>
            <Point><coordinates>-77.4,34.5,0</coordinates></Point>
          </Placemark>
        """)
        ctos = _parse(kmz)
        prov = _recognition_step(ctos[0])
        assert "sidc_2525c" in prov.lossy_fields
        assert "affiliation" in prov.lossy_fields

    def test_clean_match_still_marks_inferred_fields(self):
        """Even when the recognizer makes a clean match, the SIDC and
        affiliation were not literally read from the KMZ — they're
        inferred. lossy_fields documents that."""
        kmz = _make_kmz("""
          <Placemark>
            <name>NAI 7</name>
            <Point><coordinates>-77.4,34.5,0</coordinates></Point>
          </Placemark>
        """)
        ctos = _parse(kmz)
        prov = _recognition_step(ctos[0])
        assert "sidc_2525c" in prov.lossy_fields
        assert "affiliation" in prov.lossy_fields


class TestBackwardCompatibility:
    """The recognizer integration preserves attributes['graphic_kind']
    so Phase 2a CTOs and Phase 2b-2 CTOs share the same vocabulary."""

    @pytest.mark.parametrize("label,expected_kind", [
        ("PL ALPHA", "phase_line"),
        ("NAI 7", "named_area_of_interest"),
        ("OBJ TARGET", "objective"),
        ("AA 1", "assembly_area"),
        ("BP 1", "battle_position"),
        ("EA 1", "engagement_area"),
    ])
    def test_graphic_kind_preserved(self, label, expected_kind):
        kmz = _make_kmz(f"""
          <Placemark>
            <name>{label}</name>
            <Point><coordinates>-77.4,34.5,0</coordinates></Point>
          </Placemark>
        """)
        ctos = _parse(kmz)
        assert ctos[0].attributes["graphic_kind"] == expected_kind


class TestMultiPlacemark:
    """A KMZ with several placemarks produces one CTO each, each with
    its own correct recognition output."""

    def test_three_different_labels(self):
        kmz = _make_kmz("""
          <Placemark>
            <name>PL ALPHA</name>
            <LineString><coordinates>-77.4,34.5,0 -77.3,34.5,0</coordinates></LineString>
          </Placemark>
          <Placemark>
            <name>NAI 7</name>
            <description>enemy assembly</description>
            <Polygon><outerBoundaryIs><LinearRing><coordinates>
              -77.5,34.5,0 -77.4,34.5,0 -77.4,34.6,0 -77.5,34.5,0
            </coordinates></LinearRing></outerBoundaryIs></Polygon>
          </Placemark>
          <Placemark>
            <name>Chuck's house</name>
            <Point><coordinates>-77.4,34.5,0</coordinates></Point>
          </Placemark>
        """)
        ctos = _parse(kmz)
        assert len(ctos) == 3

        # PL ALPHA: phase line, unknown affiliation
        assert ctos[0].attributes["graphic_kind"] == "phase_line"
        assert ctos[0].symbology.affiliation == Affiliation.UNKNOWN

        # NAI 7 (enemy): NAI, hostile
        assert ctos[1].attributes["graphic_kind"] == "named_area_of_interest"
        assert ctos[1].symbology.affiliation == Affiliation.HOSTILE

        # Chuck's house: fallback, no graphic_kind
        assert "graphic_kind" not in ctos[2].attributes
        assert ctos[2].symbology.affiliation == Affiliation.UNKNOWN
