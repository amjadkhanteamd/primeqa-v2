"""Substrate-3 governance-core — the refusal vertical (D-096).

Real reasoning behind the ``GovernanceProvider`` seam: the D-085 engines
(admissibility, governance/Layer B, decomposition, refusal router) over the S1
``SemanticOrgModel`` boundary. This slice implements the **refusal vertical**
(D-096.5): the full admissibility engine (it is how no-grounding is
determined), Layer-B-for-refusal, the refusal router, and ``explanation_hash``
— end to end with real S1 grounding. ``resolve_intent`` is built whole
(grounded-or-not); ``finalize_outcome`` / ``accept_selection`` (emission) are
stubbed for the draft vertical.

Engine discipline (D-096):
  - Admissibility is requirement-anchored (origination is the excerpt; S1
    verifies, never originates — Guardrail 3 / D-083a) and substrate-authored
    (the LLM never authors it, D-085).
  - Scoped neighborhoods are single-hop ``get_related`` walks + exact-match
    ``get_entities`` (``traverse`` deferred). Edge types bind verbatim to S1's
    ``TIER_1_EDGES`` (drift-guarded by a test).
  - **Layer 1 only** (D-096.2): a constraint EXISTS and is ACTIVE — NOT
    formula-semantic verification (Layer 2 ⇒ deferred S1 §17 parser).
  - Layer B is a **reject-only sanity filter** (D-096.3): may only reject;
    never authors, reinterprets, or upgrades; no LLM, no self-validation.
  - Dismissals use phase-correct D-076 reasons (D-077); the ``refusal_kind`` is
    the outcome-level aggregate (kept distinct from per-candidate dismissals).
"""
from __future__ import annotations

import logging
import re as _re_mod
from dataclasses import dataclass, field
from dataclasses import replace as _dc_replace
from typing import Any, Optional
from uuid import uuid4

from primeqa.generation.enums import AdmissibilityLayer, OutcomeKind, RefusalKind
from primeqa.generation.explanation_hash import compute_explanation_hash
from primeqa.generation import recovery as _recovery
from primeqa.generation.tools import normalize_propose_input
from primeqa.generation.emission import (
    GroundedAcceptance,
    GroundedAutomationEffect,
    GroundedCapability,
    GroundedEmission,
    GroundedExistence,
    GroundedLayout,
    GroundedNegative,
    GroundedPositive,
    GroundedProperty,
    GroundedStateTransition,
    _Endpoint,
    _GroundedCondition,
    author_emission,
    is_emittable,
    prohibition_recipe_derivable,
)
from primeqa.generation.governance import (
    ConversationContext,
    IntentResolution,
    NextAction,
    OutcomeVerdict,
    PresentedCandidate,
    RefCheck,
    RefusalDirective,
    SelectionVerdict,
)
from primeqa.generation.protocol import (
    AttemptedInterpretation,
    GenerationOutcome,
    RefusalEntry,
)
from primeqa.semantic.edges import TIER_1_EDGES
from primeqa.generation.decision_branch import decision_branch_shape
from primeqa.generation.transition import (
    _flatten_and, _prior_constraint, temporal_boundary_shape,
)
from primeqa.generation.verified_negative import _RECORD_TYPES_KEY
from primeqa.generation import control_coverage, control_relevance
from primeqa.generation import shadow_resolution
from primeqa.resolution import field_ladder as _field_ladder
from primeqa.generation.formula_expectation import (
    as_decimal, verify_formula_expectation)
from primeqa.generation.vr_conflict import (
    _fires, entails_firing, find_staged_vr_conflict,
)
from primeqa.semantic.entity_attributes import (
    apply_transform_chain, field_formula_text, field_is_calculated,
    field_treat_null_as_zero, flow_behaviour, flow_effects,
    flow_grounded_guarded_effects, flow_grounded_same_record_effects,
    flow_grounded_temporal_effects, flow_grounded_transforms,
    flow_grounded_transition_effects, flow_cross_record_effect_ops,
    flow_collection_aggregates, flow_cross_record_premises,
    flow_subflow_calls, compose_subflow,
    flow_grounded_premise_conditioned_effects,
    vr_error_message, vr_formula_text, vr_is_active)
from primeqa.semantic.premise_reasoning import (
    classify_relation, staging_plan, aggregate_expectation)
from primeqa.test_representation.temporal import relative_date
# witness synthesis lives behind ONE entry point (DEBT E2, closed at C3);
# the transform witness moved there from this module unchanged
from primeqa.generation.witnesses import (
    boundary_witnesses as _boundary_witnesses,
    guard_witness_values as _guard_witness_values,
    picklist_alternative as _picklist_alternative,
    regex_matching_value as _regex_matching_value,
    synthesize_transform_witness as _synthesize_transform_witness)
from primeqa.semantic.formula import (Comparison, FieldRef, FunctionCall,
                                       Literal, is_parsed, parse, walk)
from primeqa.semantic.query import Entity, SemanticOrgModel
from primeqa.test_representation.identity_hash import compute_identity_hash


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# S1 edge bindings (verbatim TIER_1_EDGES keys; drift-guarded by a test)
# ---------------------------------------------------------------------------
# Data-behavior scoped neighborhood is Object-centered (D-078). All edges point
# INTO the Object, so we walk them inbound from the subject.
EDGE_VALIDATION_RULE = "APPLIES_TO"        # ValidationRule -> Object (BEHAVIOR)
EDGE_BELONGS = "BELONGS_TO"                # Field/RecordType/VR/Layout -> Object (STRUCTURAL)
EDGE_FLOW = "TRIGGERS_ON"                  # Flow -> Object (BEHAVIOR)
EDGE_OBJECT_GRANT = "GRANTS_OBJECT_ACCESS" # Profile/PermissionSet -> Object (PERMISSION)
EDGE_FIELD_GRANT = "GRANTS_FIELD_ACCESS"   # Profile/PermissionSet -> Field (PERMISSION)
EDGE_LAYOUT_FIELD = "INCLUDES_FIELD"       # Layout -> Field (CONFIG; D-124)

# permission capability-claim (D-123): the asserted capability maps to the
# grant edge's boolean property. Object grants carry all six flags; field grants
# carry only read/edit — an asserted flag the edge type doesn't model resolves
# falsy (``.get``) and refuses, fail-closed (invent-nothing, D-079).
_CAPABILITY_FLAG = {
    "read": "can_read",
    "edit": "can_edit",
    "create": "can_create",
    "delete": "can_delete",
    "view_all": "can_view_all",
    "modify_all": "can_modify_all",
}

# All edges used to build a data-behavior Object neighborhood (inbound).
OBJECT_NEIGHBORHOOD_EDGES = [
    EDGE_VALIDATION_RULE, EDGE_BELONGS, EDGE_FLOW, EDGE_OBJECT_GRANT,
]

# Per-(negative) claim_kind Layer-1 grounding dimension (D-078): the
# (edge_type, far-end entity_type) whose EXISTENCE inbound to the subject Object
# grounds the negative at Layer 1.
_NEGATIVE_LAYER1_DIM = {
    "prohibition-claim": (EDGE_VALIDATION_RULE, "ValidationRule"),
    "state-transition-claim": (EDGE_VALIDATION_RULE, "ValidationRule"),
    "automation-effect-claim": (EDGE_FLOW, "Flow"),
}
# Claim kinds whose grounding, when no Layer-1 instance is found, is an
# ONTOLOGY GAP (S1 doesn't model the alternative dimension) rather than
# no_org_constraint. automation-effect can be Apex-driven (S1 Tier-2, not
# modeled) -> ontology_gap (D-078).
_ONTOLOGY_GAP_CLAIM_KINDS = {"automation-effect-claim"}

# Inherently-negative claim kinds (D-083c): the claim_kind IS the negative.
_INHERENTLY_NEGATIVE = {"prohibition-claim"}

# The LLM's "I cannot name a concrete value" sentinel (see the automation-name
# rebind paths below). It is NOT a value: it must never cross into an executable
# recipe (a create/assert of the literal "<UNKNOWN>" is a runtime type error on a
# typed field). The tactical first instance of the broader execution-boundary
# invariant — no UNRESOLVED value crosses into an executable recipe.
_UNKNOWN_SENTINEL = "<UNKNOWN>"


def _is_placeholder_value(v) -> bool:
    """B0 hardening: an angle-bracketed string ("<UNKNOWN>", "<higher tier>",
    "<canonical uppercase normalized>") is the model's PLACEHOLDER idiom, not a
    testable literal — live-observed surviving into stored claims as literal
    expected values at the B0 exit gate. Generalizes the ``_UNKNOWN_SENTINEL``
    screen (which caught only the exact sentinel string)."""
    return (isinstance(v, str) and len(v) >= 2
            and v.startswith("<") and v.endswith(">"))


def _scrub_placeholder_values(hint: dict) -> dict:
    """Return a copy of a data-behavior ``target_subject_hint`` with
    placeholder-shaped VALUE slots normalized to ``None`` (absent), so the
    established needs-a-value refusal gates fire instead of a placeholder
    string being emitted as a claim's literal. Value slots only —
    ``automation_name``'s ``<UNKNOWN>`` sentinel and ``rejection_conditions``
    (whose values the VR derivation owns, D-294/D-295) keep their semantics."""
    if not isinstance(hint, dict):
        return {}
    scrubbed = dict(hint)
    for key in ("expected_value", "effect_value"):
        if _is_placeholder_value(scrubbed.get(key)):
            scrubbed[key] = None
    return scrubbed


# Salesforce DescribeField.type values that carry a numeric value (must parse as
# a Decimal to be an executable value on that field).
_NUMERIC_FIELD_TYPES = frozenset(
    {"int", "integer", "long", "double", "currency", "percent"})

# data-behavior claim_kinds meaningful on an Object subject (Layer-B
# meaningfulness floor; D-087 Guardrail 1 substantive).
_DATA_BEHAVIOR_CLAIM_KINDS = {
    "value-claim", "state-transition-claim", "automation-effect-claim",
    "prohibition-claim", "acceptance-claim",
}

# Dismissal reason -> reasoning phase (D-077d). Documentation of how the
# bounded enum applies; NOT persisted vocabulary (Guardrail 2).
DISMISSAL_PHASE = {
    "ambiguous_target_resolution": "interpretation",
    "lower_specificity": "interpretation",
    "insufficient_grounding": "grounding",
    "no_grant_supports_capability": "grounding",
    "no_constraint_supports_negative": "grounding",
    "type_incompatibility": "grounding",
    "archetype_mismatch": "grounding",
    "policy_threshold_not_met": "governance",
}

# Minimum substantive excerpt length — the Layer-B structural floor beyond
# Layer A's presence check (D-096.3). Full semantic-support verification is
# deferred.
_LAYER_B_MIN_EXCERPT = 5


def phase_for_reason(reason: str) -> Optional[str]:
    """The reasoning phase a D-076 dismissal reason belongs to (D-077d)."""
    return DISMISSAL_PHASE.get(reason)


# D-293 (Option A) — rejection-condition predicate taxonomy, mirrored from
# test_representation/models/conditions.py. Validated there at Condition
# construction; re-checked HERE so an invalid LLM-proposed clause REFUSES at
# grounding rather than crashing emission.
_CONDITION_VALUE_FREE = {"is_null", "is_not_null"}
_CONDITION_VALUE_BEARING = {"equals", "not_equals", "in_set", "matches_pattern"}
# D-330: cross-field comparison predicates — the right-hand side is ANOTHER
# field (clause key ``compared_to``), never a literal value.
_CONDITION_FIELD_COMPARISON = {"exceeds"}


def _ground_rejection_conditions(proposed, neighborhood: list, version_seq: int):
    """Ground each LLM-proposed prohibition rejection-condition clause (D-293,
    Option A — the business STATE under which the rejection is asserted). A clause
    is ``{field: "Object.Field", predicate, value}``; its field must BELONG_TO the
    subject (verified in the scoped neighborhood) and its predicate/value must
    satisfy the S2 ``Condition`` coupling. D-330: a CROSS-FIELD clause is
    ``{field, predicate: "exceeds", compared_to: "Object.OtherField"}`` — both
    fields must BELONG_TO the subject; ``value`` is forbidden. Returns
    ``(grounded, invalid)`` — ``invalid`` is a list of human reasons; a non-empty
    list means the caller refuses (invent-nothing). Empty ``proposed`` ->
    ``([], [])``: the dormant default, byte-identical to the pre-D-293
    condition-free prohibition."""
    fields_by_name = {
        r.entity.sf_api_name: r.entity for r in neighborhood
        if r.edge_type == EDGE_BELONGS and r.entity.entity_type == "Field"}

    def _endpoint(ent):
        return _Endpoint(entity_id=ent.id, entity_type=ent.entity_type,
                         external_id=ent.sf_api_name or str(ent.id))

    grounded: list = []
    invalid: list[str] = []
    for clause in (proposed or []):
        c = clause or {}
        fld, predicate, value = c.get("field"), c.get("predicate"), c.get("value")
        compared_to = c.get("compared_to")
        # D-304.1 (review S1): the 7th hint->claim ingress — decimal clause
        # values coerce like every other identity-bearing hint; element-wise
        # for in_set lists.
        if isinstance(value, list):
            value = [_identity_safe(v) for v in value]
        else:
            value = _identity_safe(value)
        ent = fields_by_name.get(fld)
        if ent is None:
            invalid.append(f"field {fld!r} does not BELONG_TO the subject")
            continue
        if predicate in _CONDITION_FIELD_COMPARISON:
            # D-330: the cross-field form — compared_to required, value forbidden.
            if value is not None:
                invalid.append(f"predicate {predicate!r} forbids a value"); continue
            other = fields_by_name.get(compared_to)
            if other is None:
                invalid.append(
                    f"compared_to field {compared_to!r} does not BELONG_TO "
                    f"the subject"); continue
            grounded.append(_GroundedCondition(
                field=_endpoint(ent), predicate=predicate, value=None,
                compared_to=_endpoint(other)))
            continue
        if compared_to is not None:
            invalid.append(f"predicate {predicate!r} forbids compared_to"); continue
        if predicate in _CONDITION_VALUE_FREE:
            if value is not None:
                invalid.append(f"predicate {predicate!r} forbids a value"); continue
        elif predicate in _CONDITION_VALUE_BEARING:
            if value is None:
                invalid.append(f"predicate {predicate!r} requires a value"); continue
        else:
            invalid.append(f"predicate {predicate!r} not in the condition taxonomy"); continue
        grounded.append(_GroundedCondition(
            field=_endpoint(ent), predicate=predicate, value=value))
    return grounded, invalid


def _bind_picklist_values(grounded, field_metadata):
    """D-332: bind staged ``equals`` values on picklist fields to the org's
    ACTUAL picklist values (the D-294 rail). The requirement speaks labels
    ("Home Loan"); the org speaks values ("Home") — an unbound label stages a
    create a restricted picklist rejects (INVALID_OR_NULL_FOR_RESTRICTED_
    PICKLIST), failing the acceptance for a STAGING reason, not the asserted
    behavior (the req-302 live finding). Deterministic bind, certainty bar:
    exact → keep; unique case-insensitive → the org's casing; unique
    word-prefix in either direction ("Home Loan" ↔ "Home") → the org value;
    zero or ≥2 candidates → invalid (refuse, naming the valid values).
    Applied ONLY on STAGED-value paths (acceptance / update clauses) —
    prohibition conditions are identity/display, never staged, and rebinding
    them would re-key every existing prohibition for no run-time gain."""
    out, invalid = [], []
    for gc in grounded:
        if gc.predicate != "equals" or gc.value is None:
            out.append(gc)
            continue
        bare = gc.field.external_id.rsplit(".", 1)[-1]
        vals = ((field_metadata or {}).get(bare) or {}).get("picklist_values")
        if not vals or gc.value in vals:
            out.append(gc)
            continue
        pw = str(gc.value).lower().split()
        cands = []
        for v in vals:
            vw = str(v).lower().split()
            if pw == vw or pw[:len(vw)] == vw or vw[:len(pw)] == pw:
                cands.append(v)
        if len(cands) == 1:
            out.append(_dc_replace(gc, value=cands[0]))
        else:
            invalid.append(
                f"'{gc.value}' is not an active picklist value for "
                f"{gc.field.external_id} (valid: {', '.join(map(str, vals))})")
    return out, invalid


_ARC_ACTIONS = ("submit", "approve", "reject")


def _ground_arc_prohibition(hint, neighborhood, field_metadata,
                            active_approvals):
    """D-333: ground the approval-arc prohibition's OWN inputs — the action
    list + the explicit ``attempted_change`` update. Returns
    ``(actions, (field_endpoint, bound_value), error)``; a non-None error
    refuses (invent-nothing). The arc's completeness is constructional (the
    org's own approval state realizes the rejection premise), so the D-293
    VR-derivability gate deliberately does NOT apply — these checks replace
    it: a real action vocabulary beginning with submit, exactly ONE active
    ApprovalProcess on the subject (the D-320 enumeration law), and an
    updateable, picklist-bound (D-332) attempted change."""
    actions = hint.get("approval_actions")
    if (not isinstance(actions, list) or not actions
            or any(a not in _ARC_ACTIONS for a in actions)
            or actions[0] != "submit"):
        return None, None, (
            f"approval_actions must be a non-empty list drawn from "
            f"{list(_ARC_ACTIONS)} beginning with 'submit'; got {actions!r}")
    if len(active_approvals) != 1:
        return None, None, (
            f"the approval-action arc needs exactly ONE active approval "
            f"process on the subject to bind (the D-320 enumeration law); "
            f"found {len(active_approvals)}")
    ac = hint.get("attempted_change") or {}
    fld, value = ac.get("field_name"), ac.get("value")
    if not fld or value is None:
        return None, None, (
            "the approval-action arc requires attempted_change "
            "{field_name, value} — the update the org must reject/accept "
            "after the actions run")
    fields_by_name = {
        r.entity.sf_api_name: r.entity for r in neighborhood
        if r.edge_type == EDGE_BELONGS and r.entity.entity_type == "Field"}
    ent = fields_by_name.get(fld)
    if ent is None:
        return None, None, (
            f"attempted_change field {fld!r} does not BELONG_TO the subject")
    meta = (field_metadata or {}).get(fld.rsplit(".", 1)[-1]) or {}
    if meta.get("is_calculated") or not meta.get("is_updateable", True):
        return None, None, (
            f"attempted_change field {fld!r} is not updateable "
            f"(calculated/read-only)")
    gc = _GroundedCondition(
        field=_Endpoint(entity_id=ent.id, entity_type=ent.entity_type,
                        external_id=ent.sf_api_name or str(ent.id)),
        predicate="equals", value=_identity_safe(value))
    bound, invalid = _bind_picklist_values([gc], field_metadata)
    if invalid:
        return None, None, invalid[0]
    return tuple(actions), (bound[0].field, bound[0].value), None


def _identity_safe(value):
    """D-304: canonicalization v1 FORBIDS floats in identity-bearing content
    (SPEC §6.3.2) — an LLM proposing a decimal expected value (0.63) must not
    crash persistence on typing luck. Floats coerce to their shortest-repr
    STRING at the hint→claim boundary (the S4 typed-tolerant equals, D-211,
    compares "0.63" == 0.63 numerically, so the recipe grades identically).
    Everything else passes through verbatim (D-115 §2)."""
    if isinstance(value, float) and not isinstance(value, bool):
        return repr(value)
    return value


def _ground_trigger_fields(proposed, neighborhood: list,
                           exclude_field: Optional[str] = None) -> tuple:
    """D-299: resolve the LLM-proposed entry-condition (field, value) pairs — the
    fields the create must SET so an automation's entry gate actually fires (e.g.
    ``StageName='Credit Assessment'`` + the KYC/Credit-Score fields the entry VR
    forces present). Each proposed pair is ``{field_name: "Object.Field", value}``;
    the field must BELONG_TO the subject (object-qualified name, exactly the
    check ``effect_field`` and the D-222 state-transition trigger use).

    **k16 truth-bearing guard**: ``exclude_field`` (the effect field's
    object-qualified api-name) is DROPPED if proposed as a trigger. The trigger
    sets the entry CONDITION; the value-under-test (the field the Flow must
    PRODUCE) must never be planted by the create — else the equals-assert passes
    with the Flow never firing (a silent wrong-green feeding GO/NO-GO). The
    substrate enforces this, not the prompt (invent-nothing / Guardrail-2).

    **Drop-never-refuse** (mirrors the D-222 staged trigger): an unverifiable
    field, a value-less pair, or the excluded effect field is silently DROPPED —
    never guessed, never refused (a previously-emittable automation-effect never
    regresses to a refusal because the LLM over-proposed a trigger). Returns a
    tuple of ``(_Endpoint, value)`` pairs; ``()`` when nothing verifies (the
    dormant default — today's padding-only shallow create)."""
    if not isinstance(proposed, list):
        return ()
    fields_by_name = {
        r.entity.sf_api_name: r.entity for r in neighborhood
        if r.edge_type == EDGE_BELONGS and r.entity.entity_type == "Field"}
    pairs: list = []
    for item in proposed:
        c = item or {}
        fname, value = c.get("field_name"), c.get("value")
        if not fname or value is None:
            continue
        if exclude_field is not None and fname == exclude_field:
            continue                    # k16: never plant the value-under-test
        ent = fields_by_name.get(fname)
        if ent is None:
            continue
        pairs.append((_Endpoint(
            entity_id=ent.id, entity_type=ent.entity_type,
            external_id=ent.sf_api_name or str(ent.id)), value))
    return tuple(pairs)


def _grounding_vr_formulas(claim_kind: str, neighborhood: list) -> tuple[str, ...]:
    """Formula texts of the ValidationRules that ground this negative — matched by
    the same (edge_type, far_type) the Layer-1 dimension uses (D-078). Carried
    onto the grounding so emission can attempt D-107 violating-value derivation
    (the verified-vs-caveated gate). Empty when no matched VR carries a formula,
    which keeps the caveated fallback (D-101) unchanged. Multi-VR objects
    contribute every formula; emission uses at-least-one-derivable semantics."""
    dim = _NEGATIVE_LAYER1_DIM.get(claim_kind)
    if dim is None:
        return ()
    edge_type, far_type = dim
    formulas: list[str] = []
    for r in neighborhood:
        if r.edge_type == edge_type and r.entity.entity_type == far_type:
            # D-301: an INACTIVE rule cannot fire — it must never ground a
            # prohibition, seed derivation, tie in the D-295 alignment, or
            # bind a D-297 message (all three consume THIS tuple).
            if not vr_is_active(r.entity.attributes):
                continue
            # Shape-tolerant (D-203.1): pre-cutover rows carry the designed
            # `formula_text`; post-cutover sync rows carry the raw Tooling
            # record (Metadata.errorConditionFormula). Reading only the
            # former silently demoted every negative to caveated.
            text = vr_formula_text(r.entity.attributes)
            if text:
                formulas.append(text)
    return tuple(formulas)


def _grounding_vr_messages(claim_kind: str, neighborhood: list) -> dict:
    """{VR formula_text -> user-facing error message} for the grounding VRs (D-297,
    lever 5), read from S1 at grounding via the SAME (edge_type, far_type) dimension
    as :func:`_grounding_vr_formulas` — so the keys align with what emission derives
    from. Slice 5.2 looks up the DERIVED source formula's message here and projects
    it into the recipe's ``error_message_pattern`` so the S4 grade confirms the
    CORRECT rule fired (not merely some VR). **Ambiguity guard:** a formula_text with
    >1 matching VR carrying DIFFERING messages is dropped (never bind an arbitrary
    message); a VR with no message is skipped. The LLM never authors these — they are
    the messages S1 synced from Salesforce (ground-or-refuse). DORMANT until 5.2
    reads it."""
    dim = _NEGATIVE_LAYER1_DIM.get(claim_kind)
    if dim is None:
        return {}
    edge_type, far_type = dim
    out: dict = {}
    ambiguous: set = set()
    for r in neighborhood:
        if r.edge_type != edge_type or r.entity.entity_type != far_type:
            continue
        if not vr_is_active(r.entity.attributes):
            continue                     # D-301: dead rules bind no message
        text = vr_formula_text(r.entity.attributes)
        if not text:
            continue
        msg = vr_error_message(r.entity.attributes)
        if msg is None:
            continue
        if text in out and out[text] != msg:
            ambiguous.add(text)          # same formula, differing messages -> drop
        elif text not in ambiguous:
            out[text] = msg
    for text in ambiguous:
        out.pop(text, None)
    return out


def _staged_vr_conflict_detail(
        neighborhood: list, staged_create: dict,
        staged_update: Optional[dict] = None) -> Optional[str]:
    """D-337: the authoring-time staged-state VR-conflict guard — the refusal
    detail when the claim's OWN staged values provably fire one of the
    subject's ACTIVE ValidationRules, else ``None``. Reads the same
    (``APPLIES_TO``, ValidationRule) neighborhood rows as
    :func:`_grounding_vr_formulas`, but for every STAGED-surface claim shape
    (acceptance / approval-arc / automation-effect triggers) and
    name-carrying, so the refusal names WHICH rule the staged state fires.
    Kleene evaluation (``vr_conflict``): an unstaged field is unknown and
    unknown never refuses — run-time R1 padding stays the owner of every
    non-provable case. Active-only (D-301: an inactive rule cannot fire)."""
    rules = []
    for r in neighborhood:
        if (r.edge_type != EDGE_VALIDATION_RULE
                or r.entity.entity_type != "ValidationRule"):
            continue
        if not vr_is_active(r.entity.attributes):
            continue
        text = vr_formula_text(r.entity.attributes)
        if text:
            rules.append((r.entity.sf_api_name or "", text))
    if not rules:
        return None
    return find_staged_vr_conflict(rules, staged_create, staged_update)


def _claim_condition_fields(grounded_conds) -> frozenset[str]:
    """The bare, lower-cased field api-names a prohibition claim's grounded
    conditions reference (D-295 — the LEFT side of the VR field-overlap match).
    ``_GroundedCondition.field.external_id`` is object-qualified (``Object.Field``,
    ``governance_core.py`` grounding); the bare tail (``rsplit('.',1)[-1]``) is what
    the VR formula parser speaks, so both sides of the overlap are normalized alike.
    D-330: a cross-field clause contributes BOTH its fields."""
    out = set()
    for gc in (grounded_conds or []):
        if gc.field and gc.field.external_id:
            out.add(gc.field.external_id.rsplit(".", 1)[-1].lower())
        other = getattr(gc, "compared_to", None)
        if other is not None and other.external_id:
            out.add(other.external_id.rsplit(".", 1)[-1].lower())
    return frozenset(out)


def _claim_cross_field_pairs(grounded_conds) -> frozenset[frozenset[str]]:
    """The order-free bare-field pairs of the claim's CROSS-FIELD clauses
    (D-330) — the claim-side mirror of :func:`_vr_cross_field_pairs`. Empty for
    an all-v1 clause set (the dormant default)."""
    out = set()
    for gc in (grounded_conds or []):
        other = getattr(gc, "compared_to", None)
        if other is not None and gc.field and gc.field.external_id \
                and other.external_id:
            out.add(frozenset({
                gc.field.external_id.rsplit(".", 1)[-1].lower(),
                other.external_id.rsplit(".", 1)[-1].lower()}))
    return frozenset(out)


def _vr_formula_fields(text: str) -> frozenset[str]:
    """The bare, lower-cased field api-names a VR formula references (D-295 — the
    RIGHT side of the match). Parsed via the pure D-107 parser; an unparseable
    formula contributes NO fields (→ zero overlap; it is non-derivable anyway, so a
    spurious selection would only refuse downstream, never green a wrong-rule test).
    ``walk`` recurses ``FunctionCall`` args, so ``ISBLANK``/``ISPICKVAL`` surface
    their inner ``FieldRef``; ``path[-1]`` is the bare tail, matching the claim side."""
    ast = parse(text)
    if not is_parsed(ast):
        return frozenset()
    return frozenset(
        node.path[-1].lower()
        for node in walk(ast)
        if isinstance(node, FieldRef) and node.path)


def _vr_cross_field_pairs(text: str) -> frozenset[frozenset[str]]:
    """The cross-field comparison operand-pairs a VR formula references (D-296 —
    the structural discriminator field-overlap is blind to). Each pair is an
    order-free ``frozenset`` of the two bare, lower-cased field api-names of a
    ``Comparison`` whose BOTH sides are ``FieldRef`` (e.g. ``Loan_Amount__c >
    Property_Value__c`` -> ``{{loan_amount__c, property_value__c}}``). The operator
    is deliberately IGNORED — this is a membership discriminator only; orientation
    is handled downstream by ``verified_negative._satisfy_cross_field`` via
    ``_CROSS_PAIR``. Field-vs-literal comparisons (``Amount > 10000``) and
    unparseable formulas contribute NOTHING (``frozenset()``), so the D-295.1
    generic wrong-green class carries no cross-field signature. Strictly less than
    :func:`_vr_formula_fields` — a single node-shape filter, not a formula
    evaluator. DORMANT until D-296 S2 wires it into ``_best_aligned_vr``'s tie
    branch (exact-equality guard: a pair qualifies iff it == the claim's fields)."""
    ast = parse(text)
    if not is_parsed(ast):
        return frozenset()
    return frozenset(
        frozenset({node.left.path[-1].lower(), node.right.path[-1].lower()})
        for node in walk(ast)
        if isinstance(node, Comparison)
        and isinstance(node.left, FieldRef) and isinstance(node.right, FieldRef)
        and node.left.path and node.right.path)


def _constraint_states(grounded_conds) -> list:
    """Enumerate the concrete ``{Object.Field: value}`` worlds the claim's grounded
    conditions assert are rejected — the LEFT side of the D-350 entailment (the
    Constraint IR realized for the current predicate taxonomy). ``equals`` -> the
    value; ``is_null`` -> None (blank); ``in_set`` -> one world per member (Cartesian
    across in_set fields, bounded — an over-large product refuses via ``[]``).
    ``not_equals`` / ``matches_pattern`` pin no single deterministic value and
    ``exceeds`` (cross-field) is handled by the D-330 pair filter, so all three are
    LEFT UNKNOWN (their field stays absent). Returns ``[]`` when nothing is pinned —
    the caller then declines to select (refuse-rather-than-guess)."""
    base: dict = {}
    branches: list = []   # (external_id, [values]) for in_set fields
    for gc in (grounded_conds or ()):
        p, v = gc.predicate, gc.value
        if p == "equals" and v is not None and not isinstance(v, list):
            base[gc.field.external_id] = v
        elif p == "is_null":
            base[gc.field.external_id] = None
        elif p == "in_set" and isinstance(v, (list, tuple)) and v:
            branches.append((gc.field.external_id, list(v)))
    if not base and not branches:
        return []
    states = [dict(base)]
    for fld, vals in branches:
        if len(states) * len(vals) > 64:      # bounded enumeration -> refuse
            return []
        states = [dict(s, **{fld: val}) for s in states for val in vals]
    return states


def _break_tie_by_entailment(tied_vrs, grounded_conds) -> Optional[str]:
    """Break a >=2-way field-overlap tie (D-350) by which tied VR the claim's asserted
    rejection state NECESSARILY fires (entailment, not mere possibility): a VR
    qualifies iff it fires in EVERY pinned world (:func:`entails_firing` returns True),
    reusing ``vr_conflict``'s Kleene firing (absent field = unknown). Returns the sole
    necessarily-firing VR, or ``None`` when zero / >=2 qualify (refuse-on-non-unique
    reasserted at this dimension) or the claim pins no state. A VR that only POSSIBLY
    fires (some but not all worlds) is UNKNOWN and never selected."""
    states = _constraint_states(grounded_conds)
    if not states:
        return None
    entailed = [t for t in tied_vrs if entails_firing(t, states) is True]
    return entailed[0] if len(entailed) == 1 else None


def _break_tie_by_prior_state(tied_vrs, grounded_conds) -> Optional[str]:
    """Break a >=2-way field-overlap tie by the PRIOR-STATE LOCK match (the
    VR05 arc): the sole tied VR whose ``PRIORVALUE`` constraint ``(field,
    value)`` matches a claim-pinned equals condition — the claim asserts a
    state the rule LOCKS ON as the prior ('once Approved, the value is
    protected' pins Stage=Approved; only VR05 among the tied set is
    PRIORVALUE-gated on that state). Entailment cannot resolve these ties
    (org-state functions are unknown to the single-phase ``_fires``); this
    reuses the transition IR's own prior-constraint reading — a bounded,
    semantically-honest discriminator, refuse-on-non-unique like its
    siblings."""
    pinned = {(gc.field.external_id.rsplit(".", 1)[-1].lower(), gc.value)
              for gc in (grounded_conds or ()) if gc.predicate == "equals"}
    if not pinned:
        return None
    qualifiers = []
    for t in tied_vrs:
        try:
            ast = parse(t)
            if not is_parsed(ast):
                continue
            priors = [p for p in map(_prior_constraint, _flatten_and(ast)) if p]
            if any((pf.lower(), pv) in pinned for pf, pv in priors):
                qualifiers.append(t)
        except Exception:
            continue
    return qualifiers[0] if len(qualifiers) == 1 else None


def _break_tie_by_cross_field(tied_vrs, claim_fields) -> Optional[str]:
    """Break a >=2-way field-overlap tie (D-296) using the cross-field structural
    discriminator that field-overlap is blind to. A tied VR QUALIFIES iff it carries
    a cross-field comparison pair EXACTLY equal to the claim's condition fields
    (exact-equality guard: a strict subset would admit an *incidental* cross-field on
    an over-grounded claim — rejected in D-296 / guard A). Returns the sole qualifier,
    or ``None`` when zero or >=2 qualify — refuse-on-non-unique, reasserted at this
    second dimension. Callers pass only the already-tied set. A generic ``X > N`` is
    field-vs-literal and carries no cross-field pair, so the D-295.1 wrong-green class
    can never qualify here."""
    qualifiers = [t for t in tied_vrs if claim_fields in _vr_cross_field_pairs(t)]
    return qualifiers[0] if len(qualifiers) == 1 else None


def _best_aligned_vr(vr_formulas: tuple[str, ...], grounded_conds) -> Optional[str]:
    """The VR formula that best-grounds the claim (D-295 + D-296), or ``None`` when
    the choice is not unique. Primary signal = field-overlap CARDINALITY
    ``|claim_fields ∩ vr_fields|`` (not Jaccard). Returns ``None`` when the claim has
    no condition fields or no VR shares a field (refuse floor = 1). On a **>=2-way tie
    at the top score**, D-296 breaks it with the cross-field structural discriminator
    (:func:`_break_tie_by_cross_field`, exact-equality); a tie the discriminator
    cannot uniquely resolve still returns ``None`` (D-295.1 refuse-on-non-unique).

    **D-295.1:** the earlier field-count tie-break was removed — it preferred the
    narrowest VR (the generic ``X > N``), a wrong-green vector. **D-296:** a structural
    (not field-count) discriminator now breaks a tie only toward a VR whose cross-field
    pair exactly matches the claim. **Residual (unchanged):** a mis-attributed grounding
    whose fields give a STRICT higher overlap on a wrong-but-derivable VR is still the
    unique top-scorer and is selected — bounded by grounding quality, not this selector.
    Callers apply the degenerate guard (empty conds / single VR) before this.

    **D-330 (predicate-aware hard filter):** when the claim carries a
    CROSS-FIELD clause ("Loan Amount exceeds Property Value"), only a VR whose
    formula contains that exact cross-field comparison pair can be the
    grounding rule — field-overlap alone is blind to the predicate and would
    prefer a wider same-fields rule (the req-302 AC2 mis-attribution: the
    mandatory-fields VR out-scored the cross-field VR 3-to-2). The filter
    applies BEFORE scoring; an empty filtered set refuses (``None``)."""
    claim_fields = _claim_condition_fields(grounded_conds)
    if not claim_fields:
        return None
    cross_pairs = _claim_cross_field_pairs(grounded_conds)
    if cross_pairs:
        vr_formulas = tuple(
            t for t in vr_formulas
            if cross_pairs <= _vr_cross_field_pairs(t))
        if not vr_formulas:
            return None
    # Pass 1: the top field-overlap score (each VR's fields parsed once).
    scored = [(len(claim_fields & _vr_formula_fields(t)), t) for t in vr_formulas]
    best_score = max((s for s, _ in scored), default=0)
    if best_score < 1:                          # refuse floor: no VR shares a field
        return None
    # Pass 2: the full tied set at the top score (includes the incumbent — a
    # single-pass boolean 'tie' flag would drop it and mislead the discriminator).
    tied = [t for s, t in scored if s == best_score]
    if len(tied) == 1:
        return tied[0]
    # D-350: the predicate/value-aware discriminator — which tied VR the claim's
    # asserted rejection state NECESSARILY fires — runs FIRST (the most direct
    # evidence that this is the rule under test). The D-296 structural cross-field
    # pair is the fallback when firing cannot uniquely resolve (e.g. synthetic
    # equals-"x" conditions where no VR provably fires).
    entailed = _break_tie_by_entailment(tied, grounded_conds)
    if entailed is not None:
        return entailed
    # Contradiction elimination (VR03 arc, sound Kleene narrowing): the claim
    # asserts the org rejects EVERY world its conditions pin — a tied VR that
    # is provably FALSE in at least one pinned world cannot be the claim's
    # rule ([Compliance=false, Risk in {High, Critical}]: VR07 is False in the
    # Risk=High world → eliminated; VR03 stays unknown → kept). Elimination
    # only ever REMOVES wrong candidates; a unique survivor is selected,
    # otherwise the remaining tie-breaks run over the NARROWED set.
    states = _constraint_states(grounded_conds)
    if states:
        survivors = []
        for t in tied:
            try:
                ast = parse(t)
                if is_parsed(ast) and any(
                        _fires(ast, {k.rsplit(".", 1)[-1].lower(): v
                                     for k, v in st.items()}) is False
                        for st in states):
                    continue                       # contradicted — eliminated
            except Exception:
                pass
            survivors.append(t)
        if len(survivors) == 1:
            return survivors[0]
        if survivors:
            tied = survivors
    # VR05 arc: the prior-state LOCK match — entailment is blind to org-state
    # functions, but a claim pinning the exact state a tied VR locks on as
    # PRIOR ('Stage = Approved' vs VR05's PRIORVALUE gate) uniquely names it.
    prior_locked = _break_tie_by_prior_state(tied, grounded_conds)
    if prior_locked is not None:
        return prior_locked
    # VR06 arc: the temporal-boundary SHAPE match — a claim conditioning a
    # date field uniquely names the org's one temporal-boundary rule on it
    # (AND(gate, OR(ISBLANK(d), d < TODAY()))); entailment is blind here too
    # (the gate field is unpinned → unknown). Refuse-on-non-unique as always.
    temporal = [t for t in tied
                if (lambda sh: sh is not None
                    and sh[2].lower() in claim_fields)(temporal_boundary_shape(t))]
    if len(temporal) == 1:
        return temporal[0]
    # D-296 cross-field first — an exact structural pair match is the stronger
    # evidence and must not be stolen by the shape heuristics below.
    crossed = _break_tie_by_cross_field(tied, claim_fields)
    if crossed is not None:
        return crossed
    # VR03 arc: the DECISION-shape match — a static AND(gates, OR(branches))
    # rule (org-state formulas are excluded by the recognizer: VR10 also reads
    # as gates+OR but is transition-shaped and owned by that machinery).
    # Qualifies only when the claim pins AT LEAST TWO condition fields (one
    # generic field naming several rules is D-295.1's ambiguous case — refuse
    # stands) and EVERY one of them belongs to the rule (a claim naming fields
    # outside it is about something else); the sole such decision-shaped rule
    # wins. Refuse-on-non-unique as always.
    if len(claim_fields) < 2:
        return None
    decision = [t for t in tied
                if decision_branch_shape(t) is not None
                and claim_fields <= _vr_formula_fields(t)]
    if len(decision) == 1:
        return decision[0]
    return None


