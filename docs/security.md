# Security Notes

This is a running list of security items. It will grow into the SSP
during Phase 5. The goal here is to be honest about what is and isn't
hardened, so nothing is deployed to a sensitive environment by accident.

## Dev defaults that MUST be replaced before non-dev deployment

| Item | Current | Required for IATT |
|---|---|---|
| Postgres credentials | `gateway` / `gateway` | Generated, stored in approved secret store |
| MinIO root credentials | `gateway` / `gateway-dev-password` | Generated; service accounts with least privilege |
| MinIO TLS | Off | TLS terminated at MinIO or reverse proxy |
| Postgres TLS | Off | TLS with cert validation |
| NATS auth | None | Token or mTLS |
| Gateway HTTP TLS | Off (uvicorn HTTP) | TLS via reverse proxy (nginx/Caddy/HAProxy) |
| Container ports | Bound to `127.0.0.1` | Bound only to internal interfaces; egress firewalled |
| Container images | `:latest` and floating tags | Pinned by sha256 digest, from approved mirror |
| Image provenance | Public registries | Approved internal mirror, signature-verified |

## Audit posture

- Audit log table exists from Phase 0.
- Phase 1-2 will populate the table for ingest and route changes.
- Phase 4 will populate it for RAG queries and document access.
- Phase 5 will add hash chaining to the audit table itself, plus
  forwarding to host auditd / syslog.

## Authentication

- Phase 0-4: no authentication on the control plane. Run only on
  trusted dev machines.
- Phase 5: Keycloak with local accounts initially. PIV/CAC integration
  via PKCS#11 documented but not implemented until requested.

## Data handling

- All raw captures are SHA-256 hashed at ingest.
- A daily hash chain links each day's captures (Phase 1 deliverable).
- Tracks and documents are stored with a `classification` field.
  Mixing classification levels in a single deployment is not supported
  for this version; a deployment is single-level.

## Known gaps to be tracked in POA&M (Phase 5)

- No HSM integration for key material.
- No automated dependency vulnerability scanning gate in CI yet.
- Container image scanning (Trivy / Grype) is not yet in CI.
- No formal threat model document. Will be produced during Phase 5.

## Reporting issues

Anything security-relevant goes to (TBD: distribution list / address)
and **not** to a public issue tracker.
