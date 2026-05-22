# ADR-0009: Operational Store as a Separate Service

**Status:** Accepted
**Date:** 2026-05-22
**Phase:** 2a

## Context

Phase 2a introduces persistent CTO storage in PostgreSQL+PostGIS. The
question was whether to embed the database writer as a coroutine inside
the existing gateway service or run it as its own service.

## Decision

Operate the operational store as a separate service: `tg-opstore`.

The opstore is a NATS subscriber that consumes `cto.normalized.>` and
writes to Postgres. It runs in its own container, has its own settings
namespace, and has no inbound network surface.

The gateway service still owns:

- Network ingest (CoT listeners, KMZ ingest endpoints)
- Capture to MinIO with hash chain
- CTO publication to NATS
- The read-side **query API** (`GET /cto/...`), which reads directly
  from the same Postgres the opstore writes to

## Alternatives Considered

- **Same process.** Simpler ops; one container, one log. Rejected
  because:
  - It couples ingest throughput to database write throughput.
    A burst of KMZ features could back up the network listeners.
  - It conflates two failure domains. A Postgres outage shouldn't
    affect bytes-in capture.
  - The production architecture will need them separate anyway for
    IL6 (the opstore will be on a different network segment from
    the ingest service). Doing it now means fewer surprises later.
- **Multiple opstore replicas.** Postponed. NATS allows
  competing consumers via queue groups, which would let us scale
  writes horizontally. Not needed at current throughput
  (validated 130 msg/s with zero loss in Phase 1, and KMZ ingest
  is bursty but low average rate). When we add this, the change
  is to set a `queue` on `nats.subscribe`. Zero schema impact.

## Consequences

- One additional container in the dev stack and prod deployment.
- The gateway no longer needs Postgres connection pool config for
  writes (it does need a small read pool for the query API).
- The opstore is the single writer to the `cto` and `audit_log`
  tables. Schema migrations live there.
- Operational metrics for writes (rows/sec, supersession events,
  failed writes) live on the opstore, not the gateway.

## References

- Phase 2a scope document: `docs/phase2a-scope.docx`
- ADR-0008: KMZ Ingest Design
- ADR-0010: Supersession and Identity Model