def _align_vr_to_conditions(
        vr_formulas: tuple[str, ...], grounded_conds) -> tuple[str, ...]:
    """Narrow the grounding VR formulas to the ONE that matches the claim's
    conditions (D-295), so emission's first-derivable loop grounds each prohibition
    on its OWN rule rather than the first-derivable generic VR on the object.

    Returns ``(best_vr,)`` when a VR's fields UNIQUELY overlap the claim's condition
    fields, or ``()`` when none uniquely does (no overlap, or an ambiguous >=2-way
    tie — D-295.1) — an empty tuple drives the D-293 derivability gate to REFUSE
    rather than grounding a mismatched or ambiguously-chosen rule. **Degenerate
    guard (the load-bearing first line):** with no
    grounded conditions (a condition-free prohibition, pre-D-293) or a single (or
    zero) candidate VR there is nothing to disambiguate — ``vr_formulas`` passes
    through unchanged, so those cases stay byte-identical to pre-D-295 and a
    single-VR object is never refused by alignment (the D-293 gate remains its only
    backstop). See :func:`_best_aligned_vr` for the scorer."""
    if not grounded_conds or len(vr_formulas) <= 1:
        return vr_formulas
    best = _best_aligned_vr(vr_formulas, grounded_conds)
    return (best,) if best is not None else ()


def _prohibition_refusal_detail(
        subject_name: str, vr_formulas: tuple[str, ...],
        vr_all: tuple[str, ...], grounded_conds) -> str:
    """The BA-facing reason a prohibition's behaviour instance is incomplete
    (reached only when :func:`prohibition_recipe_derivable` is False). Distinguishes
    the **D-295 mismatch** — alignment emptied a >=2-VR candidate set, i.e. the
    conditions do not uniquely select a rule — from the **D-293 derivability gap** —
    the aligned (or lone) VR cannot yet be turned into a behavioural reject recipe.
    Kept distinct so the D-247 coverage/refusal surface shows a BA *why*: refine the
    condition (D-295) vs the rule is not yet testable (D-293). The D-295 wording is
    literally true for all three ways alignment empties: no parseable rule
    references the fields, or >=2 rules tie (D-295.1)."""
    if not vr_formulas and len(vr_all) >= 2:
        return (
            f"prohibition on {subject_name}: the claim's condition fields "
            f"{sorted(_claim_condition_fields(grounded_conds))} do not uniquely "
            f"select a validation rule (no parseable rule references them, or more "
            f"than one rule matches equally); refine the conditions rather than "
            f"grounding a mismatched or ambiguous rule (D-295)")
    return (
        f"prohibition on {subject_name}: no derivable behavioural reject recipe "
        f"from the grounding validation rule(s) (non-numeric formula, or a "
        f"non-rejectable operation); refusing rather than degrading to a metadata "
        f"inspection (D-293)")


def _value_type_invalid(value: Any, meta: Optional[dict]) -> Optional[str]:
    """A human reason iff ``value`` is not a type-valid concrete value for the
    field described by ``meta`` (the D-115 value floor), else ``None`` — valid, or
    metadata absent (the certainty bar passes it through, matching
    :func:`_grounding_field_metadata`). A value-claim CREATEs the field with this
    value and asserts it back, so a numeric field needs a decimal-parseable value
    and a known picklist needs one of its active values; anything else is
    unexecutable (a create the org rejects for a parse/format reason, never the
    behaviour under test)."""
    if not meta:
        return None
    ftype = (meta.get("field_type") or "").lower()
    if ftype in _NUMERIC_FIELD_TYPES:
        if as_decimal(value) is None:
            return (f"the value {value!r} is not a valid number for this "
                    f"{ftype} field — there is no real value to create and assert")
    elif ftype == "picklist":
        pv = meta.get("picklist_values")
        if pv and value not in pv:
            return (f"the value {value!r} is not one of the field's picklist "
                    f"values {sorted(pv)} — there is no real value to create and assert")
    return None


def _grounding_field_metadata(neighborhood: list, s1, at_seq: int) -> dict:
    """Read-only S1 field metadata for every Field in the scoped neighborhood
    (D-294), keyed by BARE field api name (as the formula parser + recipe speak).
    Each value: ``{field_type, length, is_calculated, picklist_values|None}``.
    Feeds metadata-driven violation-derivation; an absent/insufficient entry makes
    derivation refuse exactly as today (the certainty bar). Same character of read
    as :func:`_grounding_vr_formulas` (grounding already reads S1), at the same
    pinned version, no live-org effect. The picklist 2-hop mirrors
    ``evolution/s1_reader.py`` (Field -> field_details.picklist_value_set_entity_id
    -> active value set). DORMANT until the derive branches are armed."""
    out: dict = {}
    for r in neighborhood:
        if r.edge_type != EDGE_BELONGS or r.entity.entity_type != "Field":
            continue
        api = r.entity.sf_api_name
        if not api:
            continue
        details = s1.get_entity_details(r.entity.id, at_seq=at_seq) or {}
        picklist_values = None
        pvs_id = details.get("picklist_value_set_entity_id")
        if pvs_id:
            rows = s1.get_picklist_values(pvs_id, at_seq=at_seq)
            picklist_values = tuple(
                v["value_api_name"] for v in rows if v.get("is_active"))
        out[api.rsplit(".", 1)[-1]] = {
            "field_type": (details.get("field_type") or "").lower(),
            "length": details.get("length"),
            # P1 (Amendment B): the field's declared scale — arms the minimally
            # violating witness (typed_value.minimal_increment) and transport
            # quantization (transport_payload already reads it; was never fed).
            "scale": details.get("scale"),
            "is_calculated": bool(details.get("is_calculated", False)),
            # D-305.1 (review B3): required-ness — an is_null acceptance clause
            # on a NON-nillable field is structurally defeated by k16 padding.
            "is_nillable": bool(details.get("is_nillable", True)),
            # D-294: writability (D-160) — a violating payload can only SET a
            # writable field; defaults TRUE to match field_details' server_default.
            "is_createable": bool(details.get("is_createable", True)),
            "is_updateable": bool(details.get("is_updateable", True)),
            "picklist_values": picklist_values,
        }
    # D-348 (VR08): the record-types rail — DeveloperName -> RecordTypeId (the last
    # sf_api_name segment is the DeveloperName) — so `RecordType.DeveloperName = "X"`
    # is satisfied by SETTING RecordTypeId. Reserved key (no consumer iterates the
    # metadata keys; verified: bare-field `.get` only).
    record_types = {
        r.entity.sf_api_name.rsplit(".", 1)[-1]: r.entity.sf_id
        for r in neighborhood
        if r.entity.entity_type == "RecordType" and r.entity.sf_id
        and r.entity.sf_api_name}
    if record_types:
        out[_RECORD_TYPES_KEY] = record_types
    return out


# ---------------------------------------------------------------------------
# Amendment B (AK 2026-07-09): the RECORD-TYPE context hypothesis + control-relevance
# nomination. RecordType is a DISTINCT context hypothesis (not a Deal_Type field
# replacement, not force-conjoined). Nominating its relevant control is a different
# operation from proving a rule fires: the req-315 requirement under-specifies the
# threshold ("Enterprise deals are subject to stricter discount controls", no number),
# so entailment cannot select the control — control_relevance nominates by
# context-gate + subject-governance + behavioural-role alignment, then formula
# analysis reads the boundary off the nominated VR.
# ---------------------------------------------------------------------------

def _record_type_devnames(field_metadata: dict) -> dict:
    """The record-types rail ``{DeveloperName: sf_id}`` (D-348), or ``{}``."""
    return dict((field_metadata or {}).get(_RECORD_TYPES_KEY) or {})


def _provable_devname_prefix(names: list) -> str:
    """The longest ``_``-terminated prefix shared by ALL DeveloperNames — a
    metadata-provable object-local namespace convention (e.g. ``PLS_BM_``), or ``""``.
    Used only to STRIP a proven prefix before an EXACT match; never fuzzy."""
    if len(names) < 2:
        return ""
    lo, hi = min(names), max(names)
    i = 0
    while i < len(lo) and i < len(hi) and lo[i] == hi[i]:
        i += 1
    p = lo[:i]
    return p[: p.rindex("_") + 1] if "_" in p else ""


def _normalize_to_devname(token: Any, devnames) -> Optional[str]:
    """Deterministic classification-token resolution (AK Decision 1): exact
    (case-insensitive) match to a DeveloperName, or an exact match after stripping a
    metadata-provable common prefix. Unique -> the DeveloperName; 0 or >=2 -> None
    (refuse). No fuzzy / semantic matching — this is grounding from stable metadata
    identity, not natural-language label grounding."""
    if not isinstance(token, str) or not token.strip() or not devnames:
        return None
    t = token.strip().casefold()
    names = list(devnames)
    exact = [n for n in names if n.casefold() == t]
    if len(exact) == 1:
        return exact[0]
    if exact:
        return None                              # >=2 exact — ambiguous, refuse
    prefix = _provable_devname_prefix(names)
    if prefix:
        stripped = [n for n in names if n[len(prefix):].casefold() == t]
        if len(stripped) == 1:
            return stripped[0]
    return None


def _record_type_endpoint(developer_name: str, neighborhood: list) -> Optional[_Endpoint]:
    """The ``_Endpoint`` for the neighborhood RecordType entity with this
    DeveloperName (the last sf_api_name segment), or ``None``."""
    for r in neighborhood:
        e = r.entity
        if (e.entity_type == "RecordType" and e.sf_api_name
                and e.sf_api_name.rsplit(".", 1)[-1] == developer_name):
            return _Endpoint(entity_id=e.id, entity_type="RecordType",
                             external_id=e.sf_api_name)
    return None


def _ground_record_type_context(grounded_conds, field_metadata: dict,
                                neighborhood: list):
    """Form the RECORD-TYPE context hypothesis when a grounded EQUALS-condition's
    value resolves (deterministic DeveloperName normalization) to a RecordType on the
    subject. Returns ``(developer_name, _Endpoint, classification_cond)`` or ``None``.
    The classification comes from a value the LLM ALREADY grounded (e.g. a
    ``Deal_Type = "Enterprise"`` clause) — never from raw-excerpt NLP, so the
    "than standard deals" comparison never enters."""
    devnames = _record_type_devnames(field_metadata)
    if not devnames:
        return None
    for gc in grounded_conds or ():
        if gc.predicate != "equals":
            continue
        dev = _normalize_to_devname(gc.value, devnames)
        if dev is None:
            continue
        ep = _record_type_endpoint(dev, neighborhood)
        if ep is not None:
            return dev, ep, gc
    return None


def _requirement_subject_role(grounded_conds, classification_cond, excerpt: str):
    """The requirement's subject field (bare api name) + behavioural role for a
    context hypothesis. Subject = the grounded condition that is NOT the
    classification token; role = that condition's predicate role, falling back to the
    excerpt frame. ``(None, UNKNOWN)`` when indeterminate (refuse-rather-than-guess)."""
    subj = next((gc for gc in (grounded_conds or ())
                 if gc is not classification_cond), None)
    if subj is None:
        return None, control_relevance.UNKNOWN
    subject_field = subj.field.external_id.rsplit(".", 1)[-1]
    role = control_relevance.role_from_condition_predicate(subj.predicate)
    if role is control_relevance.UNKNOWN:
        role = control_relevance.role_from_excerpt(excerpt)
    return subject_field, role


def _nominate_record_type_control(vr_all, grounded_conds, field_metadata,
                                  neighborhood, excerpt):
    """The full Amendment-B nomination for a prohibition. Returns
    ``(nominated_vr_formula, context_condition)`` to ground Hypothesis B, or a
    ``str`` refusal detail when the field AND record-type hypotheses BOTH role-align
    distinct controls (refuse-and-surface), or ``None`` when no context hypothesis
    applies (fall through to the existing field-overlap path)."""
    ctx = _ground_record_type_context(grounded_conds, field_metadata, neighborhood)
    if ctx is None:
        return None
    developer_name, rt_ep, classification_cond = ctx
    subject_field, req_role = _requirement_subject_role(
        grounded_conds, classification_cond, excerpt)
    if not subject_field or req_role is control_relevance.UNKNOWN:
        return None
    nominated = control_relevance.nominate(
        [(t, t) for t in vr_all], developer_name, subject_field, req_role)
    if nominated is None:
        return None
    # Refuse-and-surface (AK B2): if the FIELD hypothesis also role-aligns a
    # DIFFERENT control for the same subject+role, do not silently pick either.
    a_vr = _best_aligned_vr(vr_all, grounded_conds)
    if (a_vr is not None and a_vr != nominated
            and control_relevance.vr_role_on_field(a_vr, subject_field) == req_role):
        field_name = classification_cond.field.external_id.rsplit(".", 1)[-1]
        return (f"the requirement's classification maps plausibly to both the "
                f"{field_name} business field and the {developer_name} record type, "
                f"and each governs the {subject_field} behaviour; the system cannot "
                f"determine which control the requirement intends "
                f"(classification-mechanism-ambiguous)")
    context_cond = _GroundedCondition(
        field=rt_ep, predicate="equals", value=developer_name)
    return nominated, context_cond


# ---------------------------------------------------------------------------
# Internal candidate
# ---------------------------------------------------------------------------

@dataclass
class _Candidate:
    path_id: str
    archetype: str
    claim_kind: str
    subject_refs: list[dict]
    requirement_anchor: str
    status: str                              # admissibly_grounded | dismissed
    admissibility_layer: Optional[str] = None
    dismissal_reason: Optional[str] = None
    # B0 (D-362): grounded-recovery offer attached at dismissal time (an
    # _recovery.offer_payload dict) — carried into the refusal payload by
    # ``from_dismissed``; deliberately NOT serialized into candidate_paths
    # (``to_path``), which stay byte-stable.
    recovery: Optional[dict] = None
    # B1 arc: optional human-readable dismissal context (e.g. the subject's
    # field vocabulary on a value-claim field miss) — surfaces through
    # ``RefusalRouter.from_dismissed`` into the recovery feedback channel.
    dismissal_detail: Optional[str] = None

    def to_path(self) -> dict:
        return {
            "path_id": self.path_id,
            "archetype": self.archetype,
            "claim_kind": self.claim_kind,
            "subject_refs": self.subject_refs,
            "requirement_anchor": self.requirement_anchor,
            "admissibility_status": self.status,
            "admissibility_layer": self.admissibility_layer,
            "dismissal_reason": self.dismissal_reason,
        }


# ---------------------------------------------------------------------------
# Layer B — reject-only sanity filter (D-096.3)
# ---------------------------------------------------------------------------

class LayerBFilter:
    """Structural floor only. May ONLY reject (weak/absent excerpt anchoring);
    never authors semantics, reinterprets, or upgrades. No LLM, no
    self-validation. Returns a D-076 dismissal reason on reject, else None.
    Full semantic-support verification is a known deferred capability (D-096.3,
    gated on the same end-state as Layer 2)."""

    def reject_reason(self, excerpt: str, subject: Entity) -> Optional[str]:
        if not excerpt or len(excerpt.strip()) < _LAYER_B_MIN_EXCERPT:
            return "insufficient_grounding"   # missing / structurally-weak anchoring
        return None


def _effect_values_equal(flow_val, claim_val) -> bool:
    """D-318: typed-tolerant equality between a Flow assignment's literal scalar
    (parsed from Tooling Metadata JSON — Number → int/float, Checkbox → bool) and the
    claim's ``expected_value`` (carried VERBATIM from the LLM — usually a string).
    Exact-equality alone misses genuine numeric producers (Flow ``numberValue`` 110.0
    vs an LLM ``expected_value`` of int 110 or str "110"). Mirrors the S4 read-back
    comparator (``data_executor._values_equal``, D-211) so "binds the effect" and
    "grades the effect" agree; kept LOCAL to respect the S3/S4 substrate boundary.
    Bool-guarded (Python ``True == 1`` must not leak); numbers compare numerically
    when both sides parse; strings stay strict (an effect value is never case-folded)."""
    if isinstance(flow_val, bool) or isinstance(claim_val, bool):
        if isinstance(flow_val, bool) and isinstance(claim_val, bool):
            return flow_val is claim_val
        if isinstance(flow_val, bool) and isinstance(claim_val, str):
            return claim_val.strip().lower() == str(flow_val).lower()
        if isinstance(claim_val, bool) and isinstance(flow_val, str):
            return flow_val.strip().lower() == str(claim_val).lower()
        return False
    if flow_val == claim_val:
        return True
    try:
        return float(flow_val) == float(claim_val)
    except (TypeError, ValueError):
        return False


_NUMERIC_FIELD_TYPES = frozenset({"int", "integer", "double", "currency",
                                  "percent"})


def _field_num_meta(field_ent, s1, at_seq) -> tuple:
    """``(field_type, scale)`` for a Field entity — designed ``field_details``
    row first (the D-294 idiom), raw describe ``attributes`` fallback (the
    live capture serializes ``type``/``scale`` as strings). Best-effort:
    ``(None, None)`` on any read surprise."""
    ftype, scale = None, None
    try:
        details = s1.get_entity_details(field_ent.id, at_seq=at_seq) or {}
        ftype = details.get("field_type")
        scale = details.get("scale")
    except Exception:
        pass
    attrs = getattr(field_ent, "attributes", None) or {}
    if not ftype:
        ftype = attrs.get("type")
    if scale is None:
        scale = attrs.get("scale")
    try:
        scale = int(scale) if scale is not None else None
    except (TypeError, ValueError):
        scale = None
    return ((ftype or "").lower() or None), scale


def _numeric_effect_guard(field_ent, effect_value, s1, at_seq):
    """R2 (req-302 robustness): the plain-language refusal detail when a
    NUMERIC effect field carries a non-numeric-parseable expected value — the
    D-268 placeholder family in the effect-value lane (claim e15f7c91 shipped
    the literal string ``"<computed>"`` into an approved percent-field claim).
    ``None`` = pass. Fail-open on unknown/non-numeric field types — only a
    provably-numeric field refuses a non-number."""
    ftype, _ = _field_num_meta(field_ent, s1, at_seq)
    if ftype not in _NUMERIC_FIELD_TYPES:
        return None
    if as_decimal(effect_value) is None:
        return (f"expected_value {effect_value!r} is not a number, but "
                f"{field_ent.sf_api_name} is a {ftype} field — state the "
                f"actual numeric value the automation produces (placeholders "
                f"like '<computed>' cannot ground a test)")
    return None


def _resolve_subject_field_name(neighborhood, name):
    """B1 arc: a proposed field name → the subject's REAL qualified api-name,
    resolved DETERMINISTICALLY (the field-level twin of business-label object
    resolution). The LLM cannot know an org's field naming convention (req-320
    proposed ``Priority__c`` for ``PLS_FB_Priority__c``); the substrate owns
    the mechanics. Rules, unique-match-only — 0 or >1 candidates → ``None``
    and the caller's refusal stands (never guess):

      1. exact qualified api-name match → itself (no rewrite);
      2. unique case-insensitive BARE api-name match
         (``priority__c`` == ``priority__c``);
      3. unique case-insensitive bare SUFFIX match
         (``Priority__c`` → ``PLS_FB_Priority__c`` via ``…_priority__c``);
      4. unique label match (``Priority`` ≙ display_name, case-insensitive;
         a trailing ``__c``/underscores on the proposal are normalized away).

    F1 (D-377): the rules themselves live in the SINGLE shared engine
    (``primeqa.resolution.field_ladder.resolve_field_name`` — byte-parity
    pinned by ``tests/unit/resolution/test_field_ladder.py``); this wrapper
    only projects the neighborhood into the engine's inventory shape.
    """
    inventory = [(r.entity.sf_api_name, r.entity.display_name)
                 for r in neighborhood
                 if r.edge_type == EDGE_BELONGS
                 and r.entity.entity_type == "Field"
                 and isinstance(r.entity.sf_api_name, str)]
    return _field_ladder.resolve_field_name(inventory, name)


def _canonicalize_subject_fields(hint: dict, neighborhood: list) -> dict:
    """F1 (D-377): canonicalize EVERY subject-owned field slot through the
    ladder — the generalization of the B1 ``field_name``-only rewrite. A name
    is rewritten ONLY when the ladder resolves it UNIQUELY to a different
    real name; unresolvable/ambiguous names pass through untouched, so every
    existing refusal, drop-never-refuse, and B0-offer path still sees exactly
    what the model proposed. Effect-object-owned slots (``effect_field`` /
    ``effect_lookup_field``) are deliberately NOT touched — they resolve
    against the EFFECT object's inventory downstream (D-375). Returns the
    original dict unchanged (same identity) when nothing rewrites."""
    changed = False
    new_hint = dict(hint)

    def _res(name):
        r = _resolve_subject_field_name(neighborhood, name)
        return r if (r is not None and r != name) else None

    for slot in ("field_name", "trigger_field", "effect_via_lookup_field"):
        fixed = _res(new_hint.get(slot))
        if fixed:
            new_hint[slot] = fixed
            changed = True
    for slot in ("trigger_fields", "update_trigger_fields"):
        rows = new_hint.get(slot)
        if not isinstance(rows, list):
            continue
        new_rows, row_changed = [], False
        for row in rows:
            if isinstance(row, dict):
                key = ("field_name" if "field_name" in row
                       else "field" if "field" in row else None)
                fixed = _res(row.get(key)) if key else None
                if fixed:
                    row = {**row, key: fixed}
                    row_changed = True
            new_rows.append(row)
        if row_changed:
            new_hint[slot] = new_rows
            changed = True
    for slot in ("rejection_conditions", "acceptance_conditions",
                 "update_conditions"):
        rows = new_hint.get(slot)
        if not isinstance(rows, list):
            continue
        new_rows, row_changed = [], False
        for row in rows:
            if isinstance(row, dict):
                updates = {key: fixed for key in ("field", "field_name",
                                                  "compared_to")
                           if (fixed := _res(row.get(key)))}
                if updates:
                    row = {**row, **updates}
                    row_changed = True
            new_rows.append(row)
        if row_changed:
            new_hint[slot] = new_rows
            changed = True
    return new_hint if changed else hint


@dataclass(frozen=True)
class _XoDeferral:
    """One unverifiable cross-object effect NAME: the model-facing deferral
    ``detail`` plus the optional B0 offer payload (D-362) for the missed
    reference. The payload is what the D-340 recovery re-prompt hands back to
    the model, so an offer is only useful when it travels structured — the
    prose tail inside ``detail`` alone is un-followable telemetry. Offers stay
    FIELDS only (D-318/B0): the substrate discloses grounded alternatives, the
    model must re-propose — never a silent substitution."""

    detail: str
    offer: Optional[dict] = None


def _value_support_rerank(s1, neighborhood, cands, staged_value, at_seq):
    """F2 (D-377): STRUCTURAL value evidence over an admitted field offer —
    a candidate whose ACTIVE picklist carries the intent's own staged value
    outranks a lexical stranger (the ``Commercial_Tier__c`` residue: the
    staged 'Gold' lives in exactly one candidate's value set, so
    ``PLS_FB_Tier__c`` floats to top-1 and the D-340 offer-follow lands).
    Re-RANKS only, stable within groups: admission stays lexical (B0), no
    silent substitution anywhere — the model still re-proposes. Byte-
    identical ordering when no value is staged, no candidate's picklist
    carries it, or any S1 read fails. Returns ``(cands, supported_apis)``."""
    if (s1 is None or at_seq is None or not cands
            or not isinstance(staged_value, str) or not staged_value.strip()):
        return cands, frozenset()
    try:
        ids_by_api = {r.entity.sf_api_name: r.entity.id
                      for r in neighborhood
                      if r.edge_type == EDGE_BELONGS
                      and r.entity.entity_type == "Field"
                      and r.entity.sf_api_name}
        want = staged_value.strip().lower()
        supported = set()
        for c in cands:
            ent_id = ids_by_api.get(c.sf_api_name)
            if ent_id is None:
                continue
            details = s1.get_entity_details(ent_id, at_seq=at_seq) or {}
            pvs_id = details.get("picklist_value_set_entity_id")
            if not pvs_id:
                continue
            for v in s1.get_picklist_values(pvs_id, at_seq=at_seq) or []:
                if not v.get("is_active"):
                    continue
                if (str(v.get("value_api_name") or "").strip().lower() == want
                        or str(v.get("value_label") or "").strip().lower()
                        == want):
                    supported.add(c.sf_api_name)
                    break
        if not supported:
            return cands, frozenset()
        ordered = tuple(sorted(
            cands, key=lambda c: (c.sf_api_name not in supported,)))
        return ordered, frozenset(supported)
    except Exception:   # noqa: BLE001 — offers must never break grounding
        return cands, frozenset()


def _field_recovery_tail(proposed_names, neighborhood, *, s1=None,
                         staged_value=None, at_seq=None):
    """B0.2: ranked near-miss offers for failed FIELD references, from the
    subject's own BELONGS inventory (Field is inside the D-362 recovery
    boundary — lexical entities recover; behavioural ones never do). Returns
    ``(tail, offer)`` for the FIRST unresolved name with candidates: the
    model-facing feedback tail (alternatives, never a conclusion) and its
    telemetry payload. ``("", None)`` when everything resolves or nothing
    clears the similarity bar — callers keep their existing fallback (the
    raw field inventory). Live-observed need (the FB-V1 tier ACs): the
    alphabetized inventory line's first 8 names were all standard fields,
    hiding every custom name the model actually needed inside '+16 more' —
    ranked recovery surfaces the near-miss directly.

    F2 (D-377): callers holding the intent's staged value pass
    ``s1``/``staged_value``/``at_seq`` and the admitted set is re-ranked by
    :func:`_value_support_rerank`; omitted → byte-identical to pre-F2."""
    pool = [(r.entity.sf_api_name, r.entity.display_name)
            for r in neighborhood
            if r.edge_type == EDGE_BELONGS
            and r.entity.entity_type == "Field" and r.entity.sf_api_name]
    known = {api for api, _ in pool}
    for fld in proposed_names:
        if fld and fld not in known:
            cands = _recovery.rank_candidates(fld, pool)
            if cands:
                cands, supported = _value_support_rerank(
                    s1, neighborhood, cands, staged_value, at_seq)
                return (_recovery.format_candidates(cands),
                        _recovery.offer_payload("Field", fld, cands,
                                                value_supported=supported))
    return ("", None)


def _subject_field_inventory_line(neighborhood, limit: int = 8) -> str:
    """A compact, deterministic vocabulary line for field-miss refusal details
    (the D-340 recovery feedback truncates at ~160 chars — bare names only)."""
    bares = sorted({r.entity.sf_api_name.rsplit(".", 1)[-1]
                    for r in neighborhood
                    if r.edge_type == EDGE_BELONGS
                    and r.entity.entity_type == "Field"
                    and isinstance(r.entity.sf_api_name, str)})
    shown = ", ".join(bares[:limit])
    more = f" (+{len(bares) - limit} more)" if len(bares) > limit else ""
    return f"subject fields include: {shown}{more}"


def _flows_producing_effect(flow_entities, field_hint, expected_value, effect_object):
    """D-318: the subset of neighborhood Flow entities whose parsed Metadata effect
    (``flow_effects``) ACTUALLY produces the claim's declared effect — same-record
    (bare ``field_name`` + ``expected_value`` among the Flow's ``recordUpdates``
    assignments) or cross-object (``effect_object`` among the Flow's
    ``recordCreates``). This is how the automation-effect resolver binds the Flow
    when the LLM cannot know the org's internal Flow API name — it finds the Flow
    that verifiably produces the effect. Value equality is typed-tolerant
    (``_effect_values_equal``) so a numeric effect binds despite int/float/str shape
    drift between the LLM hint and the Tooling JSON. A Flow with empty/unparseable
    Metadata produces nothing, so the name-trust / ``flows[0]`` fallbacks keep today's
    behavior for pre-D-318 (Metadata-less) sync rows and the name-only fixtures."""
    bare = (field_hint.rsplit(".", 1)[-1]
            if isinstance(field_hint, str) and field_hint else None)
    out = []
    for ent in flow_entities:
        attrs = getattr(ent, "attributes", None)
        eff = flow_effects(attrs)
        # B1 arc: the Flow Behaviour IR's GROUNDED same-record effects join
        # the match set — before-save guarded literal `$Record` assignments
        # (FL01's mechanism) become verifiable producers. Unsupported/opaque
        # IR behaviours contribute nothing by construction, so a flow the
        # parser does not fully understand still refuses honestly.
        same_pairs = eff["same_record"] | flow_grounded_same_record_effects(attrs)
        same = bare is not None and expected_value is not None and any(
            fld == bare and _effect_values_equal(val, expected_value)
            for (fld, val) in same_pairs)
        # Completion E2: a typed update_records op on the effect object is a
        # verifiable producer too (the flow provably writes that object) —
        # the D-318 glance only saw recordCreates. Grounding still refuses
        # downstream unless the E2 branch can derive the full evidence shape.
        cross = bool(effect_object) and (
            effect_object in eff["cross_object"]
            or any(op["kind"] == "update_records"
                   and op["object"] == effect_object
                   and not op.get("on_fault_of")
                   and not op.get("temporal_path")
                   for op in flow_cross_record_effect_ops(attrs)))
        if same or cross:
            out.append(ent)
    return out


def _flows_producing_by_projection(flow_entities, field_hint, projection,
                                   expected_value=None):
    """The shared field-projection attribution: the neighborhood Flows whose
    Behaviour IR ``projection`` carries a GROUNDED behaviour on the claim's
    bare field (and, when ``expected_value`` is given, matching that value).
    Returns ``[(entity, behaviour dict), ...]``; a flow the parser does not
    fully understand contributes nothing (honest refusal upstream). The one
    owner behind the transform / temporal / transition producers — each is
    ``[(ent, beh) for ent in flows for beh in projection(ent) if
    beh matches]``, they differ only in which projection and whether value
    is checked."""
    bare = (field_hint.rsplit(".", 1)[-1]
            if isinstance(field_hint, str) and field_hint else None)
    if bare is None:
        return []
    out = []
    for ent in flow_entities:
        for beh in projection(getattr(ent, "attributes", None)):
            if beh["field"] == bare and (
                    expected_value is None
                    or _effect_values_equal(beh["value"], expected_value)):
                out.append((ent, beh))
    return out


def _flows_producing_transform(flow_entities, field_hint):
    """FL02 slice: Flows whose IR carries a GROUNDED before-save transform on
    the claim's field (``flow_grounded_transforms``) — value-LESS
    normalization producers."""
    return _flows_producing_by_projection(
        flow_entities, field_hint, flow_grounded_transforms)


def _flows_producing_temporal(flow_entities, field_hint):
    """C4 (FL08 slice): Flows whose IR carries a GROUNDED temporal stamp
    (RUN_DATE ± offset_days) on the claim's field
    (``flow_grounded_temporal_effects``) — Update-trigger stamp producers."""
    return _flows_producing_by_projection(
        flow_entities, field_hint, flow_grounded_temporal_effects)


def _flows_producing_transition(flow_entities, field_hint, expected_value):
    """C5 (FL09 slice): Flows whose IR carries a GROUNDED literal effect on an
    UPDATE-trigger flow matching the claim's (field, value)
    (``flow_grounded_transition_effects``) — prior-state transition
    producers. A value-less intent (``expected_value is None``) matches no
    transition producer (the transition shape needs the target state)."""
    if expected_value is None:
        return []
    return _flows_producing_by_projection(
        flow_entities, field_hint, flow_grounded_transition_effects,
        expected_value=expected_value)


def _flows_producing_premise_conditioned(flow_entities, field_hint,
                                         expected_value):
    """Completion (FL06 slice): flows whose IR carries a GROUNDED literal
    same-record write fired only when a sibling premise is NON-EMPTY
    (``flow_grounded_premise_conditioned_effects``). Value-less intents
    take the arm's value; a value-ful intent must match it."""
    bare = (field_hint.rsplit(".", 1)[-1]
            if isinstance(field_hint, str) and field_hint else None)
    if bare is None:
        return []
    out = []
    for ent in flow_entities:
        for beh in flow_grounded_premise_conditioned_effects(
                getattr(ent, "attributes", None)):
            if beh["field"] == bare and (
                    expected_value is None
                    or _effect_values_equal(beh["value"], expected_value)):
                out.append((ent, beh))
    return out


def _field_has_verifiable_producer(flow_entities, field_hint,
                                   effect_value, effect_object):
    """True when SOME neighborhood flow verifiably produces the claim's
    effect on ``field_hint`` — the Layer-1 admission twin of the emission
    tail's reachability. Admission must admit exactly what the tail can
    ground, or a capability that the tail owns (value-less ladder/transform/
    temporal grounding, C3b/C4) is unreachable live: the model names a
    guessed automation, the name does not resolve, and a value-less intent
    then fails the value-ful ``_flows_producing_effect`` check and is
    dismissed BEFORE the tail runs (the live tier/SLA gap). Covers: value-ful
    literal/cross-object (``_flows_producing_effect``), value-less transform
    (FL02) + temporal (FL08), value-ful transition (FL09), and the
    create-fireable ladder writer (FL03 — value-less enumerates all arms,
    value-ful matches one). The tail still refuses if it cannot DERIVE a
    witness (ground-or-refuse); this only widens the door to it."""
    ents = list(flow_entities)
    if _flows_producing_effect(ents, field_hint, effect_value, effect_object):
        return True
    if _flows_producing_transform(ents, field_hint):
        return True
    if _flows_producing_temporal(ents, field_hint):
        return True
    if effect_value is not None and _flows_producing_transition(
            ents, field_hint, effect_value):
        return True
    if _flows_producing_premise_conditioned(ents, field_hint, effect_value):
        return True
    bare = (field_hint.rsplit(".", 1)[-1]
            if isinstance(field_hint, str) and field_hint else None)
    if bare is not None:
        for ent in ents:
            for beh in flow_grounded_guarded_effects(
                    getattr(ent, "attributes", None)):
                if beh["field"] == bare and (
                        effect_value is None
                        or _effect_values_equal(beh["value"], effect_value)):
                    return True
    return False


def _field_capability_summary(flow_entities, field_hint) -> dict:
    """What the substrate can VERIFY about writes to ``field_hint`` — the
    structured descriptor shared by admission and grounding feedback (the
    convergence arc: every field-referencing refusal speaks the same
    vocabulary). Pure over the Behaviour-IR projections; bounded; never
    raises. Keys: ``literal_values`` (create-fireable ladder arms),
    ``transform`` / ``temporal`` / ``transition`` (bools), ``flows`` (the
    producing flow api names, sorted)."""
    bare = (field_hint.rsplit(".", 1)[-1]
            if isinstance(field_hint, str) and field_hint else None)
    out = {"literal_values": [], "transform": False, "temporal": False,
           "transition": False, "flows": set()}
    if bare is None:
        out["flows"] = []
        return out
    try:
        for ent in flow_entities:
            attrs = getattr(ent, "attributes", None)
            for b in flow_grounded_guarded_effects(attrs):
                if b["field"] == bare:
                    out["literal_values"].append(b["value"])
                    out["flows"].add(ent.sf_api_name)
            for beh, key in ((flow_grounded_transforms(attrs), "transform"),
                             (flow_grounded_temporal_effects(attrs),
                              "temporal"),
                             (flow_grounded_transition_effects(attrs),
                              "transition")):
                for b in beh:
                    if b["field"] == bare:
                        out[key] = True
                        out["flows"].add(ent.sf_api_name)
    except Exception:   # noqa: BLE001 — feedback must never break grounding
        pass
    out["literal_values"] = sorted({str(v) for v in out["literal_values"]})
    out["flows"] = sorted(out["flows"])
    return out


def _capability_line(field_name, summary) -> str:
    """The model-facing feedback line naming what IS verifiable on the
    field — appended to refusals so a mis-framed or mis-valued intent can
    converge instead of dead-ending. Empty when nothing is verifiable."""
    shapes = []
    if summary["literal_values"]:
        vals = summary["literal_values"][:6]
        shapes.append(f"literal value(s) {vals}")
    if summary["transform"]:
        shapes.append("a normalized transform of the input")
    if summary["temporal"]:
        shapes.append("a relative-date stamp")
    if summary["transition"]:
        shapes.append("a state-transition-guarded write")
    if not shapes:
        return ""
    flows = ", ".join(summary["flows"][:3])
    return (f" NOTE: {field_name} is verifiably WRITTEN by automation "
            f"({flows}) as {'; '.join(shapes)} — frame this as an "
            f"automation-effect claim on that field, and OMIT "
            f"expected_value where the org derives the value")


