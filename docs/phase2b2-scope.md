# Phase 2b-2 — Overlay Converter

**Status:** decisions ratified, ready to build
**Builds on:** Phase 2b-1 (OVL ingest), Phase 2a (KMZ ingest + opstore)
**Tag on completion:** `phase2b-2`
**Locked decisions:** ADR-0012

---

## 1. Purpose

Give a non-technical operator a simple way to convert a single overlay
file between KMZ and OVL so they can send the result to a teammate who
uses the other system.

The user is the human in the loop, not a planner pushing live data and
not an admin configuring routes. Typical flow:

> "My TAK teammate sent me a KMZ. My GCCS-J guy needs the same overlay
> as an OVL. I open the converter page, drop in the KMZ, click
> *Convert to OVL*, save the file, and email it on."

The reverse direction (OVL in, KMZ out) is the equally common case.

The phase also closes two longstanding gaps deferred from earlier work:
the `CTO → OVL` writer (originally part of Phase 2's bidirectional
scope) and the `CTO → KMZ` writer (carved out of Phase 2a when ingest
was split off to ship faster), and widens the KMZ ingest recognizer in
the process — see §3 and ADR-0012.

## 2. Scope summary

Four deliverables (one more than the original scope; the recognizer
widening was added when D1 was ratified):

| # | Deliverable | Why it's in this phase |
|---|---|---|
| 1 | **KMZ ingest recognizer widening** | D1 puts label-interpretation smarts in the ingest, not the writer. Every downstream consumer benefits. |
| 2 | **`CTO → OVL` writer** | Core deferred work. Inverts the Phase 2b-1 parser. |
| 3 | **`CTO → KMZ` writer** | Core deferred work. Implements the D2 round-trip strategy. |
| 4 | **Converter web page** | Operator-facing surface for items 2 and 3. |

Plus tests, ADR, README, and tag, as usual.

## 3. Scope detail

### 3.1 KMZ ingest recognizer widening *(new — from ratified D1)*

The Phase 2a KMZ ingest recognizes a short list of doctrinal prefixes.
D1 (see ADR-0012) extends it to a five-layer recognition pipeline:

1. **Extended doctrinal prefix table.** Adds LOA, LD/LDLC, FLOT/FEBA,
   SP/RP, CP/CCP, HA, SBF, ATK, PZ/LZ/DZ, MSR/ASR, TRP, AO, RFL/NFL to
   the existing list. Full mapping in `services/gateway/kmz/prefix_table.py`.
2. **Target designator pattern.** `^[A-Z]{1,2}\d{3,4}\b` (e.g. `AB1001`)
   → target reference point SIDC.
3. **Word-based recognition.** Labels containing `checkpoint`,
   `control point`, `bridge`, or `objective` (case-insensitive) map to
   the corresponding feature SIDCs.
4. **"Suspected " modifier.** Labels beginning with `Suspected ` apply
   the suspected variant to the SIDC standard-identity (character 2).
5. **Geometry fallback.** Unresolved labels get a generic
   shape-derived SIDC, marked best-effort, label preserved verbatim.

Routes preserve operator labels verbatim. Coalition / foreign-language
conventions are out of scope for v1.

Result: every KMZ ingest produces a CTO with structured symbology
fields (SIDC, affiliation, doctrinal kind) — not just geometry +
label — that the OVL writer can emit directly.

### 3.2 `CTO → OVL` writer

* Emits a valid GCCS-J / Agile overlay file from a set of CTOs.
* SIDC, modifiers, label, visibility flags, and geometry restored from
  CTO fields populated by ingest.
* Schema target: the real `<MODEL>/<milbobject>` form (same target as
  the Phase 2b-1 parser; ADR-0011).
* Validated against the two real planner overlay fixtures used in 2b-1.
* Module: `services/gateway/ovl/writer.py`.

### 3.3 `CTO → KMZ` writer

