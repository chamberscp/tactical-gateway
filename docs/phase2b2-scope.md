# Phase 2b-2 — Overlay Converter

**Status:** scoped, not started
**Builds on:** Phase 2b-1 (OVL ingest), Phase 2a (KMZ ingest + opstore)
**Tag on completion:** `phase2b-2`

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

The work also closes two longstanding gaps deferred from earlier phases:
the `CTO → OVL` writer (originally part of Phase 2's bidirectional
scope) and the `CTO → KMZ` writer (carved out of Phase 2a when ingest
was split off to ship faster).

## 2. Scope

### 2.1 Writers (the real work)

- **`CTO → OVL`.** Generate a valid GCCS-J / Agile overlay file from a
  set of CTOs. Symbology emitted from `sidc_2525d`. Modifiers, label,
  visibility flags, and geometry restored from CTO fields populated by
  the ingest path. Validated against the two real planner overlay
  fixtures used in Phase 2b-1.
- **`CTO → KMZ`.** Generate a valid KMZ from a set of CTOs. Point,
  LineString, and Polygon supported. Doctrinal names (PL, NAI, OBJ,
  etc.) preserved on the way out so Google Earth shows sensible labels.

### 2.2 Converter web page

- One page served by the gateway at `/convert` (path final at build time).
- Drag-and-drop or file-picker for the input file.
- Two buttons: **Convert to OVL** and **Convert to KMZ** (the button
  corresponding to the input format is disabled, since "KMZ → KMZ" is
  not the use case).
- Result panel below shows: the download link for the converted file,
  and the fidelity report inline.
- No authentication, no styling beyond legible. This is an operator
  utility, not a product surface.
- No persistent storage of uploads beyond the existing raw-capture
  pipeline (the upload goes through the same capture writer as any
  other ingest, so it lands in MinIO with its SHA in the chain — same
  audit story as folder ingest).

### 2.3 Fidelity report

Both writers emit a structured fidelity report alongside the file:

- Count of objects translated cleanly.
- Count of objects translated with best-effort defaults (with reasons).
- Count of objects that could not be represented (with reasons).
- List of fields dropped on the destination side (e.g. SIDC modifiers
  not expressible in KMZ).

The report is shown on the converter page in plain language, and is
attached to the CTO `provenance` chain as a step entry so the audit
trail captures what happened.

### 2.4 Round-trip tests

For each direction:

- Take a real fixture (OVL or KMZ), ingest it to CTOs, emit the
  opposite format, ingest *that* back, and diff the resulting CTOs
  against the originals.
- Quantify the loss explicitly. KMZ ↔ OVL is lossy by format design
  (OVL carries SIDC, KMZ does not), so the test asserts the *known*
  loss boundaries rather than zero loss.
- Same-format round-trip (OVL → CTO → OVL, KMZ → CTO → KMZ) is
  expected to be near-lossless and is tested as such.

## 3. Out of scope

- **Bulk / multi-file conversion.** One file in, one file out. If
  needed later, the same writers serve it without re-architecting.
- **Route-engine integration.** Once writers exist, the route engine
  can subscribe to `cto.normalized.*` and emit OVL/KMZ to live
  destinations — that's a follow-on once we have a concrete consumer
  asking for it.
- **Folder-mirror auto-emit.** Considered and rejected for this phase:
  it's a worse UX than the converter page for non-technical users
  (silent failures, no fidelity feedback, naming conventions to learn,
  loop-avoidance footguns). Trivial to add later if a real use case
  appears.
- **Editing on the page.** Users cannot modify the overlay in the
  browser. They convert format only.
- **Auth / RBAC.** Deferred to Phase 5 hardening.

## 4. Deliverables

1. `services/gateway/ovl/writer.py` — CTO → OVL writer module.
2. `services/gateway/kmz_egress.py` — CTO → KMZ writer module.
3. `services/gateway/convert_api.py` — the converter endpoint(s) and
   the small HTML page.
4. Two new integration tests under `tests/integration/`:
   - `test_kmz_to_ovl_roundtrip.py`
   - `test_ovl_to_kmz_roundtrip.py`