def _xo_update_producers(flow_entities, effect_object_api,
                         callee_registry=None):
    """Completion E2: the flows whose typed effect ops UPDATE a correlated
    set on ``effect_object_api`` — [(entity, op)]. The op's filters carry
    the pre-state template + correlation; its assignments the updated
    values. Fault-path and temporal-path ops are excluded (a fault
    handler is not the claim's main-path producer; a deferred effect is
    not immediately observable).

    Completion (composition): with a ``callee_registry`` ({flow api name:
    attributes}), a flow with NO direct match additionally offers its
    COMPOSED subflow effects (``compose_subflow`` — caller-frame
    correlation, the call-site guard as the op guard) — the FL12→SF01
    shape. Attribution stays on the CALLER (the record-triggered flow the
    org actually fires); ``via_subflow`` provenance rides the op."""
    out = []
    if not effect_object_api:
        return out
    for ent in flow_entities:
        attrs = getattr(ent, "attributes", None)
        direct = [
            op for op in flow_cross_record_effect_ops(attrs)
            if op["kind"] == "update_records"
            and op["object"] == effect_object_api
            and not op.get("on_fault_of") and not op.get("temporal_path")]
        for op in direct:
            out.append((ent, op))
        if not direct and callee_registry:
            for entry in compose_subflow(attrs, callee_registry):
                if entry.get("refusal"):
                    continue
                for op in entry.get("effect_ops") or ():
                    if op["kind"] == "update_records" \
                            and op["object"] == effect_object_api \
                            and not op.get("on_fault_of") \
                            and not op.get("temporal_path"):
                        out.append((ent, op))
    return out


def _flows_producing_rollup(flow_entities, subject_api, field_hint):
    """Completion E3: flows TRIGGERED ON ANOTHER OBJECT whose typed IR
    rolls an aggregate up onto the SUBJECT — the FL07 shape, and the
    general Salesforce roll-up idiom (parent field = Count/Sum over its
    children).

    A flow matches when ALL of (conservative, every piece typed):
    - record-triggered on a child object (≠ subject), no entry filters,
      no temporal path, no fault provenance;
    - ONE update_records op targeting ``subject_api`` whose sole filter
      is the parent correlation ``Id EqualTo ($Record, L)`` and whose
      assignment to the claimed bare field is ``("var", V)``;
    - a bounded collection aggregate (Count / Sum) writing into ``V``,
      unguarded, whose source premise queries the TRIGGER object
      correlated by the SAME lookup ``L`` (the sibling set — every child
      of the same parent).

    Returns ``[(entity, spec)]``; spec = {child_object, lookup, fn,
    source_field, premise} — everything the evidence derivation needs."""
    bare = (field_hint.rsplit(".", 1)[-1]
            if isinstance(field_hint, str) and field_hint else None)
    out = []
    if bare is None or not subject_api:
        return out
    for ent in flow_entities:
        attrs = getattr(ent, "attributes", None)
        trig = flow_behaviour(attrs).get("trigger") or {}
        child_api = trig.get("object")
        if (not trig.get("is_record_triggered") or not child_api
                or child_api == subject_api
                or trig.get("entry_filter_count")):
            continue
        for op in flow_cross_record_effect_ops(attrs):
            if (op["kind"] != "update_records"
                    or op["object"] != subject_api
                    or op.get("guard") or op.get("premise_guard")
                    or op.get("on_fault_of") or op.get("temporal_path")):
                continue
            filt = op.get("filters") or []
            if len(filt) != 1:
                continue
            f, fop, v = filt[0]
            if not (f == "Id" and fop == "EqualTo" and isinstance(v, tuple)
                    and len(v) == 2 and v[0] == "$Record"):
                continue
            lookup = v[1]
            assign = (op.get("assignments") or {}).get(bare)
            if not (isinstance(assign, (tuple, list)) and len(assign) == 2
                    and assign[0] == "var"):
                continue
            var = assign[1]
            for coll in flow_collection_aggregates(attrs):
                if coll.get("guard"):
                    continue
                agg = next((a for a in coll.get("aggregates", ())
                            if a.get("into") == var
                            and a.get("fn") in ("Count", "Sum")), None)
                if agg is None:
                    continue
                premise = next(
                    (p for p in flow_cross_record_premises(attrs)
                     if p.get("element") == coll.get("source")
                     and p.get("object") == child_api
                     and not p.get("guard")), None)
                if premise is None:
                    continue
                rel = classify_relation(premise)
                if not (rel["kind"] == "sibling_set"
                        and rel["correlation_field"] == lookup
                        and rel["subject_field"] == lookup):
                    continue
                out.append((ent, {
                    "child_object": child_api, "lookup": lookup,
                    "fn": agg["fn"], "source_field": agg.get("field"),
                    "premise": premise}))
    return out


def _drop_unobservable_producers(producers, effect_object_api):
    """Completion review (live env-59): the D-318 producer glance
    (``flow_effects``) reads RAW recordCreates/Updates and so counts writers
    whose only path to the object is a FAULT handler or a SCHEDULED path
    (env-59: FL13 logs to PLS_FB_Audit_Log__c only on fault; FL10 touches
    PLS_FB_Fulfilment_Task__c only on a +2-day schedule). Neither is
    observable in one run, so neither is a real candidate — they inflate the
    ambiguity and (FL13's case) erase the discriminator.

    A flow is dropped ONLY when its typed IR proves the exclusion: it HAS
    ops for the object and every one of them is fault/scheduled. A flow with
    NO typed ops (unparseable Metadata) is KEPT — an unknown producer must
    keep the ambiguity honest, never be silently dropped."""
    if not effect_object_api:
        return list(producers)
    out = []
    for ent in producers:
        attrs = getattr(ent, "attributes", None)
        typed = [o for o in flow_cross_record_effect_ops(attrs)
                 if o["object"] == effect_object_api]
        if not typed:
            out.append(ent)          # unparseable → keep (honest ambiguity)
            continue
        observable = (_xo_create_producers([ent], effect_object_api)
                      + _xo_eventual_create_producers([ent],
                                                      effect_object_api)
                      + _xo_update_producers([ent], effect_object_api))
        if observable:
            out.append(ent)
    return out


def _best_discriminator(disc):
    """Pick the field a model can realistically re-propose: the one whose
    literals are SHORTEST — an enum-like Kind/Type/Status ('AsyncEnrichment')
    over a prose Detail field ('Asynchronous enrichment completed for the
    confirmed order.'). Deterministic total order (max-literal-length, then
    field name), so the disclosure never varies run to run."""
    if not disc:
        return None, None
    field = min(disc, key=lambda f: (max(len(str(v)) for v in disc[f]), f))
    return field, disc[field]


def _cross_ambiguity_detail(producers, hint, default_detail):
    """The ambiguity refusal's wording. For a CROSS-OBJECT effect the legacy
    text ("name the specific automation", listing flow api names) asks the
    model for something D-318 says it cannot know — and on a real org the
    ambiguity is the NORM (env-59: three flows create PLS_FB_Audit_Log__c).
    Disclose the substrate-derived DISCRIMINATOR instead: the effect fields
    whose literals tell the candidates apart, so the model re-proposes with
    effect_field + effect_value (the B0 posture — grounded FIELD alternatives,
    never automation names). Falls back to ``default_detail`` for same-record
    ambiguity (untouched) or when nothing distinguishes the candidates."""
    obj = hint.get("effect_object")
    if not obj:
        return default_detail
    disc = _cross_effect_discriminators(list(producers), obj)
    if not disc:
        return default_detail
    field, values = _best_discriminator(disc)
    return (f"{len(producers)} automations produce a {obj} record — they are "
            f"told apart by {field} (one of {values}); name effect_field + "
            f"effect_value to select the one this requirement means")


def xo_hint_field(hint):
    """The claimed cross-object effect FIELD, if the intent named one."""
    return hint.get("effect_field")


def _tie_break_cross_producers(producers, hint):
    """Completion review (live env-59): the D-318 ambiguity gate refuses
    when several flows produce the claimed effect OBJECT — but on a real
    org that is the NORM (env-59: three flows create PLS_FB_Audit_Log__c),
    and the refusal asks for an automation name the model provably cannot
    supply (D-318/B0). When the intent names the effect FIELD (and
    optionally its value), the typed ops break the tie deterministically.
    STRICTLY a tie-break: a single producer passes through untouched, and
    a narrowing that does not land on exactly ONE leaves the ambiguity
    intact so the refusal stands — never a silent pick."""
    if len(producers) <= 1 or not hint.get("effect_object"):
        return producers
    # first drop the provably-unobservable writers (fault/scheduled paths):
    # they are not candidates, and one of them would erase the discriminator
    obj = hint.get("effect_object")
    producers = _drop_unobservable_producers(producers, obj) or producers
    if not hint.get("effect_field"):
        # an EXISTENCE-shaped claim ("a correlated record appears") is a
        # CREATE-only question: an update can never make a record appear, so
        # a co-writing updater is not a candidate and must not inflate the
        # ambiguity (env-59: FL04 creates the task, FL05 updates it —
        # "a task appears" means FL04, unambiguously).
        creators = [e for e in producers
                    if _xo_create_producers([e], obj)
                    or _xo_eventual_create_producers([e], obj)]
        if creators:
            producers = creators
        return producers
    if len(producers) <= 1:
        return producers
    narrowed = _flows_producing_cross_effect_field(
        producers, hint.get("effect_object"), hint.get("effect_field"),
        _identity_safe(hint.get("effect_value")))
    return narrowed if len(narrowed) == 1 else producers


def _op_assigns_effect(op, bare_field, expected_value) -> bool:
    """Does this typed effect op verifiably write ``bare_field`` (= the
    claimed value, when one is given)? Literal and relative-date
    assignments are the decidable shapes; a var/opaque/subject_ref
    assignment is NOT a verifiable match (never guessed)."""
    tv = (op.get("assignments") or {}).get(bare_field)
    if not tv:
        return False
    tv = tuple(tv)
    if expected_value is None:
        return True
    if tv[0] == "literal":
        return _effect_values_equal(tv[1], expected_value)
    if tv[0] == "relative_date":
        return _effect_values_equal(relative_date(tv[1]), expected_value)
    return False


def _narrow_producers_by_effect(pairs, effect_field, effect_value):
    """Completion review (live env-59 finding): when >1 flow produces the
    same effect OBJECT, the claimed effect FIELD/VALUE disambiguates —
    each producer's typed op writes its own literal (env-59's three
    PLS_FB_Audit_Log__c creators write Kind = Reopen / AsyncEnrichment /
    LedgerFault). A PURE filter over [(entity, op)]; the caller decides
    when to apply it and what an empty result means (an ambiguity the
    narrowing cannot break must REFUSE, never silently pick)."""
    bare = (effect_field.rsplit(".", 1)[-1]
            if isinstance(effect_field, str) and effect_field else None)
    if bare is None:
        return list(pairs)
    return [(e, op) for e, op in pairs
            if _op_assigns_effect(op, bare, effect_value)]


def _cross_effect_discriminators(flow_entities, effect_object_api) -> dict:
    """The substrate-derived way to TELL APART several producers of one
    effect object: {bare_field: [literal, ...]} over their typed
    observable ops, keeping only fields every candidate assigns a
    DISTINCT literal to. Feeds the ambiguity refusal's disclosure — the
    B0 posture (offer grounded FIELD alternatives; never automation
    names, the D-318 law)."""
    per_flow: list = []
    for ent in flow_entities:
        vals: dict = {}
        for _e, op in (_xo_create_producers([ent], effect_object_api)
                       + _xo_eventual_create_producers([ent],
                                                       effect_object_api)
                       + _xo_update_producers([ent], effect_object_api)):
            for f, tv in (op.get("assignments") or {}).items():
                tv = tuple(tv)
                if tv[0] == "literal":
                    vals.setdefault(f, set()).add(str(tv[1]))
        per_flow.append(vals)
    if len(per_flow) < 2:
        return {}
    common = set(per_flow[0])
    for v in per_flow[1:]:
        common &= set(v)
    out = {}
    for f in sorted(common):
        seen = [next(iter(v[f])) for v in per_flow if len(v[f]) == 1]
        if len(seen) == len(per_flow) and len(set(seen)) == len(seen):
            out[f] = sorted(seen)      # every candidate distinct on f
    return out


def _flows_producing_cross_effect_field(flow_entities, effect_object_api,
                                        effect_field, effect_value):
    """The tie-break for the D-318 ambiguity gate: the flows whose typed
    OBSERVABLE ops on ``effect_object_api`` verifiably write the claimed
    (effect_field[, effect_value]). Fault-path and scheduled ops never
    qualify (they carry no observable main-path effect)."""
    bare = (effect_field.rsplit(".", 1)[-1]
            if isinstance(effect_field, str) and effect_field else None)
    if bare is None or not effect_object_api:
        return []
    out = []
    for ent in flow_entities:
        pairs = (_xo_create_producers([ent], effect_object_api)
                 + _xo_eventual_create_producers([ent], effect_object_api)
                 + _xo_update_producers([ent], effect_object_api))
        if any(_op_assigns_effect(op, bare, effect_value) for _e, op in pairs):
            out.append(ent)
    return out


def _xo_eventual_create_producers(flow_entities, effect_object_api):
    """C9 (FL11 slice): flows whose typed IR creates the effect object on
    an ASYNC run-after-commit path — observability ``bounded_eventual``
    (the platform commits the side effect seconds later, in the same
    logical action). Scheduled paths (``deferred_reobservation_required``)
    stay excluded — a +2-day effect is not observable within one run.
    [(entity, op)]; fault-path ops excluded as everywhere."""
    out = []
    if not effect_object_api:
        return out
    for ent in flow_entities:
        for op in flow_cross_record_effect_ops(getattr(ent, "attributes",
                                                       None)):
            if op["kind"] == "create_record" \
                    and op["object"] == effect_object_api \
                    and not op.get("on_fault_of") \
                    and op.get("temporal_path") \
                    and op.get("observability") == "bounded_eventual":
                out.append((ent, op))
    return out


def _xo_create_producers(flow_entities, effect_object_api):
    """Completion Program E1: the flows whose TYPED effect ops include a
    create_record on ``effect_object_api`` — [(entity, op)]. Richer than
    the D-318 glance: the op carries the correlation assignment, the
    literal/relative-date field values, the guard, and the branch."""
    out = []
    if not effect_object_api:
        return out
    for ent in flow_entities:
        for op in flow_cross_record_effect_ops(getattr(ent, "attributes",
                                                       None)):
            if op["kind"] == "create_record" \
                    and op["object"] == effect_object_api \
                    and not op.get("on_fault_of") \
                    and not op.get("temporal_path"):
                # a fault-handler create is not the main path's producer;
                # a deferred create is not immediately observable
                out.append((ent, op))
    return out


def _field_regex_patterns(neighborhood, bare_field):
    """The REGEX format patterns the subject's ACTIVE validation rules pin on
    ``bare_field``, plus the names of active rules that MENTION the field but
    whose formulas the parser cannot read (the fail-closed set — an opaque
    rule could bounce any witness). Deterministic; patterns sorted."""
    patterns: set = set()
    opaque: set = set()
    token = _re_mod.compile(r"(?<![0-9A-Za-z_])" + _re_mod.escape(bare_field)
                            + r"(?![0-9A-Za-z_])")
    for r in neighborhood:
        e = r.entity
        if r.edge_type != EDGE_VALIDATION_RULE \
                or e.entity_type != "ValidationRule" \
                or not vr_is_active(e.attributes):
            continue
        formula = vr_formula_text(e.attributes) or ""
        if not token.search(formula):
            continue
        tree = parse(formula)
        if not is_parsed(tree):
            opaque.add(e.sf_api_name or "?")
            continue
        found_regex = False
        for node in walk(tree):
            if isinstance(node, FunctionCall) and node.name == "REGEX" \
                    and len(node.args) == 2 \
                    and isinstance(node.args[0], FieldRef) \
                    and node.args[0].path[-1] == bare_field \
                    and isinstance(node.args[1], Literal) \
                    and isinstance(node.args[1].value, str):
                patterns.add(node.args[1].value)
                found_regex = True
        if not found_regex:
            # parsed, mentions the field, but pins something the witness
            # machinery does not model (e.g. a length rule) — fail closed
            opaque.add(e.sf_api_name or "?")
    return sorted(patterns), sorted(opaque)


def _active_approvals(neighborhood) -> list:
    """D-320: the ACTIVE ApprovalProcess entities that TRIGGERS_ON the subject —
    the approval twin of the ``flows`` list. An approval process is the org's
    internal automation the LLM cannot name (like a Flow), but it carries no Flow
    Metadata, so it binds by ENUMERATION (the single approval on the subject) not
    by ``flow_effects``. ACTIVE only (the D-301 law: an inactive automation cannot
    fire, so it must never ground)."""
    return [r.entity for r in neighborhood
            if r.edge_type == EDGE_FLOW
            and r.entity.entity_type == "ApprovalProcess"
            and (r.entity.attributes or {}).get("_is_active")]


def _names_a_subject_approval(neighborhood, name) -> bool:
    """D-320: True when ``name`` matches an ApprovalProcess (active OR inactive)
    that TRIGGERS_ON the subject — a deliberate reference to a SPECIFIC approval.
    The single-approval enumeration must NOT override it: an active match already
    binds by name (D-308); an inactive match must refuse (D-301, "never grounds"),
    never silently rebind to a different active approval (the D-299 class)."""
    return bool(name) and any(
        r.edge_type == EDGE_FLOW and r.entity.entity_type == "ApprovalProcess"
        and r.entity.sf_api_name == name
        for r in neighborhood)


# ---------------------------------------------------------------------------
# Admissibility engine (D-096.1 / D-078) — substrate-authored
# ---------------------------------------------------------------------------

class AdmissibilityEngine:
    def __init__(self, s1_model: SemanticOrgModel):
        self._s1 = s1_model

    def resolve_subject(self, entity_type: str, sf_api_name: str, at_seq: int) -> list[Entity]:
        return self._s1.get_entities(entity_type, at_seq=at_seq,
                                     filters={"sf_api_name": sf_api_name})

    def scoped_neighborhood(self, subject: Entity, at_seq: int) -> list:
        """Single-hop inbound walk off the Object subject (D-096.1)."""
        return self._s1.get_related(
            subject.id, OBJECT_NEIGHBORHOOD_EDGES, "inbound", at_seq=at_seq,
        )

    # -- B0 grounded recovery (near-miss candidates from the pinned snapshot)
    #
    # A failed reference gets a SMALL, deterministically-ranked candidate set
    # drawn only from S1 at the run's pinned version — the substrate offers
    # grounded alternatives, never conclusions, and never substitutes: the
    # model must re-propose its choice, which re-enters normal resolution.
    # Bounded fail-safes: Field recovery requires a resolvable owning Object
    # ("Obj.Field" qualification — the shape the model already sends) and an
    # oversized pool yields NOT_FOUND rather than a scan (never a directory
    # dump, never a perf cliff). Never raises.

    _RECOVERY_MAX_POOL = 1500

    # The B0 recovery boundary (D-362). Candidate recovery is LEXICAL, so it
    # is offered only for entities whose identity is lexical — schema/config
    # entities. BEHAVIOURAL entities (automations) are NEVER candidate-
    # recoverable: behavioural entities require behavioural verification
    # (effect-first binding, D-318/D-299) — lexical recovery alone is
    # insufficient, and supplying an automation name lets the name-trust
    # binding attach an automation WITHOUT verifying its effect (live-observed
    # wrong-attribution at the B0 exit gate). ALLOWLIST, not blocklist:
    # unknown/new entity types default to non-recoverable.
    _RECOVERY_ALLOWED_TYPES = frozenset({
        "Object", "Field", "RecordType", "ValidationRule", "PermissionSet",
        # documented-recoverable per D-362; dormant until S1 models the type
        "CustomMetadata",
    })
    # The documented NON-recoverable behavioural types (D-362) — listed for
    # the boundary's readability; the allowlist above is the enforcement.
    _RECOVERY_BEHAVIOURAL_TYPES = frozenset({
        "Flow", "ApprovalProcess", "ApexClass", "InvocableAction"})

    def recover_reference(self, entity_type: Optional[str],
                          proposed_api: Optional[str],
                          at_seq: Optional[int],
                          context_text: Optional[str] = None,
                          ) -> _recovery.RecoveryResolution:
        if not entity_type or not proposed_api or at_seq is None:
            return _recovery.RecoveryResolution(_recovery.NOT_FOUND)
        if entity_type not in self._RECOVERY_ALLOWED_TYPES:
            return _recovery.RecoveryResolution(_recovery.NOT_FOUND)
        try:
            if self.resolve_subject(entity_type, proposed_api, at_seq):
                return _recovery.RecoveryResolution(_recovery.RESOLVED)
            if entity_type == "Field":
                pool = self._field_pool(proposed_api, at_seq)
            else:
                ents = self._s1.get_entities(entity_type, at_seq=at_seq)
                if len(ents) > self._RECOVERY_MAX_POOL:
                    return _recovery.RecoveryResolution(_recovery.NOT_FOUND)
                pool = [(e.sf_api_name, e.display_name) for e in ents
                        if e.sf_api_name]
            cands = _recovery.rank_candidates(proposed_api, pool,
                                              context_text=context_text)
        except Exception:   # noqa: BLE001 — recovery must never break resolution
            return _recovery.RecoveryResolution(_recovery.NOT_FOUND)
        if not cands:
            return _recovery.RecoveryResolution(_recovery.NOT_FOUND)
        return _recovery.RecoveryResolution(_recovery.CANDIDATES, cands)

    def _field_pool(self, proposed_api: str, at_seq: int) -> list:
        """Candidate pool for a Field miss: the BELONGS_TO fields of the
        proposed name's owning Object ("Obj.Field"). An unqualified name or an
        unresolvable owner yields an empty pool — the owner Object gets its own
        recovery at its own check site."""
        if "." not in proposed_api:
            return []
        owner_api = proposed_api.split(".", 1)[0]
        owners = self.resolve_subject("Object", owner_api, at_seq)
        if len(owners) != 1:
            return []
        return [(r.entity.sf_api_name, r.entity.display_name)
                for r in self.scoped_neighborhood(owners[0], at_seq)
                if r.edge_type == EDGE_BELONGS
                and r.entity.entity_type == "Field" and r.entity.sf_api_name]

    def _rollup_admits(self, subject, field_hint, effect_value_hint,
                       effect_object_hint, at_seq) -> bool:
        """Completion E3 — the admission twin of the emission tail's
        roll-up grounding: a flow triggered on a CHILD object verifiably
        aggregates the sibling set onto this subject field. Value-less,
        same-record-framed intents only (exactly what the tail grounds:
        the aggregate's value is chosen by the evidence, so a proposed
        value can never verifiably match). Bounded org-wide scan — the
        producer is, by definition, outside the subject's TRIGGERS_ON
        neighborhood, which is why the per-neighborhood checks miss it."""
        if (not field_hint or effect_value_hint is not None
                or effect_object_hint or at_seq is None):
            return False
        return bool(_flows_producing_rollup(
            self._s1.get_entities("Flow", at_seq=at_seq),
            subject.sf_api_name, field_hint))

    def is_negative(self, claim_kind: str, polarity_hint: str) -> bool:
        return claim_kind in _INHERENTLY_NEGATIVE or polarity_hint == "negative"

    def evaluate(self, *, archetype: str, claim_kind: str, polarity_hint: str,
                 subject: Entity, neighborhood: list, excerpt: str,
                 path_id: str = "c0", field_hint: Optional[str] = None,
                 automation_hint: Optional[str] = None,
                 effect_value_hint=None,
                 effect_object_hint: Optional[str] = None,
                 at_seq: Optional[int] = None) -> _Candidate:
        """Derive the requirement-anchored candidate and determine Layer-1
        admissibility. Substrate-authored; returns a single _Candidate."""
        cand = _Candidate(
            path_id=path_id, archetype=archetype, claim_kind=claim_kind,
            subject_refs=[{"entity_type": subject.entity_type,
                           "sf_api_name": subject.sf_api_name}],
            requirement_anchor=excerpt, status="dismissed",
        )

        # Guardrail-1 meaningfulness floor: claim_kind meaningful on the subject.
        if archetype == "data_behavior" and subject.entity_type == "Object":
            if claim_kind not in _DATA_BEHAVIOR_CLAIM_KINDS:
                cand.dismissal_reason = "archetype_mismatch"
                return cand
        else:
            # subject/archetype shape not data-behavior-on-Object -> type mismatch
            cand.dismissal_reason = "type_incompatibility"
            return cand

        if self.is_negative(claim_kind, polarity_hint):
            return self._evaluate_negative(cand, claim_kind, neighborhood)
        return self._evaluate_positive(cand, claim_kind, neighborhood, field_hint,
                                       automation_hint, effect_value_hint,
                                       effect_object_hint, subject=subject,
                                       at_seq=at_seq)

    def _evaluate_negative(self, cand: _Candidate, claim_kind: str, neighborhood: list) -> _Candidate:
        dim = _NEGATIVE_LAYER1_DIM.get(claim_kind)
        if dim is None:
            cand.dismissal_reason = "no_constraint_supports_negative"
            return cand
        edge_type, far_type = dim
        found = [r for r in neighborhood
                 if r.edge_type == edge_type and r.entity.entity_type == far_type]
        if found:
            # Layer 1: the constraint EXISTS and is ACTIVE (NOT formula-verified).
            cand.status = "admissibly_grounded"
            cand.admissibility_layer = AdmissibilityLayer.LAYER_1.value
        else:
            cand.dismissal_reason = "no_constraint_supports_negative"
        return cand

    def _evaluate_positive(self, cand: _Candidate, claim_kind: str, neighborhood: list,
                           field_hint: Optional[str] = None,
                           automation_hint: Optional[str] = None,
                           effect_value_hint=None,
                           effect_object_hint: Optional[str] = None,
                           subject: Optional[Entity] = None,
                           at_seq: Optional[int] = None) -> _Candidate:
        # Positive grounding needs supporting structure (a Field BELONGS_TO the
        # subject Object). A **value-claim** asserts ``field == V``, so it grounds
        # only when the *named* field exists (verify-at-grounding, D-115.3): an
        # unknown named field (or none named) is ``insufficient_grounding``, never
        # an any-field pass. A positive **automation-effect** grounds on its REAL
        # dimension — a Flow ``TRIGGERS_ON`` the subject (D-210.1; the same edge
        # the negative dim binds) — never the any-field proxy. Other positive
        # claim_kinds keep the object-level any-field proxy (the refusal-vertical
        # floor).
        fields = [r for r in neighborhood
                  if r.edge_type == EDGE_BELONGS and r.entity.entity_type == "Field"]
        if claim_kind == "value-claim":
            grounds = bool(field_hint) and any(
                r.entity.sf_api_name == field_hint for r in fields)
            if not grounds and field_hint:
                # B1 arc + B0.2: the named field did not resolve — a RANKED
                # near-miss offer converges the re-prompt; the raw inventory
                # stays the fallback when nothing clears the bar.
                _tail, _ = _field_recovery_tail([field_hint], neighborhood)
                cand.dismissal_detail = (
                    f"field {field_hint!r} does not resolve on the subject;"
                    + (_tail or " "
                       + _subject_field_inventory_line(neighborhood)))
        elif claim_kind == "automation-effect-claim":
            flows = [r for r in neighborhood
                     if r.edge_type == EDGE_FLOW and r.entity.entity_type == "Flow"]
            # D-299: with >1 Flow TRIGGERS_ON the subject (env-59 Opportunity has
            # three), a requirement-NAMED Flow must bind THAT flow — grounding on
            # the wrong (first-encountered) flow would assert the wrong effect.
            # A named-but-absent flow is a genuine grounding miss (refuse). No
            # name -> the any-flow floor (backward-compat; safe with one flow).
            # D-304: a named CALCULATED FIELD also grounds — the org's formula
            # engine is the automation (the same character of dimension).
            if automation_hint:
                grounds = any(r.entity.sf_api_name == automation_hint for r in flows)
                if not grounds:
                    # D-308: a named APPROVAL PROCESS also grounds — it rides
                    # the same TRIGGERS_ON rail (submission-triggered record
                    # automation). ACTIVE only (the D-301 law: an inactive
                    # automation cannot fire, so it must never ground).
                    grounds = any(
                        r.edge_type == EDGE_FLOW
                        and r.entity.entity_type == "ApprovalProcess"
                        and r.entity.sf_api_name == automation_hint
                        and (r.entity.attributes or {}).get("_is_active")
                        for r in neighborhood)
                if not grounds:
                    grounds = any(
                        r.edge_type == EDGE_BELONGS
                        and r.entity.entity_type == "Field"
                        and r.entity.sf_api_name == automation_hint
                        and field_is_calculated(r.entity.attributes)
                        for r in neighborhood)
                if not grounds:
                    # D-318: the LLM cannot know the org's internal Flow name — admit
                    # when a Flow TRIGGERS_ON the subject actually PRODUCES the claimed
                    # effect (its Metadata writes field=value or creates effect_object).
                    # The binding block then binds THAT Flow by its effect.
                    # B1 arc (C3b/C4/C5): a value-LESS producer (transform,
                    # temporal, ladder arm) or a value-ful transition producer
                    # also admits — the emission tail owns these, so admission
                    # must reach them (the live tier/SLA dismissal was here: a
                    # guessed automation name + a value-less intent failed the
                    # value-ful effect check and never reached the tail).
                    grounds = _field_has_verifiable_producer(
                        [r.entity for r in flows], field_hint,
                        effect_value_hint, effect_object_hint)
                if not grounds:
                    grounds = self._rollup_admits(
                        subject, field_hint, effect_value_hint,
                        effect_object_hint, at_seq)
            else:
                grounds = bool(flows) or self._rollup_admits(
                    subject, field_hint, effect_value_hint,
                    effect_object_hint, at_seq)
            if (not grounds and effect_object_hint == "ProcessInstance"
                    and not _names_a_subject_approval(neighborhood, automation_hint)):
                # D-320: an approval-process effect (ProcessInstance) is the org's
                # internal automation the LLM cannot name (it sends "<UNKNOWN>" /
                # an invented name / none) — admit when a single active approval
                # TRIGGERS_ON the subject. The binding block binds it by ENUMERATION
                # (approvals carry no Flow Metadata, so _flows_producing_effect never
                # matches them). A named real approval is left to the paths above
                # (active bound by name; inactive dismissed) — kept consistent with
                # the binding block so the gate never admits what binding refuses.
                grounds = bool(_active_approvals(neighborhood))
        else:
            grounds = bool(fields)
        if grounds:
            cand.status = "admissibly_grounded"
            cand.admissibility_layer = AdmissibilityLayer.LAYER_1.value
        else:
            cand.dismissal_reason = "insufficient_grounding"
            # B0: a failed FIELD reference gets a grounded near-miss offer
            # (rides the ungrounded-claim payload -> the D-340 recovery
            # re-prompt) so a wrong guess can recover instead of decaying
            # into a blanket model self-refusal. Deliberately FIELDS ONLY:
            # an automation-effect's automation hint gets NO candidates —
            # supplying Flow/ApprovalProcess names would let the D-299
            # name-trust binding attach an automation without verifying its
            # effect (live-observed wrong-attribution at the B0 exit gate);
            # the LLM never names automations (D-318), so the substrate must
            # never teach it to.
            # Convergence arc (replay-measured): the automation-effect
            # Layer-1 dismissal was a FEEDBACK BLACK HOLE — 113/144
            # historical dismissals were the `automation_name==field_name`
            # calculated-field idiom with a near-miss field, dismissed with
            # no detail and no offer, and the model abandoned the AC. The
            # dismissal now says WHY, honestly: a MISSED field carries the
            # ranked near-miss offer + tail (fields only — automation names
            # are never offered, the D-318/B0 law); a RESOLVED field with no
            # verifiable producer says so with NO offer (the reference was
            # right — candidates would be noise); a resolved field WITH
            # producers names the framing that grounds.
            _ae_field_resolved = (
                claim_kind == "automation-effect-claim" and field_hint
                and any(r.entity.sf_api_name == field_hint for r in fields))
            if not (claim_kind == "automation-effect-claim"
                    and _ae_field_resolved):
                cand.recovery = self._named_ref_recovery(
                    claim_kind, fields, field_hint,
                    staged_value=effect_value_hint, at_seq=at_seq)
            if claim_kind == "automation-effect-claim" and field_hint:
                resolved = _ae_field_resolved
                if not resolved:
                    _tail, _ = _field_recovery_tail([field_hint],
                                                    neighborhood)
                    cand.dismissal_detail = (
                        f"the observed field {field_hint!r} did not resolve "
                        f"on the subject and no automation verifiably "
                        f"produces the claimed effect." + (_tail or ""))
                else:
                    _summ = _field_capability_summary(
                        [r.entity for r in flows], field_hint)
                    _cap = _capability_line(field_hint, _summ)
                    cand.dismissal_detail = (
                        (f"no automation on the subject verifiably writes "
                         f"{field_hint!r} the claimed way." + _cap)
                        if _cap else
                        (f"{field_hint!r} resolves, but NO automation on "
                         f"the subject verifiably writes it — do not "
                         f"re-propose this as an automation effect; if the "
                         f"requirement still implies it, address it with "
                         f"no_admissible_test"))
        return cand

    def _named_ref_recovery(self, claim_kind: str, fields: list,
                            field_hint: Optional[str],
                            staged_value=None,
                            at_seq: Optional[int] = None) -> Optional[dict]:
        """B0: near-miss offer for a value-claim's Layer-1 FIELD miss — the
        hint ranks against the subject's own BELONGS_TO fields. Never raises;
        None when nothing clears the threshold. Automation hints are
        deliberately NOT recovered (see caller).

        F2 (D-377): when the intent staged a value, the admitted set is
        re-ranked by structural value evidence (:func:`_value_support_rerank`
        — a bounded picklist 2-hop per admitted candidate, <=3 reads)."""
        try:
            if claim_kind in ("value-claim",
                              "automation-effect-claim") and field_hint:
                pool = [(r.entity.sf_api_name, r.entity.display_name)
                        for r in fields if r.entity.sf_api_name]
                cands = _recovery.rank_candidates(field_hint, pool)
                if cands:
                    cands, supported = _value_support_rerank(
                        self._s1, fields, cands, staged_value, at_seq)
                    return _recovery.offer_payload(
                        "Field", field_hint, cands, value_supported=supported)
        except Exception:   # noqa: BLE001 — recovery must never break grounding
            return None
        return None


# ---------------------------------------------------------------------------
# Decomposition controller (D-083d) — canonical-per-failure-mode + top-K
# ---------------------------------------------------------------------------

class DecompositionController:
    """For the refusal vertical: one canonical candidate per failure mode
    (bounded enumeration is single-candidate here; ≥2-grounded selection is the
    draft path). Lower-specificity dismissals attach during enumeration when
    multiple constraints could ground — exercised when present."""

    MAX_K = 5

    def enumerate_candidates(self, base: _Candidate) -> list[_Candidate]:
        return [base]


# ---------------------------------------------------------------------------
# Refusal router (D-088 / D-083b) — outcome-level aggregate
# ---------------------------------------------------------------------------

class RefusalRouter:
    # Provenance (B0 telemetry honesty): every payload whose ``detail`` the
    # SUBSTRATE authored carries ``detail_source: "substrate"``; a payload
    # recording MODEL prose verbatim (the D-247 no_admissible_test hinge)
    # carries ``detail_source: "model"`` — so a model explanation can never
    # read as a substrate fact downstream (the req-320 job-76 incident).

    def underspecified(self, reason: str = "no claim_kind to anchor a candidate") -> RefusalDirective:
        return RefusalDirective(RefusalKind.UNDERSPECIFIED_REQUIREMENT, {
            "detail": reason, "detail_source": "substrate",
            "detail_layer": "resolution"})

    def ambiguous(self, matches: list[Entity]) -> RefusalDirective:
        return RefusalDirective(RefusalKind.AMBIGUOUS_REFERENCE, {
            "matched": [{"entity_type": m.entity_type, "sf_api_name": m.sf_api_name,
                         "id": str(m.id)} for m in matches],
            "detail_source": "substrate", "detail_layer": "resolution",
        })

    def no_relevant_context(self, detail: str, *, source: str = "substrate",
                            layer: str = "resolution",
                            candidates: Optional[dict] = None) -> RefusalDirective:
        payload: dict[str, Any] = {"detail": detail, "detail_source": source,
                                   "detail_layer": layer}
        if candidates:
            payload["candidates"] = candidates   # B0 offer (source: substrate)
        return RefusalDirective(RefusalKind.NO_RELEVANT_CONTEXT, payload)

    def behaviour_incomplete(self, detail: str) -> RefusalDirective:
        """D-293 decision-2: a prohibition intent that is not a COMPLETE behaviour
        instance — no derivable behavioural reject recipe from the grounding VR(s)
        (non-numeric formula, or a non-VR-rejectable operation like delete/share/
        transfer) — refuses HERE rather than degrading to the caveated metadata
        inspection (the pre-D-293 fallback that masked the AC1/2/4 collapse). A
        policy refusal: the requirement is admissible, but the substrate declines
        to author a behaviourally-empty prohibition. Lifts as violation-derivation
        widens (the out-of-scope D-293 follow-on)."""
        return RefusalDirective(RefusalKind.BEHAVIOUR_INCOMPLETE, {
            "detail": detail, "detail_source": "substrate",
            "detail_layer": "grounding"})

    def emission_deferred(self, archetype: str, claim_kind: str,
                          detail: Optional[str] = None,
                          candidates: Optional[dict] = None) -> RefusalDirective:
        """A groundable claim whose emission for this claim_kind isn't built yet
        (D-105). Operational/substrate-runtime: the requirement is admissible,
        but the emission machinery is deferred (D-097.6) — an honest capability
        boundary that lifts as kinds land, NOT an input-quality invalidity.
        ``detail`` overrides the generic message when a SPECIFIC sub-shape
        defers (D-210.1 — e.g. cross-object transitions). ``candidates`` (B0.2)
        carries a near-miss offer payload exactly as ``no_relevant_context``
        does — provenance: substrate."""
        payload: dict[str, Any] = {
            "detail": detail or (
                f"{archetype}/{claim_kind} is groundable, but emission for "
                f"this claim_kind is not yet built"),
            "detail_source": "substrate",
            "detail_layer": "grounding",
            "archetype": archetype,
            "claim_kind": claim_kind,
        }
        if candidates:
            payload["candidates"] = candidates
        return RefusalDirective(RefusalKind.EMISSION_DEFERRED, payload)

    def from_dismissed(self, cand: _Candidate, *, is_negative: bool) -> RefusalDirective:
        """Map an all-dismissed reasoning outcome to the outcome-level
        refusal_kind (D-083b aggregate)."""
        reason = cand.dismissal_reason
        if is_negative and reason == "no_constraint_supports_negative":
            cause = ("ontology_gap" if cand.claim_kind in _ONTOLOGY_GAP_CLAIM_KINDS
                     else "no_org_constraint")
            dim = _NEGATIVE_LAYER1_DIM.get(cand.claim_kind)
            what_unblocks = (["substrate-3 Apex/Tier-2 modeling"]
                             if cause == "ontology_gap" else [])
            return RefusalDirective(RefusalKind.NO_ADMISSIBLE_NEGATIVE_SCENARIO_FOUND, {
                "detail_source": "substrate", "detail_layer": "admissibility",
                "cause": cause,
                "proposed_negative_assertion": {"claim_kind": cand.claim_kind,
                                                "subject_refs": cand.subject_refs},
                "searched_constraint_dimensions": [dim[1]] if dim else [],
                "no_grounding_found_because": reason,
                "what_would_unblock": what_unblocks,
            })
        # positive ungrounded, or meaningfulness mismatch -> ungrounded-claim
        payload: dict[str, Any] = {
            "claim_kind": cand.claim_kind,
            "subject_refs": cand.subject_refs,
            "dismissal_reason": reason,
            "detail_source": "substrate",
            "detail_layer": "admissibility",
        }
        # B0 (D-362): a dismissal that carries a grounded-recovery offer
        # (near-miss candidates for the reference that failed) surfaces it on
        # the payload so the D-340 recovery re-prompt can show the model its
        # options.
        if cand.recovery:
            payload["candidates"] = cand.recovery
        # B1 arc: dismissal context (e.g. the subject's field vocabulary on a
        # named-field miss) rides the recovery feedback's `detail` slot —
        # substrate-authored, consistent with the provenance tags above.
        if cand.dismissal_detail:
            payload["detail"] = cand.dismissal_detail
        return RefusalDirective(RefusalKind.UNGROUNDED_CLAIM, payload)


