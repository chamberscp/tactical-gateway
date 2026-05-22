# ADR-0008: KMZ Ingest Design

**Status:** Accepted
**Date:** 2026-05-22
**Phase:** 2a

## Context

The gateway needs to ingest operational graphics (phase lines, NAIs, FSCMs,
boundaries, etc.) produced by planners and feed them into the canonical CTO
pipeline. KMZ is the dominant format planners use today (ATAK, Google Earth,
FalconView with KML plugin all produce it). The gateway must accept KMZ
without requiring planners to change their workflow.

Two delivery paths are required:

- **Folder watch.** A shared directory that planners can drop KMZ files
  into and have them picked up automatically. This matches the existing
  "drop in the share drive" workflow used at the MEU CE.
- **HTTP upload.** A POST endpoint that the future control GUI (Phase 3)
  and external automation can use.

Both must converge on the same parser and produce identical CTOs given
identical input.

## Decision

Build a single KMZ parser module (`kmz_parser.py`) and a thin
orchestrator (`kmz_ingest.py`) that both delivery paths share.

### Parser scope

- Read the zip; extract `doc.kml` (or the first .kml found).
- Parse standard KML Placemarks with Point, LineString, Polygon
  geometry. MultiGeometry: extract the first child geometry only;
  warn.
- Capture ExtendedData fields as a dict on the CTO, with `sidc`
  promoted to `symbology.sidc_2525c` when present.
- Doctrinal label recognition: regex match common labels (PL, NAI,
  RFA, FSCM types, etc.) and tag `attributes.graphic_kind`. Falls
  back to free-text label otherwise.
- NetworkLinks are NOT followed. Logged with warning.
- Ground/image overlays are NOT extracted.

### Orchestrator behavior

1. Validate the bytes look like a zip.
2. Compute SHA-256 and capture to MinIO with a hash chain entry,
   reusing the Phase 1 capture path. The captured object key
   becomes `attributes.parent_kmz_uri` on every emitted CTO.
3. Run the parser.
4. Publish each resulting CTO to `cto.normalized.graphic` on NATS.
5. Return a summary (success/failure, features extracted, hash).

### Delivery paths

- **Folder watch** polls the configured inbox path every 2s (default).
  On a stable, new .kmz file, ingests with `ingest_source=FOLDER`,
  `source_system=kmz-folder:<path>`. After ingest: move to
  `.processed/` (success) or `.failed/` (with .err sidecar). Silent
  replace on filename collision (decision 4a).
- **HTTP upload** at `POST /ingest/kmz` accepts multipart upload.
  On first upload, ingests with `ingest_source=UPLOAD`,
  `source_system=kmz-upload:<client_ip>`. If a CTO already exists
  with the same filename and `parent_kmz_source=upload` and
  `valid_to IS NULL`, returns HTTP 409 with metadata about the
  existing version. Client confirms replace by re-POSTing with
  `?force=true`.

### Identity model

Fresh UUID v7 per CTO on every ingest. No deterministic identity
matching across re-ingests. Whole-overlay supersession at the
opstore handles the "this file replaces the prior version" case;
per-feature identity diffing is deferred to a possible later phase
as a query-time operation (decision d).

## Alternatives Considered

- **Single ingest path (only HTTP upload).** Rejected: requires
  changing planner workflow. The "drop the file in the shared
  folder" pattern is already universal and shouldn't be disrupted.
- **inotify-based watcher.** Rejected: doesn't work cross-platform
  (Windows dev). Polling at 2s is adequate for human-scale flows.
- **Follow NetworkLinks.** Rejected on security grounds. The KMZ
  author could point at any URL. Live data should come from
  authenticated feed protocols, not from inside an uploaded KMZ.
- **Deterministic UIDs per feature.** Rejected (decision d).
  Renames and edits by non-expert users would silently mis-track
  identity. Fresh UUIDs with whole-overlay supersession is more
  honest about what we can know.

## Consequences

- Easy to add a third delivery path later (e.g., periodic pull from
  an external URL) without changing the parser.
- Same KMZ in either delivery path produces structurally identical
  CTOs (only `ingest_source`, `source_system`, and the
  `parent_kmz_source` attribute differ).
- The captured KMZ object in MinIO is the forensic record. Even if
  a feature is misclassified or filtered, the original bytes are
  recoverable.
- Folder collisions are silent; downstream operators must rely on
  the audit log (ADR-0010) to know that a re-ingest happened.

## References

- Phase 2a scope document: `docs/phase2a-scope.docx`
- ADR-0009: Operational Store Separation
- ADR-0010: Supersession and Identity Model
- B130836 USMC Operational Terms and Graphics (for doctrinal
  label recognition patterns; Phase 2b will expand)
