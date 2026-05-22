"""Writer: insert CTO rows, handle supersession on KMZ re-ingest.

Aligned with the Phase 0 flat schema:
- CTO fields like altitude_m, course_deg, sidc_2525d, raw_sha256 are
  top-level columns, not nested JSON.
- audit_log uses `target` (not `subject`), and `outcome` is NOT NULL.
- classification_enum uses single-letter codes ('U', 'C', 'S', 'TS', ...).

Supersession rule:
When a CTO arrives with ingest_source in {FOLDER, UPLOAD} and a
parent_kmz_filename, any existing CTOs from the SAME path (same
parent_kmz_source) and SAME filename that are still current
(valid_to IS NULL) get valid_to set to the new ingest's received_at.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from cto_schema import CTO

from common import get_logger

log = get_logger(__name__)


# Map our enum values to the classification_enum DB codes.
_CLASSIFICATION_DB = {
    "unclassified": "U",
    "cui": "CUI",
    "confidential": "C",
    "secret": "S",
    "top_secret": "TS",
}


def _classification_to_db(value: str) -> str:
    return _CLASSIFICATION_DB.get(value, "U")


def _geometry_to_wkt(cto: CTO) -> str:
    """Convert the CTO geometry to a WKT string for PostGIS insert."""
    g = cto.geometry
    if g.type == "Point":
        lon, lat = g.coordinates[0], g.coordinates[1]
        return f"SRID=4326;POINT({lon} {lat})"
    if g.type == "LineString":
        pts = ", ".join(f"{p[0]} {p[1]}" for p in g.coordinates)
        return f"SRID=4326;LINESTRING({pts})"
    if g.type == "Polygon":
        rings = []
        for ring in g.coordinates:
            ring_text = ", ".join(f"{p[0]} {p[1]}" for p in ring)
            rings.append(f"({ring_text})")
        return f"SRID=4326;POLYGON({', '.join(rings)})"
    raise ValueError(f"unsupported geometry type {g.type}")


def _cto_insert_sql_and_params(cto: CTO) -> tuple[str, dict]:
    """Build INSERT SQL and parameter dict for one CTO row."""
    attrs = cto.attributes or {}
    sym = cto.symbology
    alt = cto.altitude
    kin = cto.kinematics
    rp = cto.raw_pointer

    params = {
        "uid": str(cto.uid),
        "source_uid": cto.source_uid,
        "source_system": cto.source_system,
        "source_protocol": cto.source_protocol.value,
        "received_at": cto.received_at,
        "event_time": cto.event_time,
        "valid_from": cto.valid_from,
        "valid_to": cto.valid_to,
        "classification": _classification_to_db(cto.classification.value),
        "object_class": cto.object_class.value,
        "geom_wkt": _geometry_to_wkt(cto),
        # altitude (flattened)
        "altitude_m": alt.value_m if alt else None,
        "altitude_source": alt.source.value if alt else None,
        "altitude_accuracy_m": alt.accuracy_m if alt else None,
        # kinematics (flattened)
        "course_deg": kin.course_deg if kin else None,
        "speed_mps": kin.speed_mps if kin else None,
        "vertical_rate_mps": kin.vertical_rate_mps if kin else None,
        # symbology (flattened)
        "sidc_2525d": sym.sidc_2525c if sym else None,
        "cot_type": sym.cot_type if sym else None,
        "affiliation": sym.affiliation.value if (sym and sym.affiliation) else None,
        "battle_dimension": sym.battle_dimension.value if (sym and sym.battle_dimension) else None,
        # identity / display
        "callsign": cto.callsign,
        "label": cto.label,
        "remarks": cto.remarks,
        # escape hatch + provenance as JSON
        "attributes": _to_jsonb(attrs),
        "provenance": _to_jsonb([p.model_dump(mode="json") for p in (cto.provenance or [])]),
        # raw pointer (flat)
        "raw_sha256": rp.sha256 if rp else "",
        "raw_object_key": rp.object_key if rp else "",
        "raw_size_bytes": rp.size_bytes if rp else 0,
        "raw_captured_at": rp.captured_at if rp else cto.received_at,
        # Phase 2a additions
        "ingest_source": cto.ingest_source.value if cto.ingest_source else None,
        "parent_kmz_uri": attrs.get("parent_kmz_uri"),
        "parent_kmz_filename": attrs.get("parent_kmz_filename"),
        "parent_kmz_source": attrs.get("parent_kmz_source"),
    }

    sql = """
        INSERT INTO cto (
            uid, source_uid, source_system, source_protocol,
            received_at, event_time, valid_from, valid_to,
            classification, object_class, geom,
            altitude_m, altitude_source, altitude_accuracy_m,
            course_deg, speed_mps, vertical_rate_mps,
            sidc_2525d, cot_type, affiliation, battle_dimension,
            callsign, label, remarks,
            attributes, provenance,
            raw_sha256, raw_object_key, raw_size_bytes, raw_captured_at,
            ingest_source, parent_kmz_uri, parent_kmz_filename, parent_kmz_source
        ) VALUES (
            %(uid)s, %(source_uid)s, %(source_system)s, %(source_protocol)s,
            %(received_at)s, %(event_time)s, %(valid_from)s, %(valid_to)s,
            %(classification)s, %(object_class)s, ST_GeomFromEWKT(%(geom_wkt)s),
            %(altitude_m)s, %(altitude_source)s, %(altitude_accuracy_m)s,
            %(course_deg)s, %(speed_mps)s, %(vertical_rate_mps)s,
            %(sidc_2525d)s, %(cot_type)s, %(affiliation)s, %(battle_dimension)s,
            %(callsign)s, %(label)s, %(remarks)s,
            %(attributes)s::jsonb, %(provenance)s::jsonb,
            %(raw_sha256)s, %(raw_object_key)s, %(raw_size_bytes)s, %(raw_captured_at)s,
            %(ingest_source)s, %(parent_kmz_uri)s, %(parent_kmz_filename)s, %(parent_kmz_source)s
        )
        ON CONFLICT (uid) DO NOTHING
    """
    return sql, params


def _to_jsonb(value) -> str:
    """Serialize to a JSON string suitable for ::jsonb cast."""
    import json
    return json.dumps(value, default=str)


def supersede_prior(
    conn,
    *,
    parent_kmz_filename: str,
    parent_kmz_source: str,
    new_ingest_at: datetime,
    new_kmz_uri: str | None,
) -> int:
    """Mark current CTOs from the same path+filename as retired.

    Uses a raw psycopg connection (not SQLAlchemy session) so we can
    work directly with the Phase 0 schema without ORM mapping issues.
    Returns the number of rows affected.
    """
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE cto SET valid_to = %s
        WHERE parent_kmz_filename = %s
          AND parent_kmz_source = %s
          AND valid_to IS NULL
        """,
        (new_ingest_at, parent_kmz_filename, parent_kmz_source),
    )
    count = cur.rowcount or 0

    if count > 0:
        cur.execute(
            """
            INSERT INTO audit_log (
                at, actor, action, target, outcome,
                event_type, subject_uid, details
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s::jsonb
            )
            """,
            (
                new_ingest_at,
                "opstore.supersede",
                "kmz_supersede",
                parent_kmz_filename,
                "success",
                "supersession",
                None,
                _to_jsonb({
                    "parent_kmz_filename": parent_kmz_filename,
                    "parent_kmz_source": parent_kmz_source,
                    "superseded_count": count,
                    "new_kmz_uri": new_kmz_uri,
                }),
            ),
        )

    log.info("supersession applied",
             filename=parent_kmz_filename,
             source=parent_kmz_source,
             superseded=count)
    return count


