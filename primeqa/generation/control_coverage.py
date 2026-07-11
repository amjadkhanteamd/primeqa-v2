"""Control read-model — Phase 0 of control-oriented coverage (READ-ONLY telemetry).

The VRB-V1 post-merge regression analysis (2026-07-11) showed AC-level coverage
is blind to CONTROL-level loss: an AC stays "covered" while the control that
implements it silently goes unexercised (AC6 "covered" by a VR10-bound claim,
VR08 never exercised; AC4 "covered" by the VR06 claim, VR04 lost). This module
makes that loss MEASURABLE — it derives, from artifacts the pipeline already
persists, where every org control stands on a lifecycle:

    EXPECTED < NOMINATED < SELECTED < EMITTED < EXECUTED < ATTRIBUTED

- **EXPECTED** — the control is active and governs the requirement's subject
  (it is in the grounding neighborhood). The denominator.
- **NOMINATED** — some attempted intent's condition fields overlap the
  control's formula fields (it entered a selection candidate set).
- **SELECTED** — a claim's recipes pin this control's own firing signature
  (its error message, D-297) — the selector bound it.
- **EMITTED** — the claim + recipes were persisted. At generation time
  SELECTED and EMITTED coincide (finalize stamps EMITTED); they diverge only
  in reporting (e.g. an identity-dedup no-op keeps SELECTED without a fresh
  EMITTED row).
- **EXECUTED** — an S4 run exists for an emitted recipe bound to the control.
- **ATTRIBUTED** — run evidence shows the control fired for the intended
  reason (S6 ``prohibition_enforced`` with the message matched, D-297/D-345).

Read-only by contract: nothing here may refuse, select, author, or re-prompt —
it OBSERVES. The map rides ``attempted_interpretation["control_coverage"]``
(``extra='allow'``); ``explanation_hash`` canonicalizes only its four fixed
keys, so attaching the map cannot re-key any outcome (verified by test).

**PROMOTION BOUNDARY (D-361): recovery, prompting, and grounding MUST NOT
depend on this telemetry.** No selector, re-prompt loop, coverage enforcer,
prompt builder, or grounding gate may read ``control_coverage`` or
``state.control_facts`` to change what it does — the map is an observation of
the pipeline, never an input to it. This boundary holds until a future
architectural decision explicitly promotes the read-model to a behavioural
input (the Phase-1 control-oriented-recovery question, deliberately deferred
so the first Flow benchmark measures the UNADAPTED architecture).

Mechanism-agnostic on purpose: a ``ControlFact`` carries ``mechanism`` so the
same lifecycle serves Flows / Approval Processes / formula fields when their
benchmarks arrive; Phase 0 derives ``validation_rule`` facts only.

Pure module: entity-attribute accessors + the D-107 formula parser only — no
DB, no S1 port, no governance imports (governance imports THIS)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from primeqa.semantic.entity_attributes import (
    vr_error_message, vr_formula_text, vr_is_active)
from primeqa.semantic.formula import FieldRef, is_parsed, parse, walk

# Lifecycle stages, weakest -> strongest. Strings (not IntEnum) — the map is a
# persisted JSONB artifact and the report script compares by rank.
EXPECTED = "expected"
NOMINATED = "nominated"
SELECTED = "selected"
EMITTED = "emitted"
EXECUTED = "executed"
ATTRIBUTED = "attributed"
STAGES = (EXPECTED, NOMINATED, SELECTED, EMITTED, EXECUTED, ATTRIBUTED)

MECHANISM_VALIDATION_RULE = "validation_rule"

COVERAGE_VERSION = 1


def stage_rank(stage: str) -> int:
    """Ordinal of ``stage`` in the lifecycle (-1 for an unknown string, so a
    corrupted persisted stage sorts below EXPECTED rather than raising)."""
    try:
        return STAGES.index(stage)
    except ValueError:
        return -1


@dataclass(frozen=True)
class ControlFact:
    """One org control the requirement's subject is governed by.

    ``firing_signature`` is the mechanism-specific evidence key that proves
    THIS control acted — for a validation rule, its user-facing error message
    (the string emission escapes into ``error_message_pattern``, D-297)."""

    mechanism: str               # MECHANISM_* discriminator
    control_ref: str             # stable S1 identity (VR sf_api_name)
    subject_ref: str             # the governed subject (Object sf_api_name)
    formula_text: Optional[str]  # the behaviour definition (None: unparsed row)
    firing_signature: Optional[str]  # VR error message (None: message-less row)
    active: bool


def formula_fields(text: Optional[str]) -> frozenset[str]:
    """The bare, lower-cased field api-names a VR formula references — the
    local mirror of ``governance_core._vr_formula_fields`` (kept byte-equivalent
    there; duplicated because governance imports this module, so importing back
    would cycle). Unparseable/absent formulas contribute NO fields."""
    if not text:
        return frozenset()
    ast = parse(text)
    if not is_parsed(ast):
        return frozenset()
    return frozenset(
        node.path[-1].lower()
        for node in walk(ast)
        if isinstance(node, FieldRef) and node.path)


def controls_from_neighborhood(subject_api_name: str,
                               neighborhood: Iterable) -> list[ControlFact]:
    """Derive the subject's ``validation_rule`` control facts from a grounding
    neighborhood (the ``scoped_neighborhood`` row shape: each row carries an
    ``entity`` with ``entity_type`` / ``sf_api_name`` / ``attributes``).
    Deterministic order (by control_ref); duplicates collapse to first."""
    seen: dict[str, ControlFact] = {}
    for r in neighborhood or ():
        e = getattr(r, "entity", None)
        if e is None or getattr(e, "entity_type", None) != "ValidationRule":
            continue
        api = getattr(e, "sf_api_name", None)
        if not api or api in seen:
            continue
        attrs = getattr(e, "attributes", None)
        seen[api] = ControlFact(
            mechanism=MECHANISM_VALIDATION_RULE,
            control_ref=api,
            subject_ref=subject_api_name,
            formula_text=vr_formula_text(attrs),
            firing_signature=vr_error_message(attrs),
            active=vr_is_active(attrs),
        )
    return [seen[k] for k in sorted(seen)]


def match_signature(pattern: Optional[str],
                    facts: Iterable[ControlFact]) -> list[ControlFact]:
    """The controls whose firing signature the recipe's
    ``error_message_pattern`` pins. Emission mints the pattern as
    ``re.escape(message)`` (D-297), so ``fullmatch`` against each control's own
    message inverts it exactly; an invalid pattern matches nothing (observe,
    never raise). >=2 matches = an ambiguous signature — the caller records
    the ambiguity, it never picks."""
    if not pattern:
        return []
    out = []
    for f in facts:
        if f.firing_signature is None:
            continue
        try:
            if re.fullmatch(pattern, f.firing_signature):
                out.append(f)
        except re.error:
            return []
    return out


# -- persisted-artifact extraction (duck-typed: pydantic bodies OR the JSONB
#    dicts they persist as — the report script replays the same functions) ----

def _get(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def condition_fields(semantic_conditions: Any) -> frozenset[str]:
    """The bare, lower-cased condition-field names of one claim's
    ``semantic_conditions`` body (v1 or v2, object or persisted dict) — the
    claim side of the NOMINATED overlap, mirroring the shape
    ``governance_core._claim_condition_fields`` reads at selection time.
    A v2 cross-field clause contributes BOTH its fields (D-330)."""
    out: set[str] = set()
    for cond in _get(semantic_conditions, "conditions") or ():
        for ref_name in ("subject", "compared_to"):
            ref = _get(cond, ref_name)
            ext = _get(ref, "external_id") if ref is not None else None
            if ext:
                out.add(str(ext).rsplit(".", 1)[-1].lower())
    return frozenset(out)


def rejection_patterns(recipe_body: Any) -> list[str]:
    """Every ``expect_rejection.error_message_pattern`` a recipe body's steps
    carry (object or persisted dict) — the emitted-side firing signatures."""
    out: list[str] = []
    for step in _get(recipe_body, "steps") or ():
        rej = _get(step, "expect_rejection")
        if rej is None:
            continue
        pattern = _get(rej, "error_message_pattern")
        if pattern:
            out.append(pattern)
    return out


def _bundle_recipe_bodies(bundle: Any) -> list[Any]:
    """All recipe bodies one emission bundle carries: the primary
    ``observation_realization`` plus each secondary / boundary recipe's."""
    bodies = []
    primary = _get(bundle, "observation_realization")
    if primary is not None:
        bodies.append(primary)
    for slot in ("secondary_recipes", "boundary_recipes"):
        for rec in _get(bundle, slot) or ():
            body = _get(rec, "observation_realization")
            if body is not None:
                bodies.append(body)
    return bodies


