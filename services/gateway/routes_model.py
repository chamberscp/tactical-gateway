"""Route configuration model.

Routes describe which CTOs go where. A route matches CTOs by source
(or source pattern) and emits them in a target format to a destination.

Example YAML:

    routes:
      - id: tak-to-debug-tcp
        enabled: true
        match:
          source_system_glob: "cot-xml-tcp:*"
        destination:
          kind: tcp
          host: 127.0.0.1
          port: 9999
          format: cot_xml

      - id: pb-to-listener
        enabled: true
        match:
          source_protocol: cot_protobuf
        destination:
          kind: tcp
          host: 127.0.0.1
          port: 9998
          format: cot_xml

Matching is permissive: any unset match field is treated as "match all".
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class DestinationKind(str, Enum):
    TCP = "tcp"
    UDP = "udp"


class OutputFormat(str, Enum):
    COT_XML = "cot_xml"
    COT_PROTOBUF = "cot_protobuf"


class RouteMatch(BaseModel):
    """Conditions to match a CTO against. Any unset field is wildcard."""

    model_config = ConfigDict(extra="forbid")

    source_system_glob: str | None = None
    source_protocol: str | None = None  # SourceProtocol value as string
    object_class: str | None = None     # ObjectClass value as string


class Destination(BaseModel):
    """Where to send the translated message."""

    model_config = ConfigDict(extra="forbid")

    kind: DestinationKind
    host: str
    port: int = Field(ge=1, le=65535)
    format: OutputFormat


class Route(BaseModel):
    """A single source-to-destination forwarding rule."""

    model_config = ConfigDict(extra="forbid")

    id: str
    enabled: bool = True
    match: RouteMatch = Field(default_factory=RouteMatch)
    destination: Destination


class RoutesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    routes: list[Route] = Field(default_factory=list)


def load_routes(path: str) -> RoutesConfig:
    """Load routes from a YAML file. Returns empty config if file missing."""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return RoutesConfig(routes=[])
    return RoutesConfig.model_validate(data)
