"""The temporal value protocol — replay-stable relative dates (VR06 arc).

The first TEMPORAL context capability (AK, 2026-07-10): a test value expressed
RELATIVE to the run's temporal reference point, materialised exactly once at
the execution boundary. Three layers stay separate (the Percent discipline,
D-346):

    semantic constraint     Contract_Start_Date < TODAY()      (the formula)
    test-design value       RelativeDate(RUN_DATE, -1)         (this protocol)
    transport value         "2026-07-09"                       (S4, at materialisation)

The SEMANTIC layer never holds a calendar literal; the RECIPE persists the
symbolic value (inspectable + replay-stable — regenerating or re-running the
recipe re-anchors to the new run's reference, never a frozen date); only the
execution boundary turns it into a Salesforce API date string.

Bounded on purpose: ``MaterialisableValue ─ RelativeDate`` is the ONLY symbolic
value kind. The invariant split it preserves (v23-era): *unresolved* values
never cross the execution boundary; *deterministic materialisable* values may
cross authoring boundaries. The S4 boundary refuses any OTHER ``$``-shaped
symbolic value before touching Salesforce.

The wire encoding is a plain JSON dict (recipe payloads are ``dict[str, Any]``):

    {"$relative_date": {"anchor": "RUN_DATE", "offset_days": -1}}
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Optional

RELATIVE_DATE_KEY = "$relative_date"
_ANCHOR_RUN_DATE = "RUN_DATE"


def relative_date(offset_days: int) -> dict:
    """The test-design value ``RelativeDate(RUN_DATE, offset_days)`` in its
    wire encoding."""
    return {RELATIVE_DATE_KEY: {"anchor": _ANCHOR_RUN_DATE,
                                "offset_days": int(offset_days)}}


def is_relative_date(value: Any) -> bool:
    """Is ``value`` a well-formed RelativeDate wire dict?"""
    if not isinstance(value, dict) or set(value) != {RELATIVE_DATE_KEY}:
        return False
    body = value[RELATIVE_DATE_KEY]
    return (isinstance(body, dict)
            and body.get("anchor") == _ANCHOR_RUN_DATE
            and isinstance(body.get("offset_days"), int)
            and not isinstance(body.get("offset_days"), bool))


def relative_date_offset(value: Any) -> Optional[int]:
    """The offset_days of a RelativeDate value, or ``None``."""
    return (value[RELATIVE_DATE_KEY]["offset_days"]
            if is_relative_date(value) else None)


def is_symbolic(value: Any) -> bool:
    """Any ``$``-keyed symbolic dict — the S4 boundary's refuse-net: a symbolic
    value it does not recognize must error BEFORE Salesforce, never be posted."""
    return (isinstance(value, dict) and len(value) == 1
            and next(iter(value), "").startswith("$"))


def materialise(value: Any, reference_date: date) -> Any:
    """One value mapped through the materialisation boundary: a RelativeDate
    becomes its ISO date string anchored at ``reference_date``; everything else
    passes through untouched. Raises ``ValueError`` on an unrecognized symbolic
    value (the refuse-before-Salesforce net)."""
    if is_relative_date(value):
        return (reference_date
                + timedelta(days=value[RELATIVE_DATE_KEY]["offset_days"])
                ).isoformat()
    if is_symbolic(value):
        raise ValueError(
            f"unrecognized symbolic value {value!r} at the materialisation "
            f"boundary — unresolved values never cross into Salesforce")
    return value


@dataclass(frozen=True)
class TemporalReference:
    """The run's SINGLE temporal reference point — one owner, captured once
    per run, so generation time, batch time, worker-local time, and Salesforce
    org time cannot silently diverge. ``source`` records how the reference was
    obtained (e.g. ``organization_timezone`` — the org-default TimeZoneSidKey,
    the closest cheap proxy for the date semantics a VR's ``TODAY()`` sees —
    or ``utc_fallback``)."""
    reference_date: date
    reference_timezone: str
    captured_at: str            # ISO instant, diagnostics only
    source: str
