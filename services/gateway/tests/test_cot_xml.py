"""Tests for the CoT XML normalizer.

Round-trip tests verify that a CoT XML message can be parsed into a CTO
and converted back to CoT XML preserving the important semantic content.
We do not require byte-identical output (XML formatting differs); we
require that the relevant fields survive the round-trip.
"""

from __future__ import annotations

from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import pytest

from cto_schema import (
    Affiliation,
    BattleDimension,
    ObjectClass,
    RawPointer,
    SourceProtocol,
)

from services.gateway.normalizers.cot_xml import (
    CoTXmlParseError,
    cot_xml_to_cto,
    cto_to_cot_xml,
    parse_cot_type,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def raw_pointer(now: datetime) -> RawPointer:
    return RawPointer(
        sha256="a" * 64,
        object_key="raw/2026/05/20/cot_xml/aa/test",
        size_bytes=512,
        captured_at=now,
    )


SAMPLE_XML = b"""<event version="2.0" uid="ANDROID-12345" type="a-f-G-U-C" time="2026-05-20T12:00:00Z" start="2026-05-20T12:00:00Z" stale="2026-05-20T12:05:00Z" how="m-g"><point lat="34.5054000" lon="-77.4360000" hae="42.00" ce="2.50" le="5.00"/><detail><contact callsign="HAMMER-6" endpoint="*:-1:stcp"/><__group name="Blue" role="Team Member"/><track course="180.00" speed="2.50"/><remarks>test track</remarks></detail></event>"""


# ---------------------------------------------------------------------------
# CoT type parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cot_type,want_affil,want_dim", [
    ("a-f-G-U-C", Affiliation.FRIEND, BattleDimension.LAND),
    ("a-h-A-M-F", Affiliation.HOSTILE, BattleDimension.AIR),
    ("a-n-S", Affiliation.NEUTRAL, BattleDimension.SEA_SURFACE),
    ("a-u-G", Affiliation.UNKNOWN, BattleDimension.LAND),
    ("b-m-p-s-m", None, None),  # not an atom
])
def test_parse_cot_type(cot_type, want_affil, want_dim):
    affil, dim = parse_cot_type(cot_type)
    assert affil == want_affil
    assert dim == want_dim


# ---------------------------------------------------------------------------
# Parse: CoT XML -> CTO
# ---------------------------------------------------------------------------


def test_parse_basic_track(now, raw_pointer):
    cto = cot_xml_to_cto(
        xml_bytes=SAMPLE_XML,
        source_system="test-source",
        received_at=now,
        raw_pointer=raw_pointer,
    )
    assert cto.source_uid == "ANDROID-12345"
    assert cto.source_protocol == SourceProtocol.COT_XML
    assert cto.source_system == "test-source"
    assert cto.object_class == ObjectClass.TRACK
    assert cto.symbology.cot_type == "a-f-G-U-C"
    assert cto.symbology.affiliation == Affiliation.FRIEND
    assert cto.symbology.battle_dimension == BattleDimension.LAND
    assert cto.callsign == "HAMMER-6"
    assert cto.geometry.type == "Point"
    assert cto.geometry.coordinates == [-77.4360000, 34.5054000]
    assert cto.altitude is not None
    assert cto.altitude.value_m == 42.0
    assert cto.kinematics is not None
    assert cto.kinematics.course_deg == 180.0
    assert cto.kinematics.speed_mps == 2.5
    assert cto.remarks == "test track"
    assert cto.attributes.get("cot_how") == "m-g"


def test_parse_missing_point_rejected(now, raw_pointer):
    bad = b'<event version="2.0" uid="x" type="a-u-G" time="2026-05-20T12:00:00Z" start="2026-05-20T12:00:00Z" stale="2026-05-20T12:05:00Z"/>'
    with pytest.raises(CoTXmlParseError):
        cot_xml_to_cto(
            xml_bytes=bad, source_system="s", received_at=now, raw_pointer=raw_pointer,
        )


