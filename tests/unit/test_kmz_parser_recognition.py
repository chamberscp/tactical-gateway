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
     SIDC AND drive the affiliation from its position-2 character.

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
    return kmz_to_ctos(
        kmz_bytes=kmz_bytes,
        filename="test.kmz",
        source_system="test",
        received_at=datetime.now(timezone.utc),
        raw_pointer=_fake_raw_pointer(),
        ingest_source=IngestSource.FOLDER,
    )


def _recognition_step(cto):
    for entry in cto.provenance:
        if entry.step == "kmz_label_recognized":
            return entry
    raise AssertionError("CTO has no kmz_label_recognized provenance step")


# ---------- tests --------------------------------------------------------


class TestRecognitionFlowsIntoCto:

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
        assert c.symbology.sidc_2525c is not None
        assert len(c.symbology.sidc_2525c) == 15
        assert c.symbology.affiliation == Affiliation.UNKNOWN
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
        assert c.attributes["graphic_kind"] == "engagement_area"


class TestExplicitSidcDrivesAffiliation:
    """The NAI 99 finding: when an explicit ExtendedData SIDC is supplied,
    it drives both the SIDC field AND the affiliation field consistently."""

    def test_explicit_friend_sidc_yields_friend_affiliation(self):
        kmz = _make_kmz("""
          <Placemark>
            <name>NAI 99</name>
            <ExtendedData>
              <Data name="sidc"><value>GFGPGPRN-------</value></Data>
            </ExtendedData>
            <Polygon><outerBoundaryIs><LinearRing><coordinates>
              -77.5,34.5,0 -77.4,34.5,0 -77.4,34.6,0 -77.5,34.5,0
            </coordinates></LinearRing></outerBoundaryIs></Polygon>
          </Placemark>
        """)
        ctos = _parse(kmz)
        c = ctos[0]
        # Explicit SIDC preserved verbatim.
        assert c.symbology.sidc_2525c == "GFGPGPRN-------"
        # And the affiliation field reflects the SIDC's position 2.
        assert c.symbology.affiliation == Affiliation.FRIEND

    def test_explicit_hostile_sidc_yields_hostile_affiliation(self):
        kmz = _make_kmz("""
          <Placemark>
            <name>Some target</name>
            <ExtendedData>
              <Data name="sidc"><value>GHGPDPT--------</value></Data>
            </ExtendedData>
            <Point><coordinates>-77.4,34.5,0</coordinates></Point>
          </Placemark>
        """)
        ctos = _parse(kmz)
        c = ctos[0]
        assert c.symbology.sidc_2525c == "GHGPDPT--------"
        assert c.symbology.affiliation == Affiliation.HOSTILE

    def test_explicit_sidc_beats_description_hint(self):
        """An explicit SIDC saying 'friend' wins over a description hint
        saying 'enemy'. The SIDC is the authoritative signal."""
        kmz = _make_kmz("""
          <Placemark>
            <name>Contradictory thing</name>
            <description>enemy assembly observed</description>
            <ExtendedData>
              <Data name="sidc"><value>GFGPGPRN-------</value></Data>
            </ExtendedData>
            <Polygon><outerBoundaryIs><LinearRing><coordinates>
              -77.5,34.5,0 -77.4,34.5,0 -77.4,34.6,0 -77.5,34.5,0
            </coordinates></LinearRing></outerBoundaryIs></Polygon>
          </Placemark>
        """)
        ctos = _parse(kmz)
        c = ctos[0]
        assert c.symbology.affiliation == Affiliation.FRIEND

    def test_provenance_records_explicit_sidc_as_source(self):
        kmz = _make_kmz("""
          <Placemark>
            <name>NAI 99</name>
            <ExtendedData>
              <Data name="sidc"><value>GFGPGPRN-------</value></Data>
            </ExtendedData>
            <Polygon><outerBoundaryIs><LinearRing><coordinates>
              -77.5,34.5,0 -77.4,34.5,0 -77.4,34.6,0 -77.5,34.5,0
            </coordinates></LinearRing></outerBoundaryIs></Polygon>
          </Placemark>
        """)
        ctos = _parse(kmz)
        prov = _recognition_step(ctos[0])
        payload = json.loads(prov.notes)
        assert payload["explicit_sidc_used"] is True
        assert payload["affiliation_source"] == "explicit_sidc"

    def test_lossy_fields_empty_when_explicit_sidc_drives_affiliation(self):
        """When explicit SIDC drives the affiliation, neither sidc_2525c
        nor affiliation was inferred — both came directly from the source.
        lossy_fields should reflect that."""
        kmz = _make_kmz("""
          <Placemark>
            <name>NAI 99</name>
            <ExtendedData>
              <Data name="sidc"><value>GFGPGPRN-------</value></Data>
            </ExtendedData>
            <Polygon><outerBoundaryIs><LinearRing><coordinates>
              -77.5,34.5,0 -77.4,34.5,0 -77.4,34.6,0 -77.5,34.5,0
            </coordinates></LinearRing></outerBoundaryIs></Polygon>
          </Placemark>
        """)
        ctos = _parse(kmz)
        prov = _recognition_step(ctos[0])
        assert prov.lossy_fields == []  # neither field was inferred

    def test_malformed_explicit_sidc_falls_back_to_recognizer(self):
        """If the explicit SIDC isn't 15 chars or has an unknown
        identity char, fall back to the recognizer's affiliation."""
        kmz = _make_kmz("""
          <Placemark>
            <name>NAI 99</name>
            <description>enemy here</description>
            <ExtendedData>
              <Data name="sidc"><value>bogus</value></Data>
            </ExtendedData>
            <Polygon><outerBoundaryIs><LinearRing><coordinates>
              -77.5,34.5,0 -77.4,34.5,0 -77.4,34.6,0 -77.5,34.5,0
            </coordinates></LinearRing></outerBoundaryIs></Polygon>
          </Placemark>
        """)
        ctos = _parse(kmz)
        c = ctos[0]
        # SIDC preserved verbatim (parser stores what was given), but
        # affiliation falls back to recognizer's description-derived value.
        assert c.symbology.sidc_2525c == "bogus"
        assert c.symbology.affiliation == Affiliation.HOSTILE


class TestRecognitionProvenance:

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
        assert "affiliation_source" in payload

    def test_best_effort_match_marks_lossy_fields(self):
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

        assert ctos[0].attributes["graphic_kind"] == "phase_line"
        assert ctos[0].symbology.affiliation == Affiliation.UNKNOWN

        assert ctos[1].attributes["graphic_kind"] == "named_area_of_interest"
        assert ctos[1].symbology.affiliation == Affiliation.HOSTILE

        assert "graphic_kind" not in ctos[2].attributes
        assert ctos[2].symbology.affiliation == Affiliation.UNKNOWN
