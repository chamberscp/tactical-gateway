"""NATS publishing helper.

A thin wrapper that hides the JetStream/non-JetStream distinction from
the gateway code. For Phase 1 we publish to plain core NATS subjects;
moving to JetStream for durability is a Phase 2+ task and only requires
changes here, not in the producers.
"""

from __future__ import annotations

from typing import Any

from nats.aio.client import Client as NATSClient

from common import get_logger

log = get_logger(__name__)


class NatsPublisher:
    """Owns a single NATS client and publishes CTO JSON to subjects."""

    def __init__(self, client: NATSClient) -> None:
        self._client = client

    async def publish_cto(self, cto: Any) -> None:
        """Publish a CTO as JSON to cto.normalized.<object_class>."""
        subject = f"cto.normalized.{cto.object_class.value}"
        payload = cto.model_dump_json().encode("utf-8")
        await self._client.publish(subject, payload)

    async def publish_audit(self, event: dict) -> None:
        """Publish an audit event. Subject: audit.events."""
        import json
        await self._client.publish("audit.events", json.dumps(event).encode("utf-8"))