def test_parse_invalid_xml_rejected(now, raw_pointer):
    with pytest.raises(CoTXmlParseError):
        cot_xml_to_cto(
            xml_bytes=b"<not valid",
            source_system="s", received_at=now, raw_pointer=raw_pointer,
        )


def test_parse_preserves_unknown_detail(now, raw_pointer):
    xml = b'<event version="2.0" uid="x" type="a-f-G" time="2026-05-20T12:00:00Z" start="2026-05-20T12:00:00Z" stale="2026-05-20T12:05:00Z"><point lat="0" lon="0" hae="0" ce="2" le="2"/><detail><custom_thing foo="bar"><nested>hi</nested></custom_thing></detail></event>'
    cto = cot_xml_to_cto(
        xml_bytes=xml, source_system="s", received_at=now, raw_pointer=raw_pointer,
    )
    # The custom_thing element should be in cot_detail attributes.
    detail = cto.attributes.get("cot_detail")
    assert detail is not None
    assert "custom_thing" in detail
    assert detail["custom_thing"]["foo"] == "bar"


# ---------------------------------------------------------------------------
# Generate: CTO -> CoT XML
# ---------------------------------------------------------------------------


def test_roundtrip_preserves_core_fields(now, raw_pointer):
    cto = cot_xml_to_cto(
        xml_bytes=SAMPLE_XML, source_system="test", received_at=now, raw_pointer=raw_pointer,
    )
    out_bytes, lossy = cto_to_cot_xml(cto)
    assert lossy == []
    out = ET.fromstring(out_bytes)
    assert out.tag == "event"
    assert out.get("uid") == "ANDROID-12345"
    assert out.get("type") == "a-f-G-U-C"
    assert out.get("how") == "m-g"
    point = out.find("point")
    assert point is not None
    assert float(point.get("lat")) == pytest.approx(34.5054)
    assert float(point.get("lon")) == pytest.approx(-77.4360)
    assert float(point.get("hae")) == pytest.approx(42.0)
    contact = out.find("detail/contact")
    assert contact is not None
    assert contact.get("callsign") == "HAMMER-6"
    track = out.find("detail/track")
    assert track is not None
    assert float(track.get("course")) == pytest.approx(180.0)
    assert float(track.get("speed")) == pytest.approx(2.5)


def test_roundtrip_preserves_unknown_detail_subtree(now, raw_pointer):
    xml = b'<event version="2.0" uid="x" type="a-f-G" time="2026-05-20T12:00:00Z" start="2026-05-20T12:00:00Z" stale="2026-05-20T12:05:00Z"><point lat="0" lon="0" hae="0" ce="2" le="2"/><detail><__group name="Red" role="HQ"/><custom_thing foo="bar"/></detail></event>'
    cto = cot_xml_to_cto(
        xml_bytes=xml, source_system="s", received_at=now, raw_pointer=raw_pointer,
    )
    out_bytes, _ = cto_to_cot_xml(cto)
    out = ET.fromstring(out_bytes)
    group = out.find("detail/__group")
    assert group is not None
    assert group.get("name") == "Red"
    assert group.get("role") == "HQ"
    custom = out.find("detail/custom_thing")
    assert custom is not None
    assert custom.get("foo") == "bar"


def test_double_roundtrip_stable(now, raw_pointer):
    """parse -> generate -> parse should produce equivalent CTOs."""
    cto1 = cot_xml_to_cto(
        xml_bytes=SAMPLE_XML, source_system="t", received_at=now, raw_pointer=raw_pointer,
    )
    out1, _ = cto_to_cot_xml(cto1)
    cto2 = cot_xml_to_cto(
        xml_bytes=out1, source_system="t", received_at=now, raw_pointer=raw_pointer,
    )
    # UIDs differ (each call generates a new uuid7), so compare the rest.
    assert cto1.source_uid == cto2.source_uid
    assert cto1.symbology == cto2.symbology
    assert cto1.geometry == cto2.geometry
    assert cto1.callsign == cto2.callsign
    assert cto1.kinematics == cto2.kinematics
    assert cto1.altitude == cto2.altitude
