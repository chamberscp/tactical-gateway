"""Tests for the CTO schema.

These exercise the validation rules and demonstrate the intended usage
patterns. They serve double duty as documentation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from cto_schema import (
    CTO,
    Affiliation,
    Altitude,
    AltitudeSource,
    BattleDimension,
    Classification,
    Geometry,
    Kinematics,
    ObjectClass,
    RawPointer,
    SourceProtocol,
    Symbology,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def raw_pointer(now: datetime) -> RawPointer:
    return RawPointer(
        sha256="a" * 64,
        object_key="raw/2026/05/20/cot/sample.xml",
        size_bytes=1024,
        captured_at=now,
    )


@pytest.fixture
def point_geom() -> Geometry:
    return Geometry(type="Point", coordinates=[-77.4360, 34.5054])


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def test_geometry_rejects_empty_coords() -> None:
    with pytest.raises(ValidationError):
        Geometry(type="Point", coordinates=[])


def test_geometry_accepts_point() -> None:
    g = Geometry(type="Point", coordinates=[-77.4360, 34.5054])
    assert g.type == "Point"


# ---------------------------------------------------------------------------
# CTO construction
# ---------------------------------------------------------------------------


def test_minimal_cto_track(now: datetime, raw_pointer: RawPointer, point_geom: Geometry) -> None:
    """A minimal valid CTO for a friendly ground track from CoT."""
    cto = CTO(
        uid=uuid4(),
        source_uid="ANDROID-12345",
        source_system="tak-primary",
        source_protocol=SourceProtocol.COT_XML,
        received_at=now,
        event_time=now,
        valid_from=now,
        valid_to=now + timedelta(minutes=5),
        object_class=ObjectClass.TRACK,
        geometry=point_geom,
        altitude=Altitude(value_m=42.0, source=AltitudeSource.GPS),
        kinematics=Kinematics(course_deg=180.0, speed_mps=2.5),
        symbology=Symbology(
            cot_type="a-f-G-U-C",
            mil_std_2525d_sidc="SFGPUC----D----",
            affiliation=Affiliation.FRIEND,
            battle_dimension=BattleDimension.LAND,
        ),
        callsign="HAMMER-6",
        raw_pointer=raw_pointer,
    )
    assert cto.classification == Classification.UNCLASSIFIED
    assert cto.symbology.affiliation == Affiliation.FRIEND
    assert cto.callsign == "HAMMER-6"


def test_cto_is_frozen(now: datetime, raw_pointer: RawPointer, point_geom: Geometry) -> None:
    """CTOs are immutable — new facts produce new CTOs."""
    cto = CTO(
        uid=uuid4(),
        source_system="tak-primary",
        source_protocol=SourceProtocol.COT_XML,
        received_at=now,
        event_time=now,
        object_class=ObjectClass.TRACK,
        geometry=point_geom,
        raw_pointer=raw_pointer,
    )
    with pytest.raises(ValidationError):
        cto.callsign = "OTHER"  # type: ignore[misc]


def test_datetime_must_be_tz_aware(raw_pointer: RawPointer, point_geom: Geometry) -> None:
    naive = datetime(2026, 5, 20, 12, 0, 0)  # no tzinfo
    with pytest.raises(ValidationError):
        CTO(
            uid=uuid4(),
            source_system="tak-primary",
            source_protocol=SourceProtocol.COT_XML,
            received_at=naive,
            event_time=naive,
            object_class=ObjectClass.TRACK,
            geometry=point_geom,
            raw_pointer=raw_pointer,
        )


def test_attributes_escape_hatch(now: datetime, raw_pointer: RawPointer, point_geom: Geometry) -> None:
    """Source-specific fields land in attributes so nothing is lost at ingest."""
    cto = CTO(
        uid=uuid4(),
        source_system="gccs-j-1",
        source_protocol=SourceProtocol.OTH_GOLD,
        received_at=now,
        event_time=now,
        object_class=ObjectClass.TRACK,
        geometry=point_geom,
        raw_pointer=raw_pointer,
        attributes={
            "oth_track_number": "T1234",
            "oth_quality": 7,
            "ssr_modes": {"mode_3a": "1200"},
        },
    )
    assert cto.attributes["oth_track_number"] == "T1234"
    assert cto.attributes["ssr_modes"]["mode_3a"] == "1200"


def test_speed_must_be_non_negative(now: datetime, raw_pointer: RawPointer, point_geom: Geometry) -> None:
    with pytest.raises(ValidationError):
        Kinematics(speed_mps=-1.0)


def test_course_must_be_in_range() -> None:
    with pytest.raises(ValidationError):
        Kinematics(course_deg=361.0)
    with pytest.raises(ValidationError):
        Kinematics(course_deg=-1.0)


def test_serialization_roundtrip(now: datetime, raw_pointer: RawPointer, point_geom: Geometry) -> None:
    """A CTO must serialize to JSON and back without loss."""
    original = CTO(
        uid=uuid4(),
        source_uid="ANDROID-12345",
        source_system="tak-primary",
        source_protocol=SourceProtocol.COT_XML,
        received_at=now,
        event_time=now,
        object_class=ObjectClass.TRACK,
        geometry=point_geom,
        callsign="HAMMER-6",
        raw_pointer=raw_pointer,
        attributes={"custom_field": "value"},
    )
    json_str = original.model_dump_json()
    restored = CTO.model_validate_json(json_str)
    assert restored == original
