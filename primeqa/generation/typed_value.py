"""The typed value boundary (req-315 named abstraction, D-346).

A field's value lives in THREE domains and they are NOT interchangeable:

  - **Formula domain** — what a validation-rule / calc formula sees. A Percent is
    a FRACTION (20% is ``0.20``); a date is a ``Date``.
  - **Salesforce semantic domain** — the org's logical value: a picklist LABEL, a
    RecordType DeveloperName, a lookup's logical identity.
  - **Transport / API domain** — what the REST / Tooling API accepts: a Percent is
    the DISPLAY number (``20``); a RecordType is its 18-char Id; a lookup is a
    Salesforce Id; a date is ``'YYYY-MM-DD'``.

Derivation (:mod:`verified_negative`) and the D-107 VR-formula parser reason in the
**formula** domain; a recipe writes the **transport** domain. Conflating them
silently produces a test that never fires — the T8 percent trap: a formula-domain
``0.20`` written to the API as ``0.2%`` (which is ``0.002`` in formula domain), so
``Discount > 0.20`` never fires and the negative wrongly ACCEPTS.

This module is the single, typed FORMULA → TRANSPORT boundary, keyed on the S1
field type. Percent is the first (and, at v1, only) non-identity converter; the
other domains are NAMED here as the seam's future consumers so they land in one
place rather than as scattered one-offs.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from primeqa.generation.formula_expectation import _to_api_value, as_decimal

# Field types whose FORMULA value differs from their TRANSPORT value. Percent is
# the only value-scaling case today (formula fraction -> API display number ×100);
# every other numeric type is 1:1.
_TRANSPORT_SCALED = frozenset({"percent"})

# Named future consumers of this boundary (NOT converted at v1 — tracked so they
# land here, not as new one-offs):
#   - "date" / "datetime": formula Date -> 'YYYY-MM-DD' / ISO transport string.
#   - "picklist": semantic LABEL -> API value (handled at bind time by D-332).
#   - "reference" incl. RecordType: semantic name (RecordType DeveloperName) ->
#     the record's 18-char Id (the VR08 record-type gate needs this).
#   - "lookup": logical identity -> Salesforce Id.
_TRANSPORT_FUTURE = frozenset(
    {"date", "datetime", "reference", "recordtype", "lookup"})


# Numeric field types whose representable step is derivable from the field's
# declared scale (the S1 ``field_details.scale``). Mirrors verified_negative's
# numeric set; percent is special-cased below (its FORMULA domain sits two
# decimal places below its display scale).
_SCALED_NUMERIC = frozenset({"int", "integer", "double", "currency", "percent"})


def minimal_increment(field_type: Optional[str],
                      scale: Optional[int]) -> Optional[Decimal]:
    """The smallest representable step of a numeric field, in the **formula**
    domain — the increment a *minimally violating* witness moves by (AK,
    Amendment B step 3: a derived witness should be minimally violating where an
    ordered boundary is available, derived from the field's representable
    precision in the correct value domain — never an arbitrary ±1).

    A percent field's display scale is ``scale`` but its formula domain is the
    display value ÷100, so the formula-domain step is ``10^-(scale+2)`` (display
    scale 2 → ``0.0001``: ``> 0.25`` is minimally violated by ``0.2501``, which
    transports to ``25.01``). Every other numeric type steps ``10^-scale``.
    ``None`` when the type is non-numeric or the scale is unknown — the caller
    falls back to its existing behaviour (the certainty bar: never guess a
    step the field cannot represent)."""
    ft = (field_type or "").lower()
    if ft not in _SCALED_NUMERIC or scale is None:
        return None
    try:
        s = int(scale)
    except (TypeError, ValueError):
        return None
    if s < 0:
        return None
    return Decimal(1).scaleb(-(s + 2)) if ft == "percent" else Decimal(1).scaleb(-s)


def to_transport(value: Any, field_type: Optional[str],
                 scale: Optional[int] = None) -> Any:
    """One field VALUE mapped from FORMULA domain to TRANSPORT / API domain.

    Percent -> ×100 (reusing :func:`formula_expectation._to_api_value`, quantized
    to ``scale``); every other type passes through 1:1. Non-numeric values (None,
    strings, booleans) are untouched even on a percent field. JSON-safe (int when
    integral, else float)."""
    if (field_type or "").lower() not in _TRANSPORT_SCALED:
        return value
    dec = as_decimal(value)
    if dec is None:
        return value
    scaled = _to_api_value(dec, "percent", scale)
    return int(scaled) if scaled == scaled.to_integral_value() else float(scaled)


def transport_payload(payload: Optional[dict],
                      field_metadata: Optional[dict]) -> Optional[dict]:
    """Map every value in a derived (formula-domain) ``payload`` to transport
    domain, keyed on the bare field name against the D-294 metadata. Fields with
    absent / typeless metadata pass through. This IS the D-342 §1.3 percent fix,
    now the named boundary (was ``emission._to_transport_payload``). The violating
    value rides the RECIPE, not the identity-bearing claim (D-110.3), so the recipe
    grades identically."""
    if not payload or not field_metadata:
        return payload
    out = {}
    for f, v in payload.items():
        meta = field_metadata.get(f.rsplit(".", 1)[-1]) or {}
        out[f] = to_transport(v, meta.get("field_type"), meta.get("scale"))
    return out
