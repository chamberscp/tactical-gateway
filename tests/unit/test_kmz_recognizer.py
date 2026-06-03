"""Unit tests for services.gateway.kmz_recognize.

Each test exercises one D1 layer in isolation. The recognizer has no I/O
and no infrastructure dependencies; these tests run in milliseconds.

Reference: ADR-0012 §Decision §D1.
"""

from __future__ import annotations

import pytest

from services.gateway.kmz_recognize import (
    RecognitionResult,
    affiliation_from_explicit_sidc,
    recognize,
)


# ---------- Layer 2: doctrinal prefix table ------------------------------


class TestPrefixLayer:

    def test_pl_alpha_resolves_to_phase_line(self):
        r = recognize(label="PL ALPHA", description=None, geometry_type="LineString")
        assert r.matched_layer == "prefix"
        assert r.doctrinal_kind == "phase_line"
        assert r.status == "best_effort"

    def test_nai_7_resolves_to_named_area_of_interest(self):
        r = recognize(label="NAI 7", description=None, geometry_type="Polygon")
        assert r.matched_layer == "prefix"
        assert r.doctrinal_kind == "named_area_of_interest"
        assert r.status == "clean"

    def test_obj_target_resolves_to_objective(self):
        r = recognize(label="OBJ TARGET", description=None, geometry_type="Point")
        assert r.matched_layer == "prefix"
        assert r.doctrinal_kind == "objective"

    def test_pl_does_not_match_plot(self):
        r = recognize(label="PLOT 5", description=None, geometry_type="Point")
        assert r.matched_layer != "prefix"

    def test_ldlc_matches_before_ld(self):
        r = recognize(label="LDLC ALPHA", description=None, geometry_type="LineString")
        assert r.matched_layer == "prefix"
        assert r.doctrinal_kind == "ld_lc"

    def test_ccp_matches_before_cp(self):
        r = recognize(label="CCP 1", description=None, geometry_type="Point")
        assert r.matched_layer == "prefix"
        assert r.doctrinal_kind == "casualty_collection_point"

    def test_boundary_word_form(self):
        r = recognize(label="BOUNDARY 2-3", description=None, geometry_type="LineString")
        assert r.matched_layer == "prefix"
        assert r.doctrinal_kind == "boundary"

    def test_bndry_short_form(self):
        r = recognize(label="BNDRY 2-3", description=None, geometry_type="LineString")
        assert r.matched_layer == "prefix"
        assert r.doctrinal_kind == "boundary"

    def test_lowercase_input_still_matches(self):
        r = recognize(label="nai 9", description=None, geometry_type="Polygon")
        assert r.matched_layer == "prefix"
        assert r.doctrinal_kind == "named_area_of_interest"


# ---------- Layer 3: target designator pattern ---------------------------


class TestTargetDesignatorLayer:

    @pytest.mark.parametrize("label", ["AB1001", "T101", "AB1234", "Z999"])
    def test_designators_match(self, label):
        r = recognize(label=label, description=None, geometry_type="Point")
        assert r.matched_layer == "target"
        assert r.doctrinal_kind == "target_reference_point"
        assert r.status == "clean"

    @pytest.mark.parametrize("label", [
        "AB10", "ABC1001", "1001", "ab1001",
    ])
    def test_non_designators_do_not_match(self, label):
        r = recognize(label=label, description=None, geometry_type="Point")
        assert r.matched_layer != "target"


# ---------- Layer 4: word-based recognition ------------------------------


class TestWordLayer:

    def test_checkpoint_word(self):
        r = recognize(label="Checkpoint Charlie", description=None, geometry_type="Point")
        assert r.matched_layer == "word"
        assert r.doctrinal_kind == "checkpoint"

    def test_control_point_two_words(self):
        r = recognize(label="North Control Point", description=None, geometry_type="Point")
        assert r.matched_layer == "word"
        assert r.doctrinal_kind == "control_point"

    def test_bridge_word(self):
        r = recognize(label="Bridge 4", description=None, geometry_type="Point")
        assert r.matched_layer == "word"
        assert r.doctrinal_kind == "bridge"

    def test_objective_word(self):
        word_match = recognize(label="Objective Alpha but no prefix", description=None, geometry_type="Point")
        assert word_match.matched_layer == "word"

        prefix_match = recognize(label="OBJ TARGET", description=None, geometry_type="Point")
        assert prefix_match.matched_layer == "prefix"

    def test_unrelated_words_do_not_match(self):
        r = recognize(label="Random label", description=None, geometry_type="Point")
        assert r.matched_layer != "word"


# ---------- Layer 5: route detection -------------------------------------


