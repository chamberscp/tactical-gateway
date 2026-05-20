# Tactical Gateway

A locally hosted server that sits between tactical data sources (TAK,
GCCS-J) and downstream consumers (MSS, analyst tools), capturing,
normalizing, translating, and storing tactical data for plain-language
search via a local RAG pipeline.

Target deployment: IATT now, eventual ATO at IL6. Air-gapped, edge
hardware.

## Status

Phase 0 — infrastructure wiring. The directory layout, the canonical
data schema, and the docker-compose dev stack are in place. Real
protocol parsing, translation, control GUI, and RAG land in subsequent
phases per the [build plan](docs/build-plan.md).

## Components

```
                          +----------------------+
   CoT, OTH-Gold ──────►  |   Ingest Listeners   |
   KML/KMZ uploads        | (per-protocol async) |
                          +----------+-----------+
                                     | raw bytes + sha256
                                     ▼
                          +----------------------+
                          | Capture Store        |
                          | (MinIO + hash chain) |
                          +----------+-----------+
                                     |
                                     ▼
                          +----------------------+
                          | Normalizer           |───► CTO published on NATS
                          | (per-protocol -> CTO)|
                          +----------+-----------+
                                     |
                ┌────────────────────┼────────────────────┐
                ▼                    ▼                    ▼
   +---------------------+  +-----------------+  +------------------+
   | Operational Store   |  | Translator      |  | RAG Indexer      |
   | (PG + PostGIS +     |  | (CTO -> any     |  | (docs, video,    |
   |  pgvector)          |  |  output proto)  |  |  images)         |
   +----------+----------+  +--------+--------+  +--------+---------+
              │                      │                    │
              ▼                      ▼                    ▼
        Audit log         TAK / GCCS-J / MSS         Embeddings
        Tracks/CTOs       (configured per-route)     into pgvector
        Documents/chunks
                                     ▲
                                     │
                          +----------+-----------+
                          | Control GUI          |
                          | (pipes & valves)     |
                          +----------------------+
```

See [docs/adr/](docs/adr/) for architecture decision records and
[docs/build-plan.md](docs/build-plan.md) for the phased plan.

## Quickstart (development)

Requirements: Docker (or Podman with docker-compose compatibility),
Python 3.11+.

```bash
# Bring up the dev stack
make up

# Run database migrations
make migrate

# Check everything is healthy
curl http://localhost:8000/health
curl http://localhost:8000/ready

# Run tests
make test
```

Service URLs in dev:

| Service       | URL                              |
|---------------|----------------------------------|
| Gateway API   | http://localhost:8000            |
| MinIO console | http://localhost:9001            |
| Postgres      | postgresql://localhost:5432      |
| NATS monitor  | http://localhost:8222            |

Default credentials for dev only: `gateway` / `gateway` for Postgres,
`gateway` / `gateway-dev-password` for MinIO. These exist to make local
development frictionless and **must be replaced** before any deployment.

## Repository layout

```
tactical-gateway/
├── libs/
│   ├── cto_schema/      Canonical data schema (Pydantic + SQLAlchemy)
│   └── common/          Shared utilities (logging, config, audit)
├── services/
│   ├── gateway/         Ingest listeners + HTTP control plane
│   ├── normalizer/      Per-protocol normalizers
│   ├── translator/      CTO → output protocol converters
│   ├── rag/             Document ingest + query API
│   └── control_gui/     Pipes-and-valves web UI
├── deploy/
│   ├── docker/          Dockerfiles and compose
│   ├── postgres/        DB init SQL
│   └── migrations/      Alembic migrations
├── docs/
│   ├── adr/             Architecture decision records
│   └── build-plan.md    Phased implementation plan
├── scripts/             One-off operational scripts
├── tests/
│   └── integration/     End-to-end tests (require dev stack)
└── Makefile             Common dev commands
```

## Security posture (current)

This is a draft list — expanded as we approach IATT.

- Local-only by default. All compose ports bind to `127.0.0.1`.
- Default credentials are flagged in [docs/security.md](docs/security.md).
- Raw ingest data is SHA-256 hashed and chained for tamper evidence.
- Audit table exists from day one; events are sparse for now and
  enriched in later phases.
- No external network access is required for runtime; container images
  must be sourced from an approved mirror for non-dev deployment.

## License

Internal — distribution restricted. Third-party components retain their
upstream licenses; see [docs/licenses.md](docs/licenses.md) (to be
generated in Phase 5 as part of the ATO package).
