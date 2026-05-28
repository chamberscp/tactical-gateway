# ADR-0011: OVL Symbology and Ingest Approach

* Status: Accepted
* Date: 2026-05-28
* Phase: 2b-1 (OVL ingest, OVL -> CTO)
* Supersedes: none
* Related: ADR-0008 (KMZ ingest), ADR-0009 (operational store), ADR-0010
  (supersession and identity)

## Context

Phase 2b adds GCCS-J / Agile Server OVL support. 2b-1 covers ingest only
(OVL -> CTO); egress (CTO -> OVL) is deferred to 2b-2. We needed to decide the
authoritative OVL schema, how to map MIL-STD-2525 symbol codes, and how to
represent OVL graphics in the canonical tactical object (CTO) model.

Two reference implementations exist (both by the same author, studied as format
references only — no code copied):

* `mdudel/kml2xml` (MIT) — KML/KMZ -> GCCS-J OVL. Useful for *egress* (2b-2).
* `mdudel/ovlparser` — OVL -> KMZ. Useful for ingest traversal patterns.

Critically, the GCCS-J OVL that the `kml2xml` *binary* emits uses a
`<MilStdSymbol>` element form. The **real planner OVL files** we obtained use a
different, simpler on-disk schema: `<MODEL>` containing repeated `<milbobject>`
elements. The real files are the authoritative ingest target.

## Decision

### Authoritative schema

The ingest parser targets the real `<MODEL>/<milbobject>` schema, validated
against two real planner overlays (`tests/fixtures/6_2-115-10_1-10_20.ovl`,
`6_2-115-10_42-10_57.ovl`, 22 and 21 objects respectively). The `<MilStdSymbol>`
form is retained only as an egress cross-reference for 2b-2.

### SIDC mapping

`MIL_ID` carries a 15-character MIL-STD-2525 SIDC. We decode two things from it:

* **Affiliation** from character 2 (`F`/`H`/`N`/`U` -> friend/hostile/neutral/
  unknown), with the full 2525 standard-identity set mapped so uncommon
  identities are not silently bucketed as unknown.
* **Geometry class** (point / linear / area) from the function code
  (characters 5-10) via a curated lookup table in `libs/sidc/`. The table is a
  Python re-implementation seeded from `mil-sym-java` conventions and the
  working set of graphics defined in USMC B130836 — not all of 2525. Unknown
  codes degrade gracefully to vertex-count inference.

`mil-sym-java` (missioncommand) is cited as the authoritative reference for
SIDC -> symbol/geometry-class mapping.

### CTO representation

Each `milbobject` becomes a CTO with `object_class="graphic"`. Decisions:

* **D1 — Modifiers preserved verbatim.** All 2525 text amplifiers
  (`T, T1, N, W, W1, H, Q, Y`) and their paired `_VIS` flags are stored in
  `CTO.attributes.modifiers` as value+visibility pairs. `LABEL_POSITION`,
  `LINE_COLOR`, `FILL_COLOR`, `SIZE` are preserved when present. This makes a
  lossless round-trip possible for 2b-2 egress.
* **D2 — Affiliation from SIDC char-2** (above). The `N=ENY` text seen on
  hostile graphics is corroborating only, not the source of truth.
* **D3 — Geometry: drawn vertices are canonical; SIDC wins on symbol intent.**
  Canonical CTO geometry follows the actual drawn positions (1 vertex = Point,
  2 = LineString, 3+ = Polygon if closed/area-class else LineString); vertices
  are never dropped. The SIDC-implied class is recorded separately
  (`sidc_geometry_class`). When the two disagree, the SIDC governs *symbol
  interpretation* and we set `geometry_conflict=true` so egress and the future
  coverage table can see it. Observed example: `PY` (a point-class SIDC) drawn
  with multiple positions is kept as a LineString and flagged.

Coordinates are converted at the parser boundary from OVL `lat lon` (latitude
first) to CTO `[lon, lat]` (GeoJSON order).

### Pipeline reuse

OVL ingest reuses the Phase 1/2a plumbing unchanged: raw bytes captured to
MinIO with the SHA-256 hash chain, CTOs published on NATS
`cto.normalized.ovl`, and the opstore writes them (supersession-aware). The
folder watcher accepts `.ovl` alongside `.kmz`.

## Coverage

All 33 distinct function codes present in the two fixtures are covered by the
SIDC table (zero unknowns). The classification:

| Class  | Function codes |
| ------ | -------------- |
| area   | BCB, BDE, GAA, NB, NC, OAF, OAK, OEB, OFD, OGB, OGF, SLA |
| linear | BCL, GLB, GLC, OEF, OET, OLAA, OLAGM, OLAGS, OLAR, OLAV, OLC, OLI, OLKGM, OLL, OLT |
| point  | NDP, NEB, NEC, OFS, OMT, PY |

A formal coverage table (full-fidelity / best-effort / unsupported with B130836
citations) is a 2b-2 deliverable; this ADR records the ingest baseline.

## Consequences

* The parser is faithful to real planner output, not to a tool's idealized
  emission. If a future OVL variant uses the `<MilStdSymbol>` form, a second
  parser dispatch will be needed (small, isolated change).
* Lossless modifier preservation means CTO attribute payloads for graphics are
  larger than for tracks; acceptable at MEU scale.
* Affiliation is now sourced differently per object class (graphics: SIDC
  char-2; tracks: Threat/Flag/Category from the Agile schema, see
  `docs/ref-agile-track-schema.md`). The CTO `affiliation` attribute unifies
  them. This is carried into the OTH-Gold phase.
* Unknown SIDC function codes do not fail ingest; they fall back to vertex-count
  geometry and are marked `sidc_known=false` for later review.

## Revisit when

* A real OVL using the `<MilStdSymbol>` schema is encountered.
* SIDC coverage gaps appear against a broader operational sample.
* mil-sym-java is available on the target network for direct cross-checking.
