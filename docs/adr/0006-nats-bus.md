# ADR-0006: NATS JetStream as the internal message bus

**Status:** Accepted
**Date:** 2026-05-20

## Context

Components (ingest listener, normalizer, translator, RAG ingester) need
to communicate asynchronously and decouple their lifecycles. We want
durable delivery so a normalizer restart doesn't drop messages.

## Decision

Use NATS 2.10 with JetStream enabled. Single Go binary, MIT licensed,
small audit surface. Subjects are namespaced by purpose:

- `ingest.raw.{protocol}` — raw bytes published by listeners
- `cto.normalized.{class}` — CTOs published by the normalizer
- `egress.{destination}` — CTOs routed for outbound translation
- `audit.events` — structured audit events

JetStream provides at-least-once delivery with persistence to disk.

## Alternatives considered

- **Redis Streams.** Familiar, simple, but mixing cache and bus on the
  same service is an audit smell.
- **Kafka.** Industry standard but heavy: Zookeeper or KRaft to manage,
  JVM footprint, more accreditation work. Overkill for one edge box.
- **RabbitMQ.** Mature but Erlang/OTP runtime adds an unfamiliar
  language to the audit surface.
- **In-process queues only.** Couples services together and complicates
  Phase-3 distribution across machines.

## Consequences

**Positive:**
- Single static Go binary. Small attack surface.
- JetStream's at-least-once delivery handles restarts cleanly.
- Built-in monitoring HTTP endpoint for health checks.

**Negative:**
- Less ecosystem tooling than Kafka.
- At-least-once delivery means consumers must be idempotent. We design
  for this from day one (CTO UIDs are stable).

## Revisit if

- We need multi-region replication or exactly-once semantics.
- An accreditor specifically prefers a different bus.
