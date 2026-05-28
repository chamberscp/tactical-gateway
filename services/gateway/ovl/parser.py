"""OVL parser: parse a GCCS-J OVL file into the OvlModel, then convert each
milbobject into a Canonical Tactical Object (CTO).

Implements the three locked Phase 2b-1 decisions (docs/phase2b1-scope.md):

  D1  Preserve all 2525 modifier fields verbatim in CTO.attributes.modifiers.
  D2  Affiliation from SIDC character 2 (F/H/N/U).
  D3  Geometry: keep the actual drawn vertices as canonical geometry; record the
      SIDC-implied class; on conflict, SIDC governs symbol intent and we set
      geometry_conflict=true (vertices are never dropped).

The CTO produced here uses object_class="graphic" (vs "track" for CoT/OTH-Gold).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import List, Optional

from .model import OvlModel, MilbObject, Position, Modifier, MODIFIER_KEYS

# CTO schema lives in libs/cto_schema in the real repo. We import lazily / with a
# fallback shim so this module is unit-testable in isolation.
try:  # pragma: no cover - exercised in the integrated repo
    from cto_schema import CTO, Geometry  # type: ignore
    _HAVE_CTO = True
except Exception:  # pragma: no cover
    _HAVE_CTO = False

from sidc import (
    classify_affiliation,
    classify_geometry,
    function_code,
    is_known,
    GeometryClass,
)


# --------------------------------------------------------------------------
# XML -> OvlModel
# --------------------------------------------------------------------------

def _text(el: Optional[ET.Element]) -> str:
    return (el.text or "").strip() if el is not None else ""


def _bool(text: str) -> bool:
    return text.strip().lower() == "true"


def parse_milbobject(el: ET.Element) -> MilbObject:
    """Parse a single <milbobject> element into a MilbObject."""
    mil_id = _text(el.find("MIL_ID"))
    name = _text(el.find("NAME"))
    visibility = _bool(_text(el.find("VISIBILITY"))) if el.find("VISIBILITY") is not None else True

    # Modifiers: each X has a paired X_VIS. Preserve verbatim (D1).
    modifiers = {}
    for key in MODIFIER_KEYS:
        val_el = el.find(key)
        vis_el = el.find(f"{key}_VIS")
        if val_el is not None or vis_el is not None:
            modifiers[key] = Modifier(
                value=_text(val_el),
                vis=_bool(_text(vis_el)),
            )

    label_position = None
    lp_el = el.find("LABEL_POSITION")
    if lp_el is not None and _text(lp_el):
        label_position = Position.parse(_text(lp_el))

    line_color = _text(el.find("LINE_COLOR")) or None
    fill_color = _text(el.find("FILL_COLOR")) or None
    size = _text(el.find("SIZE")) or None

    positions: List[Position] = []
    for pos_el in el.findall("POSITION"):
        t = _text(pos_el)
        if t:
            positions.append(Position.parse(t))

    return MilbObject(
        mil_id=mil_id,
        name=name,
        visibility=visibility,
        modifiers=modifiers,
        label_position=label_position,
        line_color=line_color,
        fill_color=fill_color,
        size=size,
        positions=positions,
    )


def parse_ovl_bytes(data: bytes) -> OvlModel:
    """Parse raw OVL file bytes into an OvlModel."""
    root = ET.fromstring(data)
    if root.tag != "MODEL":
        raise ValueError(f"expected <MODEL> root, got <{root.tag}>")

    objects = [parse_milbobject(o) for o in root.findall("milbobject")]

    def _int_or_none(tag: str) -> Optional[int]:
        t = _text(root.find(tag))
        return int(t) if t.isdigit() else None

    return OvlModel(
        name=_text(root.find("NAME")),
        create_time=_int_or_none("CREATE_TIME"),
        modified_time=_int_or_none("MODIFIED_TIME"),
        objects=objects,
    )


def parse_ovl_file(path: str) -> OvlModel:
    with open(path, "rb") as f:
        return parse_ovl_bytes(f.read())


# --------------------------------------------------------------------------
# Geometry decision (D3)
# --------------------------------------------------------------------------

def infer_actual_geometry(positions: List[Position], sidc: str) -> str:
    """Geometry from the drawn vertices.

    1 vertex  -> Point
    2 vertices-> LineString
    3+ vertices-> Polygon if the SIDC is an area class (or ring is closed),
                  else LineString.
    """
    n = len(positions)
    if n <= 1:
        return "Point"
    if n == 2:
        return "LineString"
    # 3+ vertices: decide line vs polygon.
    sidc_class = classify_geometry(sidc)
    closed = (
        positions[0].lat == positions[-1].lat
        and positions[0].lon == positions[-1].lon
    )
    if sidc_class == GeometryClass.AREA or closed:
        return "Polygon"
    return "LineString"


def resolve_geometry(positions: List[Position], sidc: str):
    """Return (canonical_geometry_type, sidc_geometry_class, geometry_conflict).

    Canonical geometry follows the drawn vertices (never dropped). The SIDC class
    is recorded; if it disagrees with the drawn shape, SIDC governs symbol intent
    and we flag the conflict (D3).
    """
    actual = infer_actual_geometry(positions, sidc)
    sidc_class = classify_geometry(sidc)  # may be None if unknown

    # Map our actual-geometry label to a comparable GeometryClass for conflict
    # detection: Point->POINT, LineString->LINEAR, Polygon->AREA.
    actual_class = {
        "Point": GeometryClass.POINT,
        "LineString": GeometryClass.LINEAR,
        "Polygon": GeometryClass.AREA,
    }[actual]

    conflict = sidc_class is not None and sidc_class != actual_class
    sidc_class_str = sidc_class.value if sidc_class is not None else None
    return actual, sidc_class_str, conflict


# --------------------------------------------------------------------------
# MilbObject -> CTO
# --------------------------------------------------------------------------

def milbobject_to_cto_dict(obj: MilbObject, parent_ovl_uri: str = "") -> dict:
    """Convert a MilbObject to a CTO-shaped dict.

    Returns a plain dict so the function is testable without the cto_schema
    package; the integrated gateway wraps this into a CTO model (see
    milbobject_to_cto).
    """
    affiliation = classify_affiliation(obj.mil_id)
    geom_type, sidc_class, conflict = resolve_geometry(obj.positions, obj.mil_id)

    # Build canonical geometry coordinates. Our CTO Geometry stores [lon, lat]
    # (GeoJSON order); OVL gives lat/lon, so we swap here at the boundary.
    coords = [[p.lon, p.lat] for p in obj.positions]
    if geom_type == "Point":
        geometry = {"type": "Point", "coordinates": coords[0] if coords else None}
    elif geom_type == "LineString":
        geometry = {"type": "LineString", "coordinates": coords}
    else:  # Polygon -- wrap as a single linear ring, closing it if needed
        ring = coords[:]
        if ring and ring[0] != ring[-1]:
            ring = ring + [ring[0]]
        geometry = {"type": "Polygon", "coordinates": [ring]}

    # D1: modifiers preserved verbatim.
    modifiers = {
        k: {"value": m.value, "vis": m.vis} for k, m in obj.modifiers.items()
    }

    attributes = {
        "sidc": obj.mil_id,
        "affiliation": affiliation.value,            # D2
        "function_code": function_code(obj.mil_id),
        "sidc_geometry_class": sidc_class,           # D3 (may be None)
        "geometry_conflict": conflict,               # D3
        "sidc_known": is_known(obj.mil_id),
        "visibility": obj.visibility,
        "modifiers": modifiers,                      # D1
    }
    if obj.label_position is not None:
        attributes["label_position"] = [obj.label_position.lon, obj.label_position.lat]
    if obj.line_color is not None:
        attributes["line_color"] = obj.line_color
    if obj.fill_color is not None:
        attributes["fill_color"] = obj.fill_color
    if obj.size is not None:
        attributes["size"] = obj.size
    if parent_ovl_uri:
        attributes["parent_ovl_uri"] = parent_ovl_uri

    return {
        "object_class": "graphic",
        "name": obj.name,
        "geometry": geometry,
        "attributes": attributes,
    }


def ovl_to_cto_dicts(model: OvlModel, parent_ovl_uri: str = "") -> List[dict]:
    """Convert every milbobject in an overlay to CTO-shaped dicts."""
    return [milbobject_to_cto_dict(o, parent_ovl_uri) for o in model.objects]


if _HAVE_CTO:  # pragma: no cover - integrated path
    def milbobject_to_cto(obj: MilbObject, parent_ovl_uri: str = "") -> "CTO":
        d = milbobject_to_cto_dict(obj, parent_ovl_uri)
        return CTO(
            object_class=d["object_class"],
            name=d["name"],
            geometry=Geometry(**d["geometry"]),
            attributes=d["attributes"],
        )
