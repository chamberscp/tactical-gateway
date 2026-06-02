# ADR-0012: Overlay Writers, Fidelity Reporting, and KMZ Name Recognition

* Status: Accepted
* Date: 2026-06-02
* Phase: 2b-2 (overlay converter; CTO -> OVL, CTO -> KMZ)
* Supersedes: none
* Related: ADR-0008 (KMZ ingest), ADR-0011 (OVL symbology and ingest)

## Context

Phase 2b-2 builds the inverse direction of the overlay pipeline: writers
that emit valid OVL and KMZ from CTOs, plus a small converter web page so
non-technical operators can convert one format to the other on demand.
This closes two gaps deferred from earlier phases:

* `CTO -> OVL`, originally part of Phase 2's bidirectional scope; deferred
  when Phase 2a was carved out to ship ingest faster.
* `CTO -> KMZ`, same provenance.

Three asymmetries shape the design and force decisions up front:

1. **Format capacity is asymmetric.** OVL carries MIL-STD-2525 SIDC,
   modifiers, affiliation, and overlay metadata; KMZ carries geometry,
   labels, and free-text descriptions. Every OVL -> KMZ conversion
   loses representational content by format design; every KMZ -> OVL
   conversion has to *synthesize* a SIDC that the source did not carry.
2. **KMZ name semantics are richer than the Phase 2a recognizer captures.**
   2a recognizes a short prefix list (`PL`, `NAI`, `TAI`, `OBJ`, `NFA`,
   `RFA`, `ROZ`, `FSCL`, `CFL`, `BNDRY`, `EA`, `AA`, `BP`). Real planner
   KMZs carry far more doctrinal patterns than that, plus operator
   conventions (target designators, "Suspected " prefixes, control-point
   wording) that should produce structured CTOs rather than be discovered
   later inside a writer.
3. **The converter is an operator surface, not just a developer API.** A
   human drops a file and expects to receive a file. Failures need to be
   visible, fidelity loss needs to be visible, and the workflow needs to
   work on the first try without documentation.

## Decision

Four locked decisions, one per concern.

### D1 - KMZ ingest is widened; KMZ -> OVL never silently drops

**Where the smarts live.** Label-interpretation logic lives in the **KMZ
ingest**, not in the writer. Every downstream consumer (the OVL writer,
the route engine, future writers, the Phase 3 GUI, Phase 4 RAG) sees the
structured result. The writer reads CTO fields it can trust; it does not
re-derive semantics from labels.

**Recognition layers, applied in order.**

1. **Extended doctrinal prefix table.** Phase 2a's list plus the following
   additions, all locked by this ADR and tracked in
   `services/gateway/kmz/prefix_table.py`:

   `LOA` (limit of advance), `LD` / `LDLC` (line of departure /
   line of departure-line of contact), `FLOT` / `FEBA` (forward line of
   own troops / forward edge of battle area), `SP` / `RP` (start point /
   release point), `CP` / `CCP` (contact point / casualty collection
   point), `HA` (holding area), `SBF` (support by fire), `ATK` (attack
   position), `PZ` / `LZ` / `DZ` (pickup / landing / drop zone), `MSR` /
   `ASR` (main / alternate supply route), `TRP` (target reference point),
   `AO` (area of operations), `RFL` / `NFL` (restrictive fire line /
   no-fire line).

   Each prefix maps to a SIDC family in `libs/sidc/`. Where the doctrinal
   meaning admits multiple SIDC variants (e.g. `PL` phase line spans
   several function codes), the writer emits the most general variant
   and the fidelity report marks the object **best-effort**.

2. **Target designator pattern.** Labels matching the regex
   `^[A-Z]{1,2}\d{3,4}\b` (e.g. `AB1001`, `T101`) are recognized as
   target reference points and assigned the target SIDC. The operator
   label is preserved verbatim.

3. **Word-based recognition.** Labels containing any of the case-
   insensitive substrings `checkpoint`, `control point`, `bridge`, or
   `objective` are mapped to the corresponding feature SIDCs:

   | Word                | SIDC family (B130836)                |
   | ------------------- | ------------------------------------ |
   | checkpoint          | control measure - checkpoint         |
   | control point       | control measure - general            |
   | bridge              | mobility/survivability - bridge      |
   | objective           | offense - objective                  |

   Word recognition runs *after* prefix recognition, so an explicitly
   prefixed label (`OBJ TARGET`) takes precedence over a word match
   (`Objective Alpha`).

