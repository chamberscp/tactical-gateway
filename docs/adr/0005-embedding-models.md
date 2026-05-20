# ADR-0005: BGE-large-en-v1.5 for text, SigLIP-large for images

**Status:** Accepted
**Date:** 2026-05-20

## Context

We need two embedding pipelines: one for text (OPORDs, FRAGOs, SOPs,
manuals) and one for visual content (photos, video keyframes).

## Decision

- **Text:** BAAI BGE-large-en-v1.5, 1024-dimensional embeddings, MIT
  license. Run via `sentence-transformers`.
- **Images:** Google SigLIP large (`google/siglip-large-patch16-384`),
  Apache 2.0. Run via `open_clip` or `transformers`.

Storage column in pgvector is fixed at 1024 dimensions for text. Image
embeddings go to a separate column (SigLIP-large is 1024-D as well, but
we keep them in distinct columns to avoid mixing modalities in a single
ANN search).

## Alternatives considered for text

- **E5-large-v2 (MIT, Microsoft).** Comparable quality. Requires
  `query:` / `passage:` prefixes; minor footgun.
- **Nomic-embed-text-v1.5 (Apache 2.0).** Matryoshka embeddings allow
  dimension reduction at query time. Slightly behind BGE on MTEB at
  full dimension. Useful if storage becomes a constraint.
- **Closed embeddings (OpenAI, Cohere).** Disqualified by local-only
  requirement.

## Alternatives considered for images

- **OpenAI CLIP (ViT-L/14).** Older, weaker on fine-grained tasks. MIT.
- **EVA-CLIP.** Strong but heavy, complex dependencies.
- **Multi-modal Llama / LLaVA models.** Solve image *understanding* but
  are too heavy for the per-frame indexing pass.

## Consequences

**Positive:**
- Both models run comfortably on the dev GPU (4070 Ti Super).
- Both have permissive licenses suitable for government use.
- BGE has strong English performance on technical/governmental prose,
  which is what OPORDs are.

**Negative:**
- 1024-D embeddings are larger than 768-D; ~33% more storage per chunk.
  Acceptable at our scale.
- Changing embedding models requires a migration (re-embed all chunks
  and rebuild the HNSW index). Worth doing once we have real query
  quality data.

## Revisit if

- BGE produces poor retrieval quality on military prose (we'll measure
  with a held-out eval set during Phase 4).
- A meaningfully better open model ships.
- Storage becomes a constraint and Nomic's Matryoshka feature becomes
  attractive.
