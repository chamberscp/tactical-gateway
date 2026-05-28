"""Pydantic model of the real GCCS-J / Agile Server OVL schema.

Ground truth is the planner OVL files under tests/fixtures/, NOT the
<MilStdSymbol> form emitted by the kml2xml binary. The real on-disk schema is:

    <MODEL>
      <milbobject>
        <MIL_ID>15-char SIDC</MIL_ID>
        <NAME>label</NAME>
        <VISIBILITY>true|false</VISIBILITY>
        <T>..</T> <T_VIS>..</T_VIS>          (2525 text amplifiers, optional)
        <T1/> <N/> <W/> <H/> <Q/> <Y/> <W1/> (+ paired _VIS flags)
        <LABEL_POSITION>lat lon</LABEL_POSITION>   (optional)
        <LINE_COLOR/> <FILL_COLOR/> <SIZE/>        (optional styling)
        <POSITION>lat lon</POSITION>               (1+, "lat lon" lat-first)
      </milbobject>
      ...
      <CREATE_TIME>epoch</CREATE_TIME>
      <MODIFIED_TIME>epoch</MODIFIED_TIME>
      <NAME>overlay name</NAME>
    </MODEL>

This model is deliberately faithful and lossless: every modifier and its _VIS
flag is preserved so Phase 2b-2 egress can reconstruct the file exactly.
"""
from __future__ import annotations

from typing import List, Optional, Dict

try:
    from pydantic import BaseModel, Field
except ImportError:  # lightweight fallback for environments without pydantic
    def Field(default=None, default_factory=None):  # type: ignore
        return default_factory() if default_factory is not None else default

    class BaseModel:  # type: ignore
        def __init__(self, **data):
            ann = {}
            for klass in reversed(type(self).__mro__):
                ann.update(getattr(klass, "__annotations__", {}))
            for key in ann:
                if key in data:
                    setattr(self, key, data[key])
                elif hasattr(type(self), key):
                    default = getattr(type(self), key)
                    setattr(self, key, default() if callable(default) else default)
                else:
                    setattr(self, key, None)
            for key, val in data.items():
                if key not in ann:
                    setattr(self, key, val)


# The set of MIL-STD-2525 text amplifier modifiers seen in real OVLs. Each has a
# value element and a paired <X_VIS> boolean. Listed explicitly so the parser
# and the egress emitter agree on ordering and completeness.
MODIFIER_KEYS = ["T", "T1", "N", "W", "W1", "H", "Q", "Y"]


class Position(BaseModel):
    """A single geographic vertex. OVL stores 'lat lon' (latitude first)."""
    lat: float
    lon: float

    @classmethod
    def parse(cls, text: str) -> "Position":
        parts = text.strip().split()
        if len(parts) != 2:
            raise ValueError(f"POSITION must be 'lat lon', got: {text!r}")
        return cls(lat=float(parts[0]), lon=float(parts[1]))

    def to_ovl(self) -> str:
        return f"{self.lat} {self.lon}"


class Modifier(BaseModel):
    """A 2525 text amplifier: its value and whether it is displayed."""
    value: str = ""
    vis: bool = False


class MilbObject(BaseModel):
    """One graphic in an overlay (the <milbobject> element)."""
    mil_id: str                              # <MIL_ID> 15-char SIDC
    name: str = ""                           # <NAME>
    visibility: bool = True                  # <VISIBILITY>
    modifiers: Dict[str, Modifier] = Field(default_factory=dict)  # T/T1/N/W/...
    label_position: Optional[Position] = None  # <LABEL_POSITION>
    line_color: Optional[str] = None         # <LINE_COLOR>
    fill_color: Optional[str] = None         # <FILL_COLOR>
    size: Optional[str] = None               # <SIZE>
    positions: List[Position] = Field(default_factory=list)  # <POSITION>+

    @property
    def position_count(self) -> int:
        return len(self.positions)


class OvlModel(BaseModel):
    """The whole overlay (the <MODEL> element)."""
    name: str = ""                           # trailing <NAME>
    create_time: Optional[int] = None        # <CREATE_TIME> epoch seconds
    modified_time: Optional[int] = None      # <MODIFIED_TIME> epoch seconds
    objects: List[MilbObject] = Field(default_factory=list)

    @property
    def object_count(self) -> int:
        return len(self.objects)
