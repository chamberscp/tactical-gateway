"""KMZ label recognizer — D1 of ADR-0012.

Takes a placemark label (the KML <name> element text) plus the placemark's
geometry type, and returns a RecognitionResult that the KMZ parser uses to
populate Symbology and provenance fields on the resulting CTO.

Pipeline order (each layer can short-circuit):

  1. Suspected-prefix detection      — strips "Suspected " from the label
                                       and remembers the modifier intent.
  2. Doctrinal prefix table          — kmz_prefix_table.PREFIX_TABLE.
  3. Target designator pattern       — AB1001, T101, etc.
  4. Word-based recognition          — checkpoint / control point / bridge / objective.
  5. Route detection (special-case)  — preserves label verbatim.
  6. Geometry fallback               — last resort; never silently drops.

Affiliation post-processing:

After a SIDC is chosen by one of the above layers, position 2 of the SIDC
(the "standard identity" field) is rewritten based on these signals, in
priority order:

  - "Suspected " was stripped from the label → suspect / assumed_friend
    depending on the underlying context.
  - Description text contains "enemy", "hostile", "OPFOR" (case-insens.)
    → hostile.
  - Description text contains "friendly", "BLUFOR" → friend.
  - Otherwise → the deployment-configured default (env var
    KMZ_DEFAULT_AFFILIATION; module default "unknown"). This lets a
    deployment whose KMZs are usually own-side traffic set the default
    to "friend" without changing code.

The recognizer does NOT consult the KMZ's color, line style, or
ExtendedData. Those are not reliable signals in real planner KMZs.

The recognizer records *how* the affiliation was derived as
RecognitionResult.affiliation_source. This is preserved in CTO
provenance so audit queries can distinguish CTOs whose affiliation
came from a direct signal vs. from the configured default.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Literal

from .kmz_prefix_table import (
    GEOMETRY_FALLBACK_SIDC,
    PREFIX_TABLE,
    ROUTE_KIND,
    ROUTE_PATTERN,
    ROUTE_SIDC,
    TARGET_DESIGNATOR_KIND,
    TARGET_DESIGNATOR_PATTERN,
    TARGET_DESIGNATOR_SIDC,
    WORD_TABLE,
)


# Affiliation chars used at position 2 of the SIDC.
# Names mirror the cto_schema Affiliation enum values.
_AFFIL_CHAR = {
    "friend":          "F",
    "hostile":         "H",
    "neutral":         "N",
    "unknown":         "U",
    "suspect":         "S",
    "assumed_friend":  "A",
    "pending":         "P",
}

# When "Suspected " is stripped from a label, suspect is applied to
# the base affiliation (hostile/unknown/etc.); friend is upgraded to
# assumed_friend.
SUSPECTED_DEFAULT = "suspect"


def _get_default_affiliation() -> str:
    """Return the deployment-configured default affiliation.

    Read from env var KMZ_DEFAULT_AFFILIATION at call time so tests can
    override via monkeypatch. Falls back to "unknown" — the conservative
    choice — if unset or invalid.
    """
    val = os.environ.get("KMZ_DEFAULT_AFFILIATION", "unknown").strip().lower()
    if val in _AFFIL_CHAR:
        return val
    return "unknown"


# Heuristic patterns over the description text (NOT the label).
_HOSTILE_DESC = re.compile(r"\b(enemy|hostile|opfor|threat|red\s+force)\b", re.IGNORECASE)
_FRIENDLY_DESC = re.compile(r"\b(friendly|blufor|blue\s+force|own|own\s+force)\b", re.IGNORECASE)
_SUSPECTED_PREFIX = re.compile(r"^suspected\s+", re.IGNORECASE)


GeometryShape = Literal["Point", "LineString", "Polygon"]
AffiliationSource = Literal[
    "description_hostile",
    "description_friendly",
    "suspected_modifier",
    "configured_default",
]


@dataclass(frozen=True)
class RecognitionResult:
    """What the recognizer produces for one placemark."""
    sidc: str                # 15-character SIDC
    affiliation: str         # one of cto_schema.Affiliation values
    affiliation_source: AffiliationSource
    doctrinal_kind: str | None
    status: Literal["clean", "best_effort"]
    matched_layer: Literal[
        "prefix", "target", "word", "route", "geometry_fallback", "no_label"
    ]
    suspected_modifier: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)


def recognize(
    *,
    label: str | None,
    description: str | None,
    geometry_type: GeometryShape,
) -> RecognitionResult:
    """Apply the D1 recognition pipeline. Always returns a result."""
    desc = description or ""

    # Layer 1: "Suspected " prefix detection
    suspected = False
    working_label = (label or "").strip()
    m = _SUSPECTED_PREFIX.match(working_label)
    if m:
        suspected = True
        working_label = working_label[m.end():].strip()

    reasons: list[str] = []

    if not working_label:
        return _geometry_fallback(geometry_type, desc, suspected,
                                  reasons=["label was empty or whitespace only"])

    # Layer 2: doctrinal prefix table
    for entry in PREFIX_TABLE:
        if entry.pattern.match(working_label):
            sidc, source = _apply_affiliation(entry.sidc_template, desc, suspected)
            if entry.notes:
                reasons.append(entry.notes)
            if suspected:
                reasons.append("'Suspected ' prefix detected; identity set accordingly")
            return RecognitionResult(
                sidc=sidc,
                affiliation=_affil_from_sidc(sidc),
                affiliation_source=source,
                doctrinal_kind=entry.doctrinal_kind,
                status=entry.status,
                matched_layer="prefix",
                suspected_modifier=suspected,
                reasons=tuple(reasons),
            )

    # Layer 3: target designator pattern
    if TARGET_DESIGNATOR_PATTERN.match(working_label):
        sidc, source = _apply_affiliation(TARGET_DESIGNATOR_SIDC, desc, suspected)
        if suspected:
            reasons.append("'Suspected ' prefix detected; identity set accordingly")
        return RecognitionResult(
            sidc=sidc,
            affiliation=_affil_from_sidc(sidc),
            affiliation_source=source,
            doctrinal_kind=TARGET_DESIGNATOR_KIND,
            status="clean",
            matched_layer="target",
            suspected_modifier=suspected,
            reasons=tuple(reasons),
        )

    # Layer 4: word recognition
    lower = working_label.lower()
    for w in WORD_TABLE:
        if w.word in lower:
            sidc, source = _apply_affiliation(w.sidc_template, desc, suspected)
            reasons.append(w.notes)
            if suspected:
                reasons.append("'Suspected ' prefix detected; identity set accordingly")
            return RecognitionResult(
                sidc=sidc,
                affiliation=_affil_from_sidc(sidc),
                affiliation_source=source,
                doctrinal_kind=w.doctrinal_kind,
                status=w.status,
                matched_layer="word",
                suspected_modifier=suspected,
                reasons=tuple(reasons),
            )

    # Layer 5: route detection
    if ROUTE_PATTERN.match(working_label):
        sidc, source = _apply_affiliation(ROUTE_SIDC, desc, suspected)
        reasons.append("route label preserved verbatim; no further normalization")
        if suspected:
            reasons.append("'Suspected ' prefix detected; identity set accordingly")
        return RecognitionResult(
            sidc=sidc,
            affiliation=_affil_from_sidc(sidc),
            affiliation_source=source,
            doctrinal_kind=ROUTE_KIND,
            status="best_effort",
            matched_layer="route",
            suspected_modifier=suspected,
            reasons=tuple(reasons),
        )

    # Layer 6: geometry fallback
    fallback_reasons = ["no doctrinal/target/word/route pattern matched; "
                        "generic geometry SIDC assigned"]
    return _geometry_fallback(geometry_type, desc, suspected,
                              reasons=fallback_reasons + reasons)


# --- internals -----------------------------------------------------------


def _geometry_fallback(
    geometry_type: GeometryShape,
    description: str,
    suspected: bool,
    *,
    reasons: list[str],
) -> RecognitionResult:
    template = GEOMETRY_FALLBACK_SIDC[geometry_type]
    sidc, source = _apply_affiliation(template, description, suspected)
    if suspected:
        reasons = list(reasons) + ["'Suspected ' prefix detected; identity set accordingly"]
    return RecognitionResult(
        sidc=sidc,
        affiliation=_affil_from_sidc(sidc),
        affiliation_source=source,
        doctrinal_kind=None,
        status="best_effort",
        matched_layer="geometry_fallback",
        suspected_modifier=suspected,
        reasons=tuple(reasons),
    )


def _apply_affiliation(sidc: str, description: str, suspected: bool) -> tuple[str, AffiliationSource]:
    """Rewrite SIDC position 2 based on description hints + suspected flag.

    Returns (new_sidc, affiliation_source) where source identifies how
    the affiliation was chosen, for provenance / audit purposes.
    """
    if len(sidc) != 15:
        raise ValueError(f"SIDC must be 15 chars: {sidc!r}")

    # Determine the base affiliation + source from the description and
    # the deployment default.
    if _HOSTILE_DESC.search(description):
        base = "hostile"
        source: AffiliationSource = "description_hostile"
    elif _FRIENDLY_DESC.search(description):
        base = "friend"
        source = "description_friendly"
    else:
        base = _get_default_affiliation()
        source = "configured_default"

    # Apply the suspected modifier if set. Suspected wins as source
    # because it's a stronger signal than either description or default.
    if suspected:
        if base == "friend":
            affil = "assumed_friend"
        else:
            affil = SUSPECTED_DEFAULT
        source = "suspected_modifier"
    else:
        affil = base

    return sidc[0] + _AFFIL_CHAR[affil] + sidc[2:], source


def _affil_from_sidc(sidc: str) -> str:
    """Inverse: read affiliation back out of a SIDC's position 2."""
    char = sidc[1]
    for name, c in _AFFIL_CHAR.items():
        if c == char:
            return name
    return "unknown"


# --- Public helper used by the parser ------------------------------------


def affiliation_from_explicit_sidc(sidc: str) -> str | None:
    """Decode position 2 of an externally-supplied SIDC into an affiliation name.

    Used by the KMZ parser when an explicit <ExtendedData sidc=...> is
    present: the explicit SIDC is the authoritative signal, and its
    position-2 character determines the affiliation regardless of any
    description hints. Returns None if the SIDC is malformed.
    """
    if not sidc or len(sidc) != 15:
        return None
    char = sidc[1]
    for name, c in _AFFIL_CHAR.items():
        if c == char:
            return name
    return None
