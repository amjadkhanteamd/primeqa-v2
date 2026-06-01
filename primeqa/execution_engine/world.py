"""Construct the operational world for a positive create (D-115 side B, k16).

S4 must put a *valid* record on the live org to verify a requirement's field
value persists — and the create call is the easy part. Before it can create, S4
fills the **operational padding**: the target object's required-on-create fields
that the recipe does *not* set, with type-valid filler. This is the k16 boundary
made real — S4 resolves operational validity (which fields must be present, with
what kind of value) but **never** the semantic value under test:

    writable padding set = {object's required-on-create fields}
                           − {lookups (no parent construction — the §3 fence)}
                           − {the semantic field-under-test (recipe-set)}

The semantic field is structurally excluded, so there is no code path by which
S4 writes the field it is verifying.

**"Required on create" for a REST create = ``is_nillable == False``** — the
database-level NOT-NULL. The page-layout ``is_required`` (S1 attributes JSONB) is
UI-only and does *not* gate an API create, so padding on it would over-fill and
risk a padding-caused rejection; we pad on ``is_nillable`` alone. Calculated
fields (formula / rollup) are not writable (excluded). Lookups and field types S4
cannot synthesize make the recipe **unrunnable in this slice** — collected into
``unfillable`` so the executor errors *honestly* rather than guessing a value
(the §3 scope fence: scalars / simple-picklist only).

Reads S1 through the ``SemanticOrgModel`` port (the S6-3 read-through pattern):
``get_entities`` → ``get_related(BELONGS_TO)`` → ``get_entity_details``. No raw
SQL of its own; no v1 imports.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Optional

_BELONGS_TO = "BELONGS_TO"

# Field types S4 fills with a type-valid scalar (the §3 fence: scalars +
# simple-picklist). ``field_type`` is Salesforce's ``DescribeField.type`` written
# verbatim by ``sync.detail_mappers``. Types absent here (reference / lookup,
# multipicklist, id, address, location, base64, anyType, time, encryptedstring,
# …) are unfillable in slice 1 — the executor errors rather than guessing.
_TEXTUAL = frozenset({"string", "text", "textarea", "combobox"})
_NUMERIC = frozenset({"int", "integer", "double", "currency", "percent"})

# Sentinel: the field exists + is required, but slice 1 cannot synthesize a value
# for its type. Distinct from "no value needed" (which simply skips the field).
_UNFILLABLE = object()


@dataclass(frozen=True)
class PaddingResult:
    """The operational padding for one positive create.

    ``filler`` is the ``{field_api: value}`` S4 merges into the create alongside
    the recipe's semantic field. ``unfillable`` names the required fields S4
    cannot synthesize (a lookup parent, or a type slice 1 does not fill) — a
    non-empty ``unfillable`` makes the recipe unrunnable (the executor errors
    pre-create, never guesses)."""

    filler: dict[str, Any]
    unfillable: tuple[str, ...]


def resolve_operational_padding(
    object_api: str, semantic_fields: set[str], *, s1, at_seq: int,
) -> PaddingResult:
    """Compute the operational padding for a create on ``object_api`` whose
    recipe-set fields are ``semantic_fields`` (k16 — never padded, so S4 cannot
    touch the value(s) under test).

    Reads requiredness + types from S1 via ``s1`` (a ``SemanticOrgModel``-shaped
    port: ``get_entities`` / ``get_related`` / ``get_entity_details``) at version
    ``at_seq``. Returns a :class:`PaddingResult`. Does **not** raise on a missing
    object — that surfaces as an empty filler (the create attempt against the
    live org then yields the real outcome, captured as evidence); only a genuine
    S1 read error propagates.
    """
    objs = s1.get_entities(
        "Object", at_seq=at_seq, filters={"sf_api_name": object_api})
    if not objs:
        # Object not in S1 — nothing to pad; the live create surfaces the real
        # outcome and S6 interprets the absence.
        return PaddingResult(filler={}, unfillable=())
    obj = objs[0]

    fields = s1.get_related(
        obj.id, edge_types=[_BELONGS_TO], direction="inbound", at_seq=at_seq)

    filler: dict[str, Any] = {}
    unfillable: list[str] = []
    for r in fields:
        fld = r.entity
        if fld.entity_type != "Field":
            continue
        api = fld.sf_api_name
        if not api or api in semantic_fields:     # k16 — never a recipe-set field
            continue
        details = s1.get_entity_details(fld.id, at_seq=at_seq) or {}
        # Required-on-create (REST) = NOT nillable. The page-layout is_required is
        # UI-only and does not gate an API create.
        if details.get("is_nillable", True):
            continue                              # optional / defaulted — skip
        if details.get("is_calculated", False):
            continue                              # formula / rollup — not writable
        # Lookups: no parent construction in slice 1 (the §3 fence).
        if details.get("references_object_entity_id") is not None:
            unfillable.append(api)
            continue
        value = _fill_value(details, s1, at_seq)
        if value is _UNFILLABLE:
            unfillable.append(api)
        else:
            filler[api] = value

    return PaddingResult(filler=filler, unfillable=tuple(sorted(unfillable)))


def _fill_value(details: dict, s1, at_seq: int):
    """A type-valid filler for a required scalar / simple-picklist, or the
    ``_UNFILLABLE`` sentinel. Format-aware for the constrained text types so the
    filler does not itself trip a format check (which would mis-read downstream as
    a value rejection)."""
    ftype = (details.get("field_type") or "").lower()
    if ftype in _NUMERIC:
        return 1
    if ftype == "boolean":
        return False
    if ftype == "date":
        return date.today().isoformat()
    if ftype == "datetime":
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if ftype == "email":
        return "pqa@example.com"
    if ftype == "url":
        return "https://example.com"
    if ftype == "phone":
        return "5555550100"
    if ftype in _TEXTUAL:
        length = details.get("length") or 0
        val = "PQA"
        return val[:length] if 0 < length < len(val) else val
    if ftype == "picklist":
        return _picklist_value(details, s1, at_seq)
    # reference is handled by the caller (references_object_entity_id); every
    # other type (multipicklist, id, address, location, base64, time, anyType, …)
    # is not synthesizable in slice 1.
    return _UNFILLABLE


def _picklist_value(details: dict, s1, at_seq: int):
    """The default (or first active, by sort order) value of a simple
    (value-set-backed) picklist, or ``_UNFILLABLE`` when the value set / an active
    value is not readable (an inline picklist whose set S1 did not capture —
    deferred)."""
    pvs_id = details.get("picklist_value_set_entity_id")
    if not pvs_id:
        return _UNFILLABLE
    values = s1.get_related(
        pvs_id, edge_types=[_BELONGS_TO], direction="inbound", at_seq=at_seq)
    active: list[tuple[int, str]] = []
    default: Optional[str] = None
    for r in values:
        pv = r.entity
        if pv.entity_type != "PicklistValue":
            continue
        d = s1.get_entity_details(pv.id, at_seq=at_seq) or {}
        if not d.get("is_active", True):
            continue
        name = d.get("value_api_name")
        if not name:
            continue
        if d.get("is_default", False) and default is None:
            default = name
        active.append((d.get("sort_order", 0), name))
    if default is not None:
        return default
    if active:
        active.sort(key=lambda t: t[0])
        return active[0][1]
    return _UNFILLABLE
