# DoD Message Format Reference

`dod-message-formats-ref.pdf` is an excerpt of Appendices A through I from:

> Pradeep, Kris. *XML as a Data Exchange Medium for DoD Legacy Databases.*
> Naval Postgraduate School master's thesis, June 2002. Public release;
> distribution unlimited. DTIC identifier ADA405953.

We keep this excerpt in the repo because the appendices document the field
structure of several tactical message formats that the gateway may need to
ingest or emit in future phases. The thesis as a whole is not adopted as an
architectural reference — it predates CoT, TAK, the modern C2 stack, and the
canonical-object pattern this project uses. The appendices, however, are
useful as a concrete starting point if and when we need to wire up CIX or
USMTF parsing.

## What each appendix covers

| Appendix | Format  | Use case                                                     |
| -------- | ------- | ------------------------------------------------------------ |
| A        | MSGID   | Message identification header used by all CIX messages       |
| B        | LCTC    | Basic link track (general track identity and platform info)  |
| C        | XPOS    | Positional update for a link track                           |
| D        | LEXT    | Extended link track (additional attributes)                  |
| E        | BMISL   | Theater ballistic missile track                              |
| F        | CSITE   | Track correlation / site update                              |
| G        | CSEA    | Track correlation / sea-surface update                       |
| H        | CIX XML | XML wrapper structure proposed for CIX messages              |
| I        | WXOBS   | Weather observation (USMTF-format example for comparison)    |

## How to use it

Treat the field tables as **starting reference** for a format parser, not as
ground truth. The thesis is from 2002; field layouts may have evolved.
Whenever a real-world sample is available from the consuming or producing
system, use that sample as the authoritative reference and cross-check
against these tables.

If a future phase adds a CIX or USMTF normalizer, the relevant appendix
should be cited in that phase's ADR alongside any current spec we obtain.

## Why we did not keep the full thesis

The remaining chapters cover architectural arguments for XML as an exchange
medium, descriptions of legacy databases (AFATDS, JCDB, GCCS-I3, GCCS TDBM),
and source code for a 2002 Java generator program. None of that material
informs current design choices in this project. The architectural argument
is sound but has been superseded by 23 years of practice; we adopt the same
principle (canonical intermediate form, N+M translators) but implement it
with modern tooling (Pydantic, NATS, PostGIS, Python). The legacy database
descriptions are too dated to use as authoritative system documentation.