# ---------------------------------------------------------------------------
# Property-claim grounding: semantic value comparison (D-246)
# ---------------------------------------------------------------------------

def _property_value_matches(asserted: Any, s1_value: Any) -> bool:
    """Whether a property-claim's LLM-asserted value semantically equals the
    S1-stored native value (D-246). The LLM routinely expresses a correct value
    in a different representation — the string ``"8"`` for an S1 integer ``8`` —
    which a strict ``==`` wrongly dismissed as ``insufficient_grounding`` (the
    SQ-212 AC6/AC7 finding). Compare by coercing the asserted value to the S1
    value's NATIVE type so a semantically-equal value grounds, while a genuinely
    different value (``"9"`` vs ``8``) still dismisses.

    Generic over the stored native type — never type-specific (the resolver
    handles length / precision / scale, all ints here, plus any future detail
    type). Coercion that is impossible (``"abc"`` -> int) or lossy/ambiguous
    (the float ``8.7`` -> int ``8``) is rejected via a round-trip check: the
    values are treated as unequal so the resolver dismisses. Invent-nothing —
    a false match is worse than a miss, and the emitted claim still carries the
    S1-read value, never the asserted one."""
    if asserted == s1_value:
        return True
    if asserted is None or s1_value is None:
        return False
    native = type(s1_value)
    try:
        coerced = native(asserted)
    except (TypeError, ValueError):
        return False
    if coerced != s1_value:
        return False
    # Round-trip guard against lossy/ambiguous coercion (int(8.7) == 8,
    # bool("false") is True): the coerced value must reproduce the asserted
    # representation to count as a match.
    try:
        return type(asserted)(coerced) == asserted
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# GovernanceCore — implements GovernanceProvider (D-096.6)
# ---------------------------------------------------------------------------

