#!/usr/bin/env bash
# Phase 0 smoke test. Verifies the dev stack is up and reachable.
# Exits 0 on success, non-zero on first failure.
#
# Usage:  ./scripts/smoke.sh

set -euo pipefail

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

# Gateway health
echo "Checking gateway /health..."
resp=$(curl -fsS --max-time 5 http://localhost:8000/health) \
  || fail "gateway /health did not return 200"
echo "$resp" | grep -q '"status":"ok"' \
  || fail "gateway /health returned non-ok status: $resp"
pass "gateway /health"

# Gateway readiness (requires migrations applied)
echo "Checking gateway /ready..."
if curl -fsS --max-time 5 http://localhost:8000/ready >/dev/null 2>&1; then
  pass "gateway /ready"
else
  echo "WARN: /ready not yet ready — did you run 'make migrate'?"
fi

# MinIO console
echo "Checking MinIO..."
curl -fsS --max-time 5 http://localhost:9000/minio/health/live >/dev/null \
  || fail "MinIO not reachable"
pass "MinIO health"

# NATS monitor
echo "Checking NATS..."
curl -fsS --max-time 5 http://localhost:8222/healthz >/dev/null \
  || fail "NATS not reachable"
pass "NATS health"

# Postgres — through the gateway is good enough; direct check needs psql
echo "Checking Postgres via gateway..."
echo "$resp" | grep -q '"postgres":{"ok":true}' \
  || fail "gateway reports postgres unhealthy"
pass "Postgres (via gateway)"

echo
echo "All smoke checks passed."
