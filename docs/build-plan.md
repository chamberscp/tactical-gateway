# Build Plan

The system is built in six phases. Each phase has a concrete deliverable
that you can exercise on your local machine before moving on. No phase
is "done" until its deliverable is tested.

## Phase 0 — Foundation (current)

**Deliverable:** `docker compose up` brings up Postgres+PostGIS+pgvector,
MinIO, NATS, and a placeholder gateway. All health endpoints return 200.
`alembic upgrade head` creates the full schema.

**Status:** code in place. Ready for you to bring up locally and verify.

## Phase 1 — CoT capture and pass-through

**Scope:**
- CoT XML listener (TCP and UDP multicast).
- CoT protobuf listener (TAK protocol).
- Raw bytes → MinIO with hash chain.
- CoT XML → CTO normalizer.
- CoT protobuf → CTO normalizer.
- CTO → CoT XML translator.
- CTO → CoT protobuf translator.
- End-to-end pipeline: TAK sends CoT in, gateway captures, normalizes,
  and forwards CoT-PB to a downstream consumer (MSS stand-in for now).

**Deliverables:**
- Receive a CoT message from your local TAK server, see it captured in
  MinIO and stored as a CTO in Postgres.
- Configure a route, see CoT-PB emitted on the other side.
- Throughput test: sustained ingest at 1,000 msgs/sec on dev hardware.

**Risks called out earlier:** MSS-specific protobuf quirks must be
validated against a real instance once SIPR access is available. We
build to the published TAK protocol spec for now.

## Phase 2 — OTH-Gold and KMZ

**Scope:**
- OTH-Gold listener and parser (GCCS-J variant).
- OTH-Gold ↔ CTO conversion.
- KMZ/KML parser (reads files and HTTP uploads).
- CTO → KMZ generator.
- Overlay round-trip: KMZ from Google Earth → CTO → KMZ → re-imported
  to Google Earth, fidelity report shows what was preserved.

**Deliverables:**
- Upload a KMZ via the control GUI, see its features as CTOs.
- Export a set of CTOs (matching a query) as a KMZ download.

**Risk:** without sample OTH-Gold messages from your environment, the
parser is built to the published spec and will need validation on SIPR.
We isolate parser code so swapping in a corrected version is a small
change.

## Phase 3 — Control GUI

**Scope:**
- Web UI (FastAPI + HTMX + Alpine + Leaflet for any map preview).
- Sources on the left, destinations on the top — TRAX-style.
- Click a cell to configure that route (IP, port, output protocol).
- Per-route on/off toggle.
- Live status: messages/sec per route, last seen, error count.
- Source and destination CRUD.

**Deliverable:** the GUI you described, controlling actual flows from
Phase 1 and Phase 2.

## Phase 4 — Document RAG

**Scope:**
- Document upload (PDF, Word, plain text).
- OCR for scanned PDFs (Tesseract via ocrmypdf).
- Video ingest: extract audio (ffmpeg), transcribe (faster-whisper),
  sample keyframes at scene changes (PySceneDetect), embed with SigLIP.
- Image ingest: SigLIP embeddings, optional OCR for text-in-image.
- Chunking with overlap, BGE-large embeddings, pgvector storage.
- Hybrid retrieval: pgvector cosine + Postgres full-text BM25.
- Cross-encoder reranking (BGE-reranker-large).
- Llama 3.1 8B (dev) for answer synthesis with citation prompts.
- Search UI with citation rendering (click a citation → jump to source).

**Deliverable:** upload an OPORD, ask "what is the commander's intent
for phase II?", get a cited answer with links back to source pages.

**Performance target:** sub-3-second answer latency for typical queries
on dev hardware.

## Phase 5 — Hardening for IATT

**Scope:**
- STIG compliance on the RHEL host.
- FIPS 140-3 crypto (Postgres, MinIO, NATS configured with FIPS-mode
  OpenSSL where supported; document gaps where not).
- Keycloak with local accounts; PIV/CAC integration path documented.
- Audit logging expanded to cover all sensitive actions.
- Backup and restore procedures.
- Documentation package: SSP, POA&M template, dependency inventory with
  SBOM (CycloneDX), license inventory.
- Performance testing at 100 concurrent users.

**Deliverable:** IATT submission package.

## What is explicitly NOT in scope

- **OVL parsing** (FalconView or MCS) — deferred at your direction.
- **Link-16, AIS, ADS-B direct ingest** — pulled via GCCS-J instead.
- **Cross-domain solution integration** — future phase post-ATO.
- **Multi-classification ABAC** — single classification level per
  deployment for now.
