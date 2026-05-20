# ADR-0003: Capture raw bytes before normalization, with hash-chain integrity

**Status:** Accepted
**Date:** 2026-05-20

## Context

Two requirements pull in the same direction: (1) you said yes to
tamper-evident logging for evidentiary value; (2) the normalizer will
have bugs, and we want to be able to re-run it against captured data
without going back to the source system.

## Decision

Every byte that arrives at an ingest listener is:

1. Timestamped (`captured_at`) and SHA-256 hashed.
2. Written to MinIO under a deterministic key
   (`raw/{yyyy}/{mm}/{dd}/{protocol}/{sha256[:2]}/{sha256}`).
3. Linked to a daily hash-chain entry: each day's chain is
   `H(prev_chain || sha256_of_message || captured_at_iso)`.
4. Only after the raw write succeeds do we run normalization.

The resulting CTO carries a `raw_pointer` with the sha256 and object key
so any consumer can fetch the original bytes and verify the hash.

## Alternatives considered

- **Normalize first, store both.** Simpler, but if normalization crashes
  we lose the raw data. Unacceptable.
- **No hash chain, just per-object hashes.** Per-object hashes prove an
  object hasn't been altered, but don't prove the *set* of objects hasn't
  been altered (an attacker with DB write access could delete a row).
  A daily chain published to an external append-only store would close
  that gap; for IATT we keep the chain internal.
- **Cryptographic signing of each message at ingest.** Stronger but
  requires a key management story we don't have yet. Hash chain is the
  pragmatic starting point.

## Consequences

**Positive:**
- Normalizer bugs are recoverable: re-run against raw captures.
- Tamper evidence available for after-action review.
- Disk usage is bounded by retention policy (TBD per ADR-future).
- Raw captures support PCAP-style replay for training.

**Negative:**
- Storage cost: raw + normalized = ~2x data on disk. Mitigated by
  compression (Parquet for structured raw, gzip/zstd for blobs).
- Two writes per message increases ingest latency. We measure and
  optimize when Phase 1 numbers are real.

## Revisit if

- Storage cost becomes a meaningful constraint on the edge box.
- An accreditor requires stronger tamper evidence (external timestamping,
  HSM-backed signing).
