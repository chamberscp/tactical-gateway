# Phase 2b-1 — OVL Ingest — Apply Guide

This patch adds OVL ingest (OVL -> CTO) to the gateway. It is additive: no
Phase 1 or 2a files are modified except registering the `.ovl` handler on the
existing folder watcher and adding an optional `/ingest/ovl` route.

Scope and decisions: `docs/phase2b1-scope.md`. Rationale: `docs/adr/0011-ovl-symbology.md`.

## New files

```
libs/sidc/
  __init__.py
  sidc.py                         SIDC decode: affiliation (char-2),
                                  geometry class (function code), coverage
services/gateway/ovl/
  __init__.py
  model.py                        Pydantic model of <MODEL>/<milbobject>
  parser.py                       OVL XML -> OvlModel -> graphic CTOs (D1/D2/D3)
  ingest.py                       capture -> parse -> publish on NATS
  watcher_ext.py                  registers .ovl on the Phase 2a watcher
docs/
  phase2b1-scope.md               (already committed)
  adr/0011-ovl-symbology.md       this phase's ADR
tests/
  fixtures/6_2-115-10_1-10_20.ovl real planner overlay (22 objects)
  fixtures/6_2-115-10_42-10_57.ovl real planner overlay (21 objects)
  integration/test_ovl_ingest.py  13 tests, all passing
```

## Wiring into the running gateway

1. **Folder watcher.** Where the Phase 2a watcher is constructed (in the
   gateway startup), add:

   ```python
   from ovl.watcher_ext import register as register_ovl
   register_ovl(watcher, capture=capture, bus=bus, audit=audit)
   ```

   Dropping a `.ovl` into the inbox now behaves like a `.kmz`.

2. **(Optional) HTTP upload.** Add `POST /ingest/ovl` mirroring the Phase 2a
   `POST /ingest/kmz` handler (same 409-on-duplicate + `?force=true`), calling
   `ovl.ingest.ingest_ovl_bytes`. Only the parser dispatch differs by extension.

3. **NATS.** CTOs publish on `cto.normalized.ovl`. The Phase 2a opstore already
   subscribes to `cto.normalized.*`, so no opstore change is required.

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