class GovernanceCore:
    """Real governance over the S1 boundary. Refusal vertical: ``resolve_intent``
    + ``route_refusal`` are real; emission (``finalize_outcome`` /
    ``accept_selection``) is stubbed (draft vertical)."""

    def __init__(self, s1_model: SemanticOrgModel):
        self._s1 = s1_model
        self._admit = AdmissibilityEngine(s1_model)
        self._layer_b = LayerBFilter()
        self._decomp = DecompositionController()
        self._router = RefusalRouter()
        # D-376 shadow resolution: per-seq symbol-table cache (empty until the
        # first observation; init performs NO reads — test-pinned).
        self._shadow_tables: dict = {}

    # -- Layer A operational ref-existence (D-095.1) --------------------
    def check_refs_exist(self, *, intent_input: dict, ctx: ConversationContext) -> RefCheck:
        at = ctx.semantic_context.s1_version_seq
        if at is None:
            return RefCheck(ok=False, feedback="no s1_version_seq pinned in semantic_context")
        # D-207 + D-311: one propose call may carry N intents. Layer A mirrors the
        # semantic seam's per-intent posture (resolve_intent refuses ONLY at zero
        # grounded, governance_core.py resolve_intent): reject the whole call for
        # correction ONLY when EVERY intent's refs miss. When at least one intent's
        # refs resolve, proceed — the missing-ref intents flow to resolve_intent,
        # which grounds the valid ones and records each miss as a D-302
        # partial_refusal via its "post-check_refs_exist this is defensive" branch.
        # This preserves partial coverage (the base-prompt contract, prompts/base.md
        # "partial coverage with honest dismissals beats forced breadth"): one
        # unfixable ref no longer sinks a whole multi-intent batch into
        # structural-validation-failure (the L7g journey regression, D-310). The
        # all-miss case still rejects for correction, so a fixable typo on a
        # single-intent-equivalent batch keeps its correction hop; declared-AC
        # requirements get a further shot at a dropped intent via the D-247
        # coverage re-prompt.
        per_intent = normalize_propose_input(intent_input)
        if len(per_intent) == 1:
            return self._check_refs_one(per_intent[0], at,
                                        getattr(ctx, 'requirement_text', None))
        failures = [(i, rc) for i, rc in
                    ((i, self._check_refs_one(
                        pi, at, getattr(ctx, 'requirement_text', None)))
                     for i, pi in enumerate(per_intent))
                    if not rc.ok]
        if len(failures) < len(per_intent):
            return RefCheck(ok=True)   # >=1 intent's refs resolve -> proceed (partial coverage)
        missing = [m for _, rc in failures for m in rc.missing_refs]
        feedback = "; ".join(f"intent[{i}]: {rc.feedback}" for i, rc in failures)
        offers = [o for _, rc in failures for o in rc.offers]
        return RefCheck(ok=False, missing_refs=missing, feedback=feedback,
                        offers=offers)

    def _ref_miss(self, et: Optional[str], api: Optional[str], at: int,
                  context_text: Optional[str] = None):
        """B0: feedback tail + telemetry offer for one unresolved endpoint —
        ``("", None)`` when no grounded near-miss clears the threshold (the
        rejection text then stays byte-identical to pre-B0). ``context_text``
        (the requirement text) grounds the relatedness term of the ranking."""
        rec = self._admit.recover_reference(et, api, at,
                                            context_text=context_text)
        if rec.status != _recovery.CANDIDATES:
            return "", None
        return (_recovery.format_candidates(rec.candidates),
                _recovery.offer_payload(et, api, rec.candidates))

    def _check_refs_one(self, intent_input: dict, at: int,
                        context_text: Optional[str] = None) -> RefCheck:
        desc = intent_input.get("intent_descriptor") or {}
        if desc.get("no_admissible_test"):
            return RefCheck(ok=True)   # D-247: a per-AC refusal needs no S1 ref
        hint = desc.get("target_subject_hint") or {}

        # configuration metadata-relationship: target_subject_hint carries the
        # relationship {edge_type, source, target}; both endpoints must resolve.
        if desc.get("archetype_hint") == "configuration":
            # existence / property (D-122): a flat subject ref, not source/target.
            if desc.get("claim_kind_hint") in ("existence-claim", "property-claim"):
                et, api = hint.get("entity_type"), hint.get("sf_api_name")
                if not et or not api or not self._admit.resolve_subject(et, api, at):
                    tail, offer = self._ref_miss(et, api, at, context_text)
                    return RefCheck(ok=False, missing_refs=[f"{et}:{api}"],
                                    feedback=(f"subject not found at s1_version_seq {at}: "
                                              f"{et}:{api}." + tail),
                                    offers=[offer] if offer else [])
                return RefCheck(ok=True)
            missing: list[str] = []
            offers: list[dict] = []
            tails: list[str] = []
            for label in ("source", "target"):
                ep = hint.get(label) or {}
                et, api = ep.get("entity_type"), ep.get("sf_api_name")
                if not et or not api or not self._admit.resolve_subject(et, api, at):
                    missing.append(f"{label}:{et}:{api}")
                    tail, offer = self._ref_miss(et, api, at, context_text)
                    if offer:
                        offers.append(offer)
                        tails.append(f"For {label} {api!r}:{tail}")
            if missing:
                return RefCheck(ok=False, missing_refs=missing,
                                feedback=(f"relationship endpoint(s) not found at "
                                          f"s1_version_seq {at}: {missing}."
                                          + " ".join([""] + tails if tails else [])),
                                offers=offers)
            return RefCheck(ok=True)

        # permission capability-claim (D-123): two endpoints — grantee + target —
        # carried under target_subject_hint, not the flat {entity_type, sf_api_name}.
        if desc.get("archetype_hint") == "permission":
            missing = []
            offers = []
            tails = []
            for label in ("grantee", "target"):
                ep = hint.get(label) or {}
                et, api = ep.get("entity_type"), ep.get("sf_api_name")
                if not et or not api or not self._admit.resolve_subject(et, api, at):
                    missing.append(f"{label}:{et}:{api}")
                    tail, offer = self._ref_miss(et, api, at, context_text)
                    if offer:
                        offers.append(offer)
                        tails.append(f"For {label} {api!r}:{tail}")
            if missing:
                return RefCheck(ok=False, missing_refs=missing,
                                feedback=(f"grant endpoint(s) not found at "
                                          f"s1_version_seq {at}: {missing}."
                                          + " ".join([""] + tails if tails else [])),
                                offers=offers)
            return RefCheck(ok=True)

        # ui layout-claim (D-124): two endpoints — layout + field — carried under
        # target_subject_hint, not the flat {entity_type, sf_api_name}.
        if desc.get("archetype_hint") == "ui":
            missing = []
            offers = []
            tails = []
            for label in ("layout", "field"):
                ep = hint.get(label) or {}
                et, api = ep.get("entity_type"), ep.get("sf_api_name")
                if not et or not api or not self._admit.resolve_subject(et, api, at):
                    missing.append(f"{label}:{et}:{api}")
                    tail, offer = self._ref_miss(et, api, at, context_text)
                    if offer:
                        offers.append(offer)
                        tails.append(f"For {label} {api!r}:{tail}")
            if missing:
                return RefCheck(ok=False, missing_refs=missing,
                                feedback=(f"layout endpoint(s) not found at "
                                          f"s1_version_seq {at}: {missing}."
                                          + " ".join([""] + tails if tails else [])),
                                offers=offers)
            return RefCheck(ok=True)

        et, api = hint.get("entity_type"), hint.get("sf_api_name")
        if not et or not api:
            return RefCheck(ok=False, feedback=(
                "target_subject_hint must be an entity ref {entity_type, sf_api_name}; "
                "descriptive selectors are not yet supported (query_entities deferred)"))
        matches = self._admit.resolve_subject(et, api, at)
        if not matches:
            tail, offer = self._ref_miss(et, api, at, context_text)
            return RefCheck(ok=False, missing_refs=[f"{et}:{api}"],
                            feedback=(f"no {et} named {api!r} exists at "
                                      f"s1_version_seq {at}." + tail),
                            offers=[offer] if offer else [])
        return RefCheck(ok=True)   # >=1 exists; disambiguation is semantic (resolve_intent)

    # -- semantic reasoning ---------------------------------------------
    def resolve_intent(self, *, intent_input: dict, ctx: ConversationContext, state: Any) -> IntentResolution:
        """Resolve one propose call. D-207: the call may carry N intents
        (``intent_descriptors``); each resolves independently through the
        per-archetype machinery, then the results aggregate — path ids are
        re-indexed per intent (``c0..c{n-1}``), grounded candidates from every
        intent are presented together, failed intents stay recorded as
        dismissals in the merged delta, and the call refuses only when ZERO
        intents ground (the first refusal directive routes). D-302: every
        per-intent refusal directive — routed or not — is also recorded as a
        ``partial_refusals`` entry in the merged delta, keyed by the intent's
        re-indexed path slot, so a mixed batch says WHY each non-emitting
        intent emitted nothing (D-247 refusal visibility). The legacy
        singular form resolves exactly as before."""
        per_intent = normalize_propose_input(intent_input)
        # D-247: a follow-up propose turn (the coverage enforcer's single
        # re-prompt) must not collide with the prior turn's path ids — offset by
        # the candidate_paths already accumulated on the state. Offset 0 on the
        # first turn ⇒ byte-identical to the pre-D-247 behavior.
        ai = getattr(state, "attempted_interpretation", None)
        offset = len(ai.get("candidate_paths") or []) if isinstance(ai, dict) else 0
        if len(per_intent) == 1:
            res = self._resolve_one(per_intent[0], ctx, state)
            if offset:
                _reindex_paths(res, offset)
            return res

        merged: dict = {"candidate_paths": [], "dismissed_alternatives_by_reason": {},
                        "scoped_neighborhood": []}
        grounded_all: list[PresentedCandidate] = []
        first_refusal: Optional[RefusalDirective] = None
        for i, pi in enumerate(per_intent):
            res = self._resolve_one(pi, ctx, state)
            # Decomposition returns <=1 grounded candidate per intent (D-207
            # decision 7) — intent-scoped SELECTION is deliberately unbuilt.
            # C3b exception: N-arm emission (a value-less classification intent
            # that enumerates every ladder arm) returns N grounded candidates
            # with PROCEED_TO_EMIT — all EMITTED, none selected among — and
            # they all share this intent's path slot (reindexed below), so the
            # merged delta stays collision-free. The ban is on an unresolved
            # multi-candidate SELECTION in a batch, not on multi-arm emission.
            assert (len(res.grounded_candidates) <= 1
                    or res.next_action == NextAction.PROCEED_TO_EMIT), \
                "multi-intent propose met an unresolved selection for one intent"
            _reindex_paths(res, i + offset)
            d = res.interpretation_delta or {}
            merged["candidate_paths"].extend(d.get("candidate_paths") or [])
            for reason, ids in (d.get("dismissed_alternatives_by_reason") or {}).items():
                merged["dismissed_alternatives_by_reason"].setdefault(reason, []).extend(ids)
            merged["scoped_neighborhood"].extend(d.get("scoped_neighborhood") or [])
            grounded_all.extend(res.grounded_candidates)
            if res.refusal is not None:
                if first_refusal is None:
                    first_refusal = res.refusal
                desc = pi.get("intent_descriptor") or {}
                merged.setdefault("partial_refusals", []).append({
                    "path_id": f"c{i + offset}",
                    "ac_ref": desc.get("ac_ref"),
                    "archetype": desc.get("archetype_hint"),
                    "claim_kind": desc.get("claim_kind_hint"),
                    "refusal_kind": res.refusal.refusal_kind.value,
                    "payload": res.refusal.payload,
                })

        if not grounded_all:
            return IntentResolution(grounded_candidates=[], next_action=NextAction.REFUSE,
                                    interpretation_delta=merged,
                                    refusal=first_refusal or self._router.underspecified(
                                        "no intent grounded"))
        return IntentResolution(grounded_candidates=grounded_all,
                                next_action=NextAction.PROCEED_TO_EMIT,
                                interpretation_delta=merged)

    def _resolve_one(self, intent_input: dict, ctx: ConversationContext, state: Any) -> IntentResolution:
        desc = intent_input.get("intent_descriptor") or {}
        excerpt = intent_input.get("requirement_excerpt", "")
        # D-247: an explicit per-AC refusal — the model declares no admissible
        # test for this AC. Recorded (surfaces in attempted_interpretation), never
        # ground; the runtime maps it into coverage_map. The honesty hinge: the
        # substrate originates nothing, it only records the model's refusal.
        if desc.get("no_admissible_test"):
            return self._resolve_no_admissible_test(desc, excerpt)
        archetype = desc.get("archetype_hint")
        if archetype == "configuration":
            return self._resolve_configuration(intent_input, ctx, state)
        if archetype == "permission":
            return self._resolve_permission(intent_input, ctx, state)
        if archetype == "ui":
            return self._resolve_ui(intent_input, ctx, state)
        polarity = desc.get("polarity_hint")
        claim_kind = desc.get("claim_kind_hint")
        hint = _scrub_placeholder_values(desc.get("target_subject_hint") or {})
        et, api = hint.get("entity_type"), hint.get("sf_api_name")
        at = ctx.semantic_context.s1_version_seq

        matches = self._admit.resolve_subject(et, api, at) if (et and api and at is not None) else []

        # D-376 shadow semantic-resolution: read-only observation of the
        # subject the model named vs. what the intent's own structural
        # evidence supports. PROMOTION BOUNDARY: nothing may read the
        # verdicts to change behavior (see shadow_resolution's docstring).
        try:
            shadow_resolution.observe_subject_resolution(
                self._s1, self._shadow_tables, desc, excerpt, ctx, matches,
                state)
        except Exception:
            log.debug("D-376 shadow observation failed (ignored)",
                      exc_info=True)

        # ambiguity (interpretation phase: ambiguous_target_resolution)
        if len(matches) > 1:
            delta = self._delta(neighborhood=[], candidates=[_dismissed_stub(
                archetype, claim_kind, et, api, excerpt, "ambiguous_target_resolution")])
            return IntentResolution(grounded_candidates=[], next_action=NextAction.REFUSE,
                                    interpretation_delta=delta, refusal=self._router.ambiguous(matches))
        if not matches:
            # post-check_refs_exist this is defensive; with multi-intent partial
            # coverage (D-311) bad-ref intents DO reach here — same B0 recovery
            # offer as Layer A so the miss stays recoverable on the re-prompt.
            tail, offer = self._ref_miss(
                et, api, at, getattr(ctx, "requirement_text", None))
            return IntentResolution(grounded_candidates=[], next_action=NextAction.REFUSE,
                                    interpretation_delta=self._delta([], []),
                                    refusal=self._router.no_relevant_context(
                                        f"subject {et}:{api} did not resolve at version {at}."
                                        + tail, candidates=offer))
        subject = matches[0]

        # underspecified: no claim_kind to anchor a candidate -> no candidate
        # formed (no dismissal; the refusal stands alone)
        if not claim_kind:
            return IntentResolution(grounded_candidates=[], next_action=NextAction.REFUSE,
                                    interpretation_delta=self._delta([], []),
                                    refusal=self._router.underspecified())

        # D-307.1 (review SF): an expected_absence hint on any kind that has no
        # absence vertical must REFUSE, never be silently ignored — ignoring it
        # authors an artifact asserting the OPPOSITE of the intent (the
        # meaning-inversion class). Only automation-effect consumes the flag.
        if (hint.get("expected_absence") is not None
                and claim_kind != "automation-effect-claim"):
            return IntentResolution(
                grounded_candidates=[], next_action=NextAction.REFUSE,
                interpretation_delta=self._delta([], []),
                refusal=self._router.emission_deferred(
                    archetype, claim_kind,
                    detail=("expected_absence rides the automation-effect "
                            f"vertical only — on {claim_kind} the flag would "
                            "be ignored and the authored artifact would "
                            "assert the opposite of the intent")))

        # Layer B reject-only sanity floor (D-096.3) -> dismissal -> refusal
        lb_reason = self._layer_b.reject_reason(excerpt, subject)
        if lb_reason is not None:
            cand = _Candidate(path_id="c0", archetype=archetype, claim_kind=claim_kind,
                              subject_refs=[{"entity_type": subject.entity_type,
                                             "sf_api_name": subject.sf_api_name}],
                              requirement_anchor=excerpt, status="dismissed",
                              dismissal_reason=lb_reason)
            return IntentResolution(grounded_candidates=[], next_action=NextAction.REFUSE,
                                    interpretation_delta=self._delta([], [cand]),
                                    refusal=self._router.underspecified("excerpt anchoring too weak"))

        # admissibility (real S1 grounding, Layer 1)
        neighborhood = self._admit.scoped_neighborhood(subject, at)
        # B1 arc: deterministic field-name canonicalization — a proposed name
        # is rewritten to the subject's real qualified api-name when it
        # resolves UNIQUELY (bare / suffix / label rules); 0-or-ambiguous
        # keeps the proposed name and the downstream refusal/drop stands.
        # Division of responsibility: the model names the behaviour's field,
        # the substrate supplies the org's naming mechanics.
        # F1 (D-377): canonicalize EVERY subject-owned field slot (was:
        # field_name on value/automation-effect only). The F0 probe over the
        # req-320/req-315 corpus measured the gap: 566 ladder-resolvable
        # mentions the narrow rewrite left to refusal-hops (208, condition
        # slots) or silent trigger DROPS (358 — weakened staged tests).
        # Shadow observation above sees the RAW names (telemetry honesty);
        # everything downstream of here sees canonical ones.
        hint = _canonicalize_subject_fields(hint, neighborhood)
        # Control-telemetry Phase 0: OBSERVE the subject's control facts for the
        # finalize-time coverage map. Read-only — stashing changes no resolution
        # outcome; refused intents stash too (Expected must not depend on
        # emission success).
        _stash_control_facts(state, subject, neighborhood)
        base = self._admit.evaluate(archetype=archetype, claim_kind=claim_kind,
                                    polarity_hint=polarity, subject=subject,
                                    neighborhood=neighborhood, excerpt=excerpt,
                                    field_hint=hint.get("field_name"),
                                    automation_hint=hint.get("automation_name"),
                                    effect_value_hint=hint.get("expected_value"),
                                    effect_object_hint=hint.get("effect_object"),
                                    at_seq=at)
        candidates = self._decomp.enumerate_candidates(base)
        grounded = [c for c in candidates if c.status == "admissibly_grounded"]
        delta = self._delta(neighborhood, candidates)

        if not grounded:
            is_neg = self._admit.is_negative(claim_kind, polarity)
            directive = self._router.from_dismissed(base, is_negative=is_neg)
            return IntentResolution(grounded_candidates=[], next_action=NextAction.REFUSE,
                                    interpretation_delta=delta, refusal=directive)

        # Emittability gate (D-105.2): a grounded-but-unbuilt claim_kind refuses
        # (emission-deferred) instead of PROCEED_TO_EMIT -> finalize crash. The
        # expected path for value / state-transition / automation-effect until
        # their emission is built — the runtime face of D-097.6's deferral.
        if not is_emittable(archetype, claim_kind):
            return IntentResolution(grounded_candidates=[], next_action=NextAction.REFUSE,
                                    interpretation_delta=delta,
                                    refusal=self._router.emission_deferred(archetype, claim_kind))

        # Stash grounding for the prohibition-negative emission (D-101.1),
        # mirroring config's _resolve_configuration. Only prohibition-claim emits
        # in Phase 2 step 1; other data_behavior kinds remain finalize-stubbed
        # (the D-100 carve-out). D-293: an incomplete behaviour instance REFUSES
        # here rather than degrading to a caveated metadata inspection.
        if state is not None and claim_kind == "prohibition-claim":
            # D-293 (Option A): ground the LLM-proposed rejection business-state
            # (target_subject_hint.rejection_conditions) — each clause's field must
            # BELONG_TO the subject. An ungroundable/ill-formed clause refuses
            # (invent-nothing). Absent -> ([],[]) -> conditions=() (identity is
            # condition-free, the de-collapse mechanism just isn't exercised).
            grounded_conds, invalid_conds = _ground_rejection_conditions(
                hint.get("rejection_conditions"), neighborhood, at)
            if invalid_conds:
                # B0: a clause whose FIELD does not BELONG_TO the subject gets
                # a near-miss offer from the subject's own field inventory (the
                # AC4/External_Reference__c class) — grounded alternatives, the
                # model re-proposes or refuses.
                offer, tail = None, ""
                known = {r.entity.sf_api_name for r in neighborhood
                         if r.edge_type == EDGE_BELONGS
                         and r.entity.entity_type == "Field"}
                pool = [(r.entity.sf_api_name, r.entity.display_name)
                        for r in neighborhood
                        if r.edge_type == EDGE_BELONGS
                        and r.entity.entity_type == "Field" and r.entity.sf_api_name]
                for clause in (hint.get("rejection_conditions") or []):
                    fld = (clause or {}).get("field")
                    if fld and fld not in known:
                        cands = _recovery.rank_candidates(fld, pool)
                        if cands:
                            offer = _recovery.offer_payload("Field", fld, cands)
                            tail = _recovery.format_candidates(cands)
                        break
                return IntentResolution(
                    grounded_candidates=[], next_action=NextAction.REFUSE,
                    interpretation_delta=delta,
                    refusal=self._router.no_relevant_context(
                        f"rejection-condition not grounded on "
                        f"{subject.sf_api_name}: {invalid_conds}." + tail,
                        layer="grounding", candidates=offer))
            # Carry the grounding VRs' formulas so authoring (and the gate below)
            # can run the D-107 verified-vs-caveated derivation (re-found from the
            # same in-scope neighborhood Layer-1 grounding matched).
            vr_all = _grounding_vr_formulas(claim_kind, neighborhood)
            # D-295: narrow the grounding VRs to the ONE whose fields match this
            # claim's conditions, so the prohibition grounds on its OWN rule rather
            # than the first-derivable generic VR on a multi-VR object. Rebinding
            # here means the gate below AND the persisted GroundedNegative see the
            # same narrowed tuple. Condition-free / single-VR pass through unchanged
            # (byte-identical to pre-D-295); a >=2-VR set with conditions but no
            # aligning VR narrows to () -> the gate below refuses (D-295, not D-293).
            vr_formulas = _align_vr_to_conditions(vr_all, grounded_conds)
            # D-294: read-only S1 field metadata (type/picklist) for the fields in
            # the scoped neighborhood — feeds metadata-driven violation-derivation
            # (cross-field / NOT-ISBLANK / NOT-ISPICKVAL / bare-boolean). DORMANT
            # this slice (derive ignores it), so the gate result is unchanged.
            field_metadata = _grounding_field_metadata(neighborhood, self._s1, at)
            if hint.get("approval_actions") is not None:
                # D-333: the approval-action arc — its completeness is
                # constructional (the explicit attempted_change IS the update
                # under test; the org's own actions realize the approval
                # state), so the D-293 VR-derivability gate does not apply.
                # The arc STAGES its conditions (the acceptance discipline),
                # so the D-332 picklist bind applies to them here.
                staged_conds, unbindable = _bind_picklist_values(
                    grounded_conds, field_metadata)
                if unbindable:
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind,
                            detail="; ".join(unbindable)))
                actions, change, arc_error = _ground_arc_prohibition(
                    hint, neighborhood, field_metadata,
                    _active_approvals(neighborhood))
                if arc_error is not None:
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind, detail=arc_error))
                # D-337: the arc's setup create must SUCCEED (the actions +
                # the rejected attempted_change ARE the test) — staged
                # conditions that provably fire ANY active VR, including the
                # aligned one, bounce the create before the arc ever runs.
                # attempted_change is deliberately not checked (it is the
                # update under test), and no post-actions state is modeled
                # (the approval rewrites the record — that is the arc's
                # point).
                conflict = _staged_vr_conflict_detail(
                    neighborhood,
                    {c.field.external_id: c.value for c in staged_conds
                     if c.predicate == "equals"})
                if conflict is not None:
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind, detail=conflict))
                _stash_grounding(state, GroundedNegative(
                    archetype=archetype, claim_kind=claim_kind,
                    operation_hint=hint.get("operation"), version_seq=at,
                    subject=_Endpoint(
                        entity_id=subject.id, entity_type=subject.entity_type,
                        external_id=subject.sf_api_name or str(subject.id)),
                    requirement_excerpt=excerpt,
                    vr_formulas=vr_formulas,
                    conditions=tuple(staged_conds),
                    field_metadata=field_metadata,
                    vr_messages=_grounding_vr_messages(
                        claim_kind, neighborhood),
                    approval_actions=actions,
                    attempted_change=change))
            else:
                # Amendment B (AK 2026-07-09): the RECORD-TYPE context hypothesis.
                # When the LLM's grounded conditions name a classification value that
                # resolves (deterministic DeveloperName normalization) to a RecordType
                # on the subject, the requirement ("Enterprise deals are subject to
                # stricter discount controls") has a DISTINCT record-context reading
                # whose relevant control the field-overlap selector cannot reach (VR08
                # gates on RecordType, not on a claim condition field). Nominate that
                # control by control-relevance (context-gate ∧ subject-governance ∧
                # behavioural-role) — NOT entailment, which the threshold-less
                # requirement cannot satisfy. On success, ground Hypothesis B on the
                # nominated VR with the RecordType context as the claim's identity-
                # bearing condition; a str result is the refuse-and-surface detail when
                # the field AND record-type hypotheses both role-align distinct controls.
                _nom = _nominate_record_type_control(
                    vr_all, grounded_conds, field_metadata, neighborhood, excerpt)
                if isinstance(_nom, str):
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.behaviour_incomplete(
                            f"prohibition on {subject.sf_api_name}: {_nom}"))
                if _nom is not None:
                    nominated_vr, context_cond = _nom
                    vr_formulas = (nominated_vr,)
                    grounded_conds = (context_cond,)
                # D-293 decision-2 (refuse, never silently degrade): the behaviour
                # instance is complete only when a BEHAVIOURAL reject recipe is
                # derivable. A non-numeric VR (NOT-ISBLANK / picklist / cross-field) or
                # a non-VR-rejectable operation (delete / share / transfer) derives
                # nothing -> REFUSE here, rather than emitting the pre-D-293 caveated
                # inspection (which masked the AC1/2/4 collapse and degraded AC3 to a
                # bare existence check). Conditions de-collapse identity (Reading B);
                # derivability is the hard gate. D-294 widens what derives via
                # field_metadata (dormant here).
                # VR10 arc: thread the neighborhood's VR messages as sibling
                # items so the gate's yes/no matches authoring (the transition
                # path's sibling isolation + UNSAT refusal both live behind it).
                _sibling_items = [
                    (msg or text, text)
                    for text, msg in _grounding_vr_messages(
                        claim_kind, neighborhood).items()]
                if not prohibition_recipe_derivable(
                        hint.get("operation"), vr_formulas, field_metadata,
                        sibling_vr_items=_sibling_items):
                    # D-295: a MISMATCH (no rule aligns with the asserted state) and
                    # D-293's derivability gap (the aligned/only VR cannot be tested)
                    # both refuse here, with distinct BA-facing reasons.
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.behaviour_incomplete(
                            _prohibition_refusal_detail(
                                subject.sf_api_name, vr_formulas, vr_all,
                                grounded_conds)))
                _stash_grounding(state, GroundedNegative(
                    archetype=archetype, claim_kind=claim_kind,
                    operation_hint=hint.get("operation"), version_seq=at,
                    subject=_Endpoint(
                        entity_id=subject.id, entity_type=subject.entity_type,
                        external_id=subject.sf_api_name or str(subject.id)),
                    requirement_excerpt=excerpt,
                    vr_formulas=vr_formulas,
                    # D-293: the grounded business-state -> the claim's identity-bearing
                    # semantic_conditions (distinct states -> distinct claims).
                    conditions=tuple(grounded_conds),
                    # D-294: read-only field metadata for violation-derivation breadth
                    # (dormant this slice; derive ignores it).
                    field_metadata=field_metadata,
                    # D-297 (lever 5): {VR formula -> error message} for the grounding
                    # VRs; DORMANT here — 5.2 looks up the DERIVED source formula's
                    # message and projects it into the recipe's error_message_pattern.
                    vr_messages=_grounding_vr_messages(claim_kind, neighborhood)))

        # Stash grounding for the positive value-claim (D-115.3). Grounding has
        # already verified the NAMED field exists, so re-resolve it from the same
        # neighborhood. The value is carried verbatim from the intent — S3 never
        # fabricates one (D-115 §2): a value-claim with no value defers
        # (grounded-then-deferred) rather than inventing a value.
        if state is not None and claim_kind == "value-claim":
            field_ent = next(
                (r.entity for r in neighborhood
                 if r.edge_type == EDGE_BELONGS and r.entity.entity_type == "Field"
                 and r.entity.sf_api_name == hint.get("field_name")), None)
            expected_value = _identity_safe(hint.get("expected_value"))
            # No concrete value → grounded-then-deferred (D-115 §2). The LLM's
            # "<UNKNOWN>" sentinel is NOT a value — it must never cross into an
            # executable recipe (it would create/assert the literal "<UNKNOWN>",
            # a runtime type error on a typed field). Refuse at grounding.
            if (field_ent is None or expected_value is None
                    or expected_value == _UNKNOWN_SENTINEL):
                return IntentResolution(
                    grounded_candidates=[], next_action=NextAction.REFUSE,
                    interpretation_delta=delta,
                    refusal=self._router.emission_deferred(archetype, claim_kind))
            # Type-validity floor: a value-claim CREATEs the field with this value
            # and asserts it back, so a non-type-valid value is unexecutable —
            # refuse rather than emit a create the org rejects for a parse/format
            # reason (never the behaviour under test). Absent metadata → pass
            # (the certainty bar, matching _grounding_field_metadata).
            _vc_meta = _grounding_field_metadata(neighborhood, self._s1, at)
            _vc_reason = _value_type_invalid(
                expected_value,
                _vc_meta.get((field_ent.sf_api_name or "").rsplit(".", 1)[-1]))
            if _vc_reason is not None:
                return IntentResolution(
                    grounded_candidates=[], next_action=NextAction.REFUSE,
                    interpretation_delta=delta,
                    refusal=self._router.emission_deferred(
                        archetype, claim_kind, detail=_vc_reason))
            _stash_grounding(state, GroundedPositive(
                archetype=archetype, claim_kind=claim_kind, version_seq=at,
                target_object=_Endpoint(
                    entity_id=subject.id, entity_type=subject.entity_type,
                    external_id=subject.sf_api_name or str(subject.id)),
                field=_Endpoint(
                    entity_id=field_ent.id, entity_type=field_ent.entity_type,
                    external_id=field_ent.sf_api_name or str(field_ent.id)),
                value=expected_value, requirement_excerpt=excerpt))

        # D-305: the acceptance stash — every clause grounds like a D-293
        # rejection condition (BELONGS_TO + predicate/value coupling), PLUS the
        # stageability bar (equals stages a value; is_null stays absent; any
        # other predicate cannot deterministically stage a create — refuse)
        # and writability (an equals clause on a calculated/read-only field
        # cannot be staged — refuse; the D-294 rail supplies the metadata).
        if state is not None and claim_kind == "acceptance-claim":
            proposed = hint.get("acceptance_conditions")
            grounded_conds, invalid = _ground_rejection_conditions(
                proposed, neighborhood, at)
            if invalid:
                # B0.2: same near-miss offer the prohibition clauses get —
                # a clause field that does not BELONG recovers lexically.
                _tail, _offer = _field_recovery_tail(
                    [(c or {}).get("field") for c in (proposed or [])],
                    neighborhood)
                return IntentResolution(
                    grounded_candidates=[], next_action=NextAction.REFUSE,
                    interpretation_delta=delta,
                    refusal=self._router.emission_deferred(
                        archetype, claim_kind,
                        detail="; ".join(invalid) + _tail,
                        candidates=_offer))
            if not grounded_conds:
                return IntentResolution(
                    grounded_candidates=[], next_action=NextAction.REFUSE,
                    interpretation_delta=delta,
                    refusal=self._router.emission_deferred(
                        archetype, claim_kind,
                        detail=("an acceptance claim needs at least one "
                                "grounded condition clause — the business "
                                "state that DEFINES the accepted case")))
            unstageable = sorted({c.predicate for c in grounded_conds
                                  if c.predicate not in ("equals", "is_null")})
            if unstageable:
                return IntentResolution(
                    grounded_candidates=[], next_action=NextAction.REFUSE,
                    interpretation_delta=delta,
                    refusal=self._router.emission_deferred(
                        archetype, claim_kind,
                        detail=(f"acceptance conditions must be stageable on "
                                f"one create — equals/is_null only; got "
                                f"{unstageable}")))
            # D-305.1 (review S4): an is_null-ONLY set stages nothing — the
            # "acceptance" would be proven by pure padding. Require >=1 equals.
            if not any(c.predicate == "equals" for c in grounded_conds):
                return IntentResolution(
                    grounded_candidates=[], next_action=NextAction.REFUSE,
                    interpretation_delta=delta,
                    refusal=self._router.emission_deferred(
                        archetype, claim_kind,
                        detail=("an acceptance case needs at least one "
                                "equals clause — an is_null-only state would "
                                "be proven by padding, not by the case")))
            cond_meta = _grounding_field_metadata(neighborhood, self._s1, at)

            def _bare(ext):
                return ext.split(".", 1)[-1]

            # D-332: staged equals-values on picklist fields bind to the
            # org's ACTUAL picklist values ("Home Loan" → "Home") — an
            # unbound label stages a create a restricted picklist rejects,
            # failing the acceptance for a staging reason. No unique bind →
            # refuse, naming the valid values.
            grounded_conds, unbindable = _bind_picklist_values(
                grounded_conds, cond_meta)
            if unbindable:
                return IntentResolution(
                    grounded_candidates=[], next_action=NextAction.REFUSE,
                    interpretation_delta=delta,
                    refusal=self._router.emission_deferred(
                        archetype, claim_kind,
                        detail="; ".join(unbindable)))

            # D-305.1 (review S5): a create-only operation needs CREATEABLE
            # specifically (not the createable-OR-updateable floor).
            nonwritable = sorted(
                c.field.external_id for c in grounded_conds
                if c.predicate == "equals" and (
                    (m := cond_meta.get(_bare(c.field.external_id))) is None
                    or m.get("is_calculated")
                    or not m.get("is_createable", True)))
            if nonwritable:
                return IntentResolution(
                    grounded_candidates=[], next_action=NextAction.REFUSE,
                    interpretation_delta=delta,
                    refusal=self._router.emission_deferred(
                        archetype, claim_kind,
                        detail=(f"acceptance condition field(s) not "
                                f"createable (calculated/read-only): "
                                f"{nonwritable}")))
            # D-305.1 (review B3): an is_null clause on a REQUIRED field is
            # structurally defeated — k16 padding fills non-nillable fields,
            # so the executed record would CONTRADICT the claimed state and
            # green a case the org truthfully rejects. Fail closed.
            required_nulls = sorted(
                c.field.external_id for c in grounded_conds
                if c.predicate == "is_null" and not (
                    cond_meta.get(_bare(c.field.external_id)) or {}
                ).get("is_nillable", True))
            if required_nulls:
                return IntentResolution(
                    grounded_candidates=[], next_action=NextAction.REFUSE,
                    interpretation_delta=delta,
                    refusal=self._router.emission_deferred(
                        archetype, claim_kind,
                        detail=(f"is_null asserted on REQUIRED field(s) "
                                f"{required_nulls} — required-field padding "
                                f"would contradict the claimed state")))
            # D-306: the OPTIONAL update phase — "the CHANGE succeeds" (the
            # stage-progress case). Grounds through the same clause machinery
            # as the initial conditions, PLUS: equals-only (the change is
            # staged by one PATCH — nothing else stages deterministically)
            # and UPDATEABLE specifically (not the createable bar the
            # initial clauses use). Refuse-not-degrade throughout: a proposed
            # update phase that cannot ground must never silently author the
            # create-acceptance shape (a materially different claim).
            grounded_upd: tuple = ()
            proposed_upd = hint.get("update_conditions")
            if proposed_upd is not None and not isinstance(proposed_upd, list):
                # D-306.1 (review): a misshaped proposal refuses (the
                # _ground_trigger_fields posture) instead of crashing the run
                # inside the clause walker.
                return IntentResolution(
                    grounded_candidates=[], next_action=NextAction.REFUSE,
                    interpretation_delta=delta,
                    refusal=self._router.emission_deferred(
                        archetype, claim_kind,
                        detail=("update_conditions must be a list of clause "
                                "objects; got "
                                + type(proposed_upd).__name__)))
            if proposed_upd:
                upd_conds, upd_invalid = _ground_rejection_conditions(
                    proposed_upd, neighborhood, at)
                if upd_invalid:
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind,
                            detail="; ".join(upd_invalid)))
                # D-332: the staged update values bind to the org's picklist
                # values too (the PATCH stages them exactly like the create).
                upd_conds, upd_unbindable = _bind_picklist_values(
                    upd_conds, cond_meta)
                if upd_unbindable:
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind,
                            detail="; ".join(upd_unbindable)))
                non_equals = sorted({c.predicate for c in upd_conds
                                     if c.predicate != "equals"})
                if non_equals or not upd_conds:
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind,
                            detail=(f"update clauses must be one or more "
                                    f"equals (the change is staged by one "
                                    f"PATCH); got {non_equals or 'none'}")))
                nonupdatable = sorted(
                    c.field.external_id for c in upd_conds
                    if (m := cond_meta.get(_bare(c.field.external_id))) is None
                    or m.get("is_calculated")
                    or not m.get("is_updateable", True))
                if nonupdatable:
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind,
                            detail=(f"update clause field(s) not updateable "
                                    f"(calculated/read-only): "
                                    f"{nonupdatable}")))
                # D-306.1 (review): two update clauses on one field would put
                # BOTH values in the claim's identity while the PATCH stages
                # last-wins — claim/recipe meaning drift; refuse.
                seen_fields = [c.field.external_id for c in upd_conds]
                dupes = sorted({f for f in seen_fields
                                if seen_fields.count(f) > 1})
                if dupes:
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind,
                            detail=(f"duplicate update clause field(s): "
                                    f"{dupes} — one destination value per "
                                    f"field")))
                # D-306.1 (review): a change identical to the initial state is
                # a no-op PATCH — trivially "accepted" without the transition
                # ever being exercised; refuse.
                initial_pairs = {(c.field.external_id, c.value)
                                 for c in grounded_conds
                                 if c.predicate == "equals"}
                if all((c.field.external_id, c.value) in initial_pairs
                       for c in upd_conds):
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind,
                            detail=("every update clause duplicates the "
                                    "initial state — a no-op change cannot "
                                    "witness an accepted transition")))
                grounded_upd = tuple(upd_conds)
            # D-333: the approval-arc acceptance — actions run against the
            # created record BEFORE the accepted update, so the arc REQUIRES
            # the update phase; the same vocabulary/binding rules as the
            # prohibition arc, minus attempted_change (update_conditions IS
            # the change here).
            arc_actions: tuple = ()
            if hint.get("approval_actions") is not None:
                proposed_actions = hint.get("approval_actions")
                if (not isinstance(proposed_actions, list)
                        or not proposed_actions
                        or any(a not in _ARC_ACTIONS
                               for a in proposed_actions)
                        or proposed_actions[0] != "submit"):
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind,
                            detail=(f"approval_actions must be a non-empty "
                                    f"list drawn from {list(_ARC_ACTIONS)} "
                                    f"beginning with 'submit'; got "
                                    f"{proposed_actions!r}")))
                if not grounded_upd:
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind,
                            detail=("an approval-arc acceptance needs "
                                    "update_conditions — the CHANGE the org "
                                    "must accept after the actions run")))
                if len(_active_approvals(neighborhood)) != 1:
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind,
                            detail=("the approval-action arc needs exactly "
                                    "ONE active approval process on the "
                                    "subject to bind (the D-320 enumeration "
                                    "law)")))
                arc_actions = tuple(proposed_actions)
            # D-337: an acceptance claim asserts the org ACCEPTS the staged
            # state — staged values that provably fire an active VR are
            # self-contradictory against org config (perma-red by
            # construction). The update check evaluates the create ⊕ update
            # overlay (the R3 epistemics), except on the arc: the approval
            # actions rewrite the record between the phases, so no
            # post-actions state is modelable — create-only there.
            conflict = _staged_vr_conflict_detail(
                neighborhood,
                {c.field.external_id: c.value for c in grounded_conds
                 if c.predicate == "equals"},
                staged_update=(None if arc_actions else
                               {c.field.external_id: c.value
                                for c in grounded_upd}))
            if conflict is not None:
                return IntentResolution(
                    grounded_candidates=[], next_action=NextAction.REFUSE,
                    interpretation_delta=delta,
                    refusal=self._router.emission_deferred(
                        archetype, claim_kind, detail=conflict))
            _stash_grounding(state, GroundedAcceptance(
                archetype=archetype, claim_kind=claim_kind, version_seq=at,
                subject=_Endpoint(
                    entity_id=subject.id, entity_type=subject.entity_type,
                    external_id=subject.sf_api_name or str(subject.id)),
                requirement_excerpt=excerpt,
                conditions=tuple(grounded_conds),
                update_conditions=grounded_upd,
                approval_actions=arc_actions))

        # D-210.1 covers the POSITIVE shapes only; a NEGATIVE state-transition /
        # automation-effect (grounded via its VR/Flow dim) has no authored
        # negative emission — defer it rather than mis-author the positive
        # recipe (prohibition-claim is the built rejection vertical).
        # D-307: the ABSENCE intent is exempt — "the automation correctly
        # does nothing" reads as negative to a proposer, but it IS the built
        # artifact (the v2 absence claim, a positive assertion about correct
        # non-action). Substrate-routed, not prompt-gated: whatever polarity
        # label rides the hint, expected_absence selects the absence vertical.
        if (claim_kind in ("state-transition-claim", "automation-effect-claim")
                and self._admit.is_negative(claim_kind, polarity)
                and not (claim_kind == "automation-effect-claim"
                         and hint.get("expected_absence"))):
            return IntentResolution(
                grounded_candidates=[], next_action=NextAction.REFUSE,
                interpretation_delta=delta,
                refusal=self._router.emission_deferred(
                    archetype, claim_kind,
                    detail=(f"negative {claim_kind} emission is not built — "
                            f"rejection tests are the prohibition-claim "
                            f"vertical")))

        # Stash grounding for the create-scoped state-transition (D-210.1):
        # the NAMED to-state field must verify (the D-115.3 pattern) and the
        # trigger must be the subject's own creation — cross-object triggers
        # ground but defer emission (S1 has no lookup modeling to correlate).
        if state is not None and claim_kind == "state-transition-claim":
            field_ent = next(
                (r.entity for r in neighborhood
                 if r.edge_type == EDGE_BELONGS and r.entity.entity_type == "Field"
                 and r.entity.sf_api_name == hint.get("field_name")), None)
            to_value = _identity_safe(hint.get("expected_value"))
            # D-227: a cross-object trigger (the transition is provoked by
            # creating a RELATED record) VERIFIES instead of deferring — the
            # trigger object must resolve uniquely and its lookup back to the
            # subject must BELONG_TO it. Unverifiable names refuse
            # emission-deferred (a wrong-shape recipe is worse than none;
            # there is no same-object fallback for a cross-object intent).
            trig_obj_ep, trig_lookup_ep = None, None
            trigger_object = hint.get("trigger_object")
            if trigger_object and trigger_object != subject.sf_api_name:
                got = self._ground_cross_object_trigger(hint, trigger_object, at)
                if isinstance(got, str):
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind, detail=got))
                trig_obj_ep, trig_lookup_ep = got
            if field_ent is None or to_value is None:
                # Convergence arc: this refusal was a dead end (no offers,
                # no framing guidance) — the #2 replay-ranked class (the
                # SLA/reopen misframe: 100% of KIND_MISFRAME was
                # state-transition on an automation-written field, and the
                # kind-swap variant rescues 63% deterministically). A
                # missed field carries the ranked near-miss tail; a field
                # the org verifiably WRITES names the automation-effect
                # framing.
                _voc, _offer = "", None
                if field_ent is None and hint.get("field_name"):
                    # F2 (D-377): the to-state IS a staged picklist value —
                    # structural value evidence ranks the real field first.
                    _voc, _offer = _field_recovery_tail(
                        [hint.get("field_name")], neighborhood,
                        s1=self._s1, staged_value=hint.get("expected_value"),
                        at_seq=at)
                _cap = ""
                _cap_field = None
                if field_ent is not None:
                    _cap_field = field_ent.sf_api_name
                elif _offer and (_offer.get("candidates") or []):
                    _cap_field = _offer["candidates"][0].get("sf_api_name")
                if _cap_field:
                    _st_flow_ents = [
                        r.entity for r in neighborhood
                        if r.edge_type == EDGE_FLOW
                        and r.entity.entity_type == "Flow"]
                    _cap = _capability_line(
                        _cap_field,
                        _field_capability_summary(_st_flow_ents, _cap_field))
                return IntentResolution(
                    grounded_candidates=[], next_action=NextAction.REFUSE,
                    interpretation_delta=delta,
                    refusal=self._router.emission_deferred(
                        archetype, claim_kind,
                        detail=("state-transition needs a verifiable "
                                "to-state: field_name (existing on the "
                                "subject) + expected_value" + _voc + _cap),
                        candidates=_offer))
            # D-222: the OPTIONAL staged trigger — verified BELONGS_TO the
            # subject like the to-state field; absent or unverifiable -> the
            # unstaged shape (a previously-emittable claim never regresses
            # to a refusal; an unverified LLM-proposed trigger is dropped,
            # never guessed).
            trig_field_ep, trig_value = None, None
            trig_name = hint.get("trigger_field")
            if trig_name and hint.get("trigger_value") is not None:
                trig_ent = next(
                    (r.entity for r in neighborhood
                     if r.edge_type == EDGE_BELONGS
                     and r.entity.entity_type == "Field"
                     and r.entity.sf_api_name == trig_name), None)
                if trig_ent is not None:
                    trig_field_ep = _Endpoint(
                        entity_id=trig_ent.id,
                        entity_type=trig_ent.entity_type,
                        external_id=trig_ent.sf_api_name or str(trig_ent.id))
                    trig_value = _identity_safe(hint.get("trigger_value"))
            # Claim fidelity (T4): a create-scoped state-transition asserts the
            # ORG'S AUTOMATION sets the to-state on create (the recipe creates
            # WITHOUT the field and asserts the org produced it). If no grounded
            # automation actually produces it — a Flow whose effect is
            # field=to_value, or an active approval process — the test is a
            # permanent false-fail (a bare create leaves the field blank), so
            # refuse rather than author a transition the org never performs.
            # Decided by CAUSALITY (is there a producer?), never by requirement
            # vocabulary; a manual "mark/set X" capability should re-propose as an
            # acceptance-claim. Scoped to the pure create-scoped shape — a
            # cross-object (D-227) or staged (D-222) trigger is a separately
            # verified shape and is left unchanged.
            if trig_obj_ep is None and trig_field_ep is None:
                _st_flows = [r.entity for r in neighborhood
                             if r.edge_type == EDGE_FLOW
                             and r.entity.entity_type == "Flow"]
                _st_producers = _flows_producing_effect(
                    _st_flows, field_ent.sf_api_name, to_value, None)
                # B0 hardening: the approval arm is REMOVED from this floor —
                # an approval process fires only on explicit submission, never
                # on a bare record create, so it can never be the producer of
                # a create-scoped transition. The arm let a create-scoped
                # to-state ground with NO producer whenever ANY active
                # approval sat on the subject (live-observed at the B0 exit
                # gate: a vacuous stage-the-value claim credited as the FL01
                # default-priority AC). Approvals keep their real rails:
                # D-320 ProcessInstance effects and D-308 named binding.
                if not _st_producers:
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind,
                            detail=(
                                f"no org automation produces "
                                f"{field_ent.sf_api_name}={to_value!r} on create "
                                f"(no Flow with that effect; an approval process "
                                f"cannot fire on a bare create) — the org would "
                                f"never perform this transition. If this is a "
                                f"manual capability, assert it as an "
                                f"acceptance-claim (set the value, expect it to "
                                f"be accepted).")))
            _stash_grounding(state, GroundedStateTransition(
                archetype=archetype, claim_kind=claim_kind, version_seq=at,
                subject=_Endpoint(
                    entity_id=subject.id, entity_type=subject.entity_type,
                    external_id=subject.sf_api_name or str(subject.id)),
                field=_Endpoint(
                    entity_id=field_ent.id, entity_type=field_ent.entity_type,
                    external_id=field_ent.sf_api_name or str(field_ent.id)),
                to_value=to_value, requirement_excerpt=excerpt,
                trigger_field=trig_field_ep, trigger_value=trig_value,
                trigger_object=trig_obj_ep,
                trigger_lookup_field=trig_lookup_ep))

        # Stash grounding for the automation-effect (D-210.1): the matched
        # Flow (TRIGGERS_ON — the grounding dimension _evaluate_positive
        # admitted on) becomes the claim's automation ref; the effect shape
        # must verify — same-record (field on the subject) or cross-object
        # (effect object + its lookup field back to the subject). Every name
        # is LLM-proposed but S1-verified; unverifiable -> defer, never guess.
        if state is not None and claim_kind == "automation-effect-claim":
            flows = [r.entity for r in neighborhood
                     if r.edge_type == EDGE_FLOW and r.entity.entity_type == "Flow"]
            # D-299: bind the requirement-NAMED Flow (env-59 Opportunity has 3
            # flows TRIGGERS_ON it — first-encountered would name the wrong
            # automation and assert the wrong effect). A named-but-absent Flow
            # is a genuine grounding miss (refuse — the admissibility gate
            # already dismisses it, this is the defence-in-depth twin). No name
            # -> first-encountered (backward-compat; the pre-D-299 behaviour).
            automation_name = hint.get("automation_name")
            primitive = "flow"
            formula_ent = None
            # SUB-3: set when the no-name branch hit the empty-producer floor and
            # provisionally bound flows[0]. Checked AFTER the calc-field rebind so
            # a calculated observed field (which legitimately has no Flow producer)
            # can re-bind primitive='formula'; anything still non-formula then is a
            # wrong-green and refuses (symmetric with the named branch).
            no_producer_floor = False

            def _approval_binding():
                # D-320: an approval-process effect (effect_object=ProcessInstance)
                # is the org's internal automation the LLM cannot name — bind the
                # SINGLE active approval that TRIGGERS_ON the subject. Approvals
                # carry no Flow Metadata, so this is ENUMERATION, not
                # _flows_producing_effect. Returns ("bind", approval_ent) /
                # ("refuse", IntentResolution) / ("skip", None): >1 needs a name
                # (the D-299/D-318 law), 0 has no approval to bind.
                if hint.get("effect_object") != "ProcessInstance":
                    return ("skip", None)
                # A named reference to a real approval on the subject is respected,
                # not overridden (D-320): active binds by name above (D-308);
                # inactive refuses (D-301). Enumeration is only the can't-name case.
                if _names_a_subject_approval(neighborhood, automation_name):
                    return ("skip", None)
                appr = _active_approvals(neighborhood)
                if len(appr) == 1:
                    return ("bind", appr[0])
                detail = (
                    f"{len(appr)} active approval processes on the subject "
                    f"({sorted(a.sf_api_name for a in appr)}) — name the specific "
                    f"approval process") if appr else (
                    "no active approval process TRIGGERS_ON the subject")
                return ("refuse", IntentResolution(
                    grounded_candidates=[], next_action=NextAction.REFUSE,
                    interpretation_delta=delta,
                    refusal=self._router.emission_deferred(
                        archetype, claim_kind, detail=detail)))

            _named_flow_ent = None
            if automation_name:
                flow_ent = next(
                    (f for f in flows if f.sf_api_name == automation_name), None)
                # Completion review (live env-59): a RESOLVED name is binding.
                # The E1/E2/C9 discovery below rebinds flow_ent to the verified
                # producer — which silently OVERRODE an explicit name when the
                # object has several producers (named FL11_Async_Enrichment,
                # grounded FL09_Reopen_Guard: the SUB-3 wrong-attribution class
                # D-318 exists to prevent). Discovery is now scoped to the named
                # flow, so it can narrow or refuse but never SWITCH automations.
                _named_flow_ent = flow_ent
                if flow_ent is None:
                    # D-308: the named automation may be an APPROVAL PROCESS —
                    # it rides the same TRIGGERS_ON rail. NAME-ONLY binding
                    # (a first-encountered approval would be the D-299
                    # wrong-automation class) and ACTIVE-only (the D-301 law).
                    approval_ent = next(
                        (r.entity for r in neighborhood
                         if r.edge_type == EDGE_FLOW
                         and r.entity.entity_type == "ApprovalProcess"
                         and r.entity.sf_api_name == automation_name
                         and (r.entity.attributes or {}).get("_is_active")),
                        None)
                    if approval_ent is not None:
                        flow_ent = approval_ent
                        primitive = "approval_process"
                if flow_ent is None:
                    # D-304: the named automation may be a CALCULATED FIELD —
                    # the org's formula engine is the mechanism, verified by
                    # is_calculated on the BELONGS_TO field (the same character
                    # of check as TRIGGERS_ON). Two-shape tolerant reader.
                    formula_ent = next(
                        (r.entity for r in neighborhood
                         if r.edge_type == EDGE_BELONGS
                         and r.entity.entity_type == "Field"
                         and r.entity.sf_api_name == automation_name
                         and field_is_calculated(r.entity.attributes)), None)
                    if formula_ent is not None:
                        primitive = "formula"
                if flow_ent is None and formula_ent is None:
                    # D-320: the named automation may be an approval process the LLM
                    # couldn't name (it sent "<UNKNOWN>" / an invented name) — bind
                    # the single active approval on the subject (effect=ProcessInstance).
                    ab_status, ab_val = _approval_binding()
                    if ab_status == "refuse":
                        return ab_val
                    if ab_status == "bind":
                        flow_ent = ab_val
                        primitive = "approval_process"
                if flow_ent is None and formula_ent is None:
                    # D-318: the requirement-named automation didn't resolve by name
                    # (the LLM can't know internal Flow api-names) — bind the Flow
                    # that ACTUALLY produces the claimed effect (its Metadata writes
                    # field=value / creates effect_object). Deterministic disambiguation
                    # by the effect itself (the D-299 concern), and SAFER than the
                    # name-match (which never verified the named Flow's effect).
                    producers = _flows_producing_effect(
                        flows, hint.get("field_name"),
                        hint.get("expected_value"), hint.get("effect_object"))
                    producers = _tie_break_cross_producers(producers, hint)
                    if len(producers) == 1:
                        flow_ent = producers[0]
                    elif len(producers) > 1:
                        return IntentResolution(
                            grounded_candidates=[], next_action=NextAction.REFUSE,
                            interpretation_delta=delta,
                            refusal=self._router.emission_deferred(
                                archetype, claim_kind,
                                detail=_cross_ambiguity_detail(
                                    producers, hint,
                                    f"{len(producers)} Flows on the subject "
                                    f"produce the claimed effect "
                                    f"({sorted(p.sf_api_name for p in producers)})"
                                    f" — name the specific automation")))
                    else:
                        # B1 arc (C3b/C4/C5): the named automation is an
                        # unresolvable GUESS (D-318: the LLM cannot know the
                        # org's internal names) AND no value-ful producer
                        # matches — do NOT refuse here; fall through exactly
                        # like the no-name branch so the VALUE-LESS producers
                        # (transform / temporal / ladder arms) get their turn.
                        # A genuinely-unproducible field then refuses at the
                        # downstream no_producer_floor gate WITH the arm
                        # disclosure (more useful than the old name-only
                        # message).
                        flow_ent = flows[0] if flows else None
                        no_producer_floor = True
            else:
                # D-320: no name — an approval-process effect (ProcessInstance) binds
                # the single active approval on the subject (the model omitted the
                # name entirely). Only when it is NOT an approval shape do we fall to
                # the D-318 Flow effect-resolver.
                ab_status, ab_val = _approval_binding()
                if ab_status == "refuse":
                    return ab_val
                if ab_status == "bind":
                    flow_ent = ab_val
                    primitive = "approval_process"
                else:
                    # D-318: no name — prefer the Flow that PRODUCES the effect over the
                    # blind first-encountered one (a real disambiguation). SUB-3: when
                    # NO Flow's Metadata produces the effect, provisionally bind flows[0]
                    # only so a calculated observed field can reach the downstream
                    # calc-field rebind (primitive='formula'); a non-calculated field
                    # then REFUSES post-rebind (see no_producer_floor) — the named
                    # branch's own posture on the identical empty-producer case.
                    producers = _flows_producing_effect(
                        flows, hint.get("field_name"),
                        hint.get("expected_value"), hint.get("effect_object"))
                    producers = _tie_break_cross_producers(producers, hint)
                    if len(producers) == 1:
                        flow_ent = producers[0]
                    elif len(producers) > 1:
                        return IntentResolution(
                            grounded_candidates=[], next_action=NextAction.REFUSE,
                            interpretation_delta=delta,
                            refusal=self._router.emission_deferred(
                                archetype, claim_kind,
                                detail=_cross_ambiguity_detail(
                                    producers, hint,
                                    f"{len(producers)} Flows on the subject "
                                    f"produce the claimed effect "
                                    f"({sorted(p.sf_api_name for p in producers)})"
                                    f" — name the specific automation")))
                    else:
                        flow_ent = flows[0] if flows else None
                        no_producer_floor = True
            if flow_ent is None and formula_ent is None:
                # Completion E3: a subject with ZERO own flows can still be
                # a roll-up TARGET (the writer triggers on a child object) —
                # the admission gate admitted exactly this shape, so the
                # tail must attempt it before the defensive refusal.
                _r_out = self._try_rollup_resolution(
                    hint=hint, subject=subject,
                    field_ent=next(
                        (r.entity for r in neighborhood
                         if r.edge_type == EDGE_BELONGS
                         and r.entity.entity_type == "Field"
                         and r.entity.sf_api_name == hint.get("field_name")),
                        None),
                    at=at, delta=delta, archetype=archetype,
                    claim_kind=claim_kind, excerpt=excerpt,
                    grounded=grounded, state=state)
                if _r_out is not None:
                    return _r_out
                # defensive: positives admit on this edge; negatives may reach
                # here without one
                return IntentResolution(
                    grounded_candidates=[], next_action=NextAction.REFUSE,
                    interpretation_delta=delta,
                    refusal=self._router.emission_deferred(
                        archetype, claim_kind,
                        detail="no record-triggered Flow on the subject"))
            # B0 hardening (SUB-3, cross-object): the no-name branch bound
            # flows[0] only as a PROVISIONAL stand-in so a calculated observed
            # field can reach the same-record calc-field rebind below. The
            # cross-object shapes (parent-stamp, absence, plain cross-object)
            # stash and return BEFORE the same-record tail's no-producer check
            # ever runs — live-observed at the B0 exit gate: a Task-creation
            # effect no PLS_FB flow produces was attributed to the
            # alphabetically-first flow (FL01), twice, as stored claims. A
            # cross-object effect has no calc-field rebind to wait for, so an
            # empty producer set refuses HERE (ground-or-refuse; attribution
            # is the claim's whole value — D-299/D-318).
            if no_producer_floor and hint.get("effect_object"):
                # Completion (composition): a caller flow may produce the
                # effect THROUGH a subflow (FL12→SF01) — the composed view
                # is verifiable attribution, so the SUB-3 gate lets exactly
                # one such producer fall through to the xo evidence branch
                # (which re-derives the producer itself). Ambiguity and
                # zero both keep the refusal.
                _cmp_reg = None
                if any(flow_subflow_calls(getattr(f, "attributes", None))
                       for f in flows):
                    _cmp_reg = {
                        e.sf_api_name: getattr(e, "attributes", None)
                        for e in self._s1.get_entities("Flow", at_seq=at)
                        if e.sf_api_name}
                _cmp_upd = _xo_update_producers(
                    flows, hint.get("effect_object"),
                    callee_registry=_cmp_reg) if _cmp_reg else []
                # fall through ONLY into the E2 branch's own preconditions —
                # absence / parent-stamp shapes must keep refusing here (the
                # provisional flows[0] binding would otherwise reach the
                # plain xo grounding and mis-attribute, the SUB-3 bug)
                if (len(_cmp_upd) != 1 or hint.get("expected_absence")
                        or hint.get("effect_via_lookup_field")):
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind,
                            detail=(f"no Flow on the subject verifiably "
                                    f"produces the claimed cross-object "
                                    f"effect on "
                                    f"{hint.get('effect_object')!r} — the "
                                    f"automation cannot be attributed, so "
                                    f"the effect stays unverified")))
            automation_ent = formula_ent if formula_ent is not None else flow_ent
            subj_ep = _Endpoint(
                entity_id=subject.id, entity_type=subject.entity_type,
                external_id=subject.sf_api_name or str(subject.id))
            flow_ep = _Endpoint(
                entity_id=automation_ent.id, entity_type=automation_ent.entity_type,
                external_id=automation_ent.sf_api_name or str(automation_ent.id))
            # D-304: the formula primitive is SAME-RECORD only (a formula field
            # computes on its own record) — cross-object hints with it refuse.
            if primitive == "formula" and hint.get("effect_object"):
                return IntentResolution(
                    grounded_candidates=[], next_action=NextAction.REFUSE,
                    interpretation_delta=delta,
                    refusal=self._router.emission_deferred(
                        archetype, claim_kind,
                        detail=("a formula automation computes on its own "
                                "record — cross-object effect shapes do not "
                                "apply to a calculated field")))
            # D-306: the update-observe phase is SAME-RECORD only — the
            # recompute is observed on the record whose state changed. A
            # cross-object/parent-stamp hint carrying update_trigger_fields
            # refuses (authoring create-scoped instead would silently drop
            # the requirement's recalculate-on-change premise).
            if hint.get("update_trigger_fields") and hint.get("effect_object"):
                return IntentResolution(
                    grounded_candidates=[], next_action=NextAction.REFUSE,
                    interpretation_delta=delta,
                    refusal=self._router.emission_deferred(
                        archetype, claim_kind,
                        detail=("the update-observe phase is same-record "
                                "only — a cross-object/parent-stamp effect "
                                "cannot observe a recompute on the changed "
                                "record")))
            # D-307: absence ("the automation correctly produces NO
            # correlated record") is the CROSS-OBJECT shape only — refusing
            # every other combination fail-closed, because silently authoring
            # the presence shape would INVERT the claim's meaning. A
            # field-conditional absence (effect_field/effect_value alongside)
            # is not expressible in v1.
            raw_absence = hint.get("expected_absence")
            if raw_absence is not None and not isinstance(raw_absence, bool):
                # D-307.1 (review): the hint slot is un-schema'd — a string
                # "false" is truthy and a coercion either way silently INVERTS
                # meaning. Fail closed on any non-boolean.
                return IntentResolution(
                    grounded_candidates=[], next_action=NextAction.REFUSE,
                    interpretation_delta=delta,
                    refusal=self._router.emission_deferred(
                        archetype, claim_kind,
                        detail=("expected_absence must be a JSON boolean; got "
                                + type(raw_absence).__name__)))
            expected_absence = raw_absence is True
            if expected_absence:
                if not hint.get("effect_object") or hint.get(
                        "effect_via_lookup_field"):
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind,
                            detail=("absence asserts NO correlated record — "
                                    "it needs effect_object + "
                                    "effect_lookup_field (the cross-object "
                                    "shape); same-record/parent-stamp "
                                    "absence is not expressible")))
                if (hint.get("effect_field")
                        or hint.get("effect_value") is not None):
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind,
                            detail=("absence asserts NO correlated record at "
                                    "all — a field-conditional absence "
                                    "(effect_field/effect_value) is not "
                                    "expressible; drop them or assert "
                                    "presence")))
            effect_object_api = hint.get("effect_object")
            if effect_object_api and hint.get("effect_via_lookup_field"):
                # D-227 parent-stamp: the effect lands on a record the TRIGGER
                # record points to via its OWN lookup (effect_via_lookup_field,
                # verified BELONGS_TO the subject — the gate's own
                # neighborhood). effect_field is REQUIRED (a stamp without a
                # named field is unobservable — the parent row trivially
                # exists); effect_value optional (value-less stamps assert
                # not_null).
                via_name = hint.get("effect_via_lookup_field")
                via_ent = next(
                    (r.entity for r in neighborhood
                     if r.edge_type == EDGE_BELONGS
                     and r.entity.entity_type == "Field"
                     and r.entity.sf_api_name == via_name), None)
                if via_ent is None:
                    # B0: near-miss offer from the subject's own fields.
                    via_pool = [(r.entity.sf_api_name, r.entity.display_name)
                                for r in neighborhood
                                if r.edge_type == EDGE_BELONGS
                                and r.entity.entity_type == "Field"
                                and r.entity.sf_api_name]
                    via_cands = _recovery.rank_candidates(
                        via_name or "", via_pool)
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind,
                            detail=(f"effect_via_lookup_field {via_name!r} "
                                    f"does not exist on the subject — cannot "
                                    f"link the trigger record to the effect "
                                    f"parent."
                                    + _recovery.format_candidates(via_cands)),
                            candidates=_recovery.offer_payload(
                                "Field", via_name, via_cands)
                            if via_cands else None))
                grounded_eff = self._ground_cross_object_effect(
                    hint, effect_object_api, at, lookup_required=False)
                if isinstance(grounded_eff, _XoDeferral):
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind, detail=grounded_eff.detail,
                            candidates=grounded_eff.offer))
                eff_ep, _, eff_field_ep = grounded_eff
                if eff_field_ep is None:
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind,
                            detail=("a parent-stamp effect needs effect_field "
                                    "(the stamped field on the effect object) "
                                    "— without it the stamp is unobservable")))
                # D-307: the entry gate is on the SUBJECT create in the
                # parent-stamp shape too — without it the flow never stamps.
                # Same D-299 rail (BELONGS_TO verify, drop-never-refuse); the
                # subject BELONGS_TO map is the k16 guard (the stamped field
                # lives on ANOTHER object, so it can never verify as a
                # subject trigger) — the explicit exclude is defense-in-depth.
                ps_triggers = _ground_trigger_fields(
                    hint.get("trigger_fields"), neighborhood,
                    exclude_field=hint.get("effect_field"))
                # D-337: same staged-state VR-conflict guard as the other
                # trigger shapes — a bounced subject create never stamps.
                conflict = _staged_vr_conflict_detail(
                    neighborhood,
                    {ep.external_id: v for ep, v in ps_triggers})
                if conflict is not None:
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind, detail=conflict))
                _stash_grounding(state, GroundedAutomationEffect(
                    archetype=archetype, claim_kind=claim_kind, version_seq=at,
                    subject=subj_ep, automation=flow_ep,
                    requirement_excerpt=excerpt,
                    effect_field=eff_field_ep,
                    effect_value=_identity_safe(hint.get("effect_value")),
                    effect_object=eff_ep,
                    effect_via_lookup_field=_Endpoint(
                        entity_id=via_ent.id, entity_type=via_ent.entity_type,
                        external_id=via_ent.sf_api_name or str(via_ent.id)),
                    trigger_fields=ps_triggers,
                    # D-308: the bound primitive (the same-record stash always
                    # passed it; the cross-object/parent-stamp stashes relied
                    # on the "flow" default — wrong the moment an approval
                    # binds).
                    automation_primitive=primitive))
            elif effect_object_api:
                # Completion Program E1: when exactly ONE flow's TYPED IR
                # creates this effect object, three model-dependent pieces
                # become SUBSTRATE-DERIVED (deterministic, org-defined):
                # the correlation (the op's subject_ref-Id assignment IS
                # the lookup field), the asserted value for a named-but-
                # unvalued effect field (the op's literal or relative-date
                # assignment — the org-defines-the-value class, extended
                # cross-object), and the attribution (the producing flow
                # binds by its verified effect, not by name). An
                # Update-trigger producer additionally derives the
                # create→update TRANSITION from its EqualTo entry guard
                # (the C4/C5 discipline) — presence only (an absence case
                # must not stage the firing transition).
                # Completion review (live env-59), fix (1): discovery is
                # scoped to a RESOLVED named automation — it may narrow or
                # refuse, but it can never SWITCH the claim to another flow.
                _disc_flows = ([_named_flow_ent] if _named_flow_ent is not None
                               else flows)
                _xo_ops_all = _xo_create_producers(_disc_flows,
                                                   effect_object_api)
                _xo_evt_all = _xo_eventual_create_producers(_disc_flows,
                                                            effect_object_api)
                # An UPDATE producer is a candidate when the claim names a
                # FIELD (either an update or a create could set it — env-59:
                # FL04 creates the task Status='Open', FL05 updates it to
                # 'Cancelled'), or when nothing CREATES the object at all (the
                # E2 fallback derives the field from the op itself). It is NOT
                # a candidate for a bare EXISTENCE claim that a create already
                # answers: an update can never make a record appear, so
                # "a task appears" is a create-only question (E1 priority).
                _upd_is_candidate = bool(
                    xo_hint_field(hint) or not (_xo_ops_all or _xo_evt_all))
                _callee_reg = None
                if _upd_is_candidate and any(
                        flow_subflow_calls(getattr(f, "attributes", None))
                        for f in _disc_flows):
                    _callee_reg = {
                        e.sf_api_name: getattr(e, "attributes", None)
                        for e in self._s1.get_entities("Flow", at_seq=at)
                        if e.sf_api_name}
                _xo_upd_all = (
                    _xo_update_producers(_disc_flows, effect_object_api,
                                         callee_registry=_callee_reg)
                    if _upd_is_candidate else [])
                # fix (2): >1 producer of the same object is the REAL-org norm
                # (env-59: FL09 immediate + FL11 async + FL13 on-fault all
                # create PLS_FB_Audit_Log__c; FL04 creates + FL05 updates the
                # fulfilment task). The claimed effect FIELD/VALUE picks the
                # producer deterministically; a narrowing that lands on
                # nothing keeps the ambiguity (refused below), never a guess.
                _cand_n = len(_xo_ops_all) + len(_xo_evt_all) + len(_xo_upd_all)
                if _cand_n > 1 and xo_hint_field(hint):
                    _ef = hint.get("effect_field")
                    _ev = _identity_safe(hint.get("effect_value"))
                    _n_ops = _narrow_producers_by_effect(_xo_ops_all, _ef, _ev)
                    _n_evt = _narrow_producers_by_effect(_xo_evt_all, _ef, _ev)
                    _n_upd = _narrow_producers_by_effect(_xo_upd_all, _ef, _ev)
                    if _n_ops or _n_evt or _n_upd:
                        _xo_ops_all, _xo_evt_all, _xo_upd_all = \
                            _n_ops, _n_evt, _n_upd
                        _cand_n = (len(_xo_ops_all) + len(_xo_evt_all)
                                   + len(_xo_upd_all))
                # a surviving ambiguity refuses, disclosing the substrate-
                # derived DISCRIMINATOR (fields + literals — never automation
                # names, the D-318/B0 law) so the model can re-propose.
                if _cand_n > 1:
                    _disc = _cross_effect_discriminators(
                        [e for e, _ in _xo_ops_all + _xo_evt_all
                         + _xo_upd_all],
                        effect_object_api)
                    _dtail = ""
                    if _disc:
                        _df, _dv = _best_discriminator(_disc)
                        _dtail = (f" — they are told apart by {_df} "
                                  f"(one of {_dv}); name effect_field + "
                                  f"effect_value to select the one this "
                                  f"requirement means")
                    return IntentResolution(
                        grounded_candidates=[],
                        next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind,
                            detail=(f"{_cand_n} automations write "
                                    f"{effect_object_api}{_dtail}")))
                _xo_ops = _xo_ops_all
                # C9 (FL11 slice): an ASYNC bounded-eventual create producer
                # takes the same E1 rails when no immediate producer exists —
                # the grounding is identical, the READ becomes retry-until-
                # deadline. Absence refuses here BY NAME: a bounded window
                # can never prove the record will not appear.
                _xo_eventual = None
                if not _xo_ops:
                    _evt = _xo_evt_all
                    if _evt and raw_absence:
                        return IntentResolution(
                            grounded_candidates=[],
                            next_action=NextAction.REFUSE,
                            interpretation_delta=delta,
                            refusal=self._router.emission_deferred(
                                archetype, claim_kind,
                                detail=(f"{_evt[0][0].sf_api_name} creates "
                                        f"the {effect_object_api} record on "
                                        f"an ASYNCHRONOUS path — absence "
                                        f"within a bounded window is not "
                                        f"provable (the record may land "
                                        f"after any deadline)")))
                    if len(_evt) == 1:
                        _xo_ops = _evt
                        _xo_eventual = dict(self._EVENTUAL_READ_SPEC)
                xo_hint = hint
                _xo_transition = None
                if len(_xo_ops) == 1:
                    _xo_ent, _xo_op = _xo_ops[0]
                    xo_hint = dict(hint)
                    if not xo_hint.get("effect_lookup_field"):
                        _corr = next(
                            (f for f, tv in _xo_op["assignments"].items()
                             if tuple(tv) == ("subject_ref", "Id")), None)
                        if _corr:
                            xo_hint["effect_lookup_field"] = _corr
                    _efn = xo_hint.get("effect_field")
                    if _efn and xo_hint.get("effect_value") is None:
                        _tv = _xo_op["assignments"].get(
                            str(_efn).rsplit(".", 1)[-1])
                        _tv = tuple(_tv) if _tv else None
                        if _tv and _tv[0] == "literal":
                            xo_hint["effect_value"] = _tv[1]
                        elif _tv and _tv[0] == "relative_date":
                            xo_hint["effect_value"] = relative_date(_tv[1])
                    flow_ent = _xo_ent
                    primitive = "flow"
                    no_producer_floor = False
                    flow_ep = _Endpoint(
                        entity_id=_xo_ent.id,
                        entity_type=_xo_ent.entity_type,
                        external_id=_xo_ent.sf_api_name or str(_xo_ent.id))
                    _xo_trig = flow_behaviour(
                        getattr(_xo_ent, "attributes", None))["trigger"]
                    if (_xo_trig["record_trigger_type"] == "Update"
                            and not raw_absence
                            and _xo_op["guard"]
                            and all(g[1] == "EqualTo"
                                    for g in _xo_op["guard"])):
                        _xgmeta = _grounding_field_metadata(
                            neighborhood, self._s1, at)
                        _xt_create, _xt_update = {}, {}
                        for _gf, _gop, _gv in _xo_op["guard"]:
                            _alt = _picklist_alternative(
                                (_xgmeta.get(_gf) or {}).get(
                                    "picklist_values"), {_gv})
                            if _alt is None:
                                _xt_create = None
                                break
                            _xt_update[_gf] = _gv
                            _xt_create[_gf] = _alt
                        if _xt_create:
                            _xo_transition = {"create": _xt_create,
                                              "update": _xt_update}
                # Completion E2: exactly ONE flow verifiably UPDATES a
                # correlated set on the effect object (FL05's shape). The
                # entire staging is substrate-derived from the op itself:
                # the pre-state template from its FILTERS, the correlation
                # from its ($Record, Id) marker, the updated value from its
                # ASSIGNMENTS, the entry transition from its guard, and the
                # DISTRACTOR row (template value flipped to a third state)
                # whose exclusion the count assert enforces. Presence only.
                # Completion (composition): the update candidates were
                # discovered (and narrowed) above — including a caller's
                # COMPOSED subflow effects, via the lazily-built registry.
                _xo_upd_ops = [] if _xo_ops else _xo_upd_all
                if len(_xo_upd_ops) == 1 and not raw_absence \
                        and not xo_hint.get("effect_via_lookup_field"):
                    _u_ent, _u_op = _xo_upd_ops[0]
                    _u_refusal = None
                    _u_corr = next(
                        (f for f, op_, v in _u_op.get("filters") or ()
                         if op_ == "EqualTo" and isinstance(v, tuple)
                         and tuple(v) == ("$Record", "Id")), None)
                    _u_template = [
                        (f, v) for f, op_, v in _u_op.get("filters") or ()
                        if op_ == "EqualTo"
                        and not isinstance(v, tuple)]
                    _u_assigns = {
                        f: tuple(tv)
                        for f, tv in _u_op["assignments"].items()
                        if tuple(tv)[0] == "literal"}
                    _u_field = (str(xo_hint.get("effect_field"))
                                .rsplit(".", 1)[-1]
                                if xo_hint.get("effect_field")
                                else (next(iter(sorted(_u_assigns)))
                                      if len(_u_assigns) == 1 else None))
                    if _u_corr is None:
                        _u_refusal = ("the update op has no ($Record, Id) "
                                      "correlation — cannot isolate the "
                                      "updated set")
                    elif _u_field is None or _u_field not in _u_assigns:
                        _u_refusal = (f"the update op does not verifiably "
                                      f"assign {_u_field!r} — name one of "
                                      f"{sorted(_u_assigns)}")
                    if _u_refusal is None:
                        _u_updated = _u_assigns[_u_field][1]
                        # the distractor: a third state (∉ template ∪ updated)
                        _u_flip = None
                        _u_tmpl_map = dict(_u_template)
                        if _u_field in _u_tmpl_map:
                            _u_effmeta = _grounding_field_metadata(
                                self._admit.scoped_neighborhood(
                                    self._admit.resolve_subject(
                                        "Object", effect_object_api,
                                        at)[0], at), self._s1, at)
                            _alt = _picklist_alternative(
                                (_u_effmeta.get(_u_field) or {}).get(
                                    "picklist_values"),
                                {_u_tmpl_map[_u_field], _u_updated})
                            if _alt is None:
                                _u_refusal = (
                                    f"cannot derive a distractor state on "
                                    f"{_u_field!r} distinct from the "
                                    f"template and updated values")
                            else:
                                _u_flip = (_u_field, _alt)
                    if _u_refusal is not None:
                        return IntentResolution(
                            grounded_candidates=[],
                            next_action=NextAction.REFUSE,
                            interpretation_delta=delta,
                            refusal=self._router.emission_deferred(
                                archetype, claim_kind, detail=_u_refusal))
                    # entry transition (Update trigger + EqualTo guard):
                    # identical derivation to the E1 create path
                    _u_trig = flow_behaviour(
                        getattr(_u_ent, "attributes", None))["trigger"]
                    _u_transition = None
                    if (_u_trig["record_trigger_type"] == "Update"
                            and _u_op["guard"]
                            and all(g[1] == "EqualTo"
                                    for g in _u_op["guard"])):
                        _ugmeta = _grounding_field_metadata(
                            neighborhood, self._s1, at)
                        _ut_create, _ut_update = {}, {}
                        for _gf, _gop, _gv in _u_op["guard"]:
                            _alt2 = _picklist_alternative(
                                (_ugmeta.get(_gf) or {}).get(
                                    "picklist_values"), {_gv})
                            if _alt2 is None:
                                _ut_create = None
                                break
                            _ut_update[_gf] = _gv
                            _ut_create[_gf] = _alt2
                        if _ut_create:
                            _u_transition = {"create": _ut_create,
                                             "update": _ut_update}
                    if _u_transition is None:
                        return IntentResolution(
                            grounded_candidates=[],
                            next_action=NextAction.REFUSE,
                            interpretation_delta=delta,
                            refusal=self._router.emission_deferred(
                                archetype, claim_kind,
                                detail=("the set-update producer's entry "
                                        "transition is not derivable "
                                        "(non-EqualTo guard or no picklist "
                                        "alternative) — the update that "
                                        "fires it cannot be staged")))
                    _u2 = dict(xo_hint)
                    _u2["effect_lookup_field"] = _u_corr
                    _u2["effect_field"] = _u_field
                    _u2["effect_value"] = _u_updated
                    grounded_eff = self._ground_cross_object_effect(
                        _u2, effect_object_api, at)
                    if isinstance(grounded_eff, _XoDeferral):
                        return IntentResolution(
                            grounded_candidates=[],
                            next_action=NextAction.REFUSE,
                            interpretation_delta=delta,
                            refusal=self._router.emission_deferred(
                                archetype, claim_kind,
                                detail=grounded_eff.detail,
                                candidates=grounded_eff.offer))
                    eff_ep, lookup_ep, eff_field_ep = grounded_eff
                    _ueps = {}
                    for _xf in sorted(set(_u_transition["create"])
                                      | set(_u_transition["update"])):
                        _xent = next(
                            (r.entity for r in neighborhood
                             if r.edge_type == EDGE_BELONGS
                             and r.entity.entity_type == "Field"
                             and isinstance(r.entity.sf_api_name, str)
                             and r.entity.sf_api_name
                             .rsplit(".", 1)[-1] == _xf), None)
                        if _xent is None:
                            return IntentResolution(
                                grounded_candidates=[],
                                next_action=NextAction.REFUSE,
                                interpretation_delta=delta,
                                refusal=self._router.emission_deferred(
                                    archetype, claim_kind,
                                    detail=(f"entry-guard field {_xf!r} "
                                            f"does not BELONG to the "
                                            f"subject")))
                        _ueps[_xf] = _Endpoint(
                            entity_id=_xent.id,
                            entity_type=_xent.entity_type,
                            external_id=_xent.sf_api_name or str(_xent.id))
                    _u_triggers = tuple(
                        (_ueps[f], _u_transition["create"][f])
                        for f in sorted(_u_transition["create"]))
                    _u_updates = tuple(
                        (_ueps[f], _u_transition["update"][f])
                        for f in sorted(_u_transition["update"]))
                    conflict = _staged_vr_conflict_detail(
                        neighborhood,
                        {ep.external_id: v for ep, v in _u_triggers},
                        staged_update={ep.external_id: v
                                       for ep, v in _u_updates})
                    if conflict is not None:
                        return IntentResolution(
                            grounded_candidates=[],
                            next_action=NextAction.REFUSE,
                            interpretation_delta=delta,
                            refusal=self._router.emission_deferred(
                                archetype, claim_kind, detail=conflict))
                    _stash_grounding(state, GroundedAutomationEffect(
                        archetype=archetype, claim_kind=claim_kind,
                        version_seq=at, subject=subj_ep,
                        automation=_Endpoint(
                            entity_id=_u_ent.id,
                            entity_type=_u_ent.entity_type,
                            external_id=_u_ent.sf_api_name
                            or str(_u_ent.id)),
                        requirement_excerpt=excerpt,
                        effect_field=eff_field_ep,
                        effect_value=_u_updated,
                        effect_object=eff_ep,
                        effect_lookup_field=lookup_ep,
                        trigger_fields=_u_triggers,
                        update_trigger_fields=_u_updates,
                        automation_primitive="flow",
                        premise_children={
                            "count": 2,
                            "template": tuple(_u_template),
                            "distractor": _u_flip,
                            "updated_value": _u_updated}))
                    presented = [
                        PresentedCandidate(
                            path_id=c.path_id,
                            admissibility_layer=AdmissibilityLayer(
                                c.admissibility_layer),
                            summary={"archetype": c.archetype,
                                     "claim_kind": c.claim_kind})
                        for c in grounded]
                    return IntentResolution(
                        grounded_candidates=presented,
                        next_action=NextAction.PROCEED_TO_EMIT,
                        interpretation_delta=delta)
                grounded_eff = self._ground_cross_object_effect(
                    xo_hint, effect_object_api, at)
                if isinstance(grounded_eff, _XoDeferral):
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind, detail=grounded_eff.detail,
                            candidates=grounded_eff.offer))
                eff_ep, lookup_ep, eff_field_ep = grounded_eff
                # D-307: the cross-object flow fires only when the SUBJECT
                # create reaches its entry gate (the L7e live recon: a
                # padding-only create never provokes the Task) — the same
                # D-299 rail as above.
                xo_triggers = _ground_trigger_fields(
                    xo_hint.get("trigger_fields"), neighborhood,
                    exclude_field=xo_hint.get("effect_field"))
                if expected_absence and not xo_triggers:
                    # D-307.1 (review B2): drop-never-refuse INVERTS under
                    # absence — for presence a padding-only create degrades to
                    # a self-revealing honest red, but for absence it passes
                    # green-by-construction against any gated automation
                    # (0 rows regardless of org truth). An absence case needs
                    # the staged state that DEFINES it; fail closed whether
                    # the gate was dropped or never proposed.
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind,
                            detail=("an absence case needs at least one "
                                    "verified trigger (field, value) pair — "
                                    "a padding-only create would grade the "
                                    "absence green regardless of the org's "
                                    "actual behavior")))
                # D-335: a PRESENCE cross-object effect that NAMES a field must
                # carry its expected value — mirror the same-record guard below
                # ("automation-effect needs field_name + expected_value"). An
                # unvalued named-field effect is not a verifiable claim: emission's
                # cross-object arm renders `read-effect.{field} equals {value}`, and
                # a None value makes AssertionPredicate raise (crashing the WHOLE
                # batch, not just this intent). The parent-stamp `not_null` fallback
                # is deliberately NARROW (dynamic $Flow.CurrentDate stamps with "no
                # stable literal") — extending it here would silently emit a WEAKER
                # claim than the requirement stated. effect_field=None stays valid:
                # the effect IS the correlated record's creation (the SideEffect arm
                # in emission). Refuse (invent-nothing) → the model re-proposes with
                # a value or drops the field; D-302 surfaces it as a partial refusal.
                xo_effect_value = _identity_safe(
                    xo_hint.get("effect_value"))
                if not expected_absence and eff_field_ep is not None \
                        and xo_effect_value is None:
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind,
                            detail=("a cross-object automation effect that names "
                                    "an effect_field needs its expected_value — an "
                                    "unvalued field effect is not verifiable (drop "
                                    "the field to assert only that the correlated "
                                    "record is created)")))
                # E1: the substrate-derived TRANSITION replaces model
                # pairs on the same fields (the C3 identity rule) — the
                # create stages the NOT-meeting state, the update the entry
                # state that fires the producer.
                xo_update_fields = ()
                if _xo_transition is not None:
                    _xeps = {}
                    _xmiss = None
                    for _xf in sorted(set(_xo_transition["create"])
                                      | set(_xo_transition["update"])):
                        _xent = next(
                            (r.entity for r in neighborhood
                             if r.edge_type == EDGE_BELONGS
                             and r.entity.entity_type == "Field"
                             and isinstance(r.entity.sf_api_name, str)
                             and r.entity.sf_api_name
                             .rsplit(".", 1)[-1] == _xf), None)
                        if _xent is None:
                            _xmiss = _xf
                            break
                        _xeps[_xf] = _Endpoint(
                            entity_id=_xent.id,
                            entity_type=_xent.entity_type,
                            external_id=_xent.sf_api_name or str(_xent.id))
                    if _xmiss is None:
                        xo_triggers = tuple(
                            (ep, v) for ep, v in xo_triggers
                            if ep.external_id.rsplit(".", 1)[-1]
                            not in _xo_transition["create"]
                        ) + tuple((_xeps[f], _xo_transition["create"][f])
                                  for f in sorted(_xo_transition["create"]))
                        xo_update_fields = tuple(
                            (_xeps[f], _xo_transition["update"][f])
                            for f in sorted(_xo_transition["update"]))
                # D-337: the subject create carries the staged entry-gate
                # values — if they provably fire one of the SUBJECT's active
                # VRs the create bounces and no correlated record (presence
                # or absence) is ever honestly observed. The effect object's
                # own VRs are the org's concern, not the recipe's.
                conflict = _staged_vr_conflict_detail(
                    neighborhood,
                    {ep.external_id: v for ep, v in xo_triggers},
                    staged_update={ep.external_id: v
                                   for ep, v in xo_update_fields})
                if conflict is not None:
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind, detail=conflict))
                _stash_grounding(state, GroundedAutomationEffect(
                    archetype=archetype, claim_kind=claim_kind, version_seq=at,
                    subject=subj_ep, automation=flow_ep,
                    requirement_excerpt=excerpt,
                    effect_field=eff_field_ep,
                    effect_value=xo_effect_value,
                    effect_object=eff_ep, effect_lookup_field=lookup_ep,
                    trigger_fields=xo_triggers,
                    update_trigger_fields=xo_update_fields,
                    automation_primitive=primitive,     # D-308 (see above)
                    # C9: the bounded-eventual read spec (async producers)
                    eventual_read=_xo_eventual,
                    # D-307: the absence mirror (gated fail-closed above —
                    # cross-object, no effect_field/effect_value, strict bool).
                    expected_absence=expected_absence))
            else:
                field_ent = next(
                    (r.entity for r in neighborhood
                     if r.edge_type == EDGE_BELONGS and r.entity.entity_type == "Field"
                     and r.entity.sf_api_name == hint.get("field_name")), None)
                effect_value = _identity_safe(hint.get("expected_value"))
                # FL02 slice: a VALUE-LESS same-record intent ("the org stores
                # the canonical/normalized form") grounds when exactly ONE
                # Flow's IR carries a grounded TRANSFORM on the field — the
                # substrate then owns the mechanics end to end: it derives a
                # format-valid canonical witness from the field's own
                # governing REGEX rules (or refuses when it cannot), stages
                # the de-transformed raw, and asserts the post-save value.
                # Create-scoped only in v1 (an update-phase transform intent
                # falls through to the existing refusal).
                transform_meta = None
                if (field_ent is not None and effect_value is None
                        and not hint.get("update_trigger_fields")):
                    tproducers = _flows_producing_transform(
                        flows, field_ent.sf_api_name)
                    if len(tproducers) > 1:
                        return IntentResolution(
                            grounded_candidates=[], next_action=NextAction.REFUSE,
                            interpretation_delta=delta,
                            refusal=self._router.emission_deferred(
                                archetype, claim_kind,
                                detail=(f"{len(tproducers)} Flows verifiably "
                                        f"transform {field_ent.sf_api_name!r} "
                                        f"— cannot attribute the rewrite")))
                    if len(tproducers) == 1:
                        t_ent, t_beh = tproducers[0]
                        bare = field_ent.sf_api_name.rsplit(".", 1)[-1]
                        patterns, opaque_rules = _field_regex_patterns(
                            neighborhood, bare)
                        witness = _synthesize_transform_witness(
                            t_beh["transform"], patterns, opaque_rules)
                        if isinstance(witness, str):
                            return IntentResolution(
                                grounded_candidates=[],
                                next_action=NextAction.REFUSE,
                                interpretation_delta=delta,
                                refusal=self._router.emission_deferred(
                                    archetype, claim_kind, detail=witness))
                        canonical, raw = witness
                        effect_value = canonical
                        transform_meta = {
                            "chain": t_beh["transform"],
                            "staged": raw,
                            "source_field": t_beh["source_field"],
                        }
                        # the verified rewrite producer IS the automation —
                        # rebind away from any provisional flows[0] stand-in
                        flow_ent = t_ent
                        primitive = "flow"
                        no_producer_floor = False
                        flow_ep = _Endpoint(
                            entity_id=t_ent.id, entity_type=t_ent.entity_type,
                            external_id=t_ent.sf_api_name or str(t_ent.id))
                # C4 (FL08 slice): a VALUE-LESS same-record intent whose field
                # exactly ONE Flow verifiably stamps with RUN_DATE ±
                # offset_days (an Update-trigger transition producer). The
                # substrate owns the mechanics end to end: the EXPECTED value
                # is the symbolic RelativeDate (replay-stable; S4
                # materialises at the execution boundary), and the TRANSITION
                # is derived from the arm's consumed EqualTo entry filter —
                # the create stages a picklist alternative (NOT meeting the
                # filter), the update stages the filter value (newly meeting
                # it — exactly what doesRequireRecordChangedToMeetCriteria
                # promises). Anything underivable refuses with the named
                # limit (ground-or-refuse).
                temporal_meta = None
                if (field_ent is not None and effect_value is None
                        and transform_meta is None):
                    tmp_producers = _flows_producing_temporal(
                        flows, field_ent.sf_api_name)
                    if len(tmp_producers) > 1:
                        return IntentResolution(
                            grounded_candidates=[], next_action=NextAction.REFUSE,
                            interpretation_delta=delta,
                            refusal=self._router.emission_deferred(
                                archetype, claim_kind,
                                detail=(f"{len(tmp_producers)} Flows verifiably "
                                        f"stamp {field_ent.sf_api_name!r} with a "
                                        f"relative date — cannot attribute")))
                    if len(tmp_producers) == 1:
                        t_ent, t_beh = tmp_producers[0]
                        _bare_obs = field_ent.sf_api_name.rsplit(".", 1)[-1]
                        _tgmeta = _grounding_field_metadata(
                            neighborhood, self._s1, at)
                        _t_create: dict = {}
                        _t_update: dict = {}
                        _t_refusal = None
                        if t_beh["negated_guards"]:
                            _t_refusal = ("the temporal arm carries a "
                                          "negation-context — outside the v1 "
                                          "transition grammar")
                        for cond in (() if _t_refusal else t_beh["guard"]):
                            _gf, _gop, _gv = cond
                            if _gf == _bare_obs:
                                _t_refusal = (f"the arm's entry filter rides "
                                              f"the observed field {_gf!r} — "
                                              f"cannot stage the transition")
                                break
                            if _gop != "EqualTo":
                                _t_refusal = (f"entry filter {_gf!r} {_gop} is "
                                              f"outside the v1 transition "
                                              f"grammar (EqualTo only)")
                                break
                            _alt = _picklist_alternative(
                                (_tgmeta.get(_gf) or {}).get(
                                    "picklist_values"), {_gv})
                            if _alt is None:
                                _t_refusal = (f"cannot derive a create state "
                                              f"that does NOT meet the entry "
                                              f"filter on {_gf!r} — no "
                                              f"alternative active picklist "
                                              f"value")
                                break
                            _t_update[_gf] = _gv
                            _t_create[_gf] = _alt
                        if _t_refusal is None and not _t_update:
                            _t_refusal = ("the temporal arm has no consumable "
                                          "entry filter — the transition that "
                                          "fires it cannot be staged")
                        if _t_refusal is not None:
                            return IntentResolution(
                                grounded_candidates=[],
                                next_action=NextAction.REFUSE,
                                interpretation_delta=delta,
                                refusal=self._router.emission_deferred(
                                    archetype, claim_kind, detail=_t_refusal))
                        effect_value = relative_date(t_beh["offset_days"])
                        temporal_meta = {"create": _t_create,
                                         "update": _t_update}
                        flow_ent = t_ent
                        primitive = "flow"
                        no_producer_floor = False
                        flow_ep = _Endpoint(
                            entity_id=t_ent.id, entity_type=t_ent.entity_type,
                            external_id=t_ent.sf_api_name or str(t_ent.id))
                # C3b (N-arm emission): a VALUE-LESS same-record intent
                # whose field a single create-fireable Flow verifiably
                # writes via GROUNDED literal arm(s). The requirement
                # classifies without naming the org's states (tiers, bands
                # — FL03's ladder is flow-internal, exactly like FL02's
                # canonical form and FL08's offset), so the SUBSTRATE
                # enumerates: ONE grounded claim per distinct arm value,
                # each staged with its own fire witness (the C3 interval
                # machinery), each VR-conflict-checked. D-207's ordered
                # groundings + the D-339 identity dedup already carry
                # N bundles per intent.
                if (field_ent is not None and effect_value is None
                        and transform_meta is None and temporal_meta is None
                        and not hint.get("update_trigger_fields")):
                    _bare_l = field_ent.sf_api_name.rsplit(".", 1)[-1]
                    _ladder = []
                    for _lent in flows:
                        _larms = [
                            b for b in flow_grounded_guarded_effects(
                                getattr(_lent, "attributes", None))
                            if b["field"] == _bare_l]
                        if _larms:
                            _ladder.append((_lent, _larms))
                    if len(_ladder) > 1:
                        return IntentResolution(
                            grounded_candidates=[],
                            next_action=NextAction.REFUSE,
                            interpretation_delta=delta,
                            refusal=self._router.emission_deferred(
                                archetype, claim_kind,
                                detail=(f"{len(_ladder)} Flows verifiably "
                                        f"write {field_ent.sf_api_name!r} — "
                                        f"cannot attribute the "
                                        f"classification")))
                    if len(_ladder) == 1 and (
                            flow_ent is None or no_producer_floor
                            or flow_ent.id == _ladder[0][0].id):
                        l_ent, l_arms = _ladder[0]
                        _bva_on = bool(getattr(
                            getattr(ctx, "operational_context", None),
                            "enable_bva_boundaries", False))
                        if field_is_calculated(field_ent.attributes):
                            return IntentResolution(
                                grounded_candidates=[],
                                next_action=NextAction.REFUSE,
                                interpretation_delta=delta,
                                refusal=self._router.emission_deferred(
                                    archetype, claim_kind,
                                    detail=(f"{field_ent.sf_api_name!r} is "
                                            f"CALCULATED — a Flow cannot be "
                                            f"credited for a formula field")))
                        _lgmeta = _grounding_field_metadata(
                            neighborhood, self._s1, at)

                        def _lscale(bare):
                            m = _lgmeta.get(bare)
                            try:
                                return (None if m is None
                                        or m.get("scale") is None
                                        else int(m["scale"]))
                            except (TypeError, ValueError):
                                return None

                        _seen_vals: set = set()
                        _l_stashes = []
                        _l_ep = _Endpoint(
                            entity_id=l_ent.id, entity_type=l_ent.entity_type,
                            external_id=l_ent.sf_api_name or str(l_ent.id))
                        _l_field_ep = _Endpoint(
                            entity_id=field_ent.id,
                            entity_type=field_ent.entity_type,
                            external_id=field_ent.sf_api_name
                            or str(field_ent.id))
                        for _b in l_arms:
                            if _b["value"] in _seen_vals:
                                continue
                            _seen_vals.add(_b["value"])
                            _nd = _numeric_effect_guard(
                                field_ent, _b["value"], self._s1, at)
                            if _nd is not None:
                                return IntentResolution(
                                    grounded_candidates=[],
                                    next_action=NextAction.REFUSE,
                                    interpretation_delta=delta,
                                    refusal=self._router.emission_deferred(
                                        archetype, claim_kind, detail=_nd))
                            _wstat, _wit = _guard_witness_values(
                                _b["guard"], _b["negated_guards"],
                                exclude_field=_bare_l, scale_of=_lscale)
                            if _wstat == "refuse":
                                return IntentResolution(
                                    grounded_candidates=[],
                                    next_action=NextAction.REFUSE,
                                    interpretation_delta=delta,
                                    refusal=self._router.emission_deferred(
                                        archetype, claim_kind,
                                        detail=(f"arm "
                                                f"{_bare_l}={_b['value']!r}: "
                                                + _wit)))
                            _weps = {}
                            _wmiss = None
                            for _wf in sorted(_wit):
                                _went = next(
                                    (r.entity for r in neighborhood
                                     if r.edge_type == EDGE_BELONGS
                                     and r.entity.entity_type == "Field"
                                     and isinstance(r.entity.sf_api_name, str)
                                     and r.entity.sf_api_name
                                     .rsplit(".", 1)[-1] == _wf), None)
                                if _went is None:
                                    _wmiss = _wf
                                    break
                                _weps[_wf] = _Endpoint(
                                    entity_id=_went.id,
                                    entity_type=_went.entity_type,
                                    external_id=_went.sf_api_name
                                    or str(_went.id))
                            if _wmiss is not None:
                                return IntentResolution(
                                    grounded_candidates=[],
                                    next_action=NextAction.REFUSE,
                                    interpretation_delta=delta,
                                    refusal=self._router.emission_deferred(
                                        archetype, claim_kind,
                                        detail=(f"the arm's guard field "
                                                f"{_wmiss!r} does not BELONG "
                                                f"to the subject at the "
                                                f"pinned version")))
                            _pairs = tuple((_weps[f], _wit[f])
                                           for f in sorted(_wit))
                            _conf = _staged_vr_conflict_detail(
                                neighborhood,
                                {ep.external_id: v for ep, v in _pairs})
                            if _conf is not None:
                                return IntentResolution(
                                    grounded_candidates=[],
                                    next_action=NextAction.REFUSE,
                                    interpretation_delta=delta,
                                    refusal=self._router.emission_deferred(
                                        archetype, claim_kind, detail=_conf))
                            _l_stashes.append(GroundedAutomationEffect(
                                archetype=archetype, claim_kind=claim_kind,
                                version_seq=at, subject=subj_ep,
                                automation=_l_ep,
                                requirement_excerpt=excerpt,
                                effect_field=_l_field_ep,
                                effect_value=_b["value"],
                                trigger_fields=_pairs,
                                automation_primitive="flow"))
                            # Wave 2 (CP3): BOUNDARY arms — behind the same
                            # org-independent enable_bva_boundaries flag the
                            # D-346 discipline uses. Each arm's INSIDE edge
                            # values (exactly ON/next to each original
                            # threshold, re-verified against the arm's full
                            # constraint set) become additional fire claims
                            # staged AT the edge; the union across arms pins
                            # every threshold from both sides (arm X's
                            # inside edge at t is the neighbour's outside).
                            # Same rails as the interior stash — distinct
                            # identity via the staged edge value; still ONE
                            # guard field by construction here.
                            if _bva_on and len(_wit) == 1:
                                (_gf1, _), = _wit.items()
                                _conds1 = [
                                    (op, v, False) for f, op, v
                                    in _b["guard"] if f == _gf1
                                ] + [(op, v, True) for f, op, v
                                     in _b["negated_guards"] if f == _gf1]
                                for _bv, _side, _t in _boundary_witnesses(
                                        _conds1, _lscale(_gf1) or 0):
                                    if _side != "inside" or _bv == _wit[_gf1]:
                                        continue
                                    _bpairs = tuple(
                                        (_weps[f], _bv if f == _gf1
                                         else _wit[f])
                                        for f in sorted(_wit))
                                    if _staged_vr_conflict_detail(
                                            neighborhood,
                                            {ep.external_id: v
                                             for ep, v in _bpairs}) is not None:
                                        continue   # edge collides with a VR:
                                    #              skip the probe, never refuse
                                    #              the whole enumeration
                                    _l_stashes.append(GroundedAutomationEffect(
                                        archetype=archetype,
                                        claim_kind=claim_kind,
                                        version_seq=at, subject=subj_ep,
                                        automation=_l_ep,
                                        requirement_excerpt=excerpt,
                                        effect_field=_l_field_ep,
                                        effect_value=_b["value"],
                                        trigger_fields=_bpairs,
                                        automation_primitive="flow"))
                        for _g in _l_stashes:
                            _stash_grounding(state, _g)
                        # the 1:1 grounding<->presented_candidate invariant
                        # (finalize's D-339 dedup relies on it): emit ONE
                        # presented per stashed arm, all REUSING this intent's
                        # single admitted path_id (c{offset+i}) so coverage
                        # attributes every arm to the SAME AC and the
                        # per-intent candidate_paths count stays 1 (later
                        # intents' c-index offsets unaffected).
                        _src = grounded[0]
                        presented = [
                            PresentedCandidate(
                                path_id=_src.path_id,
                                admissibility_layer=AdmissibilityLayer(
                                    _src.admissibility_layer),
                                summary={"archetype": archetype,
                                         "claim_kind": claim_kind})
                            for _ in _l_stashes]
                        return IntentResolution(
                            grounded_candidates=presented,
                            next_action=NextAction.PROCEED_TO_EMIT,
                            interpretation_delta=delta)
                if field_ent is None or effect_value is None:
                    # B0.2: a field-name miss gets a RANKED near-miss offer
                    # (the inventory line alphabetized standard fields first
                    # and hid every custom name in '+N more' — the tier-AC
                    # live failure); inventory stays the fallback. The
                    # wording must NOT demand expected_value unconditionally:
                    # where the org defines the value (canonical forms,
                    # relative dates, classification arms) the honest
                    # re-proposal is VALUE-LESS and the substrate derives it.
                    if field_ent is None and hint.get("field_name"):
                        _voc, _offer = _field_recovery_tail(
                            [hint.get("field_name")], neighborhood)
                        if not _voc:
                            _voc = "; " + _subject_field_inventory_line(
                                neighborhood)
                        return IntentResolution(
                            grounded_candidates=[],
                            next_action=NextAction.REFUSE,
                            interpretation_delta=delta,
                            refusal=self._router.emission_deferred(
                                archetype, claim_kind,
                                detail=("the observed field_name did not "
                                        "resolve on the subject." + _voc
                                        + " Re-propose with a grounded "
                                        "field; OMIT expected_value when "
                                        "the requirement does not state "
                                        "the concrete value — the "
                                        "substrate derives verifiable "
                                        "values (canonical forms, relative "
                                        "dates, classification arms) where "
                                        "the org defines them"),
                                candidates=_offer))
                    # Completion (FL06 slice): the premise-conditioned
                    # same-record producer (duplicate-check idiom) gets its
                    # turn before the refusal.
                    _pc_out = self._try_premise_conditioned_resolution(
                        hint=hint, subject=subject, field_ent=field_ent,
                        at=at, neighborhood=neighborhood, delta=delta,
                        archetype=archetype, claim_kind=claim_kind,
                        excerpt=excerpt, grounded=grounded, state=state,
                        flows=flows)
                    if _pc_out is not None:
                        return _pc_out
                    # Completion E3: before refusing a value-less field no
                    # subject-flow writes, check the roll-up idiom — the
                    # writer may TRIGGER on a child object (FL07's shape:
                    # parent count/total = aggregate over its lines).
                    _r_out = self._try_rollup_resolution(
                        hint=hint, subject=subject, field_ent=field_ent,
                        at=at, delta=delta, archetype=archetype,
                        claim_kind=claim_kind, excerpt=excerpt,
                        grounded=grounded, state=state)
                    if _r_out is not None:
                        return _r_out
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind,
                            detail=("automation-effect needs a verifiable "
                                    "effect: field_name + expected_value on "
                                    "the subject (or effect_object + "
                                    "effect_lookup_field) — no transform, "
                                    "relative-date, or classification "
                                    "producer verifiably writes "
                                    + (field_ent.sf_api_name
                                       if field_ent is not None
                                       else "the field"))))
                # R2 (req-302 robustness): a numeric effect field refuses a
                # non-numeric expected value HERE — the "<computed>"
                # placeholder class never reaches a claim body again.
                _num_detail = _numeric_effect_guard(
                    field_ent, effect_value, self._s1, at)
                if _num_detail is not None:
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind, detail=_num_detail))
                # D-304.1 (review B2): the automation binding and the observed
                # field must COHERE — three crossed bindings close here:
                # (i) a formula automation observes ITSELF (field_name must be
                # the named formula field — anything else is mechanism-false
                # AND lets the calc field slip past the k16 exclude);
                # (ii) a CALCULATED observed field under an explicitly-named
                # Flow is a deterministic wrong-green (the formula engine
                # computes the expectation from the create's own inputs; the
                # claim would credit a Flow that contributed nothing and stay
                # green if that Flow broke) — refuse;
                # (iii) the no-name floor with a calculated observed field
                # re-binds the HONEST mechanism: the formula field itself.
                observed_is_calc = field_is_calculated(field_ent.attributes)
                if primitive == "formula" and \
                        field_ent.sf_api_name != automation_name:
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind,
                            detail=(f"a formula automation observes itself — "
                                    f"field_name {field_ent.sf_api_name!r} "
                                    f"must equal the calculated automation "
                                    f"{automation_name!r}")))
                # D-308.1 (review B1): the guard fires for EVERY non-formula
                # primitive — "flow"-only let an approval-bound claim observe
                # a calculated field and stay green off the formula engine
                # (the identical deterministic wrong-green D-304.1 refused).
                if primitive != "formula" and observed_is_calc:
                    if automation_name:
                        return IntentResolution(
                            grounded_candidates=[], next_action=NextAction.REFUSE,
                            interpretation_delta=delta,
                            refusal=self._router.emission_deferred(
                                archetype, claim_kind,
                                detail=(f"the observed field "
                                        f"{field_ent.sf_api_name!r} is "
                                        f"CALCULATED — a {primitive} cannot "
                                        f"stamp a formula field; name the "
                                        f"field itself as the automation")))
                    primitive = "formula"
                    flow_ep = _Endpoint(
                        entity_id=field_ent.id,
                        entity_type=field_ent.entity_type,
                        external_id=field_ent.sf_api_name or str(field_ent.id))
                # SUB-3: the no-name branch provisionally bound flows[0] because NO
                # Flow on the subject produces the claimed effect. The calc-field
                # rebind above already re-bound primitive='formula' for a calculated
                # observed field (the honest mechanism). Anything STILL non-formula
                # here has no verified producer — binding the blind flows[0] would
                # ground the claim against an automation that may not cause the
                # effect (a wrong-green). REFUSE, mirroring the named branch's
                # posture on the identical empty-producer case (ground-or-refuse).
                # C5 (FL09 slice): before refusing, try the TRANSITION
                # producers — an Update-trigger flow whose grounded literal
                # arm matches the claimed (field, value). The prior-state
                # guard ($Record__Prior) becomes the CREATE, the current
                # guard becomes the UPDATE (EqualTo → the literal;
                # NotEqualTo → a picklist alternative), and every
                # entry_changed_fields field must genuinely change between
                # the two. Underivable → the named refusal (never a guess).
                transition_meta = None
                if no_producer_floor and primitive != "formula":
                    tr_producers = _flows_producing_transition(
                        flows, field_ent.sf_api_name, effect_value)
                    if len(tr_producers) > 1:
                        return IntentResolution(
                            grounded_candidates=[], next_action=NextAction.REFUSE,
                            interpretation_delta=delta,
                            refusal=self._router.emission_deferred(
                                archetype, claim_kind,
                                detail=(f"{len(tr_producers)} Update-trigger "
                                        f"Flows verifiably produce the claimed "
                                        f"effect — cannot attribute")))
                    if len(tr_producers) == 1:
                        tr_ent, tr_beh = tr_producers[0]
                        _bare_obs = field_ent.sf_api_name.rsplit(".", 1)[-1]
                        _trmeta = _grounding_field_metadata(
                            neighborhood, self._s1, at)
                        _tr_create: dict = {}
                        _tr_update: dict = {}
                        _tr_refusal = None
                        if tr_beh["negated_guards"]:
                            _tr_refusal = ("the transition arm carries a "
                                           "negation-context — outside the v1 "
                                           "transition grammar")
                        for cond in (() if _tr_refusal
                                     else tr_beh["prior_guard"]):
                            _gf, _gop, _gv = cond
                            if _gf == _bare_obs:
                                _tr_refusal = (f"the arm's prior-state guard "
                                               f"rides the observed field "
                                               f"{_gf!r} — cannot stage")
                                break
                            if _gop != "EqualTo":
                                _tr_refusal = (f"prior-state guard {_gf!r} "
                                               f"{_gop} is outside the v1 "
                                               f"transition grammar "
                                               f"(EqualTo only)")
                                break
                            _tr_create[_gf] = _gv
                        for cond in (() if _tr_refusal else tr_beh["guard"]):
                            _gf, _gop, _gv = cond
                            if _gop == "IsNull":
                                continue          # omission-satisfied
                            if _gf == _bare_obs:
                                _tr_refusal = (f"the arm's guard rides the "
                                               f"observed field {_gf!r} — "
                                               f"cannot stage")
                                break
                            if _gop == "EqualTo":
                                _tr_update[_gf] = _gv
                            elif _gop == "NotEqualTo":
                                _alt = _picklist_alternative(
                                    (_trmeta.get(_gf) or {}).get(
                                        "picklist_values"),
                                    {_gv, _tr_create.get(_gf)})
                                if _alt is None:
                                    _tr_refusal = (
                                        f"cannot derive an update state on "
                                        f"{_gf!r} that differs from "
                                        f"{_gv!r} — no alternative active "
                                        f"picklist value")
                                    break
                                _tr_update[_gf] = _alt
                            else:
                                _tr_refusal = (f"guard {_gf!r} {_gop} is "
                                               f"outside the v1 transition "
                                               f"grammar")
                                break
                        if _tr_refusal is None:
                            # every declared changed-field must GENUINELY
                            # change between the staged create and update
                            _changed = flow_behaviour(
                                getattr(tr_ent, "attributes", None)
                            )["trigger"]["entry_changed_fields"]
                            for _cf in _changed:
                                if _cf == _bare_obs:
                                    _tr_refusal = (
                                        f"the entry filter requires a change "
                                        f"on the observed field {_cf!r} — "
                                        f"cannot stage")
                                    break
                                if _cf not in _tr_create                                         or _cf not in _tr_update                                         or _tr_create[_cf] == _tr_update[_cf]:
                                    _tr_refusal = (
                                        f"the entry filter requires a real "
                                        f"change on {_cf!r} but the derived "
                                        f"transition does not stage one")
                                    break
                        if _tr_refusal is None and not _tr_update:
                            _tr_refusal = ("the transition arm derives no "
                                           "update state — nothing fires it")
                        if _tr_refusal is not None:
                            return IntentResolution(
                                grounded_candidates=[],
                                next_action=NextAction.REFUSE,
                                interpretation_delta=delta,
                                refusal=self._router.emission_deferred(
                                    archetype, claim_kind,
                                    detail=_tr_refusal))
                        transition_meta = {"create": _tr_create,
                                           "update": _tr_update}
                        flow_ent = tr_ent
                        primitive = "flow"
                        no_producer_floor = False
                        flow_ep = _Endpoint(
                            entity_id=tr_ent.id,
                            entity_type=tr_ent.entity_type,
                            external_id=tr_ent.sf_api_name or str(tr_ent.id))
                if no_producer_floor and primitive != "formula":
                    # Completion (FL06 slice): the premise-conditioned
                    # producer's turn (value-ful intents land here).
                    _pc_out = self._try_premise_conditioned_resolution(
                        hint=hint, subject=subject, field_ent=field_ent,
                        at=at, neighborhood=neighborhood, delta=delta,
                        archetype=archetype, claim_kind=claim_kind,
                        excerpt=excerpt, grounded=grounded, state=state,
                        flows=flows)
                    if _pc_out is not None:
                        return _pc_out
                    # Completion E3: no producer TRIGGERS_ON the subject —
                    # the writer may live on a CHILD object (the roll-up
                    # idiom). Shared attempt; None -> fall through to the
                    # honest refusal below.
                    _r_out = self._try_rollup_resolution(
                        hint=hint, subject=subject, field_ent=field_ent,
                        at=at, delta=delta, archetype=archetype,
                        claim_kind=claim_kind, excerpt=excerpt,
                        grounded=grounded, state=state)
                    if _r_out is not None:
                        return _r_out
                    # C3b disclosure: when the field HAS a verifiable ladder
                    # writer but the proposed value matches no arm, name the
                    # real arm values (substrate discloses grounded
                    # alternatives — the B0 posture; the model chooses).
                    _lhint = ""
                    _bare_nf = field_ent.sf_api_name.rsplit(".", 1)[-1]
                    for _lent in flows:
                        _lvals = sorted(
                            {str(b["value"]) for b in
                             flow_grounded_guarded_effects(
                                 getattr(_lent, "attributes", None))
                             if b["field"] == _bare_nf})
                        if _lvals:
                            _lhint = (f" — the verifiable writer "
                                      f"{_lent.sf_api_name} writes one of: "
                                      f"{_lvals}; use one of those values, "
                                      f"or omit expected_value to enumerate "
                                      f"every arm")
                            break
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind,
                            detail=("no Flow on the subject produces the "
                                    "claimed effect — name the specific "
                                    "automation" + _lhint)))
                # D-299: the OPTIONAL entry-condition trigger — the (field,
                # value) pairs the create must SET so the Flow's entry gate
                # fires. Same-record only (the gate is on the subject create);
                # object-qualified BELONGS_TO verify, drop-never-refuse. The
                # effect field is excluded (k16): the create sets the entry
                # CONDITION, never the value-under-test the Flow must produce.
                # Empty -> today's padding-only shallow create.
                trigger_fields = _ground_trigger_fields(
                    hint.get("trigger_fields"), neighborhood,
                    exclude_field=field_ent.sf_api_name)
                # C3 (band-interval witnesses): when the bound Flow's IR
                # carries the grounded producer arm for THIS (field, value)
                # effect and that arm has a fire condition, the SUBSTRATE
                # derives the create state that makes the arm fire — an
                # in-band value strictly interior to the arm's interval
                # (positive guard ∧ the negation-context of every prior
                # first-match rule). Substrate-derived pairs REPLACE
                # model-proposed pairs on the same field: the model's
                # in-band pick varies run to run, the witness never does
                # (per-band identity stability is the point). Fail-closed:
                # an empty band, unreadable scale, or a guard shape outside
                # the witness grammar REFUSES with the named limit; an arm
                # whose conditions are all omission-satisfied (IsNull / the
                # k16-excluded effect field — FL01's shape) stages nothing.
                if (primitive == "flow" and flow_ent is not None
                        and not no_producer_floor and transform_meta is None):
                    _bare_eff = field_ent.sf_api_name.rsplit(".", 1)[-1]
                    _arm = next(
                        (b for b in flow_grounded_guarded_effects(
                            getattr(flow_ent, "attributes", None))
                         if b["field"] == _bare_eff
                         and _effect_values_equal(b["value"], effect_value)),
                        None)
                    if _arm is not None and (_arm["guard"]
                                             or _arm["negated_guards"]):
                        _gmeta = _grounding_field_metadata(
                            neighborhood, self._s1, at)

                        def _scale_of(bare):
                            m = _gmeta.get(bare)
                            try:
                                return (None if m is None or m.get("scale")
                                        is None else int(m["scale"]))
                            except (TypeError, ValueError):
                                return None

                        _wstatus, _wit = _guard_witness_values(
                            _arm["guard"], _arm["negated_guards"],
                            exclude_field=_bare_eff, scale_of=_scale_of)
                        if _wstatus == "refuse":
                            return IntentResolution(
                                grounded_candidates=[],
                                next_action=NextAction.REFUSE,
                                interpretation_delta=delta,
                                refusal=self._router.emission_deferred(
                                    archetype, claim_kind, detail=_wit))
                        if _wit:
                            _weps = {}
                            for _wf in sorted(_wit):
                                _went = next(
                                    (r.entity for r in neighborhood
                                     if r.edge_type == EDGE_BELONGS
                                     and r.entity.entity_type == "Field"
                                     and isinstance(r.entity.sf_api_name, str)
                                     and r.entity.sf_api_name
                                     .rsplit(".", 1)[-1] == _wf), None)
                                if _went is None:
                                    return IntentResolution(
                                        grounded_candidates=[],
                                        next_action=NextAction.REFUSE,
                                        interpretation_delta=delta,
                                        refusal=self._router.emission_deferred(
                                            archetype, claim_kind,
                                            detail=(f"the arm's guard field "
                                                    f"{_wf!r} does not BELONG "
                                                    f"to the subject at the "
                                                    f"pinned version — cannot "
                                                    f"stage the fire state")))
                                _weps[_wf] = _Endpoint(
                                    entity_id=_went.id,
                                    entity_type=_went.entity_type,
                                    external_id=_went.sf_api_name
                                    or str(_went.id))
                            trigger_fields = tuple(
                                (ep, v) for ep, v in trigger_fields
                                if ep.external_id.rsplit(".", 1)[-1]
                                not in _wit
                            ) + tuple((_weps[f], _wit[f])
                                      for f in sorted(_wit))
                # D-304 (revised at impl): NO formula-input filter. The
                # value-formula grammar is unparsed by the condition parser
                # (both the fixture and the live LTV formula return NotParsed),
                # and dropping non-input triggers would break legitimate
                # VR-survival staging fields (the D-299 class). BELONGS_TO
                # verification + the k16 exclude are the guards.
                # D-306: the update-observe phase — "recalculates when X
                # changes". Same rail as trigger_fields (BELONGS_TO verify,
                # the k16 exclude on the observed field), but NOT
                # drop-never-refuse as a SET: a proposed update phase that
                # grounds to nothing would silently author the create-scoped
                # shape — a materially different claim (meaning drift + an
                # identity collision with the create-scoped case). Per-field
                # drops within the set keep the D-299 posture.
                proposed_upd = hint.get("update_trigger_fields")
                update_trigger_fields = _ground_trigger_fields(
                    proposed_upd, neighborhood,
                    exclude_field=field_ent.sf_api_name)
                if proposed_upd and not update_trigger_fields:
                    # Convergence arc: offers for the missed update fields
                    # (the same B0.2 vocabulary as every other field miss)
                    _upd_names = [(u or {}).get("field_name")
                                  for u in (proposed_upd or [])
                                  if isinstance(u, dict)]
                    _voc, _offer = _field_recovery_tail(_upd_names,
                                                        neighborhood)
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind,
                            detail=("update_trigger_fields did not ground — "
                                    "the recalculate-on-change premise needs "
                                    "at least one verified (field, value) "
                                    "pair that is not the observed field"
                                    + _voc),
                            candidates=_offer))
                # C4/C5: the substrate-derived TRANSITION replaces model
                # pairs on the same fields (identity stability, the C3 rule)
                # — the create stages the before-state, the update the state
                # that fires the arm; both then ride the existing D-306.1
                # checks + the VR-conflict overlay.
                _tmeta = temporal_meta if temporal_meta is not None \
                    else transition_meta
                if _tmeta is not None:
                    _teps = {}
                    for _tf in sorted(set(_tmeta["create"])
                                      | set(_tmeta["update"])):
                        _tent = next(
                            (r.entity for r in neighborhood
                             if r.edge_type == EDGE_BELONGS
                             and r.entity.entity_type == "Field"
                             and isinstance(r.entity.sf_api_name, str)
                             and r.entity.sf_api_name
                             .rsplit(".", 1)[-1] == _tf), None)
                        if _tent is None:
                            return IntentResolution(
                                grounded_candidates=[],
                                next_action=NextAction.REFUSE,
                                interpretation_delta=delta,
                                refusal=self._router.emission_deferred(
                                    archetype, claim_kind,
                                    detail=(f"the entry-filter field {_tf!r} "
                                            f"does not BELONG to the subject "
                                            f"at the pinned version — cannot "
                                            f"stage the transition")))
                        _teps[_tf] = _Endpoint(
                            entity_id=_tent.id, entity_type=_tent.entity_type,
                            external_id=_tent.sf_api_name or str(_tent.id))
                    trigger_fields = tuple(
                        (ep, v) for ep, v in trigger_fields
                        if ep.external_id.rsplit(".", 1)[-1]
                        not in _tmeta["create"]
                    ) + tuple((_teps[f], _tmeta["create"][f])
                              for f in sorted(_tmeta["create"]))
                    update_trigger_fields = tuple(
                        (ep, v) for ep, v in update_trigger_fields
                        if ep.external_id.rsplit(".", 1)[-1]
                        not in _tmeta["update"]
                    ) + tuple((_teps[f], _tmeta["update"][f])
                              for f in sorted(_tmeta["update"]))
                if update_trigger_fields:
                    # D-306.1 (review): the change must be REAL and STAGEABLE.
                    # (a) A PATCH cannot stage a calculated/read-only field —
                    # the recipe would 400 on every run (perma-errored).
                    upd_meta = _grounding_field_metadata(
                        neighborhood, self._s1, at)
                    unpatchable = sorted(
                        ep.external_id for ep, _ in update_trigger_fields
                        if (m := upd_meta.get(
                            ep.external_id.split(".", 1)[-1])) is None
                        or m.get("is_calculated")
                        or not m.get("is_updateable", True))
                    if unpatchable:
                        return IntentResolution(
                            grounded_candidates=[],
                            next_action=NextAction.REFUSE,
                            interpretation_delta=delta,
                            refusal=self._router.emission_deferred(
                                archetype, claim_kind,
                                detail=(f"update trigger field(s) not "
                                        f"updateable (calculated/read-only): "
                                        f"{unpatchable}")))
                    # (b) A no-op PATCH (every update pair value-identical to
                    # the staged initial pair) cannot witness a recompute —
                    # ISCHANGED-gated automations never re-fire, so a green
                    # would credit a change that was never exercised.
                    initial = {(ep.external_id, v)
                               for ep, v in trigger_fields}
                    if all((ep.external_id, v) in initial
                           for ep, v in update_trigger_fields):
                        return IntentResolution(
                            grounded_candidates=[],
                            next_action=NextAction.REFUSE,
                            interpretation_delta=delta,
                            refusal=self._router.emission_deferred(
                                archetype, claim_kind,
                                detail=("every update trigger pair duplicates "
                                        "the staged initial value — a no-op "
                                        "change cannot witness a recompute")))
                # R3 (req-302 robustness — the D-304 deferred piece armed):
                # for the formula primitive, VERIFY the LLM's expected value
                # against the field's own calculatedFormula evaluated on the
                # intent's OWN staged inputs — the create state and, when the
                # D-306 update phase exists, the post-update state. Claim
                # 7649c167 asserted the PRE-update LTV (62.5) while the org
                # correctly recomputed 75.0 — now refused at generation with
                # the correct value in the detail. match / not_evaluable
                # (unparseable formula, unstaged formula input — padding is
                # invisible here, fail-open is load-bearing) pass through to
                # the honest run-time loop. The verifier never writes a value
                # into the claim (k16) — the detail CITES it, the human/LLM
                # re-proposes.
                if primitive == "formula":
                    _ftype, _scale = _field_num_meta(field_ent, self._s1, at)
                    _fv = verify_formula_expectation(
                        formula_text=field_formula_text(field_ent.attributes),
                        expected_value=effect_value,
                        create_inputs={ep.external_id: v
                                       for ep, v in trigger_fields},
                        update_inputs={ep.external_id: v
                                       for ep, v in update_trigger_fields},
                        field_type=_ftype, scale=_scale,
                        treat_null_as_zero=field_treat_null_as_zero(
                            field_ent.attributes))
                    if _fv.status == "pre_update_match":
                        return IntentResolution(
                            grounded_candidates=[],
                            next_action=NextAction.REFUSE,
                            interpretation_delta=delta,
                            refusal=self._router.emission_deferred(
                                archetype, claim_kind,
                                detail=(f"expected_value {effect_value!r} "
                                        f"matches the PRE-update state only — "
                                        f"after the update phase the formula "
                                        f"{field_ent.sf_api_name} computes "
                                        f"{_fv.computed_final}; assert the "
                                        f"post-update value")))
                    if _fv.status == "mismatch":
                        return IntentResolution(
                            grounded_candidates=[],
                            next_action=NextAction.REFUSE,
                            interpretation_delta=delta,
                            refusal=self._router.emission_deferred(
                                archetype, claim_kind,
                                detail=(f"expected_value {effect_value!r} "
                                        f"does not match what the formula "
                                        f"{field_ent.sf_api_name} computes "
                                        f"from the staged inputs "
                                        f"({_fv.computed_final}"
                                        + (f" after the update; "
                                           f"{_fv.computed_create} before"
                                           if _fv.computed_create
                                           != _fv.computed_final else "")
                                        + ") — note a percent field's API "
                                          "value is the formula result ×100")))
                # D-337: the staged trigger state must SURVIVE the org's own
                # validation rules — a create (or the create ⊕ update
                # overlay) that provably fires an active VR is rejected
                # before the automation could ever fire (the req-302 live
                # catch: an LTV recompute whose staged update put
                # Loan_Amount__c > Property_Value__c, bounced by the org's
                # Loan_Exceeds_Property_Value rule). Kleene: unstaged fields
                # are unknown and unknown never refuses (run-time R1 padding
                # owns those).
                conflict = _staged_vr_conflict_detail(
                    neighborhood,
                    {ep.external_id: v for ep, v in trigger_fields},
                    staged_update={ep.external_id: v
                                   for ep, v in update_trigger_fields})
                if conflict is not None:
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind, detail=conflict))
                _stash_grounding(state, GroundedAutomationEffect(
                    archetype=archetype, claim_kind=claim_kind, version_seq=at,
                    subject=subj_ep, automation=flow_ep,
                    requirement_excerpt=excerpt,
                    effect_field=_Endpoint(
                        entity_id=field_ent.id, entity_type=field_ent.entity_type,
                        external_id=field_ent.sf_api_name or str(field_ent.id)),
                    effect_value=effect_value,
                    trigger_fields=trigger_fields,
                    automation_primitive=primitive,
                    update_trigger_fields=update_trigger_fields,
                    # FL02 slice: the transform provenance — chain + the raw
                    # witness the create stages on the effect field itself
                    # (the documented k16 exception; staged != expected).
                    transform_chain=(transform_meta["chain"]
                                     if transform_meta else ()),
                    transform_staged_value=(transform_meta["staged"]
                                            if transform_meta else None),
                    transform_source_field=(transform_meta["source_field"]
                                            if transform_meta else None)))

        # grounded -> emit deferred (draft vertical). resolve_intent stays whole.
        presented = [PresentedCandidate(path_id=c.path_id,
                                        admissibility_layer=AdmissibilityLayer(c.admissibility_layer),
                                        summary={"archetype": c.archetype, "claim_kind": c.claim_kind})
                     for c in grounded]
        nxt = NextAction.AWAIT_SELECTION if len(grounded) >= 2 else NextAction.PROCEED_TO_EMIT
        return IntentResolution(grounded_candidates=presented, next_action=nxt,
                                interpretation_delta=delta)

    def _ground_cross_object_trigger(self, hint: dict, trigger_object_api: str,
                                     at: int):
        """Verify the cross-object trigger names against S1 (D-227): the
        trigger Object must resolve uniquely; ``trigger_lookup_field`` (its
        lookup back to the subject) must BELONG_TO it. Returns
        ``(trigger_ep, lookup_ep)`` on success, or the deferral-detail STRING
        on any unverifiable name — the caller routes it to emission-deferred
        (never guesses). The lookup's TARGET stays LLM-proposed +
        existence-verified, the same trust level as ``effect_lookup_field``
        (S1 referenceTo modeling is a logged refinement)."""
        matches = self._admit.resolve_subject("Object", trigger_object_api, at)
        if len(matches) != 1:
            return (f"trigger object {trigger_object_api!r} did not resolve "
                    f"uniquely in the org model ({len(matches)} matches)")
        trig = matches[0]
        trig_neigh = self._admit.scoped_neighborhood(trig, at)
        trig_fields = {r.entity.sf_api_name: r.entity for r in trig_neigh
                       if r.edge_type == EDGE_BELONGS
                       and r.entity.entity_type == "Field"}
        lookup_name = hint.get("trigger_lookup_field")
        lookup_ent = trig_fields.get(lookup_name) if lookup_name else None
        if lookup_ent is None:
            return (f"trigger lookup field {lookup_name!r} does not exist on "
                    f"{trigger_object_api} — cannot link the trigger record "
                    f"to the subject")
        trig_ep = _Endpoint(entity_id=trig.id, entity_type=trig.entity_type,
                            external_id=trig.sf_api_name or str(trig.id))
        lookup_ep = _Endpoint(
            entity_id=lookup_ent.id, entity_type=lookup_ent.entity_type,
            external_id=lookup_ent.sf_api_name or str(lookup_ent.id))
        return trig_ep, lookup_ep

    # Completion E3: the per-row value staged on a Sum aggregate's source
    # field — one deterministic constant (expected = k × this), never varied
    # (identity stability). Distinctive enough that a default/formula value
    # colliding with it by accident is implausible on a fresh record.
    _ROLLUP_SUM_STAGED = 137

    # C9 (bounded-eventual observation): the recipe-carried spec for an
    # async producer's read — advisory; the S4 executor owns the clamps.
    _EVENTUAL_READ_SPEC = {"timeout_s": 120, "poll_s": 5,
                           "reason": "async_after_commit"}

    def _try_premise_conditioned_resolution(self, *, hint, subject,
                                            field_ent, at, neighborhood,
                                            delta, archetype, claim_kind,
                                            excerpt, grounded, state, flows):
        """Completion (FL06 slice) — attempt the PREMISE-CONDITIONED
        same-record resolution: a flow's arm writes the subject's field
        only when a sibling premise is non-empty (the duplicate-check
        idiom). Returns an IntentResolution when the shape applies, else
        ``None`` (caller falls through). Everything substrate-derived:
        the correlation witness (format-rule aware), the sibling template
        (NotEqualTo → picklist alternative), the arm value."""
        if (field_ent is None or hint.get("effect_object")
                or hint.get("update_trigger_fields")
                or hint.get("expected_absence")):
            return None
        expected = _identity_safe(hint.get("expected_value"))
        matches = _flows_producing_premise_conditioned(
            flows, field_ent.sf_api_name, expected)
        if not matches:
            return None
        if len(matches) > 1:
            return IntentResolution(
                grounded_candidates=[], next_action=NextAction.REFUSE,
                interpretation_delta=delta,
                refusal=self._router.emission_deferred(
                    archetype, claim_kind,
                    detail=(f"{len(matches)} flows conditionally write "
                            f"{field_ent.sf_api_name!r} on a sibling "
                            f"premise — the writer is ambiguous")))
        res = self._ground_premise_conditioned(
            matches[0], subject, field_ent, at, neighborhood)
        if isinstance(res, str):
            return IntentResolution(
                grounded_candidates=[], next_action=NextAction.REFUSE,
                interpretation_delta=delta,
                refusal=self._router.emission_deferred(
                    archetype, claim_kind, detail=res))
        p_flow, sibling_spec, p_value, subject_staged = res
        p_eps = {}
        for _pf in sorted(subject_staged):
            _pent = next(
                (r.entity for r in neighborhood
                 if r.edge_type == EDGE_BELONGS
                 and r.entity.entity_type == "Field"
                 and isinstance(r.entity.sf_api_name, str)
                 and r.entity.sf_api_name.rsplit(".", 1)[-1] == _pf), None)
            if _pent is None:
                return IntentResolution(
                    grounded_candidates=[], next_action=NextAction.REFUSE,
                    interpretation_delta=delta,
                    refusal=self._router.emission_deferred(
                        archetype, claim_kind,
                        detail=(f"staged field {_pf!r} does not BELONG "
                                f"to the subject")))
            p_eps[_pf] = _Endpoint(
                entity_id=_pent.id, entity_type=_pent.entity_type,
                external_id=_pent.sf_api_name or str(_pent.id))
        _stash_grounding(state, GroundedAutomationEffect(
            archetype=archetype, claim_kind=claim_kind, version_seq=at,
            subject=_Endpoint(
                entity_id=subject.id, entity_type=subject.entity_type,
                external_id=subject.sf_api_name or str(subject.id)),
            automation=_Endpoint(
                entity_id=p_flow.id, entity_type=p_flow.entity_type,
                external_id=p_flow.sf_api_name or str(p_flow.id)),
            requirement_excerpt=excerpt,
            effect_field=_Endpoint(
                entity_id=field_ent.id, entity_type=field_ent.entity_type,
                external_id=field_ent.sf_api_name or str(field_ent.id)),
            effect_value=p_value,
            trigger_fields=tuple((p_eps[f], subject_staged[f])
                                 for f in sorted(subject_staged)),
            automation_primitive="flow",
            premise_sibling=sibling_spec))
        presented = [
            PresentedCandidate(
                path_id=c.path_id,
                admissibility_layer=AdmissibilityLayer(
                    c.admissibility_layer),
                summary={"archetype": c.archetype,
                         "claim_kind": c.claim_kind})
            for c in grounded]
        return IntentResolution(
            grounded_candidates=presented,
            next_action=NextAction.PROCEED_TO_EMIT,
            interpretation_delta=delta)

    # the deterministic correlation witness when NO format rule pins the
    # field — uppercase-stable (a same-org normalize transform maps it to
    # itself, so the sibling correlation survives before-save rewrites)
    _PREMISE_CORR_WITNESS = "PQAW137X"

    def _ground_premise_conditioned(self, match, subject, field_ent, at,
                                    neighborhood):
        """Derive the FL06-class evidence pieces from the matched arm, or
        the deferral-detail string. Returns ``(flow_ent, sibling_spec,
        arm_value, subject_staged)`` — sibling_spec = {"staged",
        "correlation"}; subject_staged = {bare: value} for the subject
        create (the correlation witness + the arm's guard witnesses)."""
        flow_ent, beh = match
        prem = beh["premise"]
        if prem["object"] != subject.sf_api_name:
            return (f"the conditioning premise queries {prem['object']!r} "
                    f"— only the same-object sibling shape is stageable")
        rel = classify_relation(prem)
        if rel["kind"] != "sibling_set":
            return (f"the premise correlates by {rel['kind']} — only the "
                    f"sibling-set shape is stageable")
        corr_f, corr_g = rel["correlation_field"], rel["subject_field"]
        meta = _grounding_field_metadata(neighborhood, self._s1, at)
        patterns, opaque_rules = _field_regex_patterns(neighborhood, corr_g)
        if opaque_rules:
            return (f"active rule(s) {sorted(opaque_rules)} on {corr_g} "
                    f"are unreadable — a correlation witness cannot be "
                    f"verified safe")
        witness = None
        if patterns:
            for _pat in sorted(patterns):
                witness = _regex_matching_value(_pat)
                if witness:
                    break
            if not witness:
                return (f"no matching-value witness is derivable for the "
                        f"format rule(s) on {corr_g}")
        else:
            witness = self._PREMISE_CORR_WITNESS
        staged = []
        for f, op, v in rel["literals"]:
            if op == "EqualTo":
                staged.append((f, v))
            elif op == "IsNull" and v is True:
                continue
            elif op == "IsNull" and v is False:
                return (f"the premise requires non-null {f!r} with no "
                        f"derivable witness")
            elif op == "NotEqualTo":
                _alt = _picklist_alternative(
                    (meta.get(f) or {}).get("picklist_values"), {v})
                if _alt is None:
                    return (f"the premise excludes {f}={v!r} and no "
                            f"alternative state is derivable")
                staged.append((f, _alt))
            else:
                return f"unstageable premise filter {f!r} {op}"
        staged.append((corr_f, witness))

        def _scale_of(bare):
            m = meta.get(bare)
            try:
                return (None if m is None or m.get("scale") is None
                        else int(m["scale"]))
            except (TypeError, ValueError):
                return None

        _bare_eff = field_ent.sf_api_name.rsplit(".", 1)[-1]
        _wstatus, _wit = _guard_witness_values(
            beh["guard"], beh["negated_guards"],
            exclude_field=_bare_eff, scale_of=_scale_of)
        if _wstatus == "refuse":
            return _wit
        subject_staged = dict(_wit or {})
        if subject_staged.get(corr_g, witness) != witness:
            return (f"the arm's guard pins {corr_g!r} to a value distinct "
                    f"from the correlation witness — unstageable")
        subject_staged[corr_g] = witness
        for _staged_map in (dict(staged), subject_staged):
            conflict = _staged_vr_conflict_detail(neighborhood, _staged_map)
            if conflict is not None:
                return conflict
        return flow_ent, {
            "staged": tuple(staged),
            "correlation": (corr_f, corr_g, witness)}, \
            beh["value"], subject_staged

    def _try_rollup_resolution(self, *, hint, subject, field_ent, at, delta,
                               archetype, claim_kind, excerpt, grounded,
                               state):
        """Completion E3 — attempt the ROLL-UP resolution: a flow triggered
        on a CHILD object aggregates the sibling set (Count / Sum) onto the
        subject's field. Returns an IntentResolution (grounded PROCEED or a
        named refusal) when the roll-up shape applies, or ``None`` so the
        caller falls through to its own refusal. Value-less, same-record-
        framed intents only — the aggregate's value is parameterized by the
        EVIDENCE (k staged rows), so a model-proposed value can never
        verifiably match. Bounded org-wide scan: by definition the producer
        is not in the subject's TRIGGERS_ON neighborhood."""
        if (field_ent is None
                or _identity_safe(hint.get("expected_value")) is not None
                or hint.get("effect_object")
                or hint.get("update_trigger_fields")):
            return None
        rollups = _flows_producing_rollup(
            self._s1.get_entities("Flow", at_seq=at),
            subject.sf_api_name, field_ent.sf_api_name)
        if not rollups:
            return None
        if len(rollups) > 1:
            return IntentResolution(
                grounded_candidates=[], next_action=NextAction.REFUSE,
                interpretation_delta=delta,
                refusal=self._router.emission_deferred(
                    archetype, claim_kind,
                    detail=(f"{len(rollups)} flows roll an aggregate up "
                            f"onto this field — the writer is ambiguous, "
                            f"so the effect cannot be attributed")))
        res = self._ground_rollup_effect(rollups[0], subject, field_ent, at)
        if isinstance(res, str):
            return IntentResolution(
                grounded_candidates=[], next_action=NextAction.REFUSE,
                interpretation_delta=delta,
                refusal=self._router.emission_deferred(
                    archetype, claim_kind, detail=res))
        r_flow, r_spec, r_expected = res
        _stash_grounding(state, GroundedAutomationEffect(
            archetype=archetype, claim_kind=claim_kind,
            version_seq=at,
            subject=_Endpoint(
                entity_id=subject.id, entity_type=subject.entity_type,
                external_id=subject.sf_api_name or str(subject.id)),
            automation=_Endpoint(
                entity_id=r_flow.id, entity_type=r_flow.entity_type,
                external_id=r_flow.sf_api_name or str(r_flow.id)),
            requirement_excerpt=excerpt,
            effect_field=_Endpoint(
                entity_id=field_ent.id, entity_type=field_ent.entity_type,
                external_id=field_ent.sf_api_name or str(field_ent.id)),
            effect_value=r_expected,
            automation_primitive="flow",
            rollup_spec=r_spec))
        presented = [
            PresentedCandidate(
                path_id=c.path_id,
                admissibility_layer=AdmissibilityLayer(
                    c.admissibility_layer),
                summary={"archetype": c.archetype,
                         "claim_kind": c.claim_kind})
            for c in grounded]
        return IntentResolution(
            grounded_candidates=presented,
            next_action=NextAction.PROCEED_TO_EMIT,
            interpretation_delta=delta)

    def _ground_rollup_effect(self, match, subject, field_ent, at):
        """Completion E3: derive the full roll-up evidence shape from the
        single matched producer, verifying every name against S1 at the
        pinned version. Returns ``(flow_ent, rollup_spec, expected)`` or
        the deferral-detail STRING (never guesses).

        Derivation: ``staging_plan`` (k=2 sibling rows + the premise's
        literal template) → per-row staged values (+ the Sum source field's
        constant) → ``aggregate_expectation`` (Count → k; Sum → k×staged)
        → child-object VR-conflict gate on the staged rows."""
        flow_ent, spec = match
        child_api = spec["child_object"]
        plan = staging_plan(spec["premise"], "count_equals", n=2)
        if "refusal" in plan:
            return (f"the rollup's child premise is not stageable "
                    f"({plan['refusal']}) — the sibling set cannot be "
                    f"deterministically provisioned")
        if plan["required_any"]:
            return (f"the rollup's child premise requires non-null values "
                    f"on {sorted(plan['required_any'])} with no derivable "
                    f"witness — the sibling set cannot be provisioned")
        children = self._admit.resolve_subject("Object", child_api, at)
        if len(children) != 1:
            return (f"the rollup's child object {child_api!r} does not "
                    f"resolve uniquely in the org model")
        child_neigh = self._admit.scoped_neighborhood(children[0], at)
        child_fields = {
            r.entity.sf_api_name.rsplit(".", 1)[-1]
            for r in child_neigh
            if r.edge_type == EDGE_BELONGS
            and r.entity.entity_type == "Field"
            and isinstance(r.entity.sf_api_name, str)}
        if spec["lookup"] not in child_fields:
            return (f"the rollup's correlation lookup {spec['lookup']!r} "
                    f"does not exist on {child_api} — cannot stage the "
                    f"sibling set")
        staged = list(plan["template"])
        if spec["fn"] == "Sum":
            src = spec["source_field"]
            if not src or src not in child_fields:
                return (f"the Sum rollup's source field {src!r} does not "
                        f"exist on {child_api} — cannot stage per-row "
                        f"values")
            _cmeta = _grounding_field_metadata(child_neigh, self._s1, at)
            _ftype = (_cmeta.get(src) or {}).get("field_type")
            if _ftype not in ("currency", "number", "double", "percent",
                              "int"):
                return (f"the Sum rollup's source field {src!r} is "
                        f"{_ftype or 'of unknown type'} — a numeric "
                        f"per-row value cannot be staged")
            staged.append((src, self._ROLLUP_SUM_STAGED))
            expected = aggregate_expectation(
                "Sum", plan, staged_value=self._ROLLUP_SUM_STAGED)
        else:
            expected = aggregate_expectation("Count", plan)
        if isinstance(expected, str):
            return (f"the rollup's expected value is not derivable "
                    f"({expected})")
        conflict = _staged_vr_conflict_detail(
            child_neigh, dict(staged))
        if conflict is not None:
            return conflict
        return flow_ent, {
            "child_object": child_api, "lookup": spec["lookup"],
            "count": plan["create_matching"], "staged": tuple(staged),
            "fn": spec["fn"], "expected": expected}, expected

    def _ground_cross_object_effect(self, hint: dict, effect_object_api: str,
                                    at: int, *, lookup_required: bool = True):
        """Verify the cross-object effect names against S1 (D-210.1): the
        effect Object must resolve uniquely; the lookup field (and the optional
        asserted effect field) must BELONG_TO it. Returns
        ``(effect_ep, lookup_ep_or_None, effect_field_ep_or_None)`` on success,
        or an ``_XoDeferral`` on any unverifiable name — the caller routes its
        detail + offer to emission-deferred (never guesses).
        ``lookup_required=False``
        is the D-227 parent-stamp path (the correlate is the SUBJECT's own
        ``effect_via_lookup_field``, verified by the caller)."""
        matches = self._admit.resolve_subject("Object", effect_object_api, at)
        if len(matches) != 1:
            # B0: a zero-match effect object gets a near-miss offer (a >1
            # match is ambiguity, not a miss — no candidates there).
            tail = ""
            if not matches:
                tail, _ = self._ref_miss("Object", effect_object_api, at)
            return _XoDeferral(
                f"effect object {effect_object_api!r} did not resolve "
                f"uniquely in the org model ({len(matches)} matches)." + tail)
        eff = matches[0]
        eff_neigh = self._admit.scoped_neighborhood(eff, at)
        # Key by the BARE field tail so a bare hint ("Subject") resolves the same
        # as a qualified one ("Task.Subject") — the LLM sends either (it qualifies
        # effect_lookup_field but often not effect_field). Field api-names are
        # unique per Object, so the tail is unambiguous (mirrors the bare-keyed
        # _grounding_field_metadata). The bound entity keeps its qualified
        # sf_api_name, so emission output is unchanged.
        eff_fields = {r.entity.sf_api_name.rsplit(".", 1)[-1]: r.entity
                      for r in eff_neigh
                      if r.edge_type == EDGE_BELONGS
                      and r.entity.entity_type == "Field"
                      and r.entity.sf_api_name}
        lookup_name = hint.get("effect_lookup_field")
        lookup_ent = (eff_fields.get(lookup_name.rsplit(".", 1)[-1])
                      if isinstance(lookup_name, str) and lookup_name else None)
        # B0 pool for BOTH effect-endpoint misses: the EFFECT object's own
        # BELONGS_TO fields at the pinned seq — never the subject's (the
        # missed name is asserted to live on the effect record).
        eff_pool = [(e.sf_api_name, e.display_name) for e in eff_fields.values()]
        if lookup_ent is None and lookup_required:
            lk_cands = _recovery.rank_candidates(lookup_name or "", eff_pool)
            return _XoDeferral(
                f"effect lookup field {lookup_name!r} does not exist on "
                f"{effect_object_api} — cannot correlate the effect record "
                f"to the trigger record." + _recovery.format_candidates(lk_cands),
                _recovery.offer_payload("Field", lookup_name, lk_cands)
                if lk_cands else None)
        eff_field_name = hint.get("effect_field")
        eff_field_ep = None
        if eff_field_name is not None:
            eff_field_ent = (eff_fields.get(eff_field_name.rsplit(".", 1)[-1])
                             if isinstance(eff_field_name, str) and eff_field_name
                             else None)
            if eff_field_ent is None:
                _proposed = (eff_field_name
                             if isinstance(eff_field_name, str) else "")
                ef_cands = _recovery.rank_candidates(_proposed, eff_pool)
                return _XoDeferral(
                    f"effect field {eff_field_name!r} does not exist on "
                    f"{effect_object_api}."
                    + _recovery.format_candidates(ef_cands),
                    _recovery.offer_payload("Field", _proposed, ef_cands)
                    if ef_cands else None)
            eff_field_ep = _Endpoint(
                entity_id=eff_field_ent.id, entity_type=eff_field_ent.entity_type,
                external_id=eff_field_ent.sf_api_name or str(eff_field_ent.id))
        eff_ep = _Endpoint(entity_id=eff.id, entity_type=eff.entity_type,
                           external_id=eff.sf_api_name or str(eff.id))
        lookup_ep = None if lookup_ent is None else _Endpoint(
            entity_id=lookup_ent.id, entity_type=lookup_ent.entity_type,
            external_id=lookup_ent.sf_api_name or str(lookup_ent.id))
        return eff_ep, lookup_ep, eff_field_ep

    # -- configuration metadata-relationship admissibility (D-098.1) ----
    def _resolve_no_admissible_test(self, desc: dict, excerpt: str) -> IntentResolution:
        """D-247: record a model-declared per-AC refusal (no_admissible_test). No
        grounding — produces a dismissed candidate so it surfaces in
        attempted_interpretation, plus a refusal directive that only routes if it
        is the last-standing intent (the multi-intent aggregator ignores it once
        any sibling grounds). The substrate originates nothing; it records the
        model's refusal verbatim (the honesty hinge)."""
        hint = desc.get("target_subject_hint") or {}
        cand = _Candidate(
            path_id="c0", archetype=desc.get("archetype_hint") or "data_behavior",
            claim_kind=desc.get("claim_kind_hint") or "",
            subject_refs=[{"entity_type": hint.get("entity_type"),
                           "sf_api_name": hint.get("sf_api_name")}],
            requirement_anchor=excerpt, status="dismissed",
            dismissal_reason="no_admissible_test")
        reason = desc.get("no_admissible_test_reason") or "no admissible test for this AC"
        # B0 telemetry honesty: this reason is MODEL prose recorded verbatim —
        # mark its provenance so it can never read as a substrate fact (the
        # req-320 job-76 incident: an invented "pinned version" explanation
        # presented as if the substrate had concluded it).
        return IntentResolution(
            grounded_candidates=[], next_action=NextAction.REFUSE,
            interpretation_delta=self._delta([], [cand]),
            refusal=self._router.no_relevant_context(
                reason, source="model", layer="resolution"))

    def _resolve_configuration(self, intent_input: dict, ctx: ConversationContext, state: Any) -> IntentResolution:
        """Edge-existence admissibility for a config metadata-relationship-claim
        (Layer-1-complete, D-079): resolve both endpoints, verify the asserted
        Tier-1 edge exists between them. Edge present -> admissible; absent ->
        the org lacks the assumed relationship -> ungrounded refusal."""
        desc = intent_input["intent_descriptor"]
        excerpt = intent_input.get("requirement_excerpt", "")
        claim_kind = desc.get("claim_kind_hint")
        hint = desc.get("target_subject_hint") or {}
        at = ctx.semantic_context.s1_version_seq
        src, tgt = hint.get("source") or {}, hint.get("target") or {}
        edge_type = hint.get("edge_type")

        def _cand(reason=None, status="dismissed", layer=None) -> _Candidate:
            return _Candidate(
                path_id="c0", archetype="configuration", claim_kind=claim_kind or "",
                subject_refs=[src, tgt], requirement_anchor=excerpt,
                status=status, admissibility_layer=layer, dismissal_reason=reason)

        # Configuration kinds: metadata-relationship (D-098); existence + property
        # (D-122). Others (e.g. sharing-rule -> S1 Tier-2) not yet built.
        if claim_kind == "existence-claim":
            return self._resolve_existence(intent_input, excerpt, at, state)
        if claim_kind == "property-claim":
            return self._resolve_property(intent_input, excerpt, at, state)
        if claim_kind != "metadata-relationship-claim":
            return IntentResolution([], NextAction.REFUSE, self._delta([], []),
                                    refusal=self._router.underspecified(
                                        f"configuration claim_kind {claim_kind!r} not yet supported"))

        # edge_type bound verbatim to TIER_1_EDGES — not a real edge -> ungrounded.
        if edge_type not in TIER_1_EDGES:
            return IntentResolution([], NextAction.REFUSE, self._delta([], [_cand("type_incompatibility")]),
                                    refusal=RefusalDirective(RefusalKind.UNGROUNDED_CLAIM, {
                                        "detail_source": "substrate", "detail_layer": "grounding",
                                    "detail": f"edge_type {edge_type!r} is not a Tier-1 edge type",
                                        "edge_type": edge_type}))

        src_matches = self._admit.resolve_subject(src.get("entity_type"), src.get("sf_api_name"), at)
        tgt_matches = self._admit.resolve_subject(tgt.get("entity_type"), tgt.get("sf_api_name"), at)
        if len(src_matches) > 1 or len(tgt_matches) > 1:
            return IntentResolution([], NextAction.REFUSE, self._delta([], [_cand("ambiguous_target_resolution")]),
                                    refusal=self._router.ambiguous(src_matches + tgt_matches))
        if not src_matches or not tgt_matches:
            return IntentResolution([], NextAction.REFUSE, self._delta([], []),
                                    refusal=self._router.no_relevant_context("relationship endpoint did not resolve"))
        source, target = src_matches[0], tgt_matches[0]
        subject_refs = [
            {"entity_type": source.entity_type, "sf_api_name": source.sf_api_name},
            {"entity_type": target.entity_type, "sf_api_name": target.sf_api_name},
        ]

        # Layer-1-complete: the asserted edge exists in S1 or it does not.
        related = self._s1.get_related(source.id, [edge_type], "outbound", at_seq=at)
        edge_present = any(r.entity.id == target.id for r in related)

        cand = _Candidate(path_id="c0", archetype="configuration", claim_kind=claim_kind,
                          subject_refs=subject_refs, requirement_anchor=excerpt, status="dismissed")
        if edge_present:
            cand.status = "admissibly_grounded"
            cand.admissibility_layer = AdmissibilityLayer.LAYER_1.value
            # Stash the grounding facts for emission (D-097.5): finalize_outcome
            # authors the claim + recipe bodies from these S1-resolved endpoints,
            # never from LLM-supplied data.
            if state is not None:
                _stash_grounding(state, GroundedEmission(
                    archetype="configuration", claim_kind=claim_kind,
                    edge_type=edge_type, version_seq=at,
                    source=_Endpoint(
                        entity_id=source.id, entity_type=source.entity_type,
                        external_id=source.sf_api_name or str(source.id)),
                    target=_Endpoint(
                        entity_id=target.id, entity_type=target.entity_type,
                        external_id=target.sf_api_name or str(target.id)),
                    requirement_excerpt=excerpt))
            presented = [PresentedCandidate(
                path_id="c0", admissibility_layer=AdmissibilityLayer.LAYER_1,
                summary={"edge_type": edge_type, "source": subject_refs[0], "target": subject_refs[1]})]
            return IntentResolution(grounded_candidates=presented,
                                    next_action=NextAction.PROCEED_TO_EMIT,
                                    interpretation_delta=self._delta(related, [cand]))
        cand.dismissal_reason = "insufficient_grounding"
        return IntentResolution([], NextAction.REFUSE, self._delta(related, [cand]),
                                refusal=RefusalDirective(RefusalKind.UNGROUNDED_CLAIM, {
                                    "detail_source": "substrate", "detail_layer": "grounding",
                                    "detail": "asserted relationship not present in the org",
                                    "edge_type": edge_type, "source": subject_refs[0], "target": subject_refs[1]}))

    def _field_governed_by_active_vr(self, field_entity, at: int) -> bool:
        """True iff an ACTIVE validation rule on the field's parent Object
        references this field (the T7 evidence-strength guard). Parent Object via
        the field's object-qualified api name; its APPLIES_TO neighbourhood carries
        the object's VRs. Formula parsed via the D-107 parser (walk for the field's
        bare name); a raw-substring fallback when the formula does not parse, so an
        unparseable rule (e.g. a REGEX format rule) still counts as governing."""
        api = field_entity.sf_api_name or ""
        if "." not in api:
            return False
        object_api = api.split(".", 1)[0]
        bare_l = api.rsplit(".", 1)[-1].lower()
        objs = self._admit.resolve_subject("Object", object_api, at)
        if not objs:
            return False
        for r in self._admit.scoped_neighborhood(objs[0], at):
            if (r.edge_type != EDGE_VALIDATION_RULE
                    or r.entity.entity_type != "ValidationRule"
                    or not vr_is_active(r.entity.attributes)):
                continue
            text = vr_formula_text(r.entity.attributes)
            if not text:
                continue
            ast = parse(text)
            if is_parsed(ast):
                if any(isinstance(n, FieldRef) and n.path
                       and n.path[-1].lower() == bare_l for n in walk(ast)):
                    return True
            elif bare_l in text.lower():
                return True
        return False

    def _resolve_existence(self, intent_input: dict, excerpt: str, at: int, state: Any) -> IntentResolution:
        """existence-claim (D-122): the asserted S1 entity exists (Layer-1-complete,
        D-079). A non-empty resolve grounds it; absent -> the org lacks it ->
        refuse (we never emit a false 'exists')."""
        hint = intent_input["intent_descriptor"].get("target_subject_hint") or {}
        entity_type, api = hint.get("entity_type"), hint.get("sf_api_name")
        subj = {"entity_type": entity_type, "sf_api_name": api}
        matches = self._admit.resolve_subject(entity_type, api, at)
        cand = _Candidate(path_id="c0", archetype="configuration", claim_kind="existence-claim",
                          subject_refs=[subj], requirement_anchor=excerpt, status="dismissed")
        if len(matches) > 1:
            cand.dismissal_reason = "ambiguous_target_resolution"
            return IntentResolution([], NextAction.REFUSE, self._delta([], [cand]),
                                    refusal=self._router.ambiguous(matches))
        if not matches:
            cand.dismissal_reason = "no_relevant_context"
            return IntentResolution([], NextAction.REFUSE, self._delta([], [cand]),
                                    refusal=self._router.no_relevant_context(
                                        f"{entity_type} {api!r} does not exist in the org"))
        e = matches[0]
        # Evidence strength (T7): a bare existence read on a field that an ACTIVE
        # validation rule GOVERNS is structural evidence masquerading as
        # behavioural coverage — "the field exists" never exercises the rule (e.g.
        # a format rule's regex), yet the AC would be tallied covered. Refuse so
        # the coverage map does not credit it; the recovery hop re-drives the model
        # toward a prohibition that provokes the rule.
        if e.entity_type == "Field" and self._field_governed_by_active_vr(e, at):
            cand.dismissal_reason = "insufficient_grounding"
            return IntentResolution([], NextAction.REFUSE, self._delta([], [cand]),
                                    refusal=self._router.emission_deferred(
                                        "configuration", "existence-claim",
                                        detail=(
                                            f"the field {e.sf_api_name} is governed by "
                                            f"an active validation rule; asserting only "
                                            f"that it exists does not exercise that rule "
                                            f"— refusing rather than count a metadata "
                                            f"read as behavioural coverage")))
        cand.status, cand.admissibility_layer = "admissibly_grounded", AdmissibilityLayer.LAYER_1.value
        if state is not None:
            _stash_grounding(state, GroundedExistence(
                archetype="configuration", claim_kind="existence-claim", version_seq=at,
                subject=_Endpoint(entity_id=e.id, entity_type=e.entity_type,
                                  external_id=e.sf_api_name or str(e.id)),
                requirement_excerpt=excerpt))
        presented = [PresentedCandidate(path_id="c0", admissibility_layer=AdmissibilityLayer.LAYER_1,
                     summary={"entity_type": e.entity_type, "sf_api_name": e.sf_api_name})]
        return IntentResolution(presented, NextAction.PROCEED_TO_EMIT, self._delta([], [cand]))

    def _resolve_property(self, intent_input: dict, excerpt: str, at: int, state: Any) -> IntentResolution:
        """property-claim (D-122): an S1-modeled detail property holds the asserted
        value (Layer-1-complete, D-079). Reads ``get_entity_details``; an unmodeled
        column (Tier-1 ceiling) or a value mismatch refuses — invent-nothing, the
        grounded value is READ from S1, never the assertion taken on faith."""
        hint = intent_input["intent_descriptor"].get("target_subject_hint") or {}
        entity_type, api = hint.get("entity_type"), hint.get("sf_api_name")
        property_name, asserted = hint.get("property_name"), hint.get("expected_value")
        subj = {"entity_type": entity_type, "sf_api_name": api}
        matches = self._admit.resolve_subject(entity_type, api, at)
        cand = _Candidate(path_id="c0", archetype="configuration", claim_kind="property-claim",
                          subject_refs=[subj], requirement_anchor=excerpt, status="dismissed")
        if len(matches) > 1:
            cand.dismissal_reason = "ambiguous_target_resolution"
            return IntentResolution([], NextAction.REFUSE, self._delta([], [cand]),
                                    refusal=self._router.ambiguous(matches))
        if not matches:
            cand.dismissal_reason = "no_relevant_context"
            return IntentResolution([], NextAction.REFUSE, self._delta([], [cand]),
                                    refusal=self._router.no_relevant_context(f"{entity_type} {api!r} did not resolve"))
        e = matches[0]
        details = self._s1.get_entity_details(e.id, at) or {}
        if property_name not in details:
            cand.dismissal_reason = "ontology_gap"
            return IntentResolution([], NextAction.REFUSE, self._delta([], [cand]),
                                    refusal=RefusalDirective(RefusalKind.UNGROUNDED_CLAIM, {
                                        "detail_source": "substrate", "detail_layer": "grounding",
                                    "detail": f"property {property_name!r} is not S1-modeled on "
                                                  f"{e.entity_type} (Tier-1 ceiling)", "property": property_name}))
        s1_value = details[property_name]
        # D-246: compare semantically against the S1-native type — a correct
        # value in a different representation ("8" vs int 8) grounds; a genuinely
        # different one ("9" vs 8) still dismisses. The grounded value emitted
        # below is always S1's (expected_value=s1_value), never the asserted.
        if asserted is not None and not _property_value_matches(asserted, s1_value):
            cand.dismissal_reason = "insufficient_grounding"
            return IntentResolution([], NextAction.REFUSE, self._delta([], [cand]),
                                    refusal=RefusalDirective(RefusalKind.UNGROUNDED_CLAIM, {
                                        "detail_source": "substrate", "detail_layer": "grounding",
                                    "detail": f"asserted {property_name}={asserted!r} but S1 holds {s1_value!r}",
                                        "property": property_name}))
        cand.status, cand.admissibility_layer = "admissibly_grounded", AdmissibilityLayer.LAYER_1.value
        if state is not None:
            _stash_grounding(state, GroundedProperty(
                archetype="configuration", claim_kind="property-claim", version_seq=at,
                subject=_Endpoint(entity_id=e.id, entity_type=e.entity_type,
                                  external_id=e.sf_api_name or str(e.id)),
                property_name=property_name, expected_value=s1_value, requirement_excerpt=excerpt))
        presented = [PresentedCandidate(path_id="c0", admissibility_layer=AdmissibilityLayer.LAYER_1,
                     summary={"entity_type": e.entity_type, "sf_api_name": e.sf_api_name,
                              "property": property_name, "value": s1_value})]
        return IntentResolution(presented, NextAction.PROCEED_TO_EMIT, self._delta([], [cand]))

    def _resolve_permission(self, intent_input: dict, ctx: ConversationContext, state: Any) -> IntentResolution:
        """capability-claim (D-080 / D-123): a Profile/PermissionSet grants the
        asserted capability (read/edit/...) on an Object/Field. Layer-1-complete
        (D-079): the S1 ``GRANTS_OBJECT_ACCESS`` / ``GRANTS_FIELD_ACCESS`` edge
        carries the capability bit; the grant edge present-and-set IS the full
        verification. v1 grounds **direct** grants only — a capability implied by
        sharing rules / OWD / role hierarchy has no direct grant edge (S1 Tier-2,
        unmodeled) and refuses here, never silently passes (the D-080 conservative
        posture). The grounded grant is READ from S1, never taken on the
        assertion's word (D-097.5)."""
        desc = intent_input["intent_descriptor"]
        excerpt = intent_input.get("requirement_excerpt", "")
        hint = desc.get("target_subject_hint") or {}
        at = ctx.semantic_context.s1_version_seq
        grantee_ref, target_ref = hint.get("grantee") or {}, hint.get("target") or {}
        capability = (hint.get("granted_capability") or "").strip().lower()
        grant_type = hint.get("grant_type")
        edge_type = EDGE_OBJECT_GRANT if grant_type == "object" else EDGE_FIELD_GRANT

        def _cand(reason=None, status="dismissed", layer=None) -> _Candidate:
            return _Candidate(
                path_id="c0", archetype="permission", claim_kind="capability-claim",
                subject_refs=[grantee_ref, target_ref], requirement_anchor=excerpt,
                status=status, admissibility_layer=layer, dismissal_reason=reason)

        # grant_type disambiguates the edge; anything else has no edge to inspect.
        if grant_type not in ("object", "field"):
            return IntentResolution([], NextAction.REFUSE, self._delta([], [_cand("type_incompatibility")]),
                                    refusal=self._router.underspecified(
                                        f"grant_type {grant_type!r} must be 'object' or 'field'"))
        # The asserted capability must map to a known S1 grant flag — else we
        # can't verify it (invent-nothing, ontology_gap rather than a guess).
        flag = _CAPABILITY_FLAG.get(capability)
        if flag is None:
            return IntentResolution([], NextAction.REFUSE, self._delta([], [_cand("ontology_gap")]),
                                    refusal=RefusalDirective(RefusalKind.UNGROUNDED_CLAIM, {
                                        "detail_source": "substrate", "detail_layer": "grounding",
                                    "detail": f"capability {capability!r} is not S1-modeled "
                                                  f"(known: {sorted(_CAPABILITY_FLAG)})",
                                        "capability": capability}))

        g_matches = self._admit.resolve_subject(grantee_ref.get("entity_type"), grantee_ref.get("sf_api_name"), at)
        t_matches = self._admit.resolve_subject(target_ref.get("entity_type"), target_ref.get("sf_api_name"), at)
        if len(g_matches) > 1 or len(t_matches) > 1:
            return IntentResolution([], NextAction.REFUSE, self._delta([], [_cand("ambiguous_target_resolution")]),
                                    refusal=self._router.ambiguous(g_matches + t_matches))
        if not g_matches or not t_matches:
            return IntentResolution([], NextAction.REFUSE, self._delta([], [_cand("no_relevant_context")]),
                                    refusal=self._router.no_relevant_context("grant endpoint did not resolve"))
        grantee, target = g_matches[0], t_matches[0]
        subject_refs = [
            {"entity_type": grantee.entity_type, "sf_api_name": grantee.sf_api_name},
            {"entity_type": target.entity_type, "sf_api_name": target.sf_api_name},
        ]

        # Layer-1-complete: the grant edge exists AND carries the asserted
        # capability bit. Edge absent OR bit unset -> the grant the claim assumes
        # is not present in the org -> ungrounded refusal (we never emit a false
        # 'is granted'). Direct grants only; no sharing/OWD synthesis (D-080).
        related = self._s1.get_related(grantee.id, [edge_type], "outbound", at_seq=at)
        match = next((r for r in related if r.entity.id == target.id), None)
        granted = bool(match and match.properties.get(flag))

        cand = _Candidate(path_id="c0", archetype="permission", claim_kind="capability-claim",
                          subject_refs=subject_refs, requirement_anchor=excerpt, status="dismissed")
        if granted:
            cand.status = "admissibly_grounded"
            cand.admissibility_layer = AdmissibilityLayer.LAYER_1.value
            # Stash the S1-resolved grounding for emission (D-097.5): finalize
            # authors the claim + recipe from these endpoints, never from the LLM.
            if state is not None:
                _stash_grounding(state, GroundedCapability(
                    archetype="permission", claim_kind="capability-claim", version_seq=at,
                    granting_subject=_Endpoint(
                        entity_id=grantee.id, entity_type=grantee.entity_type,
                        external_id=grantee.sf_api_name or str(grantee.id)),
                    target=_Endpoint(
                        entity_id=target.id, entity_type=target.entity_type,
                        external_id=target.sf_api_name or str(target.id)),
                    granted_capability=capability, grant_type=grant_type,
                    requirement_excerpt=excerpt))
            presented = [PresentedCandidate(
                path_id="c0", admissibility_layer=AdmissibilityLayer.LAYER_1,
                summary={"grantee": subject_refs[0], "target": subject_refs[1],
                         "capability": capability, "grant_type": grant_type})]
            return IntentResolution(presented, NextAction.PROCEED_TO_EMIT, self._delta(related, [cand]))
        cand.dismissal_reason = "insufficient_grounding"
        return IntentResolution([], NextAction.REFUSE, self._delta(related, [cand]),
                                refusal=RefusalDirective(RefusalKind.UNGROUNDED_CLAIM, {
                                    "detail_source": "substrate", "detail_layer": "grounding",
                                    "detail": f"{grantee.sf_api_name} does not grant {capability!r} on "
                                              f"{target.sf_api_name} (no direct grant edge or bit unset)",
                                    "capability": capability,
                                    "grantee": subject_refs[0], "target": subject_refs[1]}))

    def _resolve_ui(self, intent_input: dict, ctx: ConversationContext, state: Any) -> IntentResolution:
        """layout-claim (D-081 / D-124): a Field is placed on a PageLayout. Layer-1-
        complete (D-079): the S1 ``INCLUDES_FIELD`` edge (Layout -> Field) present
        IS the full verification — there is no capability-style bit; edge-existence
        IS the placement. Absent edge → the org does not place the field on that
        layout → ungrounded refusal (we never emit a false 'appears on'). v1 grounds
        placement-existence; section / row / column assertions are a v1.1 refinement.
        A metadata fact, not a UI interaction — the runtime render/enable surface is
        element-state-claim (Tier-3, deferred)."""
        desc = intent_input["intent_descriptor"]
        excerpt = intent_input.get("requirement_excerpt", "")
        hint = desc.get("target_subject_hint") or {}
        at = ctx.semantic_context.s1_version_seq
        layout_ref, field_ref = hint.get("layout") or {}, hint.get("field") or {}

        def _cand(reason=None, status="dismissed", layer=None) -> _Candidate:
            return _Candidate(
                path_id="c0", archetype="ui", claim_kind="layout-claim",
                subject_refs=[layout_ref, field_ref], requirement_anchor=excerpt,
                status=status, admissibility_layer=layer, dismissal_reason=reason)

        l_matches = self._admit.resolve_subject(layout_ref.get("entity_type"), layout_ref.get("sf_api_name"), at)
        f_matches = self._admit.resolve_subject(field_ref.get("entity_type"), field_ref.get("sf_api_name"), at)
        if len(l_matches) > 1 or len(f_matches) > 1:
            return IntentResolution([], NextAction.REFUSE, self._delta([], [_cand("ambiguous_target_resolution")]),
                                    refusal=self._router.ambiguous(l_matches + f_matches))
        if not l_matches or not f_matches:
            return IntentResolution([], NextAction.REFUSE, self._delta([], [_cand("no_relevant_context")]),
                                    refusal=self._router.no_relevant_context("layout endpoint did not resolve"))
        layout, field = l_matches[0], f_matches[0]
        subject_refs = [
            {"entity_type": layout.entity_type, "sf_api_name": layout.sf_api_name},
            {"entity_type": field.entity_type, "sf_api_name": field.sf_api_name},
        ]

        # Layer-1-complete: the INCLUDES_FIELD edge (Layout -> Field) exists or not.
        related = self._s1.get_related(layout.id, [EDGE_LAYOUT_FIELD], "outbound", at_seq=at)
        placed = any(r.entity.id == field.id for r in related)

        cand = _Candidate(path_id="c0", archetype="ui", claim_kind="layout-claim",
                          subject_refs=subject_refs, requirement_anchor=excerpt, status="dismissed")
        if placed:
            cand.status = "admissibly_grounded"
            cand.admissibility_layer = AdmissibilityLayer.LAYER_1.value
            # Stash the S1-resolved grounding for emission (D-097.5): finalize
            # authors the claim + recipe from these endpoints, never from the LLM.
            if state is not None:
                _stash_grounding(state, GroundedLayout(
                    archetype="ui", claim_kind="layout-claim", version_seq=at,
                    layout=_Endpoint(
                        entity_id=layout.id, entity_type=layout.entity_type,
                        external_id=layout.sf_api_name or str(layout.id)),
                    field=_Endpoint(
                        entity_id=field.id, entity_type=field.entity_type,
                        external_id=field.sf_api_name or str(field.id)),
                    requirement_excerpt=excerpt))
            presented = [PresentedCandidate(
                path_id="c0", admissibility_layer=AdmissibilityLayer.LAYER_1,
                summary={"layout": subject_refs[0], "field": subject_refs[1]})]
            return IntentResolution(presented, NextAction.PROCEED_TO_EMIT, self._delta(related, [cand]))
        cand.dismissal_reason = "insufficient_grounding"
        return IntentResolution([], NextAction.REFUSE, self._delta(related, [cand]),
                                refusal=RefusalDirective(RefusalKind.UNGROUNDED_CLAIM, {
                                    "detail_source": "substrate", "detail_layer": "grounding",
                                    "detail": f"{field.sf_api_name} is not placed on layout "
                                              f"{layout.sf_api_name} (no INCLUDES_FIELD edge)",
                                    "layout": subject_refs[0], "field": subject_refs[1]}))

    # -- refusal materialization ----------------------------------------
    def route_refusal(self, *, directive: RefusalDirective, ctx: ConversationContext, state: Any) -> GenerationOutcome:
        # D-376: refusal outcomes carry the shadow verdicts too (a copied ai —
        # the state's dict is not mutated). Hash-safe: explanation_hash
        # canonicalizes only its four fixed keys.
        ai = dict(state.attempted_interpretation)
        shadow_verdicts = list(getattr(state, "shadow_verdicts", None) or [])
        if shadow_verdicts:
            ai["shadow_resolution"] = shadow_resolution.attach_payload(
                shadow_verdicts)
        return GenerationOutcome(
            outcome_id=uuid4(), request_id=ctx.request_id, requirement_ref=ctx.requirement_ref,
            outcome_kind=OutcomeKind.REFUSAL,
            refusal_kind=directive.refusal_kind,
            refusal_policy_version=ctx.governance_context.refusal_policy_version,
            refusal_schema_version="v1",
            refusals=[RefusalEntry(refusal_kind=directive.refusal_kind, payload=directive.payload)],
            attempted_interpretation=AttemptedInterpretation(**ai),
            explanation_hash=compute_explanation_hash(ai),
            dismissal_taxonomy_version=ctx.governance_context.dismissal_taxonomy_version,
        )

    # -- emission (draft vertical: config metadata-relationship debut) --
    def accept_selection(self, *, selection_input, ctx, state) -> SelectionVerdict:
        """Layer-B reject-only floor over the canonical selection (D-096.3). The
        single-candidate paths auto-select (PROCEED_TO_EMIT) and never reach
        here; when >=2 candidates are presented, accept iff the LLM's chosen
        path is one the substrate actually presented. The chosen path is
        ``selection_rationale.selected_path_id`` per the D-086 select_canonical
        schema (the runtime passes that tool input verbatim). Reject-only:
        never authors or upgrades."""
        rationale = (selection_input or {}).get("selection_rationale") or {}
        chosen = rationale.get("selected_path_id")
        presented = {getattr(c, "path_id", None)
                     for c in getattr(state, "presented_candidates", []) or []}
        if chosen is None:
            return SelectionVerdict(accepted=False, finding=RefusalDirective(
                RefusalKind.STRUCTURAL_VALIDATION_FAILURE,
                {"reason": "selection carried no selected_path_id"}))
        if presented and chosen not in presented:
            return SelectionVerdict(accepted=False, finding=RefusalDirective(
                RefusalKind.STRUCTURAL_VALIDATION_FAILURE,
                {"reason": f"selected path {chosen!r} was not presented"}))
        return SelectionVerdict(accepted=True,
                                interpretation_delta={"selected_path_id": chosen})

    def finalize_outcome(self, *, outcome_input, ctx, state) -> OutcomeVerdict:
        """Author the draft: the substrate builds the S2 claim + recipe bodies
        from the grounded candidate (Guardrail 2 / D-097.5), applies the marker
        (D-097.3), and returns an OutcomeVerdict carrying the bodies. The
        persister writes claim + recipe + ledger atomically (D-097.4 / D-099);
        the LLM's ``outcome_input`` owns only linguistic realization, never
        truth or entities."""
        # All groundings stashed during resolve_intent (D-207: an ordered list,
        # one per admissibly-grounded intent). Legacy fallback: a caller (or
        # test) that still sets the pre-D-207 singular attributes gets them
        # honored as a one-element list.
        groundings = list(getattr(state, "groundings", None) or [])
        if not groundings:
            legacy = (getattr(state, "grounded_emission", None)
                      or getattr(state, "grounded_negative", None)
                      or getattr(state, "grounded_positive", None))
            if legacy is not None:
                groundings = [legacy]
        if not groundings:
            # Backstop (D-105.4): the emittability gate in resolve_intent should
            # have already refused a grounded-but-unbuilt kind. If we still reach
            # here (a gating gap), refuse gracefully — fail-loud, NOT batch-
            # destructive: a NotImplementedError would abort the whole batch,
            # whereas an emission-deferred refusal degrades just this requirement.
            paths = state.attempted_interpretation.get("candidate_paths") or [{}]
            p = paths[0]
            return OutcomeVerdict(override=self._router.emission_deferred(
                p.get("archetype", "(unknown)"), p.get("claim_kind", "(unknown)")))
        # D-300: the per-tenant bva-boundary flag rides the request's
        # OPERATIONAL context into authoring (identity-preserving by that
        # axis's contract); a ctx without the field (tests, legacy) reads OFF.
        enable_bva = getattr(getattr(ctx, "operational_context", None),
                             "enable_bva_boundaries", False)
        bundles = [author_emission(g, enable_bva_boundaries=enable_bva)
                   for g in groundings]

        # D-339: collapse re-proposal duplicates before persistence. The D-247
        # coverage re-prompt can re-send the FULL intent array, so a later
        # propose turn re-grounds intents already grounded on an earlier turn;
        # each accumulates a duplicate grounding (state.groundings) AND its
        # aligned presented_candidate, so finalize would otherwise author N
        # byte-identical claims (outcome ab65fb0c: 53 groundings -> 28 canonical
        # identities). Dedup by the EXISTING canonical identity
        # (compute_identity_hash — the very hash the persister dedups on,
        # persistence.py), keeping FIRST occurrence and its aligned
        # presented_candidate. Only runs on multi-bundle drafts (a single-bundle
        # draft is byte-identical to pre-D-339). Two genuinely different intents
        # that ground to the SAME identity collapse to one (correct — same
        # claim); the same subject with DIFFERENT semantic_conditions keeps
        # distinct hashes (both survive). The grounding<->presented_candidate
        # 1:1 alignment is an invariant (one grounded intent stashes one
        # grounding AND returns one PresentedCandidate; 0 mismatches across 72
        # historical drafts). If it is EVER violated we do NOT trim — a blind
        # index-trim could drop the wrong path_id — instead we preserve current
        # behavior and emit telemetry so the mismatch is observable.
        if len(bundles) > 1:
            presented = list(getattr(state, "presented_candidates", None) or [])
            if len(presented) != len(bundles):
                log.error(
                    "finalize_outcome D-339: grounding/presented_candidate "
                    "misalignment (bundles=%d, presented=%d) for request_id=%s "
                    "requirement=%s — skipping identity dedup to preserve "
                    "behavior", len(bundles), len(presented),
                    getattr(ctx, "request_id", None),
                    getattr(ctx, "requirement_ref", None) or {})
            else:
                bundles, kept_presented, dropped = _dedup_bundles_by_identity(
                    bundles, presented)
                if dropped:
                    # Retained presented_candidates ride back onto state so the
                    # selected_path_id(s) below describe exactly the emitted set.
                    state.presented_candidates = kept_presented
                    log.info(
                        "finalize_outcome D-339: collapsed %d duplicate emission "
                        "bundle(s) -> %d canonical identit%s for requirement=%s",
                        dropped, len(bundles),
                        "y" if len(bundles) == 1 else "ies",
                        getattr(ctx, "requirement_ref", None) or {})

        # Mark the canonical path(s) selected in the reasoning artifact. Single
        # intent keeps the pre-D-207 shape (selected_path_id="c0"); a multi-
        # intent draft has no single canonical path — it records the grounded
        # path ids under selected_path_ids instead (AttemptedInterpretation is
        # extra='allow').
        ai = dict(state.attempted_interpretation)
        if len(bundles) == 1:
            ai["selected_path_id"] = (state.presented_candidates[0].path_id
                                      if getattr(state, "presented_candidates", None)
                                      else "c0")
            delta = {"selected_path_id": ai["selected_path_id"]}
        else:
            ai["selected_path_id"] = None
            ai["selected_path_ids"] = [c.path_id for c in
                                       (getattr(state, "presented_candidates", None) or [])]
            delta = {"selected_path_ids": ai["selected_path_ids"]}

        # Control-telemetry Phase 0: the control-coverage map (stages through
        # EMITTED), from the stashed control facts + the deduped bundle set.
        # Read-only telemetry on a NEW ai key — explanation_hash canonicalizes
        # only its four fixed keys, so no outcome re-keys (test-asserted).
        # Absent facts (config-only paths, legacy states) attach nothing —
        # byte-identical to pre-telemetry outcomes.
        control_facts = list(getattr(state, "control_facts", None) or [])
        if control_facts:
            ai["control_coverage"] = control_coverage.coverage_from_bundles(
                control_facts, bundles)

        # D-376 shadow resolution: same read-only telemetry idiom — a NEW ai
        # key, hash-safe by the four-fixed-keys property (test-asserted).
        # Absent verdicts attach nothing (byte-identical outcomes).
        shadow_verdicts = list(getattr(state, "shadow_verdicts", None) or [])
        if shadow_verdicts:
            ai["shadow_resolution"] = shadow_resolution.attach_payload(
                shadow_verdicts)

        # The outcome-level marker aggregates CONSERVATIVELY across bundles
        # (D-207 decision 5): LAYER_2 only when every bundle verified; a caveat
        # on any bundle makes the outcome caveated. Per-bundle truth stays on
        # each bundle/claim (the recipes tell it per-claim).
        all_l2 = all(b.admissibility_layer == AdmissibilityLayer.LAYER_2 for b in bundles)
        caveat_required = any(b.caveat_required for b in bundles)
        caveat_kind = next((b.caveat_kind for b in bundles if b.caveat_required), None)
        outcome = GenerationOutcome(
            outcome_id=uuid4(), request_id=ctx.request_id,
            requirement_ref=ctx.requirement_ref,
            outcome_kind=OutcomeKind.DRAFT,
            # The marker (D-083 e / D-107): how deep grounding actually went,
            # read from the bundle(s) (authoring is the one site that knows).
            # LAYER_1 for a Layer-1-complete config claim and for a caveated
            # negative (formula unparsed/underivable); LAYER_2 when the
            # negative's VR formula parsed and a violating value derived with
            # certainty. The caveat posture (D-101.3) moves with the marker.
            # claims_written / recipes_written are assigned post-write (D-099).
            admissibility_layer=(AdmissibilityLayer.LAYER_2 if all_l2
                                 else AdmissibilityLayer.LAYER_1),
            caveat_required=caveat_required,
            caveat_kind=caveat_kind,
            attempted_interpretation=AttemptedInterpretation(**ai),
            explanation_hash=compute_explanation_hash(ai),
            dismissal_taxonomy_version=ctx.governance_context.dismissal_taxonomy_version,
        )
        return OutcomeVerdict(outcome=outcome, emission=bundles[0], emissions=bundles,
                              interpretation_delta=delta)

    # -- interpretation_delta assembly ----------------------------------
    @staticmethod
    def _delta(neighborhood: list, candidates: list[_Candidate]) -> dict:
        dismissed: dict[str, list[str]] = {}
        for c in candidates:
            if c.status == "dismissed" and c.dismissal_reason:
                dismissed.setdefault(c.dismissal_reason, []).append(c.path_id)
        return {
            "candidate_paths": [c.to_path() for c in candidates],
            "dismissed_alternatives_by_reason": dismissed,
            "scoped_neighborhood": [
                {"entity_type": r.entity.entity_type, "sf_api_name": r.entity.sf_api_name}
                for r in neighborhood
            ],
        }


