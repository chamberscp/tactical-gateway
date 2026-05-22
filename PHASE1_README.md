# Phase 1 — CoT Capture, Normalize, Translate, Forward

This zip is an **overlay** on your existing Phase 0 repo. Extract it into
the root of `tactical-gateway/` and it will add/replace files as
appropriate. Nothing in Phase 0 is deleted; a few files are overwritten
(see the list below).

## What's new

### Library additions
- `libs/cto_schema/src/cto_schema/uuid7.py` — time-ordered UUID v7 generation
- `libs/cto_schema/src/cto_schema/hashchain.py` — tamper-evident hash chain
- `libs/cto_schema/src/cto_schema/__init__.py` — re-exports the new modules
- `libs/cto_schema/tests/test_utilities.py` — tests for the above
- `libs/common/` — new shared library: settings (pydantic-settings) and structured logging (structlog)

### Gateway service (Phase 1 core)
- `services/gateway/capture.py` — raw bytes → MinIO + per-day hash chain manifest
- `services/gateway/normalizers/cot_xml.py` — CoT XML ⇄ CTO
- `services/gateway/normalizers/cotevent.proto` — TAK protocol protobuf schema
- `services/gateway/normalizers/cot_pb.py` — CoT protobuf ⇄ CTO + TAK wire framing
- `services/gateway/normalizers/__init__.py`
- `services/gateway/listeners.py` — TCP and UDP listeners for both protocols
- `services/gateway/routes_model.py` — YAML route config Pydantic models
- `services/gateway/route_engine.py` — match + dispatch + TCP/UDP egress senders
- `services/gateway/nats_publisher.py` — thin NATS wrapper
- `services/gateway/main.py` — **OVERWRITES** Phase 0 main; wires everything together

### Tests
- `services/gateway/tests/__init__.py`
- `services/gateway/tests/test_cot_xml.py` — round-trip tests for XML
- `services/gateway/tests/test_cot_pb_framing.py` — TAK wire-framing tests
- `services/gateway/tests/test_route_matching.py` — route match logic
- `services/gateway/tests/test_e2e.py` — full pipeline with fake MinIO + real TCP

### Tools
- `tools/cot_generator.py` — simulates moving tracks, sends CoT to a host:port
- `tools/tcp_listener.py` — listens on a TCP port and prints what arrives
- `tools/__init__.py`

### Deployment
- `deploy/docker/Dockerfile.gateway` — **OVERWRITES** Phase 0; adds protoc step, installs `common` lib
- `deploy/docker/compose.dev.yml` — **OVERWRITES** Phase 0; exposes 8087/8089 listener ports
- `deploy/routes.example.yaml` — new sample routes config

### Workspace
- `pyproject.toml` — **OVERWRITES** Phase 0; adds `common` to pytest path
- `make.ps1` — **OVERWRITES** Phase 0; new targets: `protoc`, `listen`, `generate`, `send-one`, `rebuild`

## Quickstart after extracting

```powershell
# 1. Regenerate protobuf bindings locally (so tests can run without docker)
.\make.ps1 protoc

# 2. Install the new common library locally
python -m pip install -e libs/common

# 3. Run the tests (no docker needed)
.\make.ps1 test

# 4. Rebuild and start the stack
.\make.ps1 rebuild
.\make.ps1 up
.\make.ps1 ps

# 5. End-to-end smoke: listener in one terminal, generator in another
.\make.ps1 listen 9999 xml
# (in another terminal)
.\make.ps1 send-one
# You should see the message appear in the listener window.

# 6. Load test (validates 1000+ msg/sec target)
.\make.ps1 generate --tracks 100 --rate 1000 --duration 30
```

## What the gateway accepts after this phase

| Protocol     | Transport | Default port | Listener env var       |
|--------------|-----------|--------------|------------------------|
| CoT XML      | TCP       | 8087         | `COT_XML_TCP_PORT`     |
| CoT XML      | UDP       | off          | `COT_XML_UDP_PORT`     |
| CoT Protobuf | TCP       | 8089         | `COT_PB_TCP_PORT`      |

Set port to `0` to disable a listener. For UDP multicast, also set
`COT_XML_UDP_GROUP` (e.g. `239.2.3.1`).

## What it forwards to

Driven by `/etc/gateway/routes.yaml` inside the container (a default is
baked in from `deploy/routes.example.yaml`). Routes match by source
system glob, source protocol, or object class; destinations are TCP or
UDP, with output format `cot_xml` or `cot_protobuf`.

Toggle routes at runtime:

```
POST /routes/{route_id}/enable
POST /routes/{route_id}/disable
GET  /routes
GET  /listeners
```
