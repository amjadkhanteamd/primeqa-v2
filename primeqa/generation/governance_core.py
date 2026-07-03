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

from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import uuid4

from primeqa.generation.enums import AdmissibilityLayer, OutcomeKind, RefusalKind
from primeqa.generation.explanation_hash import compute_explanation_hash
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
from primeqa.generation.verified_negative import _writable
from primeqa.semantic.entity_attributes import (
    field_is_calculated, vr_error_message, vr_formula_text, vr_is_active)
from primeqa.semantic.formula import Comparison, FieldRef, is_parsed, parse, walk
from primeqa.semantic.query import Entity, SemanticOrgModel


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


def _ground_rejection_conditions(proposed, neighborhood: list, version_seq: int):
    """Ground each LLM-proposed prohibition rejection-condition clause (D-293,
    Option A — the business STATE under which the rejection is asserted). A clause
    is ``{field: "Object.Field", predicate, value}``; its field must BELONG_TO the
    subject (verified in the scoped neighborhood) and its predicate/value must
    satisfy the S2 ``Condition`` coupling. Returns ``(grounded, invalid)`` —
    ``invalid`` is a list of human reasons; a non-empty list means the caller
    refuses (invent-nothing). Empty ``proposed`` -> ``([], [])``: the dormant
    default, byte-identical to the pre-D-293 condition-free prohibition."""
    fields_by_name = {
        r.entity.sf_api_name: r.entity for r in neighborhood
        if r.edge_type == EDGE_BELONGS and r.entity.entity_type == "Field"}
    grounded: list = []
    invalid: list[str] = []
    for clause in (proposed or []):
        c = clause or {}
        fld, predicate, value = c.get("field"), c.get("predicate"), c.get("value")
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
        if predicate in _CONDITION_VALUE_FREE:
            if value is not None:
                invalid.append(f"predicate {predicate!r} forbids a value"); continue
        elif predicate in _CONDITION_VALUE_BEARING:
            if value is None:
                invalid.append(f"predicate {predicate!r} requires a value"); continue
        else:
            invalid.append(f"predicate {predicate!r} not in the condition taxonomy"); continue
        grounded.append(_GroundedCondition(
            field=_Endpoint(entity_id=ent.id, entity_type=ent.entity_type,
                            external_id=ent.sf_api_name or str(ent.id)),
            predicate=predicate, value=value))
    return grounded, invalid


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


