"""SIDC (Symbol Identification Code) parsing for MIL-STD-2525 tactical graphics.

This module decodes the 15-character SIDC carried in OVL <MIL_ID> elements into
the pieces the gateway cares about:

  * affiliation  -- from character 2 (index 1): F/H/N/U -> friend/hostile/...
  * geometry class -- point-graphic / linear / area, derived from the function
                      code (characters 5-10, index 4:10) via a lookup table
                      seeded from mil-sym-java conventions and the real planner
                      OVL fixtures.

Reference posture (see docs/adr/0011-ovl-symbology.md):
  mil-sym-java (missioncommand) is the authoritative reference for SIDC ->
  symbol/geometry-class mapping. This table is a curated Python re-implementation
  covering the working set observed in real fixtures; it is not a copy of any
  Java source. Unknown function codes degrade gracefully (see classify_geometry).

SIDC layout (MIL-STD-2525B, 15 chars), positions are 1-indexed in the standard
but 0-indexed here:
  pos 1  (idx 0)   : coding scheme       (G = tactical graphics)
  pos 2  (idx 1)   : affiliation         (F/H/N/U/...)
  pos 3  (idx 2)   : battle dimension     (G = ground, etc.)
  pos 4  (idx 3)   : status              (P = present, A = anticipated)
  pos 5-10 (idx 4:10): function ID        (the graphic type, e.g. GLB, GAA, OLT)
  pos 11-14        : symbol modifier / echelon
  pos 15           : country / order of battle
"""
from __future__ import annotations

from enum import Enum
from typing import Optional


class Affiliation(str, Enum):
    FRIEND = "friend"
    HOSTILE = "hostile"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"
    # 2525 also defines assumed-friend, suspect, pending, joker, faker, etc.
    # We collapse the rarely-seen ones into the four operational buckets below
    # but keep the raw character available via parse_affiliation_char().


# Character-2 (affiliation) mapping. MIL-STD-2525B standard identity values.
# We map the full set so nothing silently becomes "unknown" when it is actually
# a known-but-uncommon identity.
_AFFIL_CHAR = {
    "P": Affiliation.UNKNOWN,    # Pending
    "U": Affiliation.UNKNOWN,    # Unknown
    "A": Affiliation.FRIEND,     # Assumed Friend
    "F": Affiliation.FRIEND,     # Friend
    "N": Affiliation.NEUTRAL,    # Neutral
    "S": Affiliation.HOSTILE,    # Suspect
    "H": Affiliation.HOSTILE,    # Hostile
    "G": Affiliation.FRIEND,     # Exercise Pending (exercise variants)
    "W": Affiliation.FRIEND,     # Exercise Assumed Friend
    "D": Affiliation.FRIEND,     # Exercise Friend
    "L": Affiliation.NEUTRAL,    # Exercise Neutral
    "M": Affiliation.HOSTILE,    # Exercise Suspect (rare)
    "J": Affiliation.HOSTILE,    # Joker (friendly acting hostile, treat hostile)
    "K": Affiliation.HOSTILE,    # Faker
}


class GeometryClass(str, Enum):
    POINT = "point"      # point graphic (single-location symbol)
    LINEAR = "linear"    # line graphic (open polyline)
    AREA = "area"        # area graphic (closed polygon)


