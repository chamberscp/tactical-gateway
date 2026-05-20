# Architecture Decision Records

Each ADR documents a significant architectural choice, what we considered,
why we picked what we did, and what we'd have to revisit if the choice
proved wrong. These exist for two audiences: the next engineer to touch
the system, and the ATO reviewer who needs to understand and validate
our security-relevant decisions.

## Index

- [ADR-0001: Use PostgreSQL with PostGIS and pgvector as the single operational store](0001-postgres-postgis-pgvector.md)
- [ADR-0002: Common Tactical Object (CTO) as the universal normalized schema](0002-cto-schema.md)
- [ADR-0003: Capture raw bytes before normalization with hash-chain integrity](0003-raw-capture-first.md)
- [ADR-0004: Llama 3.x family for local LLM inference](0004-local-llm-llama.md)
- [ADR-0005: BGE-large-en-v1.5 for text embeddings, SigLIP-large for images](0005-embedding-models.md)
- [ADR-0006: NATS JetStream as internal message bus](0006-nats-bus.md)
- [ADR-0007: Python primary, Rust only on demonstrated hotspots](0007-python-first.md)

## Status legend

- **Proposed** — under discussion, not yet committed to.
- **Accepted** — chosen and being implemented.
- **Deprecated** — superseded; see successor ADR.
- **Rejected** — considered but not chosen; kept for the record.
