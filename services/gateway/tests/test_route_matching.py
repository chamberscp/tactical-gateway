"""Tests for route matching logic (in-memory, no network)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from cto_schema import (
    CTO,
    Geometry,
    ObjectClass,
    RawPointer,
    SourceProtocol,
)

from services.gateway.route_engine import _matches
from services.gateway.routes_model import RouteMatch


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)


def make_cto(now, source_system="tak:1", protocol=SourceProtocol.COT_XML,
             obj_class=ObjectClass.TRACK) -> CTO:
    return CTO(
        uid=uuid4(),
        source_uid="x",
        source_system=source_system,
        source_protocol=protocol,
        received_at=now,
        event_time=now,
        object_class=obj_class,
        geometry=Geometry(type="Point", coordinates=[0.0, 0.0]),
        raw_pointer=RawPointer(
            sha256="0" * 64, object_key="k", size_bytes=1, captured_at=now,
        ),
    )


def test_empty_match_matches_anything(now):
    cto = make_cto(now)
    assert _matches(cto, RouteMatch()) is True


def test_source_system_glob_match(now):
    cto = make_cto(now, source_system="cot-xml-tcp:10.0.0.1:55001")
    assert _matches(cto, RouteMatch(source_system_glob="cot-xml-tcp:*")) is True
    assert _matches(cto, RouteMatch(source_system_glob="cot-pb-tcp:*")) is False
    assert _matches(cto, RouteMatch(source_system_glob="*")) is True


def test_source_protocol_match(now):
    cto = make_cto(now, protocol=SourceProtocol.COT_PROTOBUF)
    assert _matches(cto, RouteMatch(source_protocol="cot_protobuf")) is True
    assert _matches(cto, RouteMatch(source_protocol="cot_xml")) is False


def test_object_class_match(now):
    cto = make_cto(now, obj_class=ObjectClass.AREA)
    assert _matches(cto, RouteMatch(object_class="area")) is True
    assert _matches(cto, RouteMatch(object_class="track")) is False


def test_all_conditions_must_match(now):
    cto = make_cto(now, source_system="tak:1", protocol=SourceProtocol.COT_XML)
    m = RouteMatch(source_system_glob="tak:*", source_protocol="cot_xml")
    assert _matches(cto, m) is True
    m2 = RouteMatch(source_system_glob="tak:*", source_protocol="cot_protobuf")
    assert _matches(cto, m2) is False
