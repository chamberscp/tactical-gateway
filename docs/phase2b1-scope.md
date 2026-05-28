# Phase 2b-1 — OVL Ingest (OVL → CTO)

**Status:** Scoped, ready to build
**Predecessor:** Phase 2a (operational store + KMZ ingest, tag `phase2a`)
**Repo:** `chamberscp/tactical-gateway`

Phase 2b adds GCCS-J / Agile Server OVL support. It is split the same way
Phase 2 was: **2b-1 is ingest** (OVL → CTO), **2b-2 is egress** (CTO → OVL).
Ingest is first because parsing real planner OVLs validates the symbol mapping
against ground truth before we commit to emitting them.

---

## 1. Reference posture

| Source | Role | License posture |
| --- | --- | --- |
| Real planner OVL files (`6_2-115-10_1-10_20.ovl`, `6_2-115-10_42-10_57.ovl`) | **Authoritative ingest schema** | Operational samples, internal |
| `mdudel/kml2xml` (decompiled) | Egress reference for 2b-2 only — NOT the ingest target | MIT |
| `mdudel/ovlparser` | OVL→KMZ traversal reference | studied as reference |
| `mil-sym-java` (missioncommand) | SIDC → symbol/geometry-class mapping | reference |
| USMC B130836 | Doctrinal symbol working set | public reference |

All third-party code is studied as reference only; the implementation is a
clean Python re-write. No Java is copied.

> **Note:** the GCCS-J OVL the binary `kml2xml` tool emits (a `<MilStdSymbol>`
> form) is **not** the real on-disk schema. The real planner files use a
> `<MODEL>/<milbobject>` schema (below). The decompiled structure is retained
> only as an egress cross-reference for 2b-2.

---

## 2. Real OVL schema (ground truth)

```xml
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<MODEL>
  <milbobject>                          <!-- repeated, one per graphic -->
    <MIL_ID>GFGPGLB----D--X</MIL_ID>    <!-- 15-char MIL-STD-2525 SIDC; always present -->
    <NAME>10.15</NAME>                  <!-- feature label; always present -->
    <VISIBILITY>true</VISIBILITY>       <!-- always present -->
    <T>T MOD</T> <T_VIS>true</T_VIS>     <!-- 2525 text amplifiers (optional) -->
    <T1>T1 MOD</T1> <T1_VIS>true</T1_VIS>
    <N>ENY</N> <N_VIS>true</N_VIS>       <!-- N often "ENY" on hostile tracks -->
    <W>..</W> <W_VIS>..</W_VIS>
    <H>..</H> <H_VIS>..</H_VIS>
    <Q>..</Q> <Q_VIS>..</Q_VIS>
    <Y>..</Y> <Y_VIS>..</Y_VIS>
    <W1>..</W1> <W1_VIS>..</W1_VIS>
    <LABEL_POSITION>32.633 44.076</LABEL_POSITION>  <!-- optional -->
    <LINE_COLOR>..</LINE_COLOR>          <!-- optional (observed in file 2) -->
    <FILL_COLOR>..</FILL_COLOR>          <!-- optional (observed in file 2) -->
    <SIZE>..</SIZE>                      <!-- optional (observed in file 2) -->
    <POSITION>32.585233 43.972545</POSITION>  <!-- 1+; "lat lon", lat first -->
  </milbobject>
  <!-- ... more milbobjects ... -->
  <CREATE_TIME>1409165629</CREATE_TIME>
  <MODIFIED_TIME>1409165629</MODIFIED_TIME>
  <NAME>6.2 115 10.1-10.20</NAME>
</MODEL>
```

Observed in the two fixtures: 22 and 21 `milbobject`s respectively, a mix of
friendly and hostile affiliations, and real doctrinal SIDCs (GLB boundary,
GLC, GAA general area, OAF/OAK/OL* obstacle and tactical graphics, SLA, PY,
NB/NC/ND*/NE* nuclear-bio-chem, OE*/OF*/OG* obstacle effects, BC*/BD*, OMT).

---

## 3. Locked decisions

### D1 — Modifier fields: preserve all verbatim
Every modifier (`T, T1, N, W, H, Q, Y, W1, ...`) and its `_VIS` flag is stored
verbatim in `CTO.attributes.modifiers` as value + visibility pairs:

```json
"modifiers": {
  "T":  {"value": "T MOD", "vis": true},
  "N":  {"value": "ENY",   "vis": true}
}
```

`LABEL_POSITION`, `LINE_COLOR`, `FILL_COLOR`, `SIZE` are preserved when present.
Goal: lossless round-trip for 2b-2 egress.

