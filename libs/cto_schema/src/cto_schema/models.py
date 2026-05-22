"""Common Tactical Object schema - canonical normalized data model.

The CTO is the single internal representation of any tactical object the
gateway handles. All ingest paths convert source-format messages into CTOs;
all egress paths convert CTOs back into target-format messages. This gives
us N+M converters instead of N*M direct translations.

Design principles:
- Strongly typed. Pydantic v2 with strict validation.
- Timezone-aware datetimes always. Validators reject naive timestamps.
- Lossless ingest. Source-specific detail that doesn't fit the typed fields
  lives in the `attributes` dict so re-emit can be byte-faithful where the
  target format allows.
- Provenance tracking. Every transformation appends a ProvenanceEntry so
  audit can reconstruct what the gateway did to each object.

Phase 2a additions:
- ObjectClass.GRAPHIC for operational graphics ingested from KMZ
- IngestSource enum recording how the CTO entered the gateway
- LineString and Polygon geometries now first-class
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SourceProtocol(str, Enum):
    """The wire protocol that delivered the source bytes to the gateway."""
    COT_XML = "cot_xml"
    COT_PROTOBUF = "cot_protobuf"
    KMZ = "kmz"
    OVL = "ovl"             # reserved for Phase 2b
    OTH_GOLD = "oth_gold"   # reserved
    USMTF = "usmtf"         # reserved
    OTHER = "other"


class IngestSource(str, Enum):
    """How the CTO entered the gateway. Distinct from SourceProtocol:
    SourceProtocol = the wire format; IngestSource = the delivery channel."""
    FOLDER = "folder"   # picked up by a folder watcher
    UPLOAD = "upload"   # POSTed to an /ingest/* endpoint
    STREAM = "stream"   # received on a streaming network listener
    OTHER = "other"


class ObjectClass(str, Enum):
    """Coarse category of what the CTO represents."""
    TRACK = "track"        # live moving entity (vehicle, person, aircraft)
    AREA = "area"          # purely geographic region
    ROUTE = "route"        # planned path
    POINT = "point"        # static labeled point
    GRAPHIC = "graphic"    # operational graphic from a planner overlay


class Affiliation(str, Enum):
    """Standard mil-std-2525 affiliations."""
    FRIEND = "friend"
    HOSTILE = "hostile"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"
    PENDING = "pending"
    SUSPECT = "suspect"
    ASSUMED_FRIEND = "assumed_friend"


class BattleDimension(str, Enum):
    """Standard mil-std-2525 battle dimensions."""
    LAND = "land"
    AIR = "air"
    SEA_SURFACE = "sea_surface"
    SEA_SUBSURFACE = "sea_subsurface"
    SPACE = "space"
    SOF = "sof"
    UNKNOWN = "unknown"


class AltitudeSource(str, Enum):
    GPS = "gps"
    BAROMETRIC = "barometric"
    DERIVED = "derived"
    UNKNOWN = "unknown"


class Classification(str, Enum):
    UNCLASSIFIED = "unclassified"
    CUI = "cui"
    CONFIDENTIAL = "confidential"
    SECRET = "secret"
    TOP_SECRET = "top_secret"


# ---------------------------------------------------------------------------
# Composite types
# ---------------------------------------------------------------------------


class Geometry(BaseModel):
    """GeoJSON-shaped geometry. Coordinates are [lon, lat] or arrays thereof.

    Phase 2a supports:
    - Point: [lon, lat]
    - LineString: [[lon, lat], [lon, lat], ...]
    - Polygon: [[[lon, lat], ...], ...]  (outer ring + optional holes)

    The CTO stores this for self-description; the operational store layer
    converts to native PostGIS geometry for indexed spatial queries.
    """
    model_config = ConfigDict(extra="forbid")

    type: Literal["Point", "LineString", "Polygon"]
    coordinates: list

    @field_validator("coordinates")
    @classmethod
    def validate_coordinates(cls, v, info):
        gtype = info.data.get("type")
        if gtype == "Point":
            if not (isinstance(v, list) and len(v) >= 2 and all(isinstance(c, (int, float)) for c in v[:2])):
                raise ValueError("Point coordinates must be [lon, lat]")
        elif gtype == "LineString":
            if not (isinstance(v, list) and len(v) >= 2 and all(isinstance(p, list) and len(p) >= 2 for p in v)):
                raise ValueError("LineString coordinates must be a list of [lon, lat] pairs")
        elif gtype == "Polygon":
            if not (isinstance(v, list) and len(v) >= 1 and all(isinstance(ring, list) for ring in v)):
                raise ValueError("Polygon coordinates must be a list of rings")
            for ring in v:
                if len(ring) < 4:
                    raise ValueError("Polygon ring must have >= 4 points (first == last)")
        return v


class Altitude(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value_m: float
    source: AltitudeSource = AltitudeSource.UNKNOWN
    accuracy_m: float | None = None


class Kinematics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    course_deg: float | None = None
    speed_mps: float | None = None
    vertical_rate_mps: float | None = None


class Symbology(BaseModel):
    """Symbol-system-specific identifiers. Lossless storage of what we know."""
    model_config = ConfigDict(extra="forbid")

    cot_type: str | None = None
    sidc_2525c: str | None = None   # 15-char mil-std-2525C code (Phase 2b)
    affiliation: Affiliation | None = None
    battle_dimension: BattleDimension | None = None


class RawPointer(BaseModel):
    """Pointer to the raw source bytes captured at ingest."""
    model_config = ConfigDict(extra="forbid")

    sha256: str = Field(min_length=64, max_length=64)
    object_key: str
    size_bytes: int = Field(ge=0)
    captured_at: datetime


class ProvenanceEntry(BaseModel):
    """One step in the chain of operations applied to this CTO."""
    model_config = ConfigDict(extra="forbid")

    step: str
    actor: str
    at: datetime
    notes: str | None = None
    lossy_fields: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# CTO root
# ---------------------------------------------------------------------------


class CTO(BaseModel):
    """The Common Tactical Object. Single canonical form for all messages."""
    model_config = ConfigDict(extra="forbid")

    # Identity
    uid: UUID                       # gateway-assigned UUID v7
    source_uid: str | None = None   # uid from the source system, if any
    source_system: str              # protocol_label:peer_ip:peer_port, etc.
    source_protocol: SourceProtocol
    ingest_source: IngestSource = IngestSource.STREAM  # Phase 2a default for backcompat

    # Time
    received_at: datetime
    event_time: datetime
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    # Classification
    classification: Classification = Classification.UNCLASSIFIED

    # Type and symbology
    object_class: ObjectClass = ObjectClass.TRACK
    symbology: Symbology = Field(default_factory=Symbology)

    # Spatial
    geometry: Geometry
    altitude: Altitude | None = None
    kinematics: Kinematics | None = None

    # Human-readable identification
    callsign: str | None = None
    label: str | None = None        # display label for graphics (e.g. "PL ALPHA")
    remarks: str | None = None

    # Escape hatch
    attributes: dict[str, Any] = Field(default_factory=dict)

    # Lineage
    raw_pointer: RawPointer | None = None
    provenance: list[ProvenanceEntry] = Field(default_factory=list)

    @field_validator("received_at", "event_time", "valid_from", "valid_to")
    @classmethod
    def must_be_timezone_aware(cls, v):
        if v is None:
            return v
        if v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        return v
