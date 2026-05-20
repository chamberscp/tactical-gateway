# ADR-0007: Python primary, Rust only on demonstrated hotspots

**Status:** Accepted
**Date:** 2026-05-20

## Context

You stated a strong preference for Python: ease of QC and review by
non-specialist auditors. The risk is that Python's concurrency model
(GIL) bottlenecks the ingest path under high CoT message rates.

## Decision

- Default language is Python 3.11+.
- Async I/O via `asyncio` + `uvloop` for high-concurrency listeners.
- Treat any service that fails to meet throughput targets after
  reasonable optimization (uvloop, batching, multiprocessing) as a
  candidate to be rewritten in Rust as a microservice.
- Rust microservices, when used, are isolated behind the same NATS
  interface as Python services, so they can be swapped in or out
  without touching the rest of the system.

## Alternatives considered

- **Rust everywhere.** Best raw performance, smallest binaries, easiest
  to audit *for someone fluent in Rust*. Inverts the team's review
  capability for a problem we haven't yet measured.
- **Go everywhere.** A reasonable middle ground. Rejected to avoid two
  primary languages.

## Consequences

**Positive:**
- Single primary language for the team to maintain and audit.
- Rich ecosystem for the tactical, ML, and document-processing pieces.
- Fast iteration.

**Negative:**
- Python ingest will need careful design to hit thousands of CoT msgs/sec.
- Rust hotspot microservices are still a possibility we have to be
  prepared for.

## Revisit if

- Measured Python ingest throughput is below the target (TBD per Phase 1
  load testing) after asyncio + uvloop + batching is exhausted.
