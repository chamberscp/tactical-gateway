# ADR-0010: Supersession and Identity Model

**Status:** Accepted
**Date:** 2026-05-22
**Phase:** 2a

## Context

When a planner publishes an updated overlay (same filename, new content),
how does the operational store reconcile the new features with the old?

Two intertwined questions:

1. **Whole-overlay supersession.** Should re-ingest of `phase_iii.kmz`
   retire all CTOs from the prior version of that file?
2. **Per-feature identity.** Across re-ingests, can the gateway tell
   that "PL ALPHA" in v2 is the same conceptual feature as "PL ALPHA"
   in v1, or do we treat every feature in v2 as new?

These two questions can be answered independently. After analysis the
team settled on: yes to whole-overlay supersession, no to per-feature
identity matching.

## Decision

### Whole-overlay supersession

When a KMZ is re-ingested through the same path (folder watch OR upload,
but not crossing), all CTOs from the prior ingest of that filename
get `valid_to` set to the new ingest's `received_at`. The new ingest's
features are inserted as new rows.

"Same path" means:
- `parent_kmz_filename` matches AND
- `parent_kmz_source` matches (FOLDER vs UPLOAD)

A folder-watch re-ingest does NOT supersede prior upload CTOs of the
same filename, and vice versa. This prevents accidental clobbering
across delivery paths.

### Per-feature identity

Fresh UUID v7 per CTO every ingest. No attempt to match "feature X
in v2 corresponds to feature X in v1." If you want to know what
changed between versions, that becomes a query-time computation against
the historical data (which is fully preserved).

### Audit trail

Every supersession event writes a row to `audit_log` with:

- `actor = "opstore.supersede"`
- `action = "kmz_supersede"`
- `event_type = "supersession"`
- `subject = parent_kmz_filename`
- `details` containing the count of features superseded, the
  source path, and pointers to both old and new KMZ object keys
  in MinIO

Plus the underlying hash chain in MinIO records the SHA-256 of the
new KMZ bytes, linking it to its predecessor through the chain
(though the chain itself doesn't model "this file replaces that
file" - it just records every ingest in order).

## Alternatives Considered

### Per-feature identity matching options

- **Deterministic UID from filename + feature name.** Same name in
  re-ingest yields same UID, allowing v1↔v2 mapping. Rejected:
  silently mis-tracks the most common edit case (rename + tweak).
  A non-expert user revising an overlay would inadvertently create
  delete+new events in the audit log, making the history misleading.
- **Best-effort similarity matching.** On re-ingest, for each new
  feature find the closest match in v1 by name fuzzy-match + spatial
  proximity + type. Rejected for Phase 2a: requires tuning thresholds
  with no clear right answer, and a wrong match is worse than no
  match (it claims identity where there is none).
- **Require explicit IDs.** Rejected: not under our control. Most
  planner tools don't set placemark `id`. We accept what we get.

### Cross-path supersession options

- **Supersede across both paths (option α).** Rejected: a planner
  uploading a one-off `phase_iii.kmz` via GUI would accidentally
  retire long-running folder-watch versions of the same name.
  Cross-path replacement should be a deliberate act, not an accident.

### Hard-delete vs soft-delete

- **Hard-delete superseded CTOs.** Rejected. The historical data
  is the audit trail. Operators may need to answer "what did the
  overlay look like at time T?" indefinitely.

## Consequences

- The `cto` table grows monotonically. Retention/archival is a
  future Phase 5 concern.
- A re-ingest of an unchanged KMZ still produces N new rows
  (rather than being a no-op). The PostGIS table will accumulate
  duplicates over time if the same overlay is re-ingested
  repeatedly. This is the price of refusing per-feature
  identity claims. Acceptable for current scale; revisit if
  retention becomes an issue.
- Per-feature change history is not directly queryable but is
  reconstructible: query all historical CTOs with the same
  `parent_kmz_filename` and `parent_kmz_source`, sort by
  `received_at`, and the consumer can do its own diff.
- The default query (`GET /cto?...` without `include_historical`)
  returns only current CTOs, so consumers don't see duplicates by
  default.

## References

- Phase 2a scope document: `docs/phase2a-scope.docx`
- ADR-0008: KMZ Ingest Design
- ADR-0009: Operational Store Separation
- Conversation thread that produced these decisions (preserved in
  Claude transcript at the project root)
