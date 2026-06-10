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
from primeqa.generation.emission import (
    GroundedCapability,
    GroundedEmission,
    GroundedExistence,
    GroundedLayout,
    GroundedNegative,
    GroundedPositive,
    GroundedProperty,
    _Endpoint,
    author_emission,
    is_emittable,
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
from primeqa.semantic.entity_attributes import vr_formula_text
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
    "prohibition-claim",
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
            # Shape-tolerant (D-203.1): pre-cutover rows carry the designed
            # `formula_text`; post-cutover sync rows carry the raw Tooling
            # record (Metadata.errorConditionFormula). Reading only the
            # former silently demoted every negative to caveated.
            text = vr_formula_text(r.entity.attributes)
            if text:
                formulas.append(text)
    return tuple(formulas)


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
                 path_id: str = "c0", field_hint: Optional[str] = None) -> _Candidate:
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
        return self._evaluate_positive(cand, claim_kind, neighborhood, field_hint)

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
                           field_hint: Optional[str] = None) -> _Candidate:
        # Positive grounding needs supporting structure (a Field BELONGS_TO the
        # subject Object). A **value-claim** asserts ``field == V``, so it grounds
        # only when the *named* field exists (verify-at-grounding, D-115.3): an
        # unknown named field (or none named) is ``insufficient_grounding``, never
        # an any-field pass. Other positive claim_kinds keep the object-level
        # any-field proxy (the refusal-vertical floor).
        fields = [r for r in neighborhood
                  if r.edge_type == EDGE_BELONGS and r.entity.entity_type == "Field"]
        if claim_kind == "value-claim":
            grounds = bool(field_hint) and any(
                r.entity.sf_api_name == field_hint for r in fields)
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

    def emission_deferred(self, archetype: str, claim_kind: str) -> RefusalDirective:
        """A groundable claim whose emission for this claim_kind isn't built yet
        (D-105). Operational/substrate-runtime: the requirement is admissible,
        but the emission machinery is deferred (D-097.6) — an honest capability
        boundary that lifts as kinds land, NOT an input-quality invalidity."""
        return RefusalDirective(RefusalKind.EMISSION_DEFERRED, {
            "detail": (f"{archetype}/{claim_kind} is groundable, but emission for "
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
        desc = intent_input.get("intent_descriptor") or {}
        hint = desc.get("target_subject_hint") or {}
        at = ctx.semantic_context.s1_version_seq
        if at is None:
            return RefCheck(ok=False, feedback="no s1_version_seq pinned in semantic_context")

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
        desc = intent_input.get("intent_descriptor") or {}
        excerpt = intent_input.get("requirement_excerpt", "")
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
                                    field_hint=hint.get("field_name"))
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

        # Stash grounding for the caveated prohibition-negative emission
        # (D-101.1), mirroring config's _resolve_configuration. Only
        # prohibition-claim emits in Phase 2 step 1; other data_behavior kinds
        # remain finalize-stubbed (the D-100 carve-out).
        if state is not None and claim_kind == "prohibition-claim":
            state.grounded_negative = GroundedNegative(
                archetype=archetype, claim_kind=claim_kind,
                operation_hint=hint.get("operation"), version_seq=at,
                subject=_Endpoint(
                    entity_id=subject.id, entity_type=subject.entity_type,
                    external_id=subject.sf_api_name or str(subject.id)),
                requirement_excerpt=excerpt,
                # Carry the grounding VRs' formulas so authoring can run the
                # D-107 verified-vs-caveated gate (re-found from the same
                # in-scope neighborhood Layer-1 grounding matched).
                vr_formulas=_grounding_vr_formulas(claim_kind, neighborhood))

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
            expected_value = hint.get("expected_value")
            if field_ent is None or expected_value is None:
                return IntentResolution(
                    grounded_candidates=[], next_action=NextAction.REFUSE,
                    interpretation_delta=delta,
                    refusal=self._router.emission_deferred(archetype, claim_kind))
            state.grounded_positive = GroundedPositive(
                archetype=archetype, claim_kind=claim_kind, version_seq=at,
                target_object=_Endpoint(
                    entity_id=subject.id, entity_type=subject.entity_type,
                    external_id=subject.sf_api_name or str(subject.id)),
                field=_Endpoint(
                    entity_id=field_ent.id, entity_type=field_ent.entity_type,
                    external_id=field_ent.sf_api_name or str(field_ent.id)),
                value=expected_value, requirement_excerpt=excerpt)

        # grounded -> emit deferred (draft vertical). resolve_intent stays whole.
        presented = [PresentedCandidate(path_id=c.path_id,
                                        admissibility_layer=AdmissibilityLayer(c.admissibility_layer),
                                        summary={"archetype": c.archetype, "claim_kind": c.claim_kind})
                     for c in grounded]
        nxt = NextAction.AWAIT_SELECTION if len(grounded) >= 2 else NextAction.PROCEED_TO_EMIT
        return IntentResolution(grounded_candidates=presented, next_action=nxt,
                                interpretation_delta=delta)

    # -- configuration metadata-relationship admissibility (D-098.1) ----
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
                state.grounded_emission = GroundedEmission(
                    archetype="configuration", claim_kind=claim_kind,
                    edge_type=edge_type, version_seq=at,
                    source=_Endpoint(
                        entity_id=source.id, entity_type=source.entity_type,
                        external_id=source.sf_api_name or str(source.id)),
                    target=_Endpoint(
                        entity_id=target.id, entity_type=target.entity_type,
                        external_id=target.sf_api_name or str(target.id)),
                    requirement_excerpt=excerpt)
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
            state.grounded_emission = GroundedExistence(
                archetype="configuration", claim_kind="existence-claim", version_seq=at,
                subject=_Endpoint(entity_id=e.id, entity_type=e.entity_type,
                                  external_id=e.sf_api_name or str(e.id)),
                requirement_excerpt=excerpt)
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
        if asserted is not None and s1_value != asserted:
            cand.dismissal_reason = "insufficient_grounding"
            return IntentResolution([], NextAction.REFUSE, self._delta([], [cand]),
                                    refusal=RefusalDirective(RefusalKind.UNGROUNDED_CLAIM, {
                                        "detail": f"asserted {property_name}={asserted!r} but S1 holds {s1_value!r}",
                                        "property": property_name}))
        cand.status, cand.admissibility_layer = "admissibly_grounded", AdmissibilityLayer.LAYER_1.value
        if state is not None:
            state.grounded_emission = GroundedProperty(
                archetype="configuration", claim_kind="property-claim", version_seq=at,
                subject=_Endpoint(entity_id=e.id, entity_type=e.entity_type,
                                  external_id=e.sf_api_name or str(e.id)),
                property_name=property_name, expected_value=s1_value, requirement_excerpt=excerpt)
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
                state.grounded_emission = GroundedCapability(
                    archetype="permission", claim_kind="capability-claim", version_seq=at,
                    granting_subject=_Endpoint(
                        entity_id=grantee.id, entity_type=grantee.entity_type,
                        external_id=grantee.sf_api_name or str(grantee.id)),
                    target=_Endpoint(
                        entity_id=target.id, entity_type=target.entity_type,
                        external_id=target.sf_api_name or str(target.id)),
                    granted_capability=capability, grant_type=grant_type,
                    requirement_excerpt=excerpt)
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
                state.grounded_emission = GroundedLayout(
                    archetype="ui", claim_kind="layout-claim", version_seq=at,
                    layout=_Endpoint(
                        entity_id=layout.id, entity_type=layout.entity_type,
                        external_id=layout.sf_api_name or str(layout.id)),
                    field=_Endpoint(
                        entity_id=field.id, entity_type=field.entity_type,
                        external_id=field.sf_api_name or str(field.id)),
                    requirement_excerpt=excerpt)
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
        # Config metadata-relationship (D-098), prohibition negative (D-101), or
        # positive value-claim (D-115); all author from S1 grounding stashed
        # during resolve_intent. (grounded_positive is dormant until the value-
        # claim grounding stash lands — D-115 slice 1 side A holds it; the read is
        # ready so the backstop stays correct when it does.)
        grounded = (getattr(state, "grounded_emission", None)
                    or getattr(state, "grounded_negative", None)
                    or getattr(state, "grounded_positive", None))
        if grounded is None:
            # Backstop (D-105.4): the emittability gate in resolve_intent should
            # have already refused a grounded-but-unbuilt kind. If we still reach
            # here (a gating gap), refuse gracefully — fail-loud, NOT batch-
            # destructive: a NotImplementedError would abort the whole batch,
            # whereas an emission-deferred refusal degrades just this requirement.
            paths = state.attempted_interpretation.get("candidate_paths") or [{}]
            p = paths[0]
            return OutcomeVerdict(override=self._router.emission_deferred(
                p.get("archetype", "(unknown)"), p.get("claim_kind", "(unknown)")))
        bundle = author_emission(grounded)

        # Mark the canonical path selected in the reasoning artifact.
        ai = dict(state.attempted_interpretation)
        ai["selected_path_id"] = "c0"
        outcome = GenerationOutcome(
            outcome_id=uuid4(), request_id=ctx.request_id,
            requirement_ref=ctx.requirement_ref,
            outcome_kind=OutcomeKind.DRAFT,
            # The marker (D-083 e / D-107): how deep grounding actually went,
            # read from the bundle (authoring is the one site that knows). LAYER_1
            # for the Layer-1-complete config claim and for a caveated negative
            # (formula unparsed/underivable); LAYER_2 when the negative's VR
            # formula parsed and a violating value derived with certainty. The
            # caveat posture (D-101.3) is the registry verdict the bundle carries
            # and moves with the marker: LAYER_2 <=> caveat dropped.
            # claims_written / recipes_written are assigned post-write (D-099).
            admissibility_layer=bundle.admissibility_layer,
            caveat_required=bundle.caveat_required,
            caveat_kind=bundle.caveat_kind,
            attempted_interpretation=AttemptedInterpretation(**ai),
            explanation_hash=compute_explanation_hash(ai),
            dismissal_taxonomy_version=ctx.governance_context.dismissal_taxonomy_version,
        )
        return OutcomeVerdict(outcome=outcome, emission=bundle,
                              interpretation_delta={"selected_path_id": "c0"})

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


def _dismissed_stub(archetype, claim_kind, et, api, excerpt, reason) -> _Candidate:
    return _Candidate(
        path_id="c0", archetype=archetype or "data_behavior", claim_kind=claim_kind or "",
        subject_refs=[{"entity_type": et, "sf_api_name": api}],
        requirement_anchor=excerpt, status="dismissed", dismissal_reason=reason,
    )
