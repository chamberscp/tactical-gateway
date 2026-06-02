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
    depending on the underlying context (defaults to suspect; see notes
    on the SUSPECTED_DEFAULT constant below).
  - Description text contains "enemy", "hostile", "OPFOR" (case-insens.)
    → hostile (and SUSPECT if the suspected modifier was set).
  - Description text contains "friendly", "BLUFOR" → friend.
  - Otherwise → unknown (the table's default).

The recognizer does NOT consult the KMZ's color, line style, or
ExtendedData. Those are not reliable signals in real planner KMZs.
"""

from __future__ import annotations

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

# When "Suspected " is stripped from a label with no other affiliation
# signal, default to suspect (the hostile-side suspected). The other
# operationally common case — "Suspected friendly" — is rare enough that
# defaulting to suspect captures the dominant intent.
SUSPECTED_DEFAULT = "suspect"

# Heuristic patterns over the description text (NOT the label). The
# label has already been consumed by the prefix/word/target layers; the
# description is the operator's free-text annotation and is where they
# often note enemy/friendly intent.
_HOSTILE_DESC = re.compile(r"\b(enemy|hostile|opfor|threat|red\s+force)\b", re.IGNORECASE)
_FRIENDLY_DESC = re.compile(r"\b(friendly|blufor|blue\s+force|own|own\s+force)\b", re.IGNORECASE)
_SUSPECTED_PREFIX = re.compile(r"^suspected\s+", re.IGNORECASE)


GeometryShape = Literal["Point", "LineString", "Polygon"]


@dataclass(frozen=True)
class RecognitionResult:
    """What the recognizer produces for one placemark.

    The KMZ parser uses these fields directly:
      - sidc          → Symbology(sidc_2525c=...)
      - affiliation   → Symbology(affiliation=...)
      - doctrinal_kind → attributes["graphic_kind"]   (Phase 2a-compatible)
      - status / reasons → fidelity report entry and CTO provenance notes
    """
    sidc: str                # 15-character SIDC
    affiliation: str         # one of cto_schema.Affiliation values
    doctrinal_kind: str | None
    status: Literal["clean", "best_effort"]
    matched_layer: Literal[
        "prefix", "target", "word", "route", "geometry_fallback", "no_label"
    ]
    suspected_modifier: bool  # True if "Suspected " was stripped
    reasons: tuple[str, ...] = field(default_factory=tuple)


def recognize(
    *,
    label: str | None,
    description: str | None,
    geometry_type: GeometryShape,
) -> RecognitionResult:
    """Apply the D1 recognition pipeline. Always returns a result.

    `label` is the KML <name> text (may be None/empty).
    `description` is the KML <description> text (may be None/empty);
    used only for affiliation hinting.
    `geometry_type` is required to drive the geometry fallback layer.
    """
    desc = description or ""

    # Layer 1: "Suspected " prefix detection ------------------------------
    # We strip the modifier and pass the remainder through the same
    # pipeline; the suspected flag is applied at affiliation time.
    suspected = False
    working_label = (label or "").strip()
    m = _SUSPECTED_PREFIX.match(working_label)
    if m:
        suspected = True
        working_label = working_label[m.end():].strip()

    reasons: list[str] = []

    # If we have nothing to work with, jump straight to geometry fallback.
    if not working_label:
        return _geometry_fallback(geometry_type, desc, suspected,
                                  reasons=["label was empty or whitespace only"])

    # Layer 2: doctrinal prefix table -------------------------------------
    for entry in PREFIX_TABLE:
        if entry.pattern.match(working_label):
            sidc = entry.sidc_template
            sidc = _apply_affiliation(sidc, desc, suspected)
            affil = _affil_from_sidc(sidc)
            if entry.notes:
                reasons.append(entry.notes)
            if suspected:
                reasons.append("'Suspected ' prefix detected; identity set accordingly")
            return RecognitionResult(
                sidc=sidc, affiliation=affil,
                doctrinal_kind=entry.doctrinal_kind,
                status=entry.status,
                matched_layer="prefix",
                suspected_modifier=suspected,
                reasons=tuple(reasons),
            )

    # Layer 3: target designator pattern ---------------------------------
    if TARGET_DESIGNATOR_PATTERN.match(working_label):
        sidc = _apply_affiliation(TARGET_DESIGNATOR_SIDC, desc, suspected)
        affil = _affil_from_sidc(sidc)
        if suspected:
            reasons.append("'Suspected ' prefix detected; identity set accordingly")
        return RecognitionResult(
            sidc=sidc, affiliation=affil,
            doctrinal_kind=TARGET_DESIGNATOR_KIND,
            status="clean",
            matched_layer="target",
            suspected_modifier=suspected,
            reasons=tuple(reasons),
        )

    # Layer 4: word recognition ------------------------------------------
    # Word match is on the (case-insensitive) full working_label, not
    # just the prefix. We take the first hit in WORD_TABLE order.
    lower = working_label.lower()
    for w in WORD_TABLE:
        if w.word in lower:
            sidc = _apply_affiliation(w.sidc_template, desc, suspected)
            affil = _affil_from_sidc(sidc)
            reasons.append(w.notes)
            if suspected:
                reasons.append("'Suspected ' prefix detected; identity set accordingly")
            return RecognitionResult(
                sidc=sidc, affiliation=affil,
                doctrinal_kind=w.doctrinal_kind,
                status=w.status,
                matched_layer="word",
                suspected_modifier=suspected,
                reasons=tuple(reasons),
            )

    # Layer 5: route detection -------------------------------------------
    if ROUTE_PATTERN.match(working_label):
        sidc = _apply_affiliation(ROUTE_SIDC, desc, suspected)
        affil = _affil_from_sidc(sidc)
        reasons.append("route label preserved verbatim; no further normalization")
        if suspected:
            reasons.append("'Suspected ' prefix detected; identity set accordingly")
        return RecognitionResult(
            sidc=sidc, affiliation=affil,
            doctrinal_kind=ROUTE_KIND,
            status="best_effort",
            matched_layer="route",
            suspected_modifier=suspected,
            reasons=tuple(reasons),
        )

    # Layer 6: geometry fallback -----------------------------------------
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
    sidc = _apply_affiliation(template, description, suspected)
    affil = _affil_from_sidc(sidc)
    if suspected:
        reasons = list(reasons) + ["'Suspected ' prefix detected; identity set accordingly"]
    return RecognitionResult(
        sidc=sidc, affiliation=affil,
        doctrinal_kind=None,
        status="best_effort",
        matched_layer="geometry_fallback",
        suspected_modifier=suspected,
        reasons=tuple(reasons),
    )


def _apply_affiliation(sidc: str, description: str, suspected: bool) -> str:
    """Rewrite SIDC position 2 based on description hints + suspected flag."""
    if len(sidc) != 15:
        raise ValueError(f"SIDC must be 15 chars: {sidc!r}")

    # Determine the base affiliation from the description.
    if _HOSTILE_DESC.search(description):
        base = "hostile"
    elif _FRIENDLY_DESC.search(description):
        base = "friend"
    else:
        base = "unknown"

    # Apply the suspected modifier if set.
    if suspected:
        if base == "friend":
            affil = "assumed_friend"
        else:
            # Suspect applies to hostile / unknown / etc.
            affil = SUSPECTED_DEFAULT
    else:
        affil = base

    return sidc[0] + _AFFIL_CHAR[affil] + sidc[2:]


def _affil_from_sidc(sidc: str) -> str:
    """Inverse: read affiliation back out of a SIDC's position 2."""
    char = sidc[1]
    for name, c in _AFFIL_CHAR.items():
        if c == char:
            return name
    return "unknown"