* Emits a valid KMZ from a set of CTOs.
* Geometry: Point, LineString, Polygon.
* Label: operator's doctrinal label (`PL ALPHA`), not the SIDC string.
* Color: derived from `affiliation` per the D2 mapping (friend blue,
  hostile red, neutral green, unknown yellow).
* `<description>` field carries both a visible HTML representation and
  a hidden machine-readable block (sentinel `<!-- TGCTO-BEGIN ...
  TGCTO-END -->`) for lossless round-trip. See ADR-0012 D2.
* Module: `services/gateway/kmz/writer.py` (note: ingest moved from
  `kmz_ingest.py` here into a `kmz/` package as part of this work to
  keep ingest and egress symmetric).

### 3.4 Converter web page

* Single page served by the gateway at `/convert` (final path locked
  in implementation).
* Drag-and-drop *and* a file-picker (drag-and-drop is the primary UX;
  picker is the keyboard / screen-reader path).
* Two buttons: **Convert to OVL** and **Convert to KMZ**. The button
  corresponding to the input format is disabled — same-format
  conversion is not a use case.
* Result panel below shows: the download link for the converted file
  and the fidelity report rendered in plain language.
* No authentication, no styling beyond legible. Operator utility, not
  a product surface.
* Upload path is **first-class ingest** (D3): the uploaded bytes
  traverse `CaptureWriter.capture()` exactly as a folder-drop does,
  landing in MinIO with their SHA in the hash chain. CTOs from a
  converter upload carry `ingest_source = "upload"` distinct from the
  existing `"folder"` value.
* Endpoint module: `services/gateway/convert_api.py`.
* The HTML/CSS/JS for the page lives next to it as a single small
  template (no SPA, no build step, no client framework).

### 3.5 Fidelity report

Both writers emit a structured JSON fidelity report alongside the file
(schema in ADR-0012 D4). The page renders it inline in plain language;
the JSON is also exposed via the API for programmatic consumers. A
condensed version is attached to each resulting CTO's `provenance`
chain.

### 3.6 Round-trip tests

* **Cross-format round-trip** (KMZ → CTO → OVL → CTO → KMZ and the
  reverse): asserts the known loss boundaries — affiliation and
  doctrinal label survive; SIDC string survives via the hidden
  payload; modifier fields survive only when the destination format
  can carry them.
* **Same-format round-trip** (OVL → CTO → OVL, KMZ → CTO → KMZ):
  expected near-lossless; asserts as such.
* Test fixtures: the two Phase 2b-1 OVL fixtures plus the Phase 2a
  KMZ fixture, plus at least one synthetic KMZ that exercises every
  D1 recognizer layer.

## 4. Out of scope

* **Bulk / multi-file conversion.** One file in, one file out. The
  writers themselves are perfectly capable of bulk; the page UX is
  not. Add later if asked for.
* **Route-engine integration.** Once writers exist, the route engine
  can subscribe to `cto.normalized.*` and emit OVL/KMZ to live
  destinations. Follow-on once we have a concrete consumer.
* **Folder-mirror auto-emit** ("drop a KMZ, get an OVL next to it").
  Considered and rejected: the converter page is a better UX for
  non-technical users (visible feedback, fidelity report, no
  loop-avoidance footguns). Ten lines to add later if a real use case
  appears.
* **Editing on the page.** Format conversion only; no in-browser
  geometry edits, label edits, or symbol picker.
* **Auth / RBAC.** Deferred to Phase 5 hardening.
* **Coalition / foreign-language naming.** Out of scope for v1 of the
  recognizer (ADR-0012 D1).

## 5. Decisions (locked)

All four locked decisions are documented in **ADR-0012**.
Summary for quick reference:

* **D1.** KMZ ingest is widened; KMZ→OVL never silently drops.
  Five-layer recognition pipeline. Smarts in ingest, not writer.
* **D2.** OVL→KMZ degrades gracefully. Color from affiliation;
  SIDC and modifiers in `<description>` with hidden round-trip
  payload behind `<!-- TGCTO-BEGIN ... -->` sentinel.
* **D3.** Converter uploads are first-class ingest. Capture chain
  applies. `ingest_source = "upload"`.
