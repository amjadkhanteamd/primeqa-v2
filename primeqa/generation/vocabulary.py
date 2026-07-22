"""ORG FIELD VOCABULARY builder (D-378) — the proactive half of
offer → select → verify, aimed at the field-invention residue the ladder
cannot reach (``Commercial_Tier__c`` for ``PLS_FB_Tier__c``).

Builds a deterministic, retrieval-narrowed vocabulary block for the initial
user message: the org's REAL field names (labels + picklist values) for the
few objects most relevant to the requirement text. Data, never prose — the
frozen v30 prompt carries the contract; this module only supplies facts read
from the pinned S1 snapshot.

Anti-dump discipline (the D-362 law): a handful of ranked objects with capped,
custom-first field lists and DISCLOSED truncation — never a directory. The
custom-first ordering is the FB-V1 lesson: alphabetized inventories buried
every custom name the model actually needed behind standard ones.

Fail-soft everywhere: no admissible object, an empty table, or any error →
``""`` and the message keeps its pre-v30 shape.
"""
from __future__ import annotations

import logging
from typing import Optional

from primeqa.generation.recovery import _phrase_tokens
from primeqa.resolution import similarity as _sim
from primeqa.resolution.symbols import ObjectSymbol, SymbolTable

log = logging.getLogger(__name__)

MAX_OBJECTS = 3
MAX_ADJACENT = 4
MAX_FIELDS_PER_OBJECT = 40
MAX_PICKLIST_VALUES = 6

HEADER = "ORG FIELD VOCABULARY"

# Universal audit fields every object carries — never the vocabulary a model
# mis-invents, and 7 lines of noise per object. OwnerId / Name stay (they are
# behaviorally meaningful).
_AUDIT_FIELDS = frozenset(
    ("Id", "IsDeleted", "CreatedById", "CreatedDate", "LastModifiedById",
     "LastModifiedDate", "SystemModstamp"))


def _rank_objects(table: SymbolTable, requirement_text: str
                  ) -> list[ObjectSymbol]:
    """Deterministic relevance ranking of the org's objects against the
    requirement text, by COMPLETENESS before volume (the D-364 lesson — raw
    token counts let long incidental names outrank the requirement's own
    entity):

      1. verbatim multi-word label presence (the B0.1 exact-label signal —
         "PLS FB Order" appearing verbatim is the strongest evidence);
      2. own-token coverage ratio (ALL of a candidate's name tokens present
         beats more-but-partial overlap);
      3. raw context-hit count; api name as the total-order tail.

    Admission still requires a context hit — an unrelated org yields
    nothing, not a directory."""
    ctx_tokens = frozenset(_sim.tokenize(requirement_text))
    scored = []
    for obj in table.objects:
        if not obj.api_name or not obj.fields:
            continue
        context = _sim.context_overlap(obj.api_name, obj.label, ctx_tokens)
        if context < 1:
            continue
        phrase = _phrase_tokens(obj.label, requirement_text)
        ratio = 0.0
        for name in (obj.api_name, obj.label):
            toks = frozenset(_sim.tokenize(name))
            if toks:
                ratio = max(ratio, len(toks & ctx_tokens) / len(toks))
        scored.append(((-phrase, -round(ratio, 4), -context, obj.api_name),
                       obj))
    scored.sort(key=lambda t: t[0])
    return [obj for _, obj in scored[:MAX_OBJECTS]]


def _adjacent_objects(table: SymbolTable, top: ObjectSymbol,
                      exclude: set) -> list[ObjectSymbol]:
    """D-379: CHILDREN of the top-ranked object — objects whose fields
    reference it via lookup/master-detail. This closes the AC15-class
    retrieval gap structurally: a requirement about Orders exposes the
    Order's child neighborhood (Audit Log, Ledger Entry, Order Line, …) even
    when those names share no tokens with the requirement text.

    Deliberately ONE direction: objects the top REFERENCES are excluded —
    the live render showed OwnerId-style references dragging the 170-field
    User object into the block (the D-362 directory-dump class). Children
    pointing IN are where behavior claims about the top entity live.
    Deterministic (api-name order), capped at MAX_ADJACENT."""
    top_l = (top.api_name or "").lower()
    adj = []
    for obj in table.objects:
        if not obj.api_name or not obj.fields or obj.api_name in exclude:
            continue
        if any(f.references_object and f.references_object.lower() == top_l
               for f in obj.fields):
            adj.append(obj)
    adj.sort(key=lambda o: o.api_name)
    return adj[:MAX_ADJACENT]


def _field_line(f) -> str:
    label = f' ("{f.label}")' if f.label and f.label != f.api_name else ""
    tail = ""
    if f.picklist_values:
        vals = [api for api, _ in f.picklist_values[:MAX_PICKLIST_VALUES]]
        more = len(f.picklist_values) - len(vals)
        tail = " picklist: " + " | ".join(vals) + (f" (+{more} more)" if more > 0 else "")
    elif f.field_type:
        tail = f" {f.field_type}"
    return f"  - {f.api_name}{label}{tail}"


def build_field_vocabulary(table: Optional[SymbolTable],
                           requirement_text: Optional[str]) -> str:
    """The block, or ``""`` when nothing admissible / no table / any error."""
    if table is None or not (requirement_text or "").strip():
        return ""
    try:
        objects = _rank_objects(table, requirement_text)
        if not objects:
            return ""
        lines = [
            f"{HEADER} (real field api names at the pinned org version; "
            "copy listed names VERBATIM):"]
        for obj in objects:
            lines.extend(_object_section(obj))
        # D-379: the top object's lookup neighborhood — objects a requirement
        # about the top entity is likely to touch even when their names share
        # no tokens with the requirement text (the AC15 retrieval gap).
        adjacent = _adjacent_objects(
            table, objects[0], exclude={o.api_name for o in objects})
        if adjacent:
            lines.append(f"Related to {objects[0].api_name} via lookup:")
            for obj in adjacent:
                lines.extend(_object_section(obj))
        return "\n".join(lines)
    except Exception:   # noqa: BLE001 — vocabulary must never break generation
        log.debug("D-378 vocabulary build failed (omitting block)",
                  exc_info=True)
        return ""


def _object_section(obj: ObjectSymbol) -> list[str]:
    """One object's block lines: header + custom-first fields (the FB-V1
    inventory lesson), universal audit fields dropped, disclosed truncation."""
    label = f' ("{obj.label}")' if obj.label else ""
    lines = [f"Object {obj.api_name}{label}:"]
    fields = sorted(
        (f for f in obj.fields if f.api_name not in _AUDIT_FIELDS),
        key=lambda f: (not f.api_name.endswith("__c"), f.api_name))
    shown = fields[:MAX_FIELDS_PER_OBJECT]
    lines.extend(_field_line(f) for f in shown)
    dropped = len(fields) - len(shown)
    if dropped > 0:
        lines.append(f"  … +{dropped} more fields (ask via a "
                     "best-label proposal; the substrate offers "
                     "grounded alternatives)")
    return lines
