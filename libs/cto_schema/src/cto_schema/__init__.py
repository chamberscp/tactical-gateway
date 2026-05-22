"""Common Tactical Object schema - the canonical normalized data model."""

from cto_schema.hashchain import (
    GENESIS_PREV_HASH,
    ChainEntry,
    compute_entry_hash,
    make_entry,
    verify_chain,
)
from cto_schema.models import (
    CTO,
    Affiliation,
    Altitude,
    AltitudeSource,
    BattleDimension,
    Classification,
    Geometry,
    IngestSource,
    Kinematics,
    ObjectClass,
    ProvenanceEntry,
    RawPointer,
    SourceProtocol,
    Symbology,
)
from cto_schema.uuid7 import uuid7, uuid7_timestamp_ms

__all__ = [
    # Core models
    "CTO",
    "Affiliation",
    "Altitude",
    "AltitudeSource",
    "BattleDimension",
    "Classification",
    "Geometry",
    "IngestSource",
    "Kinematics",
    "ObjectClass",
    "ProvenanceEntry",
    "RawPointer",
    "SourceProtocol",
    "Symbology",
    # Hash chain
    "ChainEntry",
    "GENESIS_PREV_HASH",
    "compute_entry_hash",
    "make_entry",
    "verify_chain",
    # UUID v7
    "uuid7",
    "uuid7_timestamp_ms",
]

__version__ = "0.3.0"
