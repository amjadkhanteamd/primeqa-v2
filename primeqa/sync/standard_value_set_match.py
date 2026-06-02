"""Standard picklist field → StandardValueSet content-matching (D-118).

S1 Tier-2 slice 1. No Salesforce API exposes a *standard* picklist field's
StandardValueSet linkage (corrections-log §22): REST describe carries inline
``picklistValues`` only, standard fields are absent from the Tooling
``CustomField`` object, and ``FieldDefinition`` 400s at v66.0. So we detect the
link by **content-matching** — a standard field's active describe value set is
linked to the one synced StandardValueSet whose active value set is **exactly
equal**.

Policy: exact set-equality, **fail-closed** (D-118). 0 matches → no link
(honest absence); >1 (two SVSes with identical value lists) → no link
(ambiguous; refuse rather than guess); empty field set → no link. A *false*
link (wrong accepted-values → a wrong downstream test) is worse than a *missing*
one — so exact-match, not the looser subset/overlap tolerance (deferred to a
follow-up slice with its own disambiguation). Exact-match is high-confidence,
produces zero false links, and **self-heals**: if an admin edits the field's
values, the next sync's match fails and the edge correctly drops.

Pure functions — no DB, no I/O. Wired into ``phase_field`` (``phases.py``),
which sets the resulting ``_value_set_external_id = "SVS:{full_name}"`` marker
so the existing ``_map_field_details`` resolution + ``HAS_PICKLIST_VALUES`` edge
derivation emit the edge unchanged (no new edge type / column / migration —
lock-clean against the D-019 taxonomy + the D-024 design-lock).
"""
from __future__ import annotations

from typing import Optional


def field_active_value_names(field: dict) -> set:
    """A standard field's active picklist value api-names, from its REST
    describe ``picklistValues`` (each entry ``{value, label, active, ...}``).
    Skips inactive entries and entries missing ``value``."""
    return {
        pv["value"]
        for pv in (field.get("picklistValues") or [])
        if isinstance(pv, dict) and pv.get("value") and pv.get("active", True)
    }


def _svs_active_value_names(metadata: dict) -> set:
    """A StandardValueSet's active value api-names, from its Metadata
    ``standardValue`` list (each entry ``{valueName, label, isActive, ...}``).
    Mirrors ``extract_picklist_value_payloads_from_metadata``'s ``valueName`` +
    skip-non-dict / skip-missing-valueName handling, plus an ``isActive``
    filter so the comparison is active-values-on-both-sides."""
    return {
        v["valueName"]
        for v in (metadata.get("standardValue") or [])
        if isinstance(v, dict) and v.get("valueName") and v.get("isActive", True)
    }


def build_svs_index(svs_metadata_cache: dict) -> dict:
    """Index synced StandardValueSets by their active value set, for O(1)
    exact-match lookup. Maps ``frozenset(value api-names) -> [full_name, ...]``
    — a list, so two SVSes with identical value sets land together and are
    detected as ambiguous. Empty value sets are skipped (never a meaningful
    match). Built once per field phase from ``ctx.svs_metadata_cache``."""
    index: dict = {}
    for full_name, metadata in (svs_metadata_cache or {}).items():
        key = frozenset(_svs_active_value_names(metadata or {}))
        if not key:
            continue
        index.setdefault(key, []).append(full_name)
    return index


def match_standard_value_set(field_value_names: set, svs_index: dict) -> Optional[str]:
    """The single StandardValueSet ``full_name`` whose active value set equals
    the field's (exact set-equality, D-118), or ``None`` — fail-closed on an
    empty field set, 0 matches, or >1 (ambiguous identical-value SVSes)."""
    if not field_value_names:
        return None
    matches = svs_index.get(frozenset(field_value_names))
    if matches is not None and len(matches) == 1:
        return matches[0]
    return None