4. **"Suspected " prefix modifier.** Labels beginning with the case-
   insensitive string `Suspected ` are recognized as a modifier on
   whatever the rest of the label resolves to. The modifier is applied
   to the **standard identity** position (character 2) of the resulting
   SIDC, using the 2525 suspected variants (`S` for suspected hostile,
   `A` for assumed/suspected friend, etc., per the SIDC table). The
   "Suspected " text is stripped from the label before further
   recognition runs against it.

5. **Routes.** Labels that resolve to a route control measure (MSR, ASR,
   or matched by word patterns "route" / "axis") preserve the operator
   label verbatim with no further normalization. Routes are the one
   class where operators actively name the route ("Route Blue", "Axis
   Boyd") and that naming carries operational meaning.

6. **Geometry fallback.** Anything not resolved by layers 1-5 is
   assigned a generic geometry-shape SIDC of unknown affiliation
   (Point/LineString/Polygon -> 2525 unknown-class symbol), with the
   operator label preserved verbatim. Marked **best-effort** in the
   fidelity report. The CTO is *never silently dropped*; the operator
   sees every input object on the destination side and can correct it
   manually.

**Coalition / foreign-language naming conventions are explicitly out of
scope for v1.** The recognizer targets US doctrinal conventions only.

### D2 - OVL -> KMZ degrades gracefully and round-trips

**Visible representation.** A KMZ produced from an OVL renders in Google
Earth as a normal overlay:

* Geometry preserved verbatim.
* Label set to the operator's label (the doctrinal name like `PL ALPHA`,
  not the SIDC string).
* Color from CTO `affiliation` field, mapped to the 2525 rendering
  conventions: friend = blue (`#0080FF` outline / `#0080FF40` fill),
  hostile = red (`#FF0000` / `#FF000040`), neutral = green
  (`#00FF00` / `#00FF0040`), unknown = yellow (`#FFFF00` /
  `#FFFF0040`). These are conventions, not standards; they help a
  trained operator read the overlay at a glance.

**Hidden round-trip payload.** The KML `<description>` element on every
placemark carries two parallel representations of the structured data
that KMZ cannot natively encode:

1. **Visible HTML block.** Human-readable summary rendered in the
   Google Earth balloon when the placemark is clicked. Lines for SIDC,
   affiliation, overlay name, and any modifier values.
2. **Hidden machine-readable block.** Marked with an HTML comment
   sentinel (`<!-- TGCTO-BEGIN ... TGCTO-END -->`) carrying the same
   data as plain `key: value` lines. The KMZ ingest parser looks for
   this sentinel first; if found, it deserializes the structured fields
   verbatim and round-trips them losslessly. If absent, the parser
   falls back to D1's heuristics.

The hidden block isolates the round-trip path from the visible
formatting. Future changes to the HTML representation (prettification,
added fields, restyling) cannot break re-ingest, because re-ingest
reads only the hidden block.

### D3 - Converter uploads are first-class ingest

A file uploaded through the converter web page traverses the same
`CaptureWriter.capture()` path as a folder-watch drop:

* Raw bytes written to MinIO under `raw/<yyyy>/<mm>/<dd>/<protocol>/...`.
* Hash chain entry appended to the daily manifest.
* Resulting CTOs carry `ingest_source = "upload"` (distinct from the
  existing `"folder"` value) so audit queries can filter the two.
* Conversion is logged in CTO `provenance` as a step with
  `step = "convert_<src>_to_<dst>"` and `actor = "gateway.converter"`.

Deduplication by SHA-256 is intrinsic to the capture writer: the same
file uploaded twice produces one MinIO object and one chain entry tied
to the same SHA. The CTO rows from each conversion are still independent
(fresh UUID v7 per ingest, per the Phase 2a identity rule).

This makes the converter "a thin wrapper around the existing ingest path
plus a writer call," which is the simplest and most auditable shape it
can have.

### D4 - Fidelity report shape