def _claim_condition_fields(grounded_conds) -> frozenset[str]:
    """The bare, lower-cased field api-names a prohibition claim's grounded
    conditions reference (D-295 — the LEFT side of the VR field-overlap match).
    ``_GroundedCondition.field.external_id`` is object-qualified (``Object.Field``,
    ``governance_core.py`` grounding); the bare tail (``rsplit('.',1)[-1]``) is what
    the VR formula parser speaks, so both sides of the overlap are normalized alike."""
    return frozenset(
        gc.field.external_id.rsplit(".", 1)[-1].lower()
        for gc in (grounded_conds or [])
        if gc.field and gc.field.external_id)


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
    Callers apply the degenerate guard (empty conds / single VR) before this."""
    claim_fields = _claim_condition_fields(grounded_conds)
    if not claim_fields:
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
    return _break_tie_by_cross_field(tied, claim_fields)   # D-296; None if non-unique


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
    return out


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

    def is_negative(self, claim_kind: str, polarity_hint: str) -> bool:
        return claim_kind in _INHERENTLY_NEGATIVE or polarity_hint == "negative"

    def evaluate(self, *, archetype: str, claim_kind: str, polarity_hint: str,
                 subject: Entity, neighborhood: list, excerpt: str,
                 path_id: str = "c0", field_hint: Optional[str] = None,
                 automation_hint: Optional[str] = None) -> _Candidate:
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
                                       automation_hint)

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
                           automation_hint: Optional[str] = None) -> _Candidate:
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
            else:
                grounds = bool(flows)
        else:
            grounds = bool(fields)
        if grounds:
            cand.status = "admissibly_grounded"
            cand.admissibility_layer = AdmissibilityLayer.LAYER_1.value
        else:
            cand.dismissal_reason = "insufficient_grounding"
        return cand


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
    def underspecified(self, reason: str = "no claim_kind to anchor a candidate") -> RefusalDirective:
        return RefusalDirective(RefusalKind.UNDERSPECIFIED_REQUIREMENT, {"detail": reason})

    def ambiguous(self, matches: list[Entity]) -> RefusalDirective:
        return RefusalDirective(RefusalKind.AMBIGUOUS_REFERENCE, {
            "matched": [{"entity_type": m.entity_type, "sf_api_name": m.sf_api_name,
                         "id": str(m.id)} for m in matches],
        })

    def no_relevant_context(self, detail: str) -> RefusalDirective:
        return RefusalDirective(RefusalKind.NO_RELEVANT_CONTEXT, {"detail": detail})

    def behaviour_incomplete(self, detail: str) -> RefusalDirective:
        """D-293 decision-2: a prohibition intent that is not a COMPLETE behaviour
        instance — no derivable behavioural reject recipe from the grounding VR(s)
        (non-numeric formula, or a non-VR-rejectable operation like delete/share/
        transfer) — refuses HERE rather than degrading to the caveated metadata
        inspection (the pre-D-293 fallback that masked the AC1/2/4 collapse). A
        policy refusal: the requirement is admissible, but the substrate declines
        to author a behaviourally-empty prohibition. Lifts as violation-derivation
        widens (the out-of-scope D-293 follow-on)."""
        return RefusalDirective(RefusalKind.BEHAVIOUR_INCOMPLETE, {"detail": detail})

    def emission_deferred(self, archetype: str, claim_kind: str,
                          detail: Optional[str] = None) -> RefusalDirective:
        """A groundable claim whose emission for this claim_kind isn't built yet
        (D-105). Operational/substrate-runtime: the requirement is admissible,
        but the emission machinery is deferred (D-097.6) — an honest capability
        boundary that lifts as kinds land, NOT an input-quality invalidity.
        ``detail`` overrides the generic message when a SPECIFIC sub-shape
        defers (D-210.1 — e.g. cross-object transitions)."""
        return RefusalDirective(RefusalKind.EMISSION_DEFERRED, {
            "detail": detail or (
                f"{archetype}/{claim_kind} is groundable, but emission for "
                f"this claim_kind is not yet built"),
            "archetype": archetype,
            "claim_kind": claim_kind,
        })

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
                "cause": cause,
                "proposed_negative_assertion": {"claim_kind": cand.claim_kind,
                                                "subject_refs": cand.subject_refs},
                "searched_constraint_dimensions": [dim[1]] if dim else [],
                "no_grounding_found_because": reason,
                "what_would_unblock": what_unblocks,
            })
        # positive ungrounded, or meaningfulness mismatch -> ungrounded-claim
        return RefusalDirective(RefusalKind.UNGROUNDED_CLAIM, {
            "claim_kind": cand.claim_kind,
            "subject_refs": cand.subject_refs,
            "dismissal_reason": reason,
        })


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

    # -- Layer A operational ref-existence (D-095.1) --------------------
    def check_refs_exist(self, *, intent_input: dict, ctx: ConversationContext) -> RefCheck:
        at = ctx.semantic_context.s1_version_seq
        if at is None:
            return RefCheck(ok=False, feedback="no s1_version_seq pinned in semantic_context")
        # D-207: one propose call may carry N intents; every intent's refs must
        # resolve (any miss is one operational correction — the model fixes the
        # offending descriptor and retries the whole call).
        per_intent = normalize_propose_input(intent_input)
        if len(per_intent) == 1:
            return self._check_refs_one(per_intent[0], at)
        failures = [(i, rc) for i, rc in
                    ((i, self._check_refs_one(pi, at)) for i, pi in enumerate(per_intent))
                    if not rc.ok]
        if not failures:
            return RefCheck(ok=True)
        missing = [m for _, rc in failures for m in rc.missing_refs]
        feedback = "; ".join(f"intent[{i}]: {rc.feedback}" for i, rc in failures)
        return RefCheck(ok=False, missing_refs=missing, feedback=feedback)

    def _check_refs_one(self, intent_input: dict, at: int) -> RefCheck:
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
                    return RefCheck(ok=False, missing_refs=[f"{et}:{api}"],
                                    feedback=f"subject not found at s1_version_seq {at}: {et}:{api}")
                return RefCheck(ok=True)
            missing: list[str] = []
            for label in ("source", "target"):
                ep = hint.get(label) or {}
                et, api = ep.get("entity_type"), ep.get("sf_api_name")
                if not et or not api or not self._admit.resolve_subject(et, api, at):
                    missing.append(f"{label}:{et}:{api}")
            if missing:
                return RefCheck(ok=False, missing_refs=missing,
                                feedback=f"relationship endpoint(s) not found at s1_version_seq {at}: {missing}")
            return RefCheck(ok=True)

        # permission capability-claim (D-123): two endpoints — grantee + target —
        # carried under target_subject_hint, not the flat {entity_type, sf_api_name}.
        if desc.get("archetype_hint") == "permission":
            missing = []
            for label in ("grantee", "target"):
                ep = hint.get(label) or {}
                et, api = ep.get("entity_type"), ep.get("sf_api_name")
                if not et or not api or not self._admit.resolve_subject(et, api, at):
                    missing.append(f"{label}:{et}:{api}")
            if missing:
                return RefCheck(ok=False, missing_refs=missing,
                                feedback=f"grant endpoint(s) not found at s1_version_seq {at}: {missing}")
            return RefCheck(ok=True)

        # ui layout-claim (D-124): two endpoints — layout + field — carried under
        # target_subject_hint, not the flat {entity_type, sf_api_name}.
        if desc.get("archetype_hint") == "ui":
            missing = []
            for label in ("layout", "field"):
                ep = hint.get(label) or {}
                et, api = ep.get("entity_type"), ep.get("sf_api_name")
                if not et or not api or not self._admit.resolve_subject(et, api, at):
                    missing.append(f"{label}:{et}:{api}")
            if missing:
                return RefCheck(ok=False, missing_refs=missing,
                                feedback=f"layout endpoint(s) not found at s1_version_seq {at}: {missing}")
            return RefCheck(ok=True)

        et, api = hint.get("entity_type"), hint.get("sf_api_name")
        if not et or not api:
            return RefCheck(ok=False, feedback=(
                "target_subject_hint must be an entity ref {entity_type, sf_api_name}; "
                "descriptive selectors are not yet supported (query_entities deferred)"))
        matches = self._admit.resolve_subject(et, api, at)
        if not matches:
            return RefCheck(ok=False, missing_refs=[f"{et}:{api}"],
                            feedback=f"no {et} named {api!r} exists at s1_version_seq {at}")
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
            # decision 7) — intent-scoped selection is deliberately unbuilt.
            assert len(res.grounded_candidates) <= 1, \
                "multi-intent propose met >1 grounded candidate for one intent"
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
        hint = desc.get("target_subject_hint") or {}
        et, api = hint.get("entity_type"), hint.get("sf_api_name")
        at = ctx.semantic_context.s1_version_seq

        matches = self._admit.resolve_subject(et, api, at) if (et and api and at is not None) else []

        # ambiguity (interpretation phase: ambiguous_target_resolution)
        if len(matches) > 1:
            delta = self._delta(neighborhood=[], candidates=[_dismissed_stub(
                archetype, claim_kind, et, api, excerpt, "ambiguous_target_resolution")])
            return IntentResolution(grounded_candidates=[], next_action=NextAction.REFUSE,
                                    interpretation_delta=delta, refusal=self._router.ambiguous(matches))
        if not matches:
            # post-check_refs_exist this is defensive
            return IntentResolution(grounded_candidates=[], next_action=NextAction.REFUSE,
                                    interpretation_delta=self._delta([], []),
                                    refusal=self._router.no_relevant_context(
                                        f"subject {et}:{api} did not resolve at version {at}"))
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
        base = self._admit.evaluate(archetype=archetype, claim_kind=claim_kind,
                                    polarity_hint=polarity, subject=subject,
                                    neighborhood=neighborhood, excerpt=excerpt,
                                    field_hint=hint.get("field_name"),
                                    automation_hint=hint.get("automation_name"))
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
                return IntentResolution(
                    grounded_candidates=[], next_action=NextAction.REFUSE,
                    interpretation_delta=delta,
                    refusal=self._router.no_relevant_context(
                        f"rejection-condition not grounded on "
                        f"{subject.sf_api_name}: {invalid_conds}"))
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
            # D-293 decision-2 (refuse, never silently degrade): the behaviour
            # instance is complete only when a BEHAVIOURAL reject recipe is
            # derivable. A non-numeric VR (NOT-ISBLANK / picklist / cross-field) or
            # a non-VR-rejectable operation (delete / share / transfer) derives
            # nothing -> REFUSE here, rather than emitting the pre-D-293 caveated
            # inspection (which masked the AC1/2/4 collapse and degraded AC3 to a
            # bare existence check). Conditions de-collapse identity (Reading B);
            # derivability is the hard gate. D-294 widens what derives via
            # field_metadata (dormant here).
            if not prohibition_recipe_derivable(
                    hint.get("operation"), vr_formulas, field_metadata):
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
            if field_ent is None or expected_value is None:
                return IntentResolution(
                    grounded_candidates=[], next_action=NextAction.REFUSE,
                    interpretation_delta=delta,
                    refusal=self._router.emission_deferred(archetype, claim_kind))
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
                return IntentResolution(
                    grounded_candidates=[], next_action=NextAction.REFUSE,
                    interpretation_delta=delta,
                    refusal=self._router.emission_deferred(
                        archetype, claim_kind,
                        detail="; ".join(invalid)))
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
            _stash_grounding(state, GroundedAcceptance(
                archetype=archetype, claim_kind=claim_kind, version_seq=at,
                subject=_Endpoint(
                    entity_id=subject.id, entity_type=subject.entity_type,
                    external_id=subject.sf_api_name or str(subject.id)),
                requirement_excerpt=excerpt,
                conditions=tuple(grounded_conds),
                update_conditions=grounded_upd))

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
                return IntentResolution(
                    grounded_candidates=[], next_action=NextAction.REFUSE,
                    interpretation_delta=delta,
                    refusal=self._router.emission_deferred(
                        archetype, claim_kind,
                        detail=("state-transition needs a verifiable to-state: "
                                "field_name (existing on the subject) + "
                                "expected_value")))
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
            if automation_name:
                flow_ent = next(
                    (f for f in flows if f.sf_api_name == automation_name), None)
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
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind,
                            detail=(f"the requirement-named automation "
                                    f"{automation_name!r} is neither a Flow "
                                    f"that TRIGGERS_ON the subject (found "
                                    f"{sorted(f.sf_api_name for f in flows)}) "
                                    f"nor an active approval process on it "
                                    f"nor a calculated field on it")))
            else:
                flow_ent = flows[0] if flows else None
            if flow_ent is None and formula_ent is None:
                # defensive: positives admit on this edge; negatives may reach
                # here without one
                return IntentResolution(
                    grounded_candidates=[], next_action=NextAction.REFUSE,
                    interpretation_delta=delta,
                    refusal=self._router.emission_deferred(
                        archetype, claim_kind,
                        detail="no record-triggered Flow on the subject"))
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
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind,
                            detail=(f"effect_via_lookup_field {via_name!r} "
                                    f"does not exist on the subject — cannot "
                                    f"link the trigger record to the effect "
                                    f"parent")))
                grounded_eff = self._ground_cross_object_effect(
                    hint, effect_object_api, at, lookup_required=False)
                if isinstance(grounded_eff, str):
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind, detail=grounded_eff))
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
                    # D-307: the entry gate is on the SUBJECT create in the
                    # parent-stamp shape too — without it the flow never
                    # stamps. Same D-299 rail (BELONGS_TO verify, drop-never-
                    # refuse); the subject BELONGS_TO map is the k16 guard
                    # (the stamped field lives on ANOTHER object, so it can
                    # never verify as a subject trigger) — the explicit
                    # exclude is defense-in-depth.
                    trigger_fields=_ground_trigger_fields(
                        hint.get("trigger_fields"), neighborhood,
                        exclude_field=hint.get("effect_field")),
                    # D-308: the bound primitive (the same-record stash always
                    # passed it; the cross-object/parent-stamp stashes relied
                    # on the "flow" default — wrong the moment an approval
                    # binds).
                    automation_primitive=primitive))
            elif effect_object_api:
                grounded_eff = self._ground_cross_object_effect(
                    hint, effect_object_api, at)
                if isinstance(grounded_eff, str):       # the deferral detail
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind, detail=grounded_eff))
                eff_ep, lookup_ep, eff_field_ep = grounded_eff
                # D-307: the cross-object flow fires only when the SUBJECT
                # create reaches its entry gate (the L7e live recon: a
                # padding-only create never provokes the Task) — the same
                # D-299 rail as above.
                xo_triggers = _ground_trigger_fields(
                    hint.get("trigger_fields"), neighborhood,
                    exclude_field=hint.get("effect_field"))
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
                _stash_grounding(state, GroundedAutomationEffect(
                    archetype=archetype, claim_kind=claim_kind, version_seq=at,
                    subject=subj_ep, automation=flow_ep,
                    requirement_excerpt=excerpt,
                    effect_field=eff_field_ep,
                    effect_value=_identity_safe(hint.get("effect_value")),
                    effect_object=eff_ep, effect_lookup_field=lookup_ep,
                    trigger_fields=xo_triggers,
                    automation_primitive=primitive,     # D-308 (see above)
                    # D-307: the absence mirror (gated fail-closed above —
                    # cross-object, no effect_field/effect_value, strict bool).
                    expected_absence=expected_absence))
            else:
                field_ent = next(
                    (r.entity for r in neighborhood
                     if r.edge_type == EDGE_BELONGS and r.entity.entity_type == "Field"
                     and r.entity.sf_api_name == hint.get("field_name")), None)
                effect_value = _identity_safe(hint.get("expected_value"))
                if field_ent is None or effect_value is None:
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind,
                            detail=("automation-effect needs a verifiable "
                                    "effect: field_name + expected_value on "
                                    "the subject, or effect_object + "
                                    "effect_lookup_field")))
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
                    return IntentResolution(
                        grounded_candidates=[], next_action=NextAction.REFUSE,
                        interpretation_delta=delta,
                        refusal=self._router.emission_deferred(
                            archetype, claim_kind,
                            detail=("update_trigger_fields did not ground — "
                                    "the recalculate-on-change premise needs "
                                    "at least one verified (field, value) "
                                    "pair that is not the observed field")))
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
                    update_trigger_fields=update_trigger_fields))

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

    def _ground_cross_object_effect(self, hint: dict, effect_object_api: str,
                                    at: int, *, lookup_required: bool = True):
        """Verify the cross-object effect names against S1 (D-210.1): the
        effect Object must resolve uniquely; the lookup field (and the optional
        asserted effect field) must BELONG_TO it. Returns
        ``(effect_ep, lookup_ep_or_None, effect_field_ep_or_None)`` on success,
        or the deferral-detail STRING on any unverifiable name — the caller
        routes it to emission-deferred (never guesses). ``lookup_required=False``
        is the D-227 parent-stamp path (the correlate is the SUBJECT's own
        ``effect_via_lookup_field``, verified by the caller)."""
        matches = self._admit.resolve_subject("Object", effect_object_api, at)
        if len(matches) != 1:
            return (f"effect object {effect_object_api!r} did not resolve "
                    f"uniquely in the org model ({len(matches)} matches)")
        eff = matches[0]
        eff_neigh = self._admit.scoped_neighborhood(eff, at)
        eff_fields = {r.entity.sf_api_name: r.entity for r in eff_neigh
                      if r.edge_type == EDGE_BELONGS
                      and r.entity.entity_type == "Field"}
        lookup_name = hint.get("effect_lookup_field")
        lookup_ent = eff_fields.get(lookup_name) if lookup_name else None
        if lookup_ent is None and lookup_required:
            return (f"effect lookup field {lookup_name!r} does not exist on "
                    f"{effect_object_api} — cannot correlate the effect record "
                    f"to the trigger record")
        eff_field_name = hint.get("effect_field")
        eff_field_ep = None
        if eff_field_name is not None:
            eff_field_ent = eff_fields.get(eff_field_name)
            if eff_field_ent is None:
                return (f"effect field {eff_field_name!r} does not exist on "
                        f"{effect_object_api}")
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
        return IntentResolution(
            grounded_candidates=[], next_action=NextAction.REFUSE,
            interpretation_delta=self._delta([], [cand]),
            refusal=self._router.no_relevant_context(reason))

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
                                    "detail": "asserted relationship not present in the org",
                                    "edge_type": edge_type, "source": subject_refs[0], "target": subject_refs[1]}))

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
                                    "detail": f"{field.sf_api_name} is not placed on layout "
                                              f"{layout.sf_api_name} (no INCLUDES_FIELD edge)",
                                    "layout": subject_refs[0], "field": subject_refs[1]}))

    # -- refusal materialization ----------------------------------------
    def route_refusal(self, *, directive: RefusalDirective, ctx: ConversationContext, state: Any) -> GenerationOutcome:
        ai = state.attempted_interpretation
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
