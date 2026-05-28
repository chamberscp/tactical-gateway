# Build Roadmap

The system is built in phases. Each phase has a concrete, testable deliverable.
This file supersedes the original 6-phase plan: **OTH-Gold is now its own
phase** rather than being bundled into the old "Phase 2", reflecting that the
Agile track schema is a substantial field dictionary in its own right.

| Phase | Name | Status |
| --- | --- | --- |
| 0 | Foundation | ✅ complete |
| 1 | CoT capture & pass-through | ✅ complete (`phase1`) |
| 2a | Operational store + KMZ ingest | ✅ complete (`phase2a`) |
| 2b-1 | OVL ingest (OVL → CTO) | ▶ scoped, next to build |
| 2b-2 | OVL egress (CTO → OVL) | ⬚ scoped (deferred half) |
| 3 | OTH-Gold (track exchange) | ⬚ **new phase — scoping next** |
| 4 | Control GUI | ⬚ planned |
| 5 | Document RAG | ⬚ planned |
| 6 | IATT / ATO hardening | ⬚ planned |

> Phase numbers after OTH-Gold shifted by one versus the original plan
> (old Phase 3 GUI → now Phase 4, etc.). Tags already cut (`phase1`,
> `phase2a`) are unaffected.

---

## ✅ Phase 0 — Foundation
Repo layout, CTO schema (Pydantic + SQLAlchemy), docker-compose dev stack
(Postgres+PostGIS+pgvector, MinIO, NATS), Alembic migrations, audit_log,
health/readiness endpoints.

## ✅ Phase 1 — CoT capture & pass-through  (`phase1`)
CoT XML + protobuf listeners, raw capture to MinIO with SHA-256 hash chain,
CoT↔CTO normalizers and translators, YAML-driven route engine, CoT traffic
generator, integration tests.

## ✅ Phase 2a — Operational store + KMZ ingest  (`phase2a`)
`tg-opstore` service (supersession-aware writer), KMZ parser (Point/Line/Poly),
folder watcher + `POST /ingest/kmz` (409 + force), doctrinal name recognition,
spatial query API (`GET /cto?within=<bbox>`), ADRs 0008–0010.

## ▶ Phase 2b-1 — OVL ingest (OVL → CTO)  *(next)*
Parse real GCCS-J `<MODEL>/<milbobject>` OVL into graphic CTOs. SIDC-derived
affiliation, verbatim 2525 modifier preservation, geometry inference (SIDC wins
on conflict), `.ovl` capture + watcher, ADR-0011.
**Scope:** `docs/phase2b1-scope.md`. Validated against two real planner OVLs.

## ⬚ Phase 2b-2 — OVL egress (CTO → OVL)
`CTO → OVL` emitter (MODEL/milbobject form), route destination type
`ovl_file`, symbol coverage table (full / best-effort / unsupported with
B130836 citations), full KMZ → CTO → OVL → CTO → KMZ fidelity test.

## ⬚ Phase 3 — OTH-Gold (track exchange)  *(new, scoping next)*
GCCS-J track-exchange format. The **Agile Client track attribute schema**
(`docs/ref-agile-track-schema.md`) is the target field dictionary — ~50 of its
60 fields map to OTH-Gold set/series fields.

Scope outline (to be detailed in its own scope doc):
- OTH-Gold listener + parser (GCCS-J variant).
- OTH-Gold ↔ CTO conversion for the `track` object class.
- Coordinate normalizer handling packed DMS (`334631N 0784849W`) and decimal.
- Track-side affiliation from `Threat` / `Flag` / `Category` (vs graphics'
  SIDC char-2) — unifying the CTO `affiliation` attribute across object classes.
- Verbatim preservation of track fields (IFF modes, sensor, LTN, SCONUM, IRCS,
  Track Type Real-World/Simulation) in `CTO.attributes`.
- Enriches the Phase 1 CoT track CTO with the same attribute set.
**Risk:** without OTH-Gold samples from the environment, the parser is built to
the published spec + this Agile schema and validated on SIPR.

## ⬚ Phase 4 — Control GUI
TRAX-style pipes-and-valves panel, live map of current CTOs (tracks + graphics),
KMZ/OVL upload UX, route enable/disable, per-route status.

## ⬚ Phase 5 — Document RAG
PDF/Word/text ingest, OCR via ocrmypdf, chunking + embeddings into pgvector,
query API. All inference local (air-gapped).

## ⬚ Phase 6 — IATT / ATO hardening
FIPS 140-3 configured crypto, SBOM (syft / CycloneDX), STIG pass, authn on
upload endpoints, audit enrichment, ATO package + ADRs.