* **D4.** Fidelity report has a fixed JSON shape; page renders it in
  plain language; CTO provenance carries a condensed version.

## 6. Deliverables (file list)

1. ADR-0012 — Overlay writers, fidelity reporting, and KMZ name
   recognition.  *(done)*
2. Widened recognizer:
   * `services/gateway/kmz/prefix_table.py` — extended prefix mapping.
   * `services/gateway/kmz/recognize.py` — five-layer recognition pipeline.
   * Updates to `services/gateway/kmz/ingest.py` (was `kmz_ingest.py`,
     moved into the `kmz/` package for symmetry with `ovl/`).
3. Writers:
   * `services/gateway/ovl/writer.py`
   * `services/gateway/kmz/writer.py`
4. Converter:
   * `services/gateway/convert_api.py`
   * `services/gateway/convert.html` (template, served by the API).
5. Tests:
   * `tests/unit/test_kmz_recognizer.py` — every layer of D1.
   * `tests/integration/test_kmz_to_ovl_roundtrip.py`
   * `tests/integration/test_ovl_to_kmz_roundtrip.py`
   * `tests/integration/test_converter_endpoint.py`
6. `PHASE2B2_README.md` — operator-facing usage doc.
7. Tag `phase2b-2`.

## 7. Build order

1. **Widened recognizer + unit tests.** D1's recognition pipeline is
   shared infrastructure; it has to land before either writer is
   meaningful. Smallest, most testable starting point.
2. **`CTO → OVL` writer + unit tests.** Inverts the 2b-1 parser
   against the same fixtures. Validate same-format round-trip.
3. **`CTO → KMZ` writer + unit tests.** Implements the D2 hidden-
   payload trick. Validate same-format round-trip.
4. **Cross-format round-trip integration tests.** Quantifies the
   honest loss boundaries.
5. **Converter API + page.** Thin wrapper. Validates D3 end-to-end.
6. **README, smoke test in the dev stack, tag.**

## 8. Risks

* **OVL writer fidelity vs. real GCCS-J consumers.** We have the
  schema and two real fixtures but no SIPR-side instance to load the
  emitted OVL into. Mitigation: keep the writer code isolated so
  swapping in fixes after a real test is small; lean heavily on the
  round-trip test as the validation that the bytes are at least
  internally consistent.
* **Symbol coverage gaps.** B130836 enumerates the doctrinally
  important graphics, but planners can technically draw anything.
  The fidelity report is the honest answer — best-effort is
  documented, not hidden.
* **User confusion when fidelity report shows loss.** Mitigation:
  the page wording is "we converted N of M cleanly, here's what
  changed," not "FAILED." The file is still given to them.
* **Recognizer false positives.** Word-based recognition could mis-
  classify (e.g. a label like "Possible bridge location" gets the
  bridge SIDC). The recognizer marks word-based matches best-effort
  precisely so the operator on the destination side sees the flag
  and can adjust. Mitigation is honesty in the fidelity report, not
  trying to eliminate ambiguity that the source data doesn't resolve.

## 9. Definition of done

* All four deliverables implemented with unit tests passing.
* Cross-format and same-format round-trip integration tests pass
  against the real fixtures.
* Converter page works end-to-end in the dev stack: drop a `.kmz`,
  get a `.ovl` plus an inline fidelity report; same in reverse.
* Hash chain verified after a converter upload (`CHAIN OK`).
* ADR-0012 finalized; `PHASE2B2_README.md` written.
* Tagged `phase2b-2` on a commit that smoke-tests green.

## 10. Estimated shape (not a commitment)

* Recognizer widening + unit tests: small. A day, maybe two.
* Writers + unit tests + same-format round-trips: most of the work.
  Comparable to the OVL parser effort, since we're inverting the same
  mapping table.
* Cross-format round-trip tests: small if the writers are clean.
* Converter page + endpoint: a single sitting at most. Boring code,
  no novel patterns.
* ADR (done) + README + tag mechanics: an evening.

Net: a phase of similar size to 2b-1.
