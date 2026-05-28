# Reference — Agile Client Track Attribute Schema (live tracks)

Source: fields pulled by Chris from multiple tracks in Agile Client, May 2026.
Left = Agile label; parenthetical = observed example value(s).

**Object class:** these describe `track` CTOs (moving entities), NOT `graphic`
CTOs (OVL/KMZ control measures). Different object_class, different normalizer.

**Where this feeds:**
- Phase 1 (CoT): validates the track CTO; extends attributes (IFF, sensor, LTN, Track Type).
- Future OTH-Gold phase: THIS is the target field dictionary for the OTH-Gold
  normalizer, the same role the real .ovl files play for OVL ingest.

## Field groups (60 fields)

### Identity / label
Name (Unit Name), Short Name, Trademark, Hull Number, SCONUM, IRCS (WDQ4909),
UID, UID Amp, URN, LTN (T00TU5), GFMDIOUID, Last Sender UID, Reporting Resp UID (W0F)

### Kinematics  → CTO geometry + motion attributes
Position (334631N 0784849W — packed DMS, lat then lon),
Course (010.51 T — degrees true, may be blank),
Speed (483 KTS), Altitude (ft),
Average Speed, Motion Data (Land Site), Motion Model, Time on Leg

### Time
DTG (standard DTG format), Time Late, Num Reports, Max Rpts,
(CREATE/MODIFIED implicit)

### Classification / affiliation  → CTO affiliation (track-side source)
Category (AIR, LND, MER), Threat (NEU, FRD), Flag (Country),
PIF, DI, Ship Class (Unequated), Ship Type, TMS Track Type (General Track, Platform),
Track Type (Real World, Simulation), Track Scope (OTH),
AirCraft SubType (Commercial, General Aviation, Military), Provider Type

### IFF / transponder
Mode 1 IFF, Mode 2 IFF, Mode 3 IFF (0560),
Transponder Class ID, Transponder ID (SD:72078),
Transponder Type (DOSCRY, COTRTR)

### Provenance / source  → CTO capture provenance
Report Owner Producer (unknown), Source (FAA, 99CG99), Original source (FAA),
Originator (FAA), Sensor (OTR, SBUAS, BFT), Raw Data, Pairing Logic (False),
Ambiguity Reason

### Lineage / association  → CTO lineage (same pattern as parent_kmz_uri)
Parent, Entity Chain, Channel XREF, XRef, Hierarchy Level, JC3IEDM, UIC, Alert

## Decisions implied (carry into OTH-Gold phase scoping)
1. CTO.affiliation must derive from object_class-appropriate source:
   graphic => SIDC char-2; track => Threat/Flag/Category.
2. Coordinate normalizer must accept BOTH packed DMS (334631N 0784849W) and
   OVL decimal "lat lon".
3. Preserve all track fields verbatim in CTO.attributes (same rule as OVL modifiers).
4. Track Type Real World vs Simulation is a first-class attribute (exercise hygiene).
5. Raw Data field corroborates our capture-raw-bytes + hash-chain design.

## NOT in scope for Phase 2b
Phase 2b is OVL graphics only. This schema is banked for the OTH-Gold phase
and as a Phase 1 track-CTO enrichment backlog item.
