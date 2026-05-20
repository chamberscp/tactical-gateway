"""Common Tactical Object schema — the canonical normalized data model."""

from cto_schema.models import (
    CTO,
    Affiliation,
    Altitude,
    AltitudeSource,
    BattleDimension,
    Classification,
    Geometry,
    Kinematics,
    ObjectClass,
    ProvenanceEntry,
    RawPointer,
    SourceProtocol,
    Symbology,
)

__all__ = [
    "CTO",
    "Affiliation",
    "Altitude",
    "AltitudeSource",
    "BattleDimension",
    "Classification",
    "Geometry",
    "Kinematics",
    "ObjectClass",
    "ProvenanceEntry",
    "RawPointer",
    "SourceProtocol",
    "Symbology",
]

__version__ = "0.1.0"