# Function-code -> geometry class.
#
# Keyed by the function ID (idx 4:10 of the SIDC), trailing dashes stripped for
# matching so "GAA---" and "GAA" both resolve. We match longest-known-prefix so
# specific codes win over family prefixes.
#
# Seeded from the function codes observed in the two real planner fixtures plus
# common mil-sym-java tactical-graphics families. Coverage is intentionally the
# B130836 working set, not all of 2525 (documented in ADR-0011).
_FUNCTION_GEOMETRY = {
    # --- Areas (closed polygons) ---
    "GAA": GeometryClass.AREA,    # General area
    "GAG": GeometryClass.AREA,    # Assembly area (kml2xml default area code)
    "OAF": GeometryClass.AREA,    # Obstacle free area
    "OAK": GeometryClass.AREA,    # Obstacle restricted area
    "OAR": GeometryClass.AREA,    # (obstacle area family)
    "SLA": GeometryClass.AREA,    # Strong point / area family
    "PY":  GeometryClass.POINT,   # Point-type per 2525; see conflict note below
    "NB":  GeometryClass.AREA,    # Nuclear/bio/chem area
    "NC":  GeometryClass.AREA,
    "OEB": GeometryClass.AREA,    # Obstacle effect block (area)
    "OFD": GeometryClass.AREA,    # Obstacle / fortified area
    "OFS": GeometryClass.POINT,   # Obstacle point feature
    "OGB": GeometryClass.AREA,
    "OGF": GeometryClass.AREA,
    "BCB": GeometryClass.AREA,    # Bypass / crossing block
    "BDE": GeometryClass.AREA,
    # --- Linear (open polylines) ---
    "GLB": GeometryClass.LINEAR,  # Boundary
    "GLC": GeometryClass.LINEAR,  # Line of contact / phase-line family
    "GLP": GeometryClass.LINEAR,  # Phase line
    "OLT": GeometryClass.LINEAR,  # Obstacle line (tank ditch etc.)
    "OLC": GeometryClass.LINEAR,
    "OLL": GeometryClass.LINEAR,
    "OLI": GeometryClass.LINEAR,
    "OLKGM": GeometryClass.LINEAR,
    "OLAA": GeometryClass.LINEAR,
    "OLAR": GeometryClass.LINEAR,
    "OLAV": GeometryClass.LINEAR,
    "OLAGS": GeometryClass.LINEAR,
    "OLAGM": GeometryClass.LINEAR,
    "OET": GeometryClass.LINEAR,  # Obstacle effect tilt/turn (linear)
    "OEF": GeometryClass.LINEAR,
    "OMT": GeometryClass.POINT,   # Obstacle point (mine, single location)
    "NDP": GeometryClass.POINT,
    "NEB": GeometryClass.POINT,
    "NEC": GeometryClass.POINT,
    # --- Points (single-location graphics) ---
    "GPRI": GeometryClass.POINT,  # Reference / point graphic (kml2xml default)
    "BCL": GeometryClass.LINEAR,
}


def parse_affiliation_char(sidc: str) -> str:
    """Return the raw affiliation character (SIDC position 2), or '' if absent."""
    return sidc[1] if len(sidc) > 1 else ""


def classify_affiliation(sidc: str) -> Affiliation:
    """Map SIDC character 2 to an Affiliation. Defaults to UNKNOWN."""
    return _AFFIL_CHAR.get(parse_affiliation_char(sidc).upper(), Affiliation.UNKNOWN)


def function_code(sidc: str) -> str:
    """Extract the function ID (SIDC positions 5-10) with trailing dashes stripped."""
    if len(sidc) < 5:
        return ""
    raw = sidc[4:10]
    return raw.replace("-", "")


def classify_geometry(sidc: str) -> Optional[GeometryClass]:
    """Return the SIDC-implied geometry class, or None if the code is unknown.

    Matches the longest known function-code prefix so specific codes (e.g.
    'OLKGM') win over family prefixes (e.g. 'OL'). Returns None when the code is
    not in the working set; callers fall back to vertex-count inference and flag
    the graphic for coverage-table review (see ADR-0011).
    """
    fc = function_code(sidc)
    if not fc:
        return None
    if fc in _FUNCTION_GEOMETRY:
        return _FUNCTION_GEOMETRY[fc]
    # Longest-prefix fallback: try progressively shorter prefixes.
    for n in range(len(fc), 1, -1):
        prefix = fc[:n]
        if prefix in _FUNCTION_GEOMETRY:
            return _FUNCTION_GEOMETRY[prefix]
    return None


def is_known(sidc: str) -> bool:
    """True if the SIDC function code is in the curated working set."""
    return classify_geometry(sidc) is not None
