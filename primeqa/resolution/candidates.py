"""Phase A — per-node candidate generation (recall) + the deterministic
field/state ladders.

The ladders mirror the B1 field-name resolver
(``governance_core._resolve_subject_field_name``): unique-match-only — 0 or
>1 candidates → ``None``, never a guess. Object candidacy is admission-only
here (exact api / exact label / lexical >= MIN_SCORE); ORDERING is the
solver's job (Phase B dominance), so no cap is applied — the pool is an org's
object list (~150), not an entity dump.
"""
from __future__ import annotations

from typing import Optional

from primeqa.resolution import similarity as sim
from primeqa.resolution.resolved import CandidateEvidence
from primeqa.resolution.symbols import FieldSymbol, ObjectSymbol, SymbolTable


def _strict_label_norm(text: Optional[str]) -> str:
    """Case/underscore/whitespace-insensitive label form — SF suffixes are
    NOT stripped: an API-shaped guess ("Order__c") must never count as an
    exact match of a business label ("Order"). The suffix-tolerant form
    (``similarity.norm_label``) stays reserved for the FIELD ladder, whose
    unique-match-only contract makes it safe there."""
    return " ".join((text or "").lower().replace("_", " ").split())


def object_candidates(term: str, table: SymbolTable,
                      requirement_text: Optional[str] = None,
                      ) -> list[tuple[ObjectSymbol, CandidateEvidence]]:
    """All objects admitted against ``term``, each with its feature vector.
    Admission: exact api-name, exact (strictly normalized) label, or lexical
    similarity >= MIN_SCORE. Deterministic; api-name-sorted (the solver
    re-orders by dominance)."""
    if not (term or "").strip():
        return []
    term = term.strip()
    term_l = term.lower()
    term_norm = _strict_label_norm(term)
    ctx_tokens = (frozenset(sim.tokenize(requirement_text))
                  if requirement_text else frozenset())
    out: list[tuple[ObjectSymbol, CandidateEvidence]] = []
    for obj in table.objects:
        if not obj.api_name:
            continue
        exact_api = obj.api_name.lower() == term_l
        exact_label = bool(term_norm) and _strict_label_norm(obj.label) == term_norm
        lexical = round(sim.similarity(term, obj.api_name, obj.label), 4)
        if not (exact_api or exact_label or lexical >= sim.MIN_SCORE):
            continue
        context = sim.context_overlap(obj.api_name, obj.label, ctx_tokens)
        out.append((obj, CandidateEvidence(
            sf_api_name=obj.api_name, entity_type="Object",
            features={"exact_api": exact_api, "exact_label": exact_label,
                      "lexical": lexical, "context": context})))
    return out


def resolve_field(obj: ObjectSymbol, name: Optional[str]
                  ) -> Optional[FieldSymbol]:
    """The 4-rule unique-match ladder onto ``obj``'s own field inventory:
    exact qualified api-name → unique bare (ci) → unique ``_``-suffix (ci) →
    unique normalized label. 0-or->1 at any rule that fires → ``None``."""
    if not obj or not (name or "").strip():
        return None
    n = name.strip()
    nl = n.lower()
    for f in obj.fields:
        if f.qualified_api_name and f.qualified_api_name.lower() == nl:
            return f
    bare = nl.split(".", 1)[1] if "." in nl else nl
    hits = [f for f in obj.fields if (f.api_name or "").lower() == bare]
    if hits:
        return hits[0] if len(hits) == 1 else None
    hits = [f for f in obj.fields
            if (f.api_name or "").lower().endswith("_" + bare)]
    if hits:
        return hits[0] if len(hits) == 1 else None
    norm = sim.norm_label(bare)
    if not norm:
        return None
    hits = [f for f in obj.fields if f.label and sim.norm_label(f.label) == norm]
    return hits[0] if len(hits) == 1 else None


def resolve_state(fld: Optional[FieldSymbol], value_term: Optional[str]
                  ) -> Optional[str]:
    """Unique-match a business state word onto the field's picklist values
    (api name or label, ci). Returns the value api-name, or ``None`` (also
    for non-picklist fields)."""
    if fld is None or not (value_term or "").strip() or not fld.picklist_values:
        return None
    v = value_term.strip().lower()
    hits = [api for api, label in fld.picklist_values
            if api.lower() == v or (label or "").strip().lower() == v]
    # dedup (api==label collisions produce one hit twice)
    uniq = sorted(set(hits))
    return uniq[0] if len(uniq) == 1 else None
