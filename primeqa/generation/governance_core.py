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
    GroundedEmission,
    GroundedNegative,
    _Endpoint,
    author_emission,
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
                 path_id: str = "c0") -> _Candidate:
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
        return self._evaluate_positive(cand, claim_kind, neighborhood)

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

    def _evaluate_positive(self, cand: _Candidate, claim_kind: str, neighborhood: list) -> _Candidate:
        # v1 positive grounding (refusal-vertical proxy): a positive claim needs
        # supporting structure (a Field) in the neighborhood. Full positive
        # grounding is draft-vertical territory.
        fields = [r for r in neighborhood
                  if r.edge_type == EDGE_BELONGS and r.entity.entity_type == "Field"]
        if fields:
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
                                    neighborhood=neighborhood, excerpt=excerpt)
        candidates = self._decomp.enumerate_candidates(base)
        grounded = [c for c in candidates if c.status == "admissibly_grounded"]
        delta = self._delta(neighborhood, candidates)

        if not grounded:
            is_neg = self._admit.is_negative(claim_kind, polarity)
            directive = self._router.from_dismissed(base, is_negative=is_neg)
            return IntentResolution(grounded_candidates=[], next_action=NextAction.REFUSE,
                                    interpretation_delta=delta, refusal=directive)

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
                requirement_excerpt=excerpt)

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

        # Only metadata-relationship-claim is built for the config debut (D-098.1).
        if claim_kind != "metadata-relationship-claim":
            return IntentResolution([], NextAction.REFUSE, self._delta([], []),
                                    refusal=self._router.underspecified(
                                        f"configuration claim_kind {claim_kind!r} not yet supported "
                                        f"(debut: metadata-relationship-claim)"))

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
        """Layer-B reject-only floor over the canonical selection. The single-
        candidate config debut auto-selects (PROCEED_TO_EMIT) and never reaches
        here; when >=2 candidates are presented, accept iff the chosen path was
        one the substrate actually presented (never authors/upgrades, D-096.3)."""
        chosen = (selection_input or {}).get("path_id")
        presented = {getattr(c, "path_id", None)
                     for c in getattr(state, "presented_candidates", []) or []}
        if chosen is not None and presented and chosen not in presented:
            return SelectionVerdict(accepted=False, finding=RefusalDirective(
                RefusalKind.STRUCTURAL_VALIDATION_FAILURE,
                {"reason": f"selected path {chosen!r} was not presented"}))
        return SelectionVerdict(accepted=True,
                                interpretation_delta={"selected_path_id": chosen or "c0"})

    def finalize_outcome(self, *, outcome_input, ctx, state) -> OutcomeVerdict:
        """Author the draft: the substrate builds the S2 claim + recipe bodies
        from the grounded candidate (Guardrail 2 / D-097.5), applies the marker
        (D-097.3), and returns an OutcomeVerdict carrying the bodies. The
        persister writes claim + recipe + ledger atomically (D-097.4 / D-099);
        the LLM's ``outcome_input`` owns only linguistic realization, never
        truth or entities."""
        # Config metadata-relationship (D-098) or prohibition negative (D-101);
        # both author from S1 grounding stashed during resolve_intent.
        grounded = (getattr(state, "grounded_emission", None)
                    or getattr(state, "grounded_negative", None))
        if grounded is None:
            # The remaining data_behavior kinds (value-claim positives,
            # state-transition, automation-effect) stay stubbed — Phase 3+
            # (D-100) — rather than emit an unauthored draft.
            raise NotImplementedError(
                "emission is built for the config metadata-relationship debut "
                "(D-098/D-099) and the prohibition negative (D-101); other "
                "archetypes/kinds are deferred (D-100)")
        bundle = author_emission(grounded)

        # Mark the canonical path selected in the reasoning artifact.
        ai = dict(state.attempted_interpretation)
        ai["selected_path_id"] = "c0"
        outcome = GenerationOutcome(
            outcome_id=uuid4(), request_id=ctx.request_id,
            requirement_ref=ctx.requirement_ref,
            outcome_kind=OutcomeKind.DRAFT,
            # The marker (D-083 e): how deep grounding actually went — LAYER_1
            # for both the Layer-1-complete config claim and the Layer-1-
            # plausible negative. The caveat posture (D-101.3) is the registry
            # verdict: False/None for config, True/<kind> for the negative.
            # claims_written / recipes_written are assigned post-write (D-099).
            admissibility_layer=AdmissibilityLayer.LAYER_1,
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