def _stash_control_facts(state: Any, subject: Entity, neighborhood: list) -> None:
    """Control-telemetry Phase 0: accumulate the subject's control facts on the
    state for the finalize-time coverage map (read-only — observes, never
    selects/refuses). Dedup by (subject, control_ref): the D-247 re-prompt
    resolves many intents against the same subject; one fact set suffices."""
    if state is None:
        return
    if not hasattr(state, "control_facts") or state.control_facts is None:
        state.control_facts = []
    known = {(f.subject_ref, f.control_ref) for f in state.control_facts}
    for fact in control_coverage.controls_from_neighborhood(
            subject.sf_api_name or "", neighborhood):
        if (fact.subject_ref, fact.control_ref) not in known:
            state.control_facts.append(fact)


def _stash_grounding(state: Any, grounding: Any) -> None:
    """Append one intent's grounding to the state's ordered ``groundings`` list
    (D-207). ``finalize_outcome`` authors one bundle per entry, in propose
    order. Replaces the pre-D-207 singular ``grounded_emission`` /
    ``grounded_negative`` / ``grounded_positive`` stashes (which could carry
    only one grounding per requirement and silently overwrote on N)."""
    if state is None:
        return
    if not hasattr(state, "groundings") or state.groundings is None:
        state.groundings = []
    state.groundings.append(grounding)