# -- the map ------------------------------------------------------------------

def build_coverage_map(facts: Iterable[ControlFact],
                       condition_field_sets: Iterable[frozenset[str]],
                       emitted_patterns: Iterable[str]) -> dict:
    """The generation-time control-coverage map (stages through EMITTED).

    Persisted verbatim onto ``attempted_interpretation["control_coverage"]``.
    Each control entry carries its own ``fields`` + ``signature`` so the report
    script can replay EXECUTED/ATTRIBUTED against runs WITHOUT re-reading S1 at
    a version that may since have drifted. Inactive controls are listed (the
    org's truth) but are NOT Expected — an inactive rule cannot fire (D-301) —
    and never advance past the listing."""
    facts = list(facts)
    cond_sets = [frozenset(s) for s in condition_field_sets]
    patterns = list(emitted_patterns)

    controls: dict[str, dict] = {}
    ambiguous: set[str] = set()

    matched_by_ref: dict[str, bool] = {}
    for p in patterns:
        hits = match_signature(p, facts)
        if len(hits) > 1:
            ambiguous.update(h.control_ref for h in hits)
        for h in hits:
            matched_by_ref[h.control_ref] = True

    for f in facts:
        fields = formula_fields(f.formula_text)
        if not f.active:
            stage = None
        elif matched_by_ref.get(f.control_ref):
            stage = EMITTED
        elif fields and any(fields & cs for cs in cond_sets):
            stage = NOMINATED
        else:
            stage = EXPECTED
        entry = {
            "mechanism": f.mechanism,
            "subject": f.subject_ref,
            "active": f.active,
            "stage": stage,
            "fields": sorted(fields),
            "signature": f.firing_signature,
        }
        if f.control_ref in ambiguous:
            entry["signature_ambiguous"] = True
        controls[f.control_ref] = entry

    active = [c for c in controls.values() if c["active"]]
    counts = {
        "expected": len(active),
        "nominated": sum(1 for c in active
                         if stage_rank(c["stage"] or "") >= stage_rank(NOMINATED)),
        "emitted": sum(1 for c in active if c["stage"] == EMITTED),
        "inactive_listed": len(controls) - len(active),
    }
    return {"version": COVERAGE_VERSION, "controls": controls, "counts": counts}


def coverage_from_bundles(facts: Iterable[ControlFact],
                          bundles: Iterable[Any]) -> dict:
    """The finalize-time entry point: extract each bundle's condition fields +
    rejection patterns and build the map. Bundles are ``EmissionBundle``
    objects; the same extraction replays over persisted claim/recipe dicts."""
    cond_sets: list[frozenset[str]] = []
    patterns: list[str] = []
    for b in bundles or ():
        cond_sets.append(condition_fields(_get(b, "semantic_conditions")))
        for body in _bundle_recipe_bodies(b):
            patterns.extend(rejection_patterns(body))
    return build_coverage_map(facts, cond_sets, patterns)


__all__ = [
    "ATTRIBUTED", "EMITTED", "EXECUTED", "EXPECTED", "NOMINATED", "SELECTED",
    "STAGES", "COVERAGE_VERSION", "MECHANISM_VALIDATION_RULE", "ControlFact",
    "build_coverage_map", "condition_fields", "controls_from_neighborhood",
    "coverage_from_bundles", "formula_fields", "match_signature",
    "rejection_patterns", "stage_rank",
]
