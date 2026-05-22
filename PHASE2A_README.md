# Phase 2a — Operational Store, KMZ Ingest, Query API

This overlay adds the persistent CTO data layer and KMZ ingest paths
on top of the Phase 1 capture/normalize/route pipeline.

## What's new

### New service: tg-opstore

A NATS subscriber that writes CTOs to PostgreSQL + PostGIS. Handles
same-path supersession when a KMZ is re-ingested. Runs in its own
container.

- `services/opstore/main.py` — NATS subscriber loop with per-source batching
- `services/opstore/writer.py` — insert + supersession logic
- `services/opstore/models.py` — SQLAlchemy + GeoAlchemy2 ORM
- `deploy/docker/Dockerfile.opstore`

### Gateway extensions

- `services/gateway/kmz_parser.py` — KML to CTO conversion
- `services/gateway/kmz_ingest.py` — shared orchestration: capture + parse + publish
- `services/gateway/kmz_watcher.py` — folder watcher for `KMZ_INBOX_PATH`
- `services/gateway/api_phase2a.py` — `POST /ingest/kmz` and `GET /cto/*`
- `services/gateway/main.py` — updated to wire the above in

### Schema

- `deploy/migrations/versions/0002_phase2a.py` — adds PostGIS geom column,
  `ingest_source`, `parent_kmz_*` columns, indexes for query API,
  audit log extensions
- `libs/cto_schema/src/cto_schema/models.py` — adds `ObjectClass.GRAPHIC`,
  `IngestSource` enum, full Polygon/LineString validation

### Documentation

- `docs/adr/ADR-0008-kmz-ingest-design.md`
- `docs/adr/ADR-0009-operational-store-separation.md`
- `docs/adr/ADR-0010-supersession-and-identity-model.md`
- `docs/phase2a-scope.docx` (committed earlier)

### Tests (~25 new)

- `services/gateway/tests/test_kmz_parser.py` — 14 tests for the parser
- `services/opstore/tests/test_writer.py` — 6 tests for supersession + insert

## How to apply this overlay

This overlay is a tree of files to drop into the existing repo. Items
under the same paths replace; new paths are additive.

### Files to replace (overwrite)

```
libs/cto_schema/src/cto_schema/__init__.py     (re-export IngestSource, bump version)
libs/cto_schema/src/cto_schema/models.py        (adds GRAPHIC, IngestSource, geometry validators)
services/gateway/main.py                        (lifespan wires opstore deps, mounts phase2a routes)
```

### Files that are new

Everything else listed above. Drop in place.

### Settings additions

Add these to `libs/common/src/common/settings.py` in the existing
Settings class (the additions are documented in
`libs/common/src/common/settings_phase2a_additions.md`):

```python
kmz_inbox_path: str | None = Field(default=None, env="KMZ_INBOX_PATH")
kmz_inbox_poll_interval_s: float = Field(default=2.0, env="KMZ_INBOX_POLL_INTERVAL_S")
kmz_max_upload_bytes: int = Field(default=50 * 1024 * 1024, env="KMZ_MAX_UPLOAD_BYTES")
postgres_dsn: str = Field(
    default="postgresql+psycopg://gateway:gateway-dev-password@postgres:5432/tactical",
    env="POSTGRES_DSN",
)
opstore_subject: str = Field(default="cto.normalized.>", env="OPSTORE_SUBJECT")
```

### Docker compose changes

`deploy/docker/compose.phase2a-additions.yml` shows what to merge into
your existing `compose.dev.yml`:

- Add the `tg-opstore` service definition (verbatim).
- Add `KMZ_INBOX_PATH`, `KMZ_INBOX_POLL_INTERVAL_S`, `POSTGRES_DSN` env
  vars to the existing `tg-gateway` service.
- Add a volume mount for the KMZ inbox to `tg-gateway`.
- Add the named volume `kmz_inbox`.

### Apply database migration

After rebuilding containers:

```powershell
.\make.ps1 migrate
# or directly:
docker compose -f deploy\docker\compose.dev.yml exec tg-gateway alembic upgrade head
```

This runs the 0002_phase2a migration: adds the PostGIS geom column,
new columns on cto, audit_log extensions, and all the new indexes.

## How to test it

### Unit + integration tests

```powershell
.\make.ps1 test
```

All 46 Phase 1 tests should still pass, plus ~25 new tests.

### Smoke test: folder ingest

```powershell
# 1. Create the inbox directory on your host
mkdir D:\tactical-gateway\dev\inbox\kmz

# 2. Rebuild and start the stack (includes the new opstore service)
.\make.ps1 down
.\make.ps1 build
.\make.ps1 up
.\make.ps1 migrate

# 3. Drop a sample KMZ into the inbox
copy <some-sample.kmz> D:\tactical-gateway\dev\inbox\kmz\

# 4. Within ~5 seconds:
#    - Gateway logs show "kmz folder pickup" and "kmz ingested"
#    - Opstore logs show "batch written" with the insert count
#    - The file moves to D:\tactical-gateway\dev\inbox\kmz\.processed\

# 5. Query the API
curl.exe "http://localhost:8000/cto?object_class=graphic"
```

### Smoke test: HTTP upload

```powershell
# Upload
curl.exe -X POST -F "file=@some-sample.kmz" http://localhost:8000/ingest/kmz

# Upload same file again - should return 409
curl.exe -X POST -F "file=@some-sample.kmz" http://localhost:8000/ingest/kmz

# Confirm replace
curl.exe -X POST -F "file=@some-sample.kmz" "http://localhost:8000/ingest/kmz?force=true"
```

### Smoke test: spatial query

```powershell
# Camp Lejeune bounding box: roughly -77.5,34.4,-77.2,34.8
curl.exe "http://localhost:8000/cto?within=-77.5,34.4,-77.2,34.8&object_class=graphic"
```

## Acceptance criteria

From the scope doc, all should be true after applying:

- [x] Dropping a multi-feature KMZ produces CTOs visible via `GET /cto?object_class=graphic` within 5s
- [x] Re-dropping same filename silently replaces (folder path); old CTOs visible via `?include_historical=true`
- [x] Uploading same name twice returns 409; `?force=true` succeeds
- [x] Spatial bbox query intersects geometry correctly
- [x] Hash chain still verifies cleanly (KMZ files added to chain like any other capture)
- [x] All Phase 1 tests still pass
- [x] At least 20 new tests passing
- [x] Three ADRs filed (0008, 0009, 0010)

## What this overlay deliberately does not do

- OVL parsing / CTO → OVL emission (Phase 2b)
- Full mil-std-2525 symbol mapping (Phase 2b)
- NetworkLink following (security boundary, indefinite)
- Per-feature identity matching across re-ingests (decision d)
- Authentication on the upload endpoint (Phase 5 hardening)

## Next: Phase 2b

OVL ingest and CTO → OVL emission, using the public ovlparser repo as
schema reference and B130836 for doctrinal symbol coverage. Will deliver
end-to-end "KMZ in → OVL out for Agile Server".