Every conversion emits a fidelity report alongside the produced file.
The report is a structured JSON document with the following shape:

```json
{
  "source_format": "kmz" | "ovl",
  "destination_format": "ovl" | "kmz",
  "objects": {
    "total": N,
    "clean": M,        // recognized and represented without loss
    "best_effort": K,  // represented with degraded fidelity; see entries
    "dropped": 0       // always 0 under D1 / D2; field reserved for future
  },
  "entries": [
    {
      "object_uid": "...",
      "label": "...",
      "status": "clean" | "best_effort",
      "reasons": ["no SIDC derivable", "modifier T1 not expressible in KMZ", ...]
    }
  ],
  "fields_dropped_globally": ["overlay_name (KMZ has no equivalent)", ...]
}
```

The converter web page renders the report inline below the download
link in plain language ("converted 22 of 22 objects; 4 used best-effort
defaults; click for details"). The full JSON is also exposed via an
endpoint for programmatic consumers.

A condensed report is attached to each resulting CTO's `provenance`
chain so the audit trail captures what happened to each object.

## Coverage

The KMZ name recognizer prefix table, as expanded by D1, covers the
following doctrinal categories (full mapping in
`services/gateway/kmz/prefix_table.py` and cross-referenced to B130836):

* **Maneuver control measures:** PL, LOA, LD, LDLC, FLOT, FEBA, BNDRY,
  AO, EA, AA, BP, HA, SBF, ATK
* **Mobility:** SP, RP, MSR, ASR
* **Combat service support:** CCP, CP
* **Targeting:** OBJ, TAI, NAI, TRP, target designators (D1 layer 2)
* **Fire support:** FSCL, CFL, RFL, NFL, NFA, RFA
* **Aviation / airspace:** ROZ, PZ, LZ, DZ
* **Word-recognized:** checkpoint, control point, bridge, objective

The OVL writer's SIDC coverage inherits from Phase 2b-1's SIDC table
(all 33 function codes from the test fixtures, plus the B130836
working set). A formal coverage report (full-fidelity / best-effort /
unsupported) is a 2b-2 deliverable and will be appended to this ADR's
"Coverage" section before tagging.

## Consequences

* **2b-2 is larger than originally scoped.** The widened recognizer was
  not in the original scope doc; D1 added it. The four-deliverable shape
  (recognizer, OVL writer, KMZ writer, converter page) is now reflected
  in the revised scope doc.
* **The recognizer is shared infrastructure.** Any future ingest format
  that carries free-text labels (chat, document references, future
  geospatial formats) can reuse the same word-recognition layer. This
  is a deliberate investment, not just a 2b-2 expedient.
* **Round-trip fidelity becomes testable, not aspirational.** The
  hidden plain-text block in KMZ descriptions makes OVL -> CTO -> KMZ
  -> CTO -> OVL a clean round-trip when both sides are this system. A
  KMZ created by an external tool (Google Earth, third-party software)
  will lack the hidden block and fall back to D1 heuristics on re-
  ingest, which is the correct degradation.
* **The 2525 color mapping is a convention.** Google Earth users
  unaware of MIL-STD-2525 conventions will see colored shapes; they
  will not see "friendly" or "hostile" semantically. This is acceptable
  because the audience for these KMZs is tactical users who know the
  conventions.
* **Audit completeness is preserved.** Treating every upload as ingest
  keeps the chain unbroken. The IATT/ATO posture pushes toward "capture
  by default"; the converter does not deviate from that.
* **The recognizer's word-based layer is intentionally narrow.** Only
  four words trigger recognition in v1. Adding more (helipad, ambush,
  observation post, etc.) is a small data-entry change; we defer until
  a real operational sample motivates each addition, to avoid
  speculative coverage.

## Revisit when

* A planner KMZ shows a recurring label pattern that the recognizer
  misses (expand layer 1 or layer 3).
* A coalition or foreign-language naming convention becomes
  operationally relevant (lift the v1 scope restriction).
* An external KMZ-producing tool standardizes on a different "hidden
  payload" convention that we should also recognize (extend D2's
  sentinel detection).
* GCCS-J / Agile Server validates an emitted OVL and reveals a writer
  fidelity gap (likely; address with isolated writer patches).
