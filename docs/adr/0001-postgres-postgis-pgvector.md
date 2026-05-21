# ADR-0001: PostgreSQL with PostGIS and pgvector as the single operational store

**Status:** Accepted
**Date:** 2026-05-20

## Context

The system needs to store three classes of data: normalized tactical
objects (CTOs) with spatial geometry; document metadata and chunks with
high-dimensional embedding vectors for RAG; and an audit log. A naive
approach would use three different stores (Postgres + a vector DB like
Qdrant + an audit sink). That adds operational surface area, audit
complexity, and ATO burden.

## Decision

Use a single PostgreSQL 16 instance with the `postgis` and `vector`
extensions. PostGIS for geometry, pgvector for embeddings, native
tables for audit.

## Alternatives considered

- **Qdrant or Milvus for vectors, Postgres for everything else.** Better
  raw vector search performance at very high vector counts (>10M). For
  our scale (one MEU's documents, perhaps 1-5M chunks) pgvector's HNSW
  index is competitive. Not worth two databases.
- **Elasticsearch for documents + Postgres for tracks.** ES has good
  hybrid search but heavy operational footprint, licensing concerns
  (Elastic license vs OpenSearch fork), and another product to STIG.
- **SQLite + faiss.** Considered for the edge case of an extremely
  resource-constrained box. Insufficient for the concurrency target
  (100 users).

## Consequences

**Positive:**
- One database to back up, restore, monitor, audit, and STIG.
- Hybrid search (vector + BM25 via Postgres full-text) in one query.
- Transactional consistency between CTOs, documents, and audit records.
- pgvector and PostGIS are both mature, open, and FIPS-friendly when
  Postgres is built with FIPS-mode OpenSSL.

**Negative:**
- At very large vector counts, dedicated vector DBs are faster. If we
  exceed ~10M chunks, revisit.
- HNSW index rebuilds on large insert batches are slow; we'll need
  batching strategies in the RAG ingest pipeline.

## Update 2026-05-20 — Image selection

Initial implementation used the `pgvector/pgvector:pg16` image on the
assumption that it included PostGIS. It does not. To get both extensions
we now build a custom image (`deploy/docker/Dockerfile.postgres`) based
on the official `postgis/postgis:16-3.4` and install `postgresql-16-pgvector`
from the Debian apt repository (the same source the pgvector/pgvector
image draws from). This gives us PostGIS 3.4.x and pgvector 0.8.x in a
single Postgres 16 image. Trade-off: a one-time local build versus a
pre-built pull. Worth it; the dependency story is clearer this way
because we control exactly what's installed.

## Revisit if

- Vector count exceeds 10M, or query latency exceeds 200ms p95 at scale.
- An accreditor specifically blocks one of the extensions.