def _dedup_bundles_by_identity(bundles: list, presented: list) -> tuple:
    """D-339: collapse authored bundles that share a canonical claim identity,
    keeping FIRST occurrence and its index-aligned presented_candidate.

    Identity is the EXISTING ``compute_identity_hash(archetype, claim_kind,
    asserted_truth, semantic_conditions)`` — the same fingerprint the persister
    dedups on (``persistence._write_emission``), so this reuses S2's canonical
    definition of "same claim" rather than inventing a second one. Two distinct
    intents that ground to the same claim collapse to one; the same subject with
    different ``semantic_conditions`` yields distinct hashes and both survive.

    Pure and order-preserving: callers own the caller-side alignment guard and
    telemetry. ``bundles`` and ``presented`` MUST be the same length and
    index-corresponding (finalize enforces this before calling). Returns
    ``(retained_bundles, retained_presented, dropped_count)``."""
    seen: set[str] = set()
    kept_bundles: list = []
    kept_presented: list = []
    dropped = 0
    for bundle, cand in zip(bundles, presented):
        h = compute_identity_hash(
            bundle.archetype, bundle.claim_kind,
            bundle.asserted_truth, bundle.semantic_conditions)
        if h in seen:
            dropped += 1
            continue
        seen.add(h)
        kept_bundles.append(bundle)
        kept_presented.append(cand)
    return kept_bundles, kept_presented, dropped


def _reindex_paths(res: IntentResolution, i: int) -> None:
    """Re-key one intent's path ids from the per-intent machinery's fixed
    ``c0`` to the intent's slot ``c{i}`` so a merged multi-intent delta stays
    collision-free (D-207). Mutates the resolution in place."""
    new_id = f"c{i}"
    d = res.interpretation_delta or {}
    for p in d.get("candidate_paths") or []:
        if p.get("path_id") == "c0":
            p["path_id"] = new_id
    dismissed = d.get("dismissed_alternatives_by_reason") or {}
    for reason, ids in dismissed.items():
        dismissed[reason] = [new_id if x == "c0" else x for x in ids]
    for c in res.grounded_candidates:
        if c.path_id == "c0":
            c.path_id = new_id


def _dismissed_stub(archetype, claim_kind, et, api, excerpt, reason) -> _Candidate:
    return _Candidate(
        path_id="c0", archetype=archetype or "data_behavior", claim_kind=claim_kind or "",
        subject_refs=[{"entity_type": et, "sf_api_name": api}],
        requirement_anchor=excerpt, status="dismissed", dismissal_reason=reason,
    )