def insert_ctos(conn, ctos: Iterable[CTO]) -> int:
    """Insert CTOs using raw SQL against the Phase 0 schema."""
    cur = conn.cursor()
    inserted = 0
    for cto in ctos:
        sql, params = _cto_insert_sql_and_params(cto)
        cur.execute(sql, params)
        if cur.rowcount:
            inserted += cur.rowcount
    log.info("ctos inserted", count=inserted)
    return inserted


def write_ctos_with_supersession(conn, ctos: list[CTO]) -> tuple[int, int]:
    """End-to-end write path for a batch of CTOs from the same source.

    If the batch has KMZ parent metadata, applies same-path supersession
    before inserting the new CTOs. Returns (inserted, superseded).
    """
    if not ctos:
        return 0, 0

    sample = ctos[0]
    attrs = sample.attributes or {}
    pkf = attrs.get("parent_kmz_filename")
    pks = attrs.get("parent_kmz_source")
    superseded = 0
    if pkf and pks:
        superseded = supersede_prior(
            conn,
            parent_kmz_filename=pkf,
            parent_kmz_source=pks,
            new_ingest_at=sample.received_at,
            new_kmz_uri=attrs.get("parent_kmz_uri"),
        )

    inserted = insert_ctos(conn, ctos)
    return inserted, superseded
