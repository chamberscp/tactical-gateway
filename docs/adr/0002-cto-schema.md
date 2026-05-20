# ADR-0002: Common Tactical Object (CTO) as the universal normalized schema

**Status:** Accepted
**Date:** 2026-05-20

## Context

The gateway ingests data in multiple protocols (CoT XML, CoT protobuf,
OTH-Gold, KML/KMZ) and must emit data in those same formats plus others.
With N input protocols and M output protocols, naive direct translation
requires N × M converters. We need a hub-and-spoke design with a single
canonical representation in the middle.

## Decision

Define the **Common Tactical Object (CTO)**, a Pydantic-modeled,
JSON-serializable record that is a superset of the fields needed by any
supported protocol. Every input is normalized to a CTO; every output is
generated from a CTO. Conversion count is therefore N + M.

The CTO carries:

- Identity (UID, source UID, source system, source protocol)
- Temporal data (received, event, validity window) — always TZ-aware
- Classification and caveats
- Object class (track, point, area, route, symbol, overlay, annotation)
- Geometry (GeoJSON-compatible, WGS84)
- Altitude with provenance
- Kinematics (course, heading, speed, vertical rate)
- Symbology (SIDC 2525D, CoT type, affiliation, battle dimension)
- Human-readable fields (callsign, label, remarks)
- Open `attributes` dict for protocol-specific fields
- Raw pointer (sha256 + object key) back to the original bytes
- Provenance chain of transformations

## Alternatives considered

- **NIEM tactical IEPDs.** Too heavy for our use case. Useful as a
  reference for field naming.
- **Use CoT as the internal format.** CoT can't cleanly represent
  KML-style styled overlays, OTH-Gold's quality/source metadata, or
  arbitrary annotations. Lossy.
- **TAK protobuf as the internal format.** Same issues plus tighter
  coupling to one vendor's evolution.
- **Roll our own without an escape hatch.** Risks data loss whenever an
  input protocol carries a field we didn't anticipate.

## Consequences

**Positive:**
- N + M converters, not N × M.
- Open `attributes` field guarantees ingest is lossless even when our
  typed schema lags behind reality. Information loss only happens at
  egress, when the target can't represent something — and we log it.
- Raw pointer means we can always re-run normalization against an
  improved schema without re-acquiring the data.

**Negative:**
- Maintaining a superset schema requires discipline. New protocols may
  push fields into `attributes` first, then earn typed status later.
- Validation rules in Pydantic must be evolved carefully — too strict
  rejects real data, too loose accepts garbage.

## Revisit if

- A new input protocol carries fundamentally different semantics (e.g.
  full STANAG 4609 motion imagery metadata) that doesn't fit the
  geo-object model.