class TestRouteLayer:

    def test_route_blue(self):
        r = recognize(label="Route Blue", description=None, geometry_type="LineString")
        assert r.matched_layer == "route"
        assert r.doctrinal_kind == "route"
        assert r.status == "best_effort"

    def test_axis_boyd(self):
        r = recognize(label="Axis Boyd", description=None, geometry_type="LineString")
        assert r.matched_layer == "route"

    def test_route_word_inside_label_does_not_trigger(self):
        r = recognize(label="PL Route", description=None, geometry_type="LineString")
        assert r.matched_layer == "prefix"


# ---------- Layer 6: geometry fallback -----------------------------------


class TestGeometryFallback:

    def test_empty_label_falls_through(self):
        r = recognize(label="", description=None, geometry_type="Point")
        assert r.matched_layer == "geometry_fallback"
        assert r.status == "best_effort"

    def test_none_label_falls_through(self):
        r = recognize(label=None, description=None, geometry_type="LineString")
        assert r.matched_layer == "geometry_fallback"

    def test_whitespace_label_falls_through(self):
        r = recognize(label="   ", description=None, geometry_type="Polygon")
        assert r.matched_layer == "geometry_fallback"

    def test_chucks_house_falls_through(self):
        r = recognize(label="Chuck's house", description=None, geometry_type="Point")
        assert r.matched_layer == "geometry_fallback"

    @pytest.mark.parametrize("geom", ["Point", "LineString", "Polygon"])
    def test_each_geometry_yields_a_sidc(self, geom):
        r = recognize(label="unknown thing", description=None, geometry_type=geom)
        assert r.matched_layer == "geometry_fallback"
        assert len(r.sidc) == 15


# ---------- Suspected prefix + affiliation rewriting ---------------------


class TestSuspectedModifier:

    def test_suspected_enemy_position_is_suspect(self):
        r = recognize(label="Suspected EA 1", description=None, geometry_type="Polygon")
        assert r.doctrinal_kind == "engagement_area"
        assert r.suspected_modifier is True
        assert r.affiliation == "suspect"

    def test_suspected_friendly_becomes_assumed_friend(self):
        r = recognize(
            label="Suspected AA 5",
            description="friendly assembly area",
            geometry_type="Polygon",
        )
        assert r.suspected_modifier is True
        assert r.affiliation == "assumed_friend"

    def test_suspected_strips_from_label_before_recognition(self):
        r1 = recognize(label="Suspected NAI 7", description=None, geometry_type="Polygon")
        r2 = recognize(label="NAI 7", description=None, geometry_type="Polygon")
        assert r1.doctrinal_kind == r2.doctrinal_kind == "named_area_of_interest"

    def test_lowercase_suspected_works(self):
        r = recognize(label="suspected NAI 7", description=None, geometry_type="Polygon")
        assert r.suspected_modifier is True
        assert r.doctrinal_kind == "named_area_of_interest"


# ---------- Affiliation from description hints --------------------------


class TestAffiliationFromDescription:

    def test_enemy_description_yields_hostile(self):
        r = recognize(label="NAI 7", description="enemy assembly observed",
                      geometry_type="Polygon")
        assert r.affiliation == "hostile"
        assert r.sidc[1] == "H"

    def test_friendly_description_yields_friend(self):
        r = recognize(label="AA 1", description="friendly assembly area",
                      geometry_type="Polygon")
        assert r.affiliation == "friend"
        assert r.sidc[1] == "F"

    def test_no_description_yields_default(self):
        """Default is 'unknown' when env var is unset.

        With KMZ_DEFAULT_AFFILIATION unset/default, no description hint
        produces affiliation 'unknown' and source 'configured_default'.
        """
        r = recognize(label="NAI 7", description=None, geometry_type="Polygon")
        assert r.affiliation == "unknown"
        assert r.sidc[1] == "U"
        assert r.affiliation_source == "configured_default"

    def test_opfor_yields_hostile(self):
        r = recognize(label="NAI 7", description="OPFOR likely here",
                      geometry_type="Polygon")
        assert r.affiliation == "hostile"

    def test_blufor_yields_friend(self):
        r = recognize(label="AA 1", description="BLUFOR holding",
                      geometry_type="Polygon")
        assert r.affiliation == "friend"


# ---------- Configurable default affiliation -----------------------------


