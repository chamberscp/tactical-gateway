"""Phase 2b-1 tests: OVL ingest parser against the two real planner fixtures.

Run: pytest tests/integration/test_ovl_ingest.py -v
"""
import os
import sys

try:
    import pytest  # noqa: F401
except ImportError:
    pytest = None  # allows running under the standalone runner without pytest

# Make the ovl package and sidc lib importable in isolation.
HERE = os.path.dirname(__file__)
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "services", "gateway"))


from ovl.parser import (  # noqa: E402
    parse_ovl_file,
    ovl_to_cto_dicts,
    resolve_geometry,
    infer_actual_geometry,
)
from ovl.model import Position  # noqa: E402

FIX = os.path.join(REPO, "tests", "fixtures")
FILE1 = os.path.join(FIX, "6_2-115-10_1-10_20.ovl")
FILE2 = os.path.join(FIX, "6_2-115-10_42-10_57.ovl")


# --------------------------------------------------------------------------
# Structure / counts
# --------------------------------------------------------------------------

def test_file1_object_count():
    model = parse_ovl_file(FILE1)
    assert model.object_count == 22
    assert model.name == "6.2 115 10.1-10.20"
    assert model.create_time == 1409165629


def test_file2_object_count():
    model = parse_ovl_file(FILE2)
    assert model.object_count == 21
    assert model.name == "6.2 115 10.42-10.57"


def test_cto_count_matches_objects():
    for path, expected in [(FILE1, 22), (FILE2, 21)]:
        ctos = ovl_to_cto_dicts(parse_ovl_file(path))
        assert len(ctos) == expected
        assert all(c["object_class"] == "graphic" for c in ctos)


# --------------------------------------------------------------------------
# D2: affiliation from SIDC char-2
# --------------------------------------------------------------------------

def test_affiliation_friend_and_hostile_present():
    ctos = ovl_to_cto_dicts(parse_ovl_file(FILE1))
    affils = {c["attributes"]["affiliation"] for c in ctos}
    assert "friend" in affils
    assert "hostile" in affils


def test_affiliation_matches_sidc_char2():
    ctos = ovl_to_cto_dicts(parse_ovl_file(FILE1))
    for c in ctos:
        sidc = c["attributes"]["sidc"]
        expect = {"F": "friend", "H": "hostile", "N": "neutral", "U": "unknown"}.get(
            sidc[1], "unknown"
        )
        assert c["attributes"]["affiliation"] == expect


def test_eny_corroborates_hostile():
    # The GH... (hostile) objects in file1 carry N=ENY. Confirm the hostile
    # affiliation comes from the SIDC, and ENY is present in preserved modifiers.
    ctos = ovl_to_cto_dicts(parse_ovl_file(FILE1))
    hostiles = [c for c in ctos if c["attributes"]["affiliation"] == "hostile"]
    assert hostiles
    eny = [c for c in hostiles
           if c["attributes"]["modifiers"].get("N", {}).get("value") == "ENY"]
    assert eny, "expected at least one hostile object with N=ENY preserved"


# --------------------------------------------------------------------------
# D1: modifier fields preserved verbatim
# --------------------------------------------------------------------------

def test_modifiers_preserved_verbatim():
    ctos = ovl_to_cto_dicts(parse_ovl_file(FILE1))
    # Find a boundary (GLB) object that carries T / T1 modifiers.
    glb = [c for c in ctos if c["attributes"]["function_code"].startswith("GLB")]
    assert glb
    with_t = [c for c in glb if "T" in c["attributes"]["modifiers"]]
    assert with_t
    m = with_t[0]["attributes"]["modifiers"]["T"]
    assert m["value"] == "T MOD"
    assert m["vis"] is True


def test_vis_flags_roundtrip_bool():
    ctos = ovl_to_cto_dicts(parse_ovl_file(FILE1))
    for c in ctos:
        for k, m in c["attributes"]["modifiers"].items():
            assert isinstance(m["vis"], bool)


def test_file2_style_fields_preserved():
    # File 2 contains LINE_COLOR / FILL_COLOR / SIZE on some objects.
    model = parse_ovl_file(FILE2)
    assert any(o.line_color is not None for o in model.objects)


# --------------------------------------------------------------------------
# D3: geometry inference + conflict flag
# --------------------------------------------------------------------------

