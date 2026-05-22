"""HTTP routes for Phase 2a: KMZ upload, query API.

Uses raw psycopg connections to read against the Phase 0 flat schema.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from cto_schema import IngestSource
from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from common import get_logger

from .kmz_ingest import KmzIngestor

log = get_logger(__name__)


# Reverse map: classification_enum DB code -> our enum value
_CLASSIFICATION_FROM_DB = {
    "U": "unclassified",
    "CUI": "cui",
    "C": "confidential",
    "S": "secret",
    "TS": "top_secret",
}


def build_routes(
    *,
    kmz_ingestor: KmzIngestor,
    db_dsn: str,
) -> APIRouter:
    """Construct the Phase 2a API router with dependencies wired in.

    db_dsn is a raw psycopg DSN (without the +psycopg SQLAlchemy suffix).
    """
    router = APIRouter()

    def _connect():
        return psycopg.connect(db_dsn)

    # ----- KMZ upload -----

    @router.post("/ingest/kmz")
    async def ingest_kmz(
        request: Request,
        file: UploadFile = File(...),
        force: bool = Query(
            False, description="Override filename-collision protection"
        ),
    ) -> JSONResponse:
        filename = file.filename or "uploaded.kmz"
        if not filename.lower().endswith(".kmz"):
            raise HTTPException(400, "filename must end with .kmz")

        if not force:
            with _connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT count(*), min(received_at), max(parent_kmz_uri)
                    FROM cto
                    WHERE parent_kmz_filename = %s
                      AND parent_kmz_source = %s
                      AND valid_to IS NULL
                    """,
                    (filename, IngestSource.UPLOAD.value),
                )
                row = cur.fetchone()
                count, earliest, uri = row
                if count and count > 0:
                    return JSONResponse(
                        status_code=409,
                        content={
                            "error": "filename_exists",
                            "message": f"A KMZ named {filename!r} was previously uploaded.",
                            "existing": {
                                "filename": filename,
                                "feature_count": int(count),
                                "ingested_at": earliest.isoformat() if earliest else None,
                                "object_uri": uri,
                            },
                            "action": "POST /ingest/kmz?force=true with the new file to replace",
                        },
                    )

        kmz_bytes = await file.read()
        client_host = request.client.host if request.client else "unknown"
        source_label = f"kmz-upload:{client_host}"

        result = await kmz_ingestor.ingest(
            kmz_bytes=kmz_bytes,
            filename=filename,
            ingest_source=IngestSource.UPLOAD,
            source_label=source_label,
        )

        if not result.ok:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "ingest_failed",
                    "message": result.error,
                    "filename": filename,
                    "sha256": result.sha256,
                },
            )

        return JSONResponse(
            status_code=201,
            content={
                "filename": filename,
                "sha256": result.sha256,
                "object_uri": result.object_key,
                "features_extracted": result.features_extracted,
                "replaced": force,
            },
        )

    # ----- Query API -----

    @router.get("/cto/{uid}")
    async def get_cto(uid: UUID) -> dict[str, Any]:
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute(_select_columns_sql() + " WHERE uid = %s", (str(uid),))
            row = cur.fetchone()
            if row is None:
                raise HTTPException(404, "not found")
            return _row_to_dict(row, cur.description)

    @router.get("/cto")
    async def list_ctos(
        within: str | None = Query(
            None, description="Bounding box as 'minLon,minLat,maxLon,maxLat'"
        ),
        time_from: datetime | None = Query(None, description="Event time >="),
        time_to: datetime | None = Query(None, description="Event time <="),
        source_system: str | None = Query(
            None, description="Source system substring match (case-insensitive)"
        ),
        object_class: str | None = Query(
            None, description="Filter by object_class"
        ),
        include_historical: bool = Query(
            False, description="If true, include CTOs with valid_to in the past"
        ),
        limit: int = Query(100, ge=1, le=1000),
        cursor: str | None = Query(
            None, description="Opaque pagination cursor from prior response"
        ),
    ) -> dict[str, Any]:
        conds: list[str] = []
        params: list[Any] = []

        if not include_historical:
            conds.append("valid_to IS NULL")
        if time_from is not None:
            conds.append("event_time >= %s")
            params.append(time_from)
        if time_to is not None:
            conds.append("event_time <= %s")
            params.append(time_to)
        if source_system:
            conds.append("source_system ILIKE %s")
            params.append(f"%{source_system}%")
        if object_class:
            conds.append("object_class = %s")
            params.append(object_class)
        if within:
            try:
                parts = [float(x) for x in within.split(",")]
                if len(parts) != 4:
                    raise ValueError("expected 4 values")
                min_lon, min_lat, max_lon, max_lat = parts
            except ValueError:
                raise HTTPException(
                    400, "within must be 'minLon,minLat,maxLon,maxLat'"
                )
            conds.append(
                "ST_Intersects(geom, ST_MakeEnvelope(%s, %s, %s, %s, 4326))"
            )
            params.extend([min_lon, min_lat, max_lon, max_lat])

        if cursor:
            try:
                ts_str, uid_str = cursor.split("|", 1)
                cursor_ts = datetime.fromisoformat(ts_str)
                cursor_uid = UUID(uid_str)
                conds.append(
                    "(event_time < %s OR (event_time = %s AND uid < %s))"
                )
                params.extend([cursor_ts, cursor_ts, str(cursor_uid)])
            except (ValueError, TypeError):
                raise HTTPException(400, "invalid cursor")

        where = " WHERE " + " AND ".join(conds) if conds else ""
        sql = (
            _select_columns_sql()
            + where
            + " ORDER BY event_time DESC, uid DESC LIMIT %s"
        )
        params.append(limit + 1)

        with _connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
            desc = cur.description

        has_more = len(rows) > limit
        items_rows = rows[:limit]
        items = [_row_to_dict(r, desc) for r in items_rows]

        next_cursor = None
        if has_more and items_rows:
            last = items_rows[-1]
            # event_time is the 5th column in our select; uid is the 1st
            event_time = items[-1]["event_time"]
            uid = items[-1]["uid"]
            next_cursor = f"{event_time}|{uid}"

        return {
            "items": items,
            "count": len(items),
            "next_cursor": next_cursor,
        }

    return router


