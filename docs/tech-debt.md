# Tech Debt

Known issues and deferred cleanups, captured as encountered. Each item
notes what's wrong, why it was deferred, and what a fix involves. None
of these block current functionality; they are tracked so they aren't
forgotten.

## 1. source_protocol_enum drift (Postgres vs. Python)

The Postgres `source_protocol_enum` type and the Python `SourceProtocol`
enum have diverged in both directions:

- Postgres has `kml` and `internal`; Python no longer uses them.
- Python has `usmtf` and `other` (reserved); Postgres lacks them.

Migration `0004` closed only the `ovl` gap (the one Phase 2b-1 needed).
The rest is unreconciled. A future migration should add `usmtf`/`other`
to the Postgres type. Removing `kml`/`internal` is harder — Postgres
cannot drop an enum value without recreating the type, so the pragmatic
choice is to leave them in place (harmless) unless a type rebuild is
warranted for another reason.

Discovered: Phase 2b-1 (2026-05-28).

## 2. Pre-existing cto_schema test failures (4)

`libs/cto_schema/tests/test_models.py` has 4 failing tests, proven
pre-existing (present before Phase 2b-1; confirmed via git stash):

- `test_minimal_cto_track` — references field `mil_std_2525d_sidc`, but
  the model field is `sidc_2525c`.
- `test_cto_is_frozen` — expects the model to be frozen; it isn't.
- `test_speed_must_be_non_negative` — expects a validation the model
  does not enforce.
- `test_course_must_be_in_range` — expects a validation the model does
  not enforce.

Either the tests or the model are stale. Resolution requires deciding
which is authoritative (likely update the tests to match the shipped
model, or add the missing validations if they are genuinely wanted).
Out of scope for Phase 2b-1; full suite otherwise passes.

Discovered: Phase 2b-1 (2026-05-28).

## 3. verify_chain.py has no manifest export path

`tools/verify_chain.py` verifies a local `manifest.jsonl`, but the
capture writer stores the daily manifest as a MinIO object
(`raw/<yyyy>/<mm>/<dd>/manifest.jsonl`), never on local disk. There is
no first-class way to pull the manifest out for verification; it must be
fetched manually via a MinIO client one-liner before running the tool.

A small CLI improvement would let `verify_chain` (or a companion tool)
fetch the manifest from MinIO directly given a date, or add an exporter.
Pre-existing (affects the KMZ chain equally); not OVL-specific.

Discovered: Phase 2b-1 (2026-05-28).