### D2 — Affiliation from SIDC character 2
`SIDC[1]` → affiliation: `F`=friend, `H`=hostile, `N`=neutral, `U`=unknown.
Stored as `CTO.attributes.affiliation`. The `N=ENY` text is corroborating only,
not the source of truth.

### D3 — Geometry inference: both signals, SIDC wins on conflict
The real data shows SIDC class and vertex count do **not** always agree
(e.g. a `PY` point-class SIDC drawn with 4 positions). Therefore:

- `actual_geometry` from POSITION count: **1 = Point, 2 = LineString,
  3+ = Polygon** (if closed / area-class SIDC) else LineString.
- `sidc_geometry_class` from the SIDC function code via mil-sym-java mapping
  (point-graphic / linear / area).
- **Canonical CTO geometry = the actual drawn positions — vertices are never
  dropped.**
- On conflict, the SIDC governs *symbol intent*: store `sidc_geometry_class`
  and set `geometry_conflict: true` in attributes so egress and the coverage
  table can see it.

---

## 4. In scope (2b-1)

1. **OVL XML model** — Pydantic classes mirroring the `MODEL`/`milbobject`
   schema above, including optional modifier and style fields.
2. **OVL → CTO parser** — affiliation (D2), modifiers (D1), geometry rule (D3),
   coordinate parsing of decimal `lat lon` pairs.
3. **SIDC ↔ B130836 mapping table** — seeded from mil-sym-java; the SIDCs
   observed in the fixtures are the first real entries.
4. **Capture + publish** — `.ovl` bytes → MinIO with hash chain (reuse Phase 1
   capture), CTOs published on `cto.normalized.*`, opstore writes them.
5. **Folder watcher extension** — accept `.ovl` in the existing inbox
   alongside `.kmz`.
6. **ADR-0011** — OVL symbology approach, the recovered schema as reference,
   and the license posture above.
7. **Tests** — see acceptance criteria.

## 5. Out of scope (deferred to 2b-2)

- `CTO → OVL` emitter (MODEL/milbobject form)
- Route destination type `ovl_file`
- Symbol coverage table (full / best-effort / unsupported)
- Full KMZ → CTO → OVL → CTO → KMZ round-trip fidelity test
- JMS / JBI direct push to GCCS-J (file drop remains the interface)

---

## 6. Acceptance criteria

2b-1 is complete when all of the following hold:

- [ ] Dropping `6_2-115-10_1-10_20.ovl` in the inbox produces **22** graphic
      CTOs within ~5s; the second fixture produces **21**.
- [ ] Affiliation is parsed from SIDC char-2: the friendly/hostile mix in the
      fixtures is reflected in `CTO.attributes.affiliation`.
- [ ] All modifier fields and `_VIS` flags are preserved verbatim and a
      round-trip serialization reproduces them exactly.
- [ ] Geometry is inferred per D3; at least one `geometry_conflict: true` case
      (the `PY` 4-position graphic) is flagged while keeping the polygon.
- [ ] Coordinates parse correctly (decimal `lat lon`, latitude first).
- [ ] `.ovl` capture is added to the hash chain and `tools/verify_chain.py`
      still verifies cleanly.
- [ ] All Phase 1 and Phase 2a tests still pass; ≥15 new tests for 2b-1.
- [ ] ADR-0011 filed.

---

## 7. Related reference (not 2b scope)

The Agile Client **track** attribute schema (live tracks: aircraft/ships/units)
is banked separately at `docs/ref-agile-track-schema.md`. It is a different
object class (`track`, not `graphic`) and feeds Phase 1 CoT enrichment and the
new **OTH-Gold phase** (see roadmap). Two cross-cutting decisions it surfaced:

- `CTO.affiliation` derives from an object-class-appropriate source: graphics
  from SIDC char-2 (D2), tracks from `Threat`/`Flag`/`Category`.
- The coordinate normalizer must accept **both** OVL decimal `lat lon` and
  packed DMS (e.g. `334631N 0784849W`) from the track side.

---

## 8. Next steps

1. Acknowledge this scope.
2. Build 2b-1: OVL model, parser, SIDC table, capture/NATS wiring, watcher,
   ADR-0011, tests (~1,200–1,500 lines).
3. Apply, rebuild, run the smoke test (drop both fixtures, verify counts and
   affiliation).
4. Tag `phase2b-1`.
5. Scope 2b-2 (egress) against the kml2xml egress reference + this schema.