def _select_columns_sql() -> str:
    """Standard column list for CTO read queries. Includes geom as GeoJSON
    so the response is self-describing without a separate geometry_json
    column."""
    return """
        SELECT
            uid,
            source_uid,
            source_system,
            source_protocol,
            received_at,
            event_time,
            valid_from,
            valid_to,
            classification,
            object_class,
            ST_AsGeoJSON(geom) AS geom_geojson,
            altitude_m,
            altitude_source,
            altitude_accuracy_m,
            course_deg,
            speed_mps,
            vertical_rate_mps,
            sidc_2525d,
            cot_type,
            affiliation,
            battle_dimension,
            callsign,
            label,
            remarks,
            attributes,
            provenance,
            raw_sha256,
            raw_object_key,
            raw_size_bytes,
            raw_captured_at,
            ingest_source,
            parent_kmz_uri,
            parent_kmz_filename,
            parent_kmz_source
        FROM cto
    """


def _row_to_dict(row: tuple, description: list) -> dict[str, Any]:
    """Convert a psycopg row + cursor.description into a JSON-friendly dict."""
    out: dict[str, Any] = {}
    for col, value in zip(description, row):
        name = col.name
        if value is None:
            out[name] = None
        elif name == "uid":
            out[name] = str(value)
        elif name == "geom_geojson":
            out["geometry"] = json.loads(value) if value else None
        elif name == "classification":
            out[name] = _CLASSIFICATION_FROM_DB.get(value, value)
        elif isinstance(value, datetime):
            out[name] = value.isoformat()
        else:
            out[name] = value
    return out