5. ADR-0012 — Overlay writers and fidelity-report model.
6. `PHASE2B2_README.md` — same shape as the prior phase READMEs.

## 5. Decisions to lock in before code

These are the equivalents of Phase 2b-1's D1/D2/D3. They need answers
at the top of the build, not during it.

### D1 — When KMZ → OVL needs a SIDC and the source has only a label, what do we do?

KMZ doesn't carry SIDC. To produce an OVL, the writer must assign one.
The Phase 2a ingest already recognises doctrinal name prefixes (PL,
NAI, TAI, OBJ, NFA, RFA, ROZ, FSCL, CFL, BNDRY, EA, AA, BP) and stores
the `graphic_kind`. Proposed rule:

- If `graphic_kind` is known and maps to a SIDC in the B130836 mapping
  table → emit that SIDC, mark *clean* in the fidelity report.
- If `graphic_kind` is known but ambiguous (e.g. PL has multiple SIDC
  variants) → emit the most general variant, mark *best-effort*.
- If no `graphic_kind` and the geometry suggests a class (a Point with
  no doctrinal label) → emit the generic unknown-affiliation SIDC for
  that geometry, mark *best-effort*.
- Otherwise → drop the object from the OVL, list it in the fidelity
  report's "not representable" section.

Confirm or override before build.

### D2 — When OVL → KMZ has rich SIDC data the KMZ format can't represent, what survives?

KMZ's representational capacity is much smaller. Proposed rule: KMZ
output preserves geometry, label, doctrinal-name kind (so Google Earth
labels remain meaningful), and color/style as a best-effort hint from
affiliation (friend = blue, hostile = red, neutral = green, unknown =
yellow — matching MIL-STD-2525 conventions). Modifiers, SIDC string,
and overlay name are recorded in the KML `<description>` as plain text
so they round-trip back on re-ingest, but they're not driving rendering.

Confirm or override.

### D3 — Where does the uploaded file go?

Two reasonable choices:

- **Treat it as ingest.** Run it through the same capture writer; it
  lands in MinIO with its SHA in the daily chain, just like a
  folder-drop. The converter is then "a thin wrapper around the
  existing ingest + a writer call." Best for audit consistency.
- **Treat it as ephemeral.** Hold the bytes in memory just long enough
  to convert, then discard. Avoids cluttering the chain with files
  that may be the same overlay re-uploaded by multiple people. Faster.

Proposed: **treat it as ingest.** The audit trail value outweighs the
chain volume. The chain already deduplicates by SHA so re-uploads of
the same file are idempotent.

Confirm or override.

## 6. Risks

- **OVL writer fidelity vs. real GCCS-J consumers.** We have the
  schema and two real fixtures but no SIPR-side instance to load the
  emitted OVL into. Mitigation: keep the writer code isolated so
  swapping in fixes after a real test is small; lean heavily on the
  round-trip test as the validation that the bytes are at least
  internally consistent.
- **Symbol coverage gaps.** B130836 enumerates the doctrinally
  important graphics, but planners can technically draw anything. The
  fidelity report is the honest answer — best-effort is documented,
  not hidden.
- **User confusion when fidelity report shows loss.** Mitigation: the
  page wording is "we converted N of M cleanly, here's what changed,"
  not "FAILED." The file is still given to them.

## 7. Definition of done

- Both writers implemented with unit tests.
- Round-trip integration tests pass against the real fixtures.
- Converter page works end-to-end in the dev stack: drop a `.kmz`,
  get a `.ovl` plus an inline fidelity report; same in reverse.
- ADR-0012 written and merged.
- `PHASE2B2_README.md` documents how to use the converter and what
  the fidelity-report fields mean.
- Tagged `phase2b-2` on the same branch.

## 8. Estimated shape (not a commitment)

- Writers + tests: most of the work. Comparable to the OVL parser
  effort, since you're inverting the same mapping table.
- Converter page + endpoint: a single day at most. Boring code.
- ADR + README: an evening.

Net: a phase of similar size to 2b-1.