def test_geometry_point_line_area():
    assert infer_actual_geometry([Position(lat=1, lon=2)], "GFMPOFS-------X") == "Point"
    assert infer_actual_geometry(
        [Position(lat=1, lon=2), Position(lat=3, lon=4)], "GFGPGLB-------X"
    ) == "LineString"
    area = infer_actual_geometry(
        [Position(lat=1, lon=2), Position(lat=3, lon=4),
         Position(lat=5, lon=6), Position(lat=1, lon=2)],
        "GFGPGAA-------X",
    )
    assert area == "Polygon"


def test_py_conflict_flagged():
    # GFGPPY / GHGPPY are point-class SIDCs per 2525, but in file2 they are drawn
    # with multiple positions (LineString) => conflict flagged, vertices kept.
    ctos = ovl_to_cto_dicts(parse_ovl_file(FILE2))
    py = [c for c in ctos if c["attributes"]["function_code"].startswith("PY")]
    assert py, "expected a PY graphic in file2"
    conflicted = [c for c in py if c["attributes"]["geometry_conflict"]]
    assert conflicted, "expected PY multi-position graphic to be flagged as conflict"
    # SIDC says point, drawn shape is not a point, and geometry is preserved:
    assert conflicted[0]["attributes"]["sidc_geometry_class"] == "point"
    assert conflicted[0]["geometry"]["type"] in ("LineString", "Polygon")
    # vertices preserved (not collapsed to a point):
    assert len(conflicted[0]["geometry"]["coordinates"]) >= 2


def test_no_vertices_dropped():
    for path in (FILE1, FILE2):
        model = parse_ovl_file(path)
        ctos = ovl_to_cto_dicts(model)
        for obj, cto in zip(model.objects, ctos):
            g = cto["geometry"]
            if g["type"] == "Point":
                assert obj.position_count == 1
            elif g["type"] == "LineString":
                assert len(g["coordinates"]) == obj.position_count
            else:  # Polygon: ring may add a closing vertex
                ring = g["coordinates"][0]
                assert len(ring) in (obj.position_count, obj.position_count + 1)


def test_coordinate_order_lon_lat():
    # OVL stores 'lat lon'; CTO geometry must be [lon, lat] (GeoJSON order).
    # First object of file1: <POSITION>32.585233 43.972545</POSITION>
    ctos = ovl_to_cto_dicts(parse_ovl_file(FILE1))
    first = ctos[0]["geometry"]
    pt = first["coordinates"][0] if first["type"] != "Point" else first["coordinates"]
    lon, lat = pt
    assert 43 < lon < 45     # longitude ~43.97
    assert 32 < lat < 33     # latitude ~32.58


# --------------------------------------------------------------------------
# Integrated path: real CTO construction (runs only where cto_schema installed)
# --------------------------------------------------------------------------

def _have_cto_schema():
    try:
        import cto_schema  # noqa: F401
        return True
    except Exception:
        return False


def test_real_cto_construction():
    """When cto_schema is available (the integrated repo), ovl_to_ctos must
    build valid CTO objects: GRAPHIC class, OVL protocol, SIDC + affiliation in
    Symbology, label from NAME, modifiers preserved in attributes, geometry
    valid against the cto_schema validator."""
    if not _have_cto_schema():
        print("SKIP test_real_cto_construction (cto_schema not installed)")
        return
    from datetime import datetime, timezone
    from ovl.parser import ovl_to_ctos
    from cto_schema import ObjectClass, SourceProtocol, IngestSource

    data = open(FILE1, "rb").read()
    ctos = ovl_to_ctos(
        ovl_bytes=data,
        filename="6_2-115-10_1-10_20.ovl",
        source_system="ovl-folder:/inbox",
        received_at=datetime.now(timezone.utc),
        raw_pointer=None,
        ingest_source=IngestSource.FOLDER,
    )
    assert len(ctos) == 22
    c = ctos[0]
    assert c.object_class == ObjectClass.GRAPHIC
    assert c.source_protocol == SourceProtocol.OVL
    assert c.label == "10.15"               # first object's NAME in file1
    assert c.symbology.sidc_2525c == c.attributes["sidc"]
    assert c.event_time == c.received_at
    # affiliation populated on at least the hostile ones
    hostiles = [x for x in ctos if x.attributes["affiliation"] == "hostile"]
    assert hostiles and hostiles[0].symbology.affiliation is not None
