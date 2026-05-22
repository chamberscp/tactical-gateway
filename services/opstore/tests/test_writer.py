"""Tests for the opstore writer including supersession logic.

These tests use an in-memory SQLite backend with the Geoalchemy2 fallback
for the geom column. PostGIS-specific spatial functions are NOT exercised
here - those run in integration tests against a real Postgres+PostGIS.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from cto_schema import (
    CTO,
    Classification,
    Geometry,
    IngestSource,
    ObjectClass,
    SourceProtocol,
    Symbology,
    uuid7,
)
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from services.opstore.models import AuditLogRow, Base, CtoRow
from services.opstore.writer import (
    insert_ctos,
    supersede_prior,
    write_ctos_with_supersession,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine():
    """In-memory SQLite engine for unit testing.

    The geom column is declared GEOMETRY(GEOMETRY, 4326) which SQLite
    treats as a generic column - we store WKT strings there and just
    don't exercise spatial functions. That's fine for the supersession
    tests; spatial integration testing happens against real Postgres.
    """
    eng = create_engine("sqlite:///:memory:", future=True)
    # Geoalchemy2 needs spatialite for indexes, but for the columns alone
    # we can sidestep by creating tables explicitly without spatial index.
    with eng.begin() as conn:
        conn.execute(text("""
            CREATE TABLE cto (
                uid TEXT PRIMARY KEY,
                source_uid TEXT,
                source_system TEXT NOT NULL,
                source_protocol TEXT NOT NULL,
                ingest_source TEXT,
                received_at TEXT NOT NULL,
                event_time TEXT NOT NULL,
                valid_from TEXT,
                valid_to TEXT,
                classification TEXT NOT NULL,
                object_class TEXT NOT NULL,
                geom TEXT,
                geometry_json TEXT,
                symbology TEXT,
                altitude TEXT,
                kinematics TEXT,
                callsign TEXT,
                label TEXT,
                remarks TEXT,
                attributes TEXT NOT NULL DEFAULT '{}',
                parent_kmz_uri TEXT,
                parent_kmz_filename TEXT,
                parent_kmz_source TEXT,
                raw_pointer TEXT,
                provenance TEXT NOT NULL DEFAULT '[]'
            )
        """))
        conn.execute(text("""
            CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                at TEXT NOT NULL,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                subject TEXT,
                subject_uid TEXT,
                event_type TEXT,
                details TEXT
            )
        """))
    return eng


def make_kmz_cto(
    *,
    filename: str = "phase_iii.kmz",
    ingest_source: IngestSource = IngestSource.FOLDER,
    received_at: datetime | None = None,
    label: str = "PL ALPHA",
    feature_index: int = 0,
) -> CTO:
    received_at = received_at or datetime.now(timezone.utc)
    return CTO(
        uid=uuid7(),
        source_system=f"kmz-{ingest_source.value}:/test",
        source_protocol=SourceProtocol.KMZ,
        ingest_source=ingest_source,
        received_at=received_at,
        event_time=received_at,
        classification=Classification.UNCLASS,
        object_class=ObjectClass.GRAPHIC,
        geometry=Geometry(type="LineString", coordinates=[[-77.4, 34.5], [-77.3, 34.5]]),
        symbology=Symbology(),
        label=label,
        attributes={
            "kmz_feature_index": feature_index,
            "parent_kmz_filename": filename,
            "parent_kmz_source": ingest_source.value,
            "parent_kmz_uri": f"raw/kmz/aa/{filename}",
        },
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_insert_ctos_writes_rows(engine):
    ctos = [make_kmz_cto(feature_index=i, label=f"PL {chr(65+i)}") for i in range(3)]
    with Session(engine) as session:
        inserted = insert_ctos(session, ctos)
        session.commit()
    assert inserted == 3
    with Session(engine) as session:
        count = session.execute(text("SELECT count(*) FROM cto")).scalar_one()
    assert count == 3


def test_insert_ctos_idempotent_on_uid(engine):
    cto = make_kmz_cto()
    with Session(engine) as session:
        insert_ctos(session, [cto])
        session.commit()
    # Second insert of same uid should be a no-op
    with Session(engine) as session:
        inserted = insert_ctos(session, [cto])
        session.commit()
    assert inserted == 0
    with Session(engine) as session:
        count = session.execute(text("SELECT count(*) FROM cto")).scalar_one()
    assert count == 1


def test_supersession_within_same_path(engine):
    """Re-ingest of same filename + source: prior CTOs get valid_to set."""
    t1 = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 5, 22, 13, 0, 0, tzinfo=timezone.utc)

    # v1 ingest
    v1_ctos = [
        make_kmz_cto(received_at=t1, feature_index=i, label=f"PL {chr(65+i)}")
        for i in range(3)
    ]
    with Session(engine) as session:
        ins, sup = write_ctos_with_supersession(session, v1_ctos)
        session.commit()
    assert ins == 3
    assert sup == 0

    # v2 ingest of same filename + source
    v2_ctos = [
        make_kmz_cto(received_at=t2, feature_index=i, label=f"PL {chr(65+i)} v2")
        for i in range(3)
    ]
    with Session(engine) as session:
        ins, sup = write_ctos_with_supersession(session, v2_ctos)
        session.commit()
    assert ins == 3
    assert sup == 3

    # The v1 CTOs should have valid_to set; v2 should have valid_to NULL
    with Session(engine) as session:
        current = session.execute(
            text("SELECT count(*) FROM cto WHERE valid_to IS NULL")
        ).scalar_one()
        historical = session.execute(
            text("SELECT count(*) FROM cto WHERE valid_to IS NOT NULL")
        ).scalar_one()
    assert current == 3
    assert historical == 3


def test_no_cross_path_supersession(engine):
    """Folder watch and upload of same filename do NOT supersede each other."""
    t1 = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 5, 22, 13, 0, 0, tzinfo=timezone.utc)

    # v1 via folder watch
    v1_ctos = [
        make_kmz_cto(
            received_at=t1, feature_index=i,
            ingest_source=IngestSource.FOLDER,
        )
        for i in range(2)
    ]
    with Session(engine) as session:
        write_ctos_with_supersession(session, v1_ctos)
        session.commit()

    # v2 via upload, same filename - should NOT supersede the folder CTOs
    v2_ctos = [
        make_kmz_cto(
            received_at=t2, feature_index=i,
            ingest_source=IngestSource.UPLOAD,
        )
        for i in range(2)
    ]
    with Session(engine) as session:
        ins, sup = write_ctos_with_supersession(session, v2_ctos)
        session.commit()
    assert ins == 2
    assert sup == 0  # no cross-path supersession

    # All 4 should still be "current" (valid_to IS NULL), 2 from each path
    with Session(engine) as session:
        current = session.execute(
            text("SELECT count(*) FROM cto WHERE valid_to IS NULL")
        ).scalar_one()
        folder_count = session.execute(text(
            "SELECT count(*) FROM cto WHERE parent_kmz_source = 'folder' AND valid_to IS NULL"
        )).scalar_one()
        upload_count = session.execute(text(
            "SELECT count(*) FROM cto WHERE parent_kmz_source = 'upload' AND valid_to IS NULL"
        )).scalar_one()
    assert current == 4
    assert folder_count == 2
    assert upload_count == 2


def test_supersession_writes_audit_log(engine):
    """Each supersession event produces an audit_log row."""
    t1 = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 5, 22, 13, 0, 0, tzinfo=timezone.utc)

    with Session(engine) as session:
        write_ctos_with_supersession(session, [make_kmz_cto(received_at=t1)])
        session.commit()
    with Session(engine) as session:
        write_ctos_with_supersession(session, [make_kmz_cto(received_at=t2)])
        session.commit()
    with Session(engine) as session:
        rows = session.execute(
            text("SELECT actor, action, subject, event_type FROM audit_log")
        ).all()
    assert len(rows) == 1
    actor, action, subject, event_type = rows[0]
    assert action == "kmz_supersede"
    assert subject == "phase_iii.kmz"
    assert event_type == "supersession"


def test_no_supersession_for_non_kmz_cto(engine):
    """A CTO without parent_kmz_filename triggers no supersession lookup."""
    cto = CTO(
        uid=uuid7(),
        source_system="cot-xml-tcp:127.0.0.1:1234",
        source_protocol=SourceProtocol.COT_XML,
        ingest_source=IngestSource.STREAM,
        received_at=datetime.now(timezone.utc),
        event_time=datetime.now(timezone.utc),
        object_class=ObjectClass.TRACK,
        geometry=Geometry(type="Point", coordinates=[-77.4, 34.5]),
    )
    with Session(engine) as session:
        ins, sup = write_ctos_with_supersession(session, [cto])
        session.commit()
    assert ins == 1
    assert sup == 0
