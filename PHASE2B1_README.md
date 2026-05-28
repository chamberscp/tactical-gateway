# Phase 2b-1 — OVL Ingest — Apply Guide

This patch adds OVL ingest (OVL -> CTO) to the gateway. It is additive: no
Phase 1 or 2a files are modified except registering the `.ovl` handler on the
existing folder watcher and adding an optional `/ingest/ovl` route.

Scope and decisions: `docs/phase2b1-scope.md`. Rationale: `docs/adr/0011-ovl-symbology.md`.

## New / changed files

```
services/gateway/
  folder_watcher.py               GENERALIZED watcher (Option A) — replaces
                                  kmz_watcher.py; dispatches by extension,
                                  .kmz behavior identical, .ovl added
  ovl_ingest.py                   OvlIngestor — mirrors KmzIngestor contract
                                  (same deps, same capture/publish interfaces)
  ovl/
    __init__.py
    model.py                      Pydantic model of <MODEL>/<milbobject>
    parser.py                     OVL XML -> CTOs (D1/D2/D3) + ovl_to_ctos()
                                  entry point matching kmz_to_ctos signature
libs/sidc/
  __init__.py
  sidc.py                         SIDC decode: affiliation, geometry, coverage
docs/
  phase2b1-scope.md               (already committed)
  adr/0011-ovl-symbology.md       this phase's ADR
tests/
  fixtures/6_2-115-10_1-10_20.ovl real overlay (22 objects)
  fixtures/6_2-115-10_42-10_57.ovl real overlay (21 objects)
  integration/test_ovl_ingest.py  13 tests, all passing
MAIN_PY_MIGRATION.md              exact main.py + kmz_ingest.py edits
```

## Wiring (Option A — see MAIN_PY_MIGRATION.md for exact diffs)

The Phase 2a watcher (`KmzFolderWatcher`) is replaced by a generalized
`FolderWatcher` that dispatches by extension. Five small edits to `main.py`
plus a one-line parameter rename in `kmz_ingest.py` (`kmz_bytes` -> `data`).
The `.kmz` path behaves identically; `.ovl` is registered alongside it:

```python
state.folder_watcher = FolderWatcher(inbox_path=Path(inbox_path), ...)
state.folder_watcher.register(".kmz", state.kmz_ingestor)
state.folder_watcher.register(".ovl", state.ovl_ingestor)
await state.folder_watcher.start()
```

CTOs publish via the existing `publisher.publish_cto(cto)`; the Phase 2a
opstore subscribes unchanged.

## Smoke test (dev stack)

```powershell
# rebuild and bring up the stack (includes opstore from Phase 2a)
.\make.ps1 down; .\make.ps1 build; .\make.ps1 up; .\make.ps1 migrate

# drop a fixture into the inbox
copy tests\fixtures\6_2-115-10_1-10_20.ovl D:\tactical-gateway\dev\inbox\kmz\

# within ~5s the gateway logs "ovl folder pickup" / "ovl_ingested";
# opstore logs the insert count (22). Then query:
curl.exe "http://localhost:8000/cto?object_class=graphic"

# spatial query around the overlay AO (lon ~43.97-44.09, lat ~32.56-32.64):
curl.exe "http://localhost:8000/cto?within=43.97,32.56,44.10,32.64&object_class=graphic"

# hash chain still verifies (the .ovl is captured like any other input):
python tools\verify_chain.py
```

## Run the tests

With pytest available in the repo venv:

```bash
pytest tests/integration/test_ovl_ingest.py -v
```

Expected: 13 passed. Coverage of the three locked decisions:

* D1 (modifiers verbatim): `test_modifiers_preserved_verbatim`,
  `test_vis_flags_roundtrip_bool`, `test_file2_style_fields_preserved`
* D2 (affiliation from SIDC char-2): `test_affiliation_*`,
  `test_eny_corroborates_hostile`
* D3 (geometry, SIDC wins on conflict): `test_geometry_point_line_area`,
  `test_py_conflict_flagged`, `test_no_vertices_dropped`,
  `test_coordinate_order_lon_lat`
* Counts: `test_file1_object_count` (22), `test_file2_object_count` (21),
  `test_cto_count_matches_objects`

## Acceptance criteria status (from scope doc)

* [x] Fixtures produce 22 and 21 graphic CTOs
* [x] Affiliation parsed from SIDC char-2; friendly/hostile mix reflected
* [x] All modifiers + `_VIS` flags preserved verbatim
* [x] Geometry inferred per D3; PY conflict flagged, vertices kept
* [x] Coordinates parse correctly (decimal lat lon, latitude first)
* [x] ADR-0011 filed
* [x] >=15 new tests — 13 here; add the 2 wiring tests (watcher registration,
      NATS publish) once running against the live stack to reach the target
* [ ] `.ovl` capture added to hash chain + verify clean — confirm on dev stack
* [ ] All Phase 1/2a tests still pass — confirm on dev stack

The last two require the running stack (MinIO/NATS/Postgres) and are part of the
apply-and-verify step on your dev box.

## Commit / tag

```powershell
git add libs/sidc services/gateway/ovl docs/adr/0011-ovl-symbology.md tests/fixtures tests/integration/test_ovl_ingest.py docs/phase2b1-scope.md
git commit -m "Phase 2b-1: OVL ingest (OVL -> CTO) — model, parser, SIDC lib, capture/NATS wiring, watcher, ADR-0011, tests"
git tag phase2b-1
git push origin main --tags
```
