"""Doctrinal prefix → SIDC mapping for the KMZ ingest recognizer.

Pure data, no logic. The recognition pipeline in kmz_recognize.py consumes
this table; tests reference it directly.

The entries here lock the **default** SIDC for each doctrinal prefix.
"Default" means: unknown affiliation (standard-identity 'U'), present
status, status code 'P', and the canonical function code from B130836.
The recognizer adjusts the affiliation character per the rules in
kmz_recognize.py (e.g. when a "Suspected " prefix is detected, position 2
is rewritten to the suspected variant).

Each table entry is:
  (regex, sidc_template, doctrinal_kind, status, notes)

- regex: matched against the *trimmed* label. Use ^ to anchor at the
  start; the recognition pipeline relies on prefix matching.
- sidc_template: 15-character SIDC with 'U' at position 2 (affiliation),
  to be rewritten by the recognizer based on other signals.
- doctrinal_kind: short string preserved on the CTO for downstream
  consumers (kept compatible with the Phase 2a values).
- status: "clean" if the prefix maps to a single canonical SIDC;
  "best_effort" if the doctrinal kind admits multiple SIDC variants
  and we are emitting the most general one.
- notes: reason text appended to fidelity report entries when status
  is "best_effort". Empty string for clean matches.

The SIDC format reference:
  Position 1     - coding scheme ('G' = graphic / tactical-graphic)
  Position 2     - standard identity / affiliation
                   (F friend, H hostile, N neutral, U unknown,
                    S suspect, A assumed-friend, P pending, G exercise...)
  Positions 3-4  - battle dimension + status (we use 'GP' = ground present)
  Positions 5-10 - function code
  Positions 11-15- modifiers (echelon, country, etc. — typically '-----')

Notes on coverage:
- This table covers the doctrinal prefixes locked in ADR-0012 D1 layer 1.
- The "word recognition" layer (checkpoint, control point, bridge,
  objective) is implemented separately in kmz_recognize.py since the
  patterns are mid-string, not prefixes.
- Target designators (e.g. AB1001) are also handled in kmz_recognize.py
  via a regex pattern, not this prefix table.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PrefixEntry:
    pattern: re.Pattern[str]
    sidc_template: str
    doctrinal_kind: str
    status: str  # "clean" or "best_effort"
    notes: str   # reason text for best_effort; empty for clean


# Re-usable affiliation slot. Position 2 (index 1) is 'U' (unknown);
# kmz_recognize.py rewrites this based on context.
def _g(function_code: str) -> str:
    """Build a 15-char graphic SIDC template, unknown affiliation, ground/present.

    Coding scheme 'G', standard-identity 'U' (unknown), battle dimension
    'G' (ground), status 'P' (present), then the 6-char function code,
    then 5 modifier dashes.
    """
    if len(function_code) > 6:
        raise ValueError(f"function code too long: {function_code!r}")
    fc = function_code.ljust(6, "-")
    return f"GUGP{fc}-----"


# Convenience for case-insensitive whole-prefix patterns. The trailing
# group requires either whitespace+content or end-of-string so that "PL"
# matches "PL ALPHA" but not "PLOT".
def _prefix(p: str) -> re.Pattern[str]:
    return re.compile(rf"^{p}(\s+\S|$)", re.IGNORECASE)


def _prefix_word(p: str) -> re.Pattern[str]:
    """Prefix followed by a word boundary (allows things like BNDRY|BOUNDARY)."""
    return re.compile(rf"^{p}\b", re.IGNORECASE)


# Order is significant: longer / more specific prefixes must come before
# shorter ones that would prefix-match the same input.
# (e.g. LDLC before LD; CCP before CP; MSR before none-prefix routes)
PREFIX_TABLE: tuple[PrefixEntry, ...] = (

    # --- Maneuver control measures ---------------------------------------
    PrefixEntry(_prefix("PL"),    _g("GLP"),  "phase_line",
                "best_effort", "phase line variant inferred; planner may have meant a specific PL type"),
    PrefixEntry(_prefix("LDLC"),  _g("GLF"),  "ld_lc",
                "clean", ""),
    PrefixEntry(_prefix("LD"),    _g("GLL"),  "line_of_departure",
                "clean", ""),
    PrefixEntry(_prefix("LOA"),   _g("OLAA"), "limit_of_advance",
                "clean", ""),
    PrefixEntry(_prefix("FEBA"),  _g("OLF"),  "feba",
                "clean", ""),
    PrefixEntry(_prefix("FLOT"),  _g("OLT"),  "flot",
                "clean", ""),
    PrefixEntry(_prefix_word("BNDRY|BOUNDARY"), _g("GLB"), "boundary",
                "best_effort", "boundary echelon not derivable from label"),

    # --- Areas -----------------------------------------------------------
    PrefixEntry(_prefix("AO"),    _g("GAG"),  "area_of_operations",
                "clean", ""),
    PrefixEntry(_prefix("EA"),    _g("OAE"),  "engagement_area",
                "clean", ""),
    PrefixEntry(_prefix("AA"),    _g("GAA"),  "assembly_area",
                "clean", ""),
    PrefixEntry(_prefix("BP"),    _g("DAB"),  "battle_position",
                "clean", ""),
    PrefixEntry(_prefix("HA"),    _g("OAH"),  "holding_area",
                "clean", ""),
    PrefixEntry(_prefix("SBF"),   _g("OAS"),  "support_by_fire",
                "clean", ""),
    PrefixEntry(_prefix("ATK"),   _g("OAK"),  "attack_position",
                "clean", ""),

    # --- Mobility --------------------------------------------------------
    PrefixEntry(_prefix("SP"),    _g("OPP"),  "start_point",
                "clean", ""),
    PrefixEntry(_prefix("RP"),    _g("OPR"),  "release_point",
                "clean", ""),
    PrefixEntry(_prefix("MSR"),   _g("OLLS"), "main_supply_route",
                "clean", ""),
    PrefixEntry(_prefix("ASR"),   _g("OLLA"), "alt_supply_route",
                "clean", ""),

    # --- Combat service support -----------------------------------------
    PrefixEntry(_prefix("CCP"),   _g("OPCC"), "casualty_collection_point",
                "clean", ""),
    PrefixEntry(_prefix("CP"),    _g("OPC"),  "contact_point",
                "clean", ""),

    # --- Targeting ------------------------------------------------------
    PrefixEntry(_prefix("OBJ"),   _g("OAO"),  "objective",
                "clean", ""),
    PrefixEntry(_prefix("TAI"),   _g("GPRI"), "target_area_of_interest",
                "clean", ""),
    PrefixEntry(_prefix("NAI"),   _g("GPRN"), "named_area_of_interest",
                "clean", ""),
    PrefixEntry(_prefix("TRP"),   _g("DPT"),  "target_reference_point",
                "clean", ""),

    # --- Fire support ---------------------------------------------------
    PrefixEntry(_prefix("FSCL"),  _g("LF"),   "fscl",
                "clean", ""),
    PrefixEntry(_prefix("CFL"),   _g("LC"),   "cfl",
                "clean", ""),
    PrefixEntry(_prefix("RFL"),   _g("LR"),   "rfl",
                "clean", ""),
    PrefixEntry(_prefix("NFL"),   _g("LN"),   "nfl",
                "clean", ""),
    PrefixEntry(_prefix("NFA"),   _g("AN"),   "no_fire_area",
                "clean", ""),
    PrefixEntry(_prefix("RFA"),   _g("AR"),   "restrictive_fire_area",
                "clean", ""),

    # --- Aviation / airspace --------------------------------------------
    PrefixEntry(_prefix("ROZ"),   _g("ARZ"),  "roz",
                "clean", ""),
    PrefixEntry(_prefix("PZ"),    _g("PPZ"),  "pickup_zone",
                "clean", ""),
    PrefixEntry(_prefix("LZ"),    _g("PPL"),  "landing_zone",
                "clean", ""),
    PrefixEntry(_prefix("DZ"),    _g("PPD"),  "drop_zone",
                "clean", ""),
)


# --- Word-recognition table (D1 layer 3) ---------------------------------

@dataclass(frozen=True)
class WordEntry:
    word: str         # case-insensitive substring
    sidc_template: str
    doctrinal_kind: str
    status: str
    notes: str


WORD_TABLE: tuple[WordEntry, ...] = (
    WordEntry("checkpoint",   _g("OPCK"), "checkpoint",
              "best_effort", "matched by word; specific checkpoint variant not derivable"),
    WordEntry("control point", _g("OPC"), "control_point",
              "best_effort", "matched by word; generic control point assigned"),
    WordEntry("bridge",       _g("EBB"),  "bridge",
              "best_effort", "matched by word; bridge feature, affiliation may need review"),
    WordEntry("objective",    _g("OAO"),  "objective",
              "best_effort", "matched by word; prefer 'OBJ ' prefix for clean match"),
)


# --- Target designator pattern (D1 layer 2) ------------------------------

# Per ADR-0012 D1: ^[A-Z]{1,2}\d{3,4}\b  (e.g. AB1001, T101, AB1234).
TARGET_DESIGNATOR_PATTERN = re.compile(r"^[A-Z]{1,2}\d{3,4}\b")
TARGET_DESIGNATOR_SIDC = _g("DPT")
TARGET_DESIGNATOR_KIND = "target_reference_point"


# --- Geometry-fallback SIDCs (D1 layer 5) --------------------------------

# Generic graphic SIDCs by geometry shape. Used when no other layer
# resolved the label. Affiliation stays unknown; status is best_effort.
GEOMETRY_FALLBACK_SIDC = {
    "Point":      _g("GPP"),  # generic point graphic
    "LineString": _g("GLG"),  # generic linear graphic
    "Polygon":    _g("GAG"),  # generic area graphic
}


# --- Route detection (D1 special-case) -----------------------------------

# Routes get their operator label preserved verbatim and are not
# further normalized. We still tag them with a SIDC so the writer has
# something to emit.
ROUTE_PATTERN = re.compile(r"^(route|axis)\b", re.IGNORECASE)
ROUTE_SIDC = _g("OLR")
ROUTE_KIND = "route"