class TestConfigurableDefaultAffiliation:
    """KMZ_DEFAULT_AFFILIATION env var changes the default fallback."""

    def test_default_is_unknown_when_unset(self, monkeypatch):
        monkeypatch.delenv("KMZ_DEFAULT_AFFILIATION", raising=False)
        r = recognize(label="NAI 7", description=None, geometry_type="Polygon")
        assert r.affiliation == "unknown"
        assert r.affiliation_source == "configured_default"

    def test_default_friend_via_env(self, monkeypatch):
        """A deployment whose KMZs are usually own-side traffic sets
        the default to 'friend'."""
        monkeypatch.setenv("KMZ_DEFAULT_AFFILIATION", "friend")
        r = recognize(label="NAI 7", description=None, geometry_type="Polygon")
        assert r.affiliation == "friend"
        assert r.sidc[1] == "F"
        assert r.affiliation_source == "configured_default"

    def test_default_neutral_via_env(self, monkeypatch):
        monkeypatch.setenv("KMZ_DEFAULT_AFFILIATION", "neutral")
        r = recognize(label="NAI 7", description=None, geometry_type="Polygon")
        assert r.affiliation == "neutral"
        assert r.sidc[1] == "N"

    def test_invalid_env_falls_back_to_unknown(self, monkeypatch):
        """An unrecognized value silently falls back to 'unknown' rather
        than crashing the recognizer."""
        monkeypatch.setenv("KMZ_DEFAULT_AFFILIATION", "bogus")
        r = recognize(label="NAI 7", description=None, geometry_type="Polygon")
        assert r.affiliation == "unknown"

    def test_default_friend_still_overridden_by_hostile_description(self, monkeypatch):
        """Description hints win over the configured default."""
        monkeypatch.setenv("KMZ_DEFAULT_AFFILIATION", "friend")
        r = recognize(label="NAI 7", description="enemy holding",
                      geometry_type="Polygon")
        assert r.affiliation == "hostile"
        assert r.affiliation_source == "description_hostile"

    def test_default_friend_still_overridden_by_suspected(self, monkeypatch):
        """Suspected modifier wins over the configured default."""
        monkeypatch.setenv("KMZ_DEFAULT_AFFILIATION", "friend")
        r = recognize(label="Suspected NAI 7", description=None,
                      geometry_type="Polygon")
        # Suspected + base "friend" → assumed_friend
        assert r.affiliation == "assumed_friend"
        assert r.affiliation_source == "suspected_modifier"


# ---------- affiliation_source tracking ---------------------------------


class TestAffiliationSource:

    def test_source_is_description_hostile(self):
        r = recognize(label="NAI 7", description="enemy", geometry_type="Polygon")
        assert r.affiliation_source == "description_hostile"

    def test_source_is_description_friendly(self):
        r = recognize(label="NAI 7", description="friendly", geometry_type="Polygon")
        assert r.affiliation_source == "description_friendly"

    def test_source_is_suspected_modifier(self):
        r = recognize(label="Suspected NAI 7", description=None,
                      geometry_type="Polygon")
        assert r.affiliation_source == "suspected_modifier"

    def test_source_is_configured_default_when_no_other_signal(self):
        r = recognize(label="NAI 7", description=None, geometry_type="Polygon")
        assert r.affiliation_source == "configured_default"


# ---------- affiliation_from_explicit_sidc helper -----------------------


class TestAffiliationFromExplicitSidc:

    @pytest.mark.parametrize("sidc,expected", [
        ("GFGPGPRN-------", "friend"),
        ("GHGPGPRN-------", "hostile"),
        ("GNGPGPRN-------", "neutral"),
        ("GUGPGPRN-------", "unknown"),
        ("GSGPGPRN-------", "suspect"),
        ("GAGPGPRN-------", "assumed_friend"),
    ])
    def test_decodes_each_standard_identity(self, sidc, expected):
        assert affiliation_from_explicit_sidc(sidc) == expected

    @pytest.mark.parametrize("bad", [
        None, "", "too-short", "G?GPGPRN-------", "x" * 16,
    ])
    def test_returns_none_for_malformed(self, bad):
        assert affiliation_from_explicit_sidc(bad) is None


# ---------- Result invariants -------------------------------------------


class TestResultInvariants:

    @pytest.mark.parametrize("label,desc,geom", [
        ("PL ALPHA", None, "LineString"),
        ("", None, "Point"),
        ("Chuck's house", "friendly", "Point"),
        ("Suspected NAI 7", "enemy", "Polygon"),
        ("Route Blue", None, "LineString"),
        ("AB1001", None, "Point"),
    ])
    def test_never_returns_none(self, label, desc, geom):
        r = recognize(label=label, description=desc, geometry_type=geom)
        assert isinstance(r, RecognitionResult)

    @pytest.mark.parametrize("label,desc,geom", [
        ("PL ALPHA", None, "LineString"),
        ("", None, "Point"),
        ("Chuck's house", "friendly", "Point"),
        ("Suspected NAI 7", "enemy", "Polygon"),
    ])
    def test_sidc_is_always_15_chars(self, label, desc, geom):
        r = recognize(label=label, description=desc, geometry_type=geom)
        assert len(r.sidc) == 15

    @pytest.mark.parametrize("label,desc,geom", [
        ("PL ALPHA", None, "LineString"),
        ("", None, "Point"),
        ("Chuck's house", None, "Point"),
    ])
    def test_affiliation_is_a_known_enum_value(self, label, desc, geom):
        r = recognize(label=label, description=desc, geometry_type=geom)
        assert r.affiliation in {
            "friend", "hostile", "neutral", "unknown",
            "pending", "suspect", "assumed_friend",
        }
