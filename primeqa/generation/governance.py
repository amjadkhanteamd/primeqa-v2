"""Substrate-3 governance seam (D-095.1 / the spine proposal).

The interface by which the orchestration spine (``runtime.py``) invokes
governance — admissibility, Layer B, refusal routing. The spine depends only
on this ``Protocol`` so it is independently testable now with governance
mocked; the governance-core slice provides the real implementation (real S1
grounding, admissibility computation, Layer-B semantic enforcement, the
refusal router).

The split this seam encodes (D-095.1):

  - ``check_refs_exist`` is the **operational** Layer-A grounding check
    (S1 entity-ref existence). Its result type (:class:`RefCheck`) carries
    NO semantic-provenance fields — a miss is an operational rejection
    (``rejected_for_correction`` -> ``structural-validation-failure``),
    never a semantic dismissal. It is reached as a Layer-A precondition,
    before the semantic seam.
  - ``resolve_intent`` / ``accept_selection`` / ``finalize_outcome`` are the
    **semantic** seam — reached only after all of Layer A passes. They author
    the semantic substance (candidates, admissibility_layer, dismissals,
    outcomes); the spine only *stores* their ``interpretation_delta`` into
    ``attempted_interpretation``.
  - ``route_refusal`` materializes a substrate-authored
    :class:`RefusalDirective` into a :class:`GenerationOutcome` — used for
    semantic refusals (resolve_intent -> REFUSE) and for spine-detected
    operational refusals (budget exhausted; persistent Layer-A failure ->
    ``structural-validation-failure``).

Result types reference the Slice-0 protocol types
(:class:`~primeqa.generation.protocol.GenerationOutcome` etc.). ``ctx`` is the
spine's :class:`ConversationContext` (defined here to avoid a runtime<->seam
import cycle); ``state`` is the spine's ``RequirementState`` (typed ``Any``
here for the same reason — the real impl may TYPE_CHECKING-import it).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Protocol

from primeqa.generation.enums import AdmissibilityLayer, RefusalKind
from primeqa.generation.protocol import (
    GenerationOutcome,
    GovernanceContext,
    OperationalContext,
    SemanticContext,
)


# ---------------------------------------------------------------------------
# Conversation context (per-requirement; injected into seam calls)
# ---------------------------------------------------------------------------

@dataclass
class ConversationContext:
    """What a per-requirement conversation needs (D-095.4). The bounded shared
    interpretation context is computed once per batch and injected here — no
    hidden shared conversational state across requirements."""

    request_id: Any
    requirement_ref: dict[str, Any]
    requirement_text: str
    semantic_context: SemanticContext
    governance_context: GovernanceContext
    operational_context: OperationalContext
    shared_context: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

class NextAction(str, Enum):
    """What the spine should do after the substrate processes an intent."""

    AWAIT_SELECTION = "await_selection"   # >=2 grounded candidates -> select_canonical
    PROCEED_TO_EMIT = "proceed_to_emit"   # exactly 1 grounded candidate -> auto-select -> emit
    REFUSE = "refuse"                     # 0 grounded / policy -> substrate-routed refusal


@dataclass
class RefCheck:
    """Operational Layer-A S1-ref-existence result (D-095.1).

    Operational identity is structural: this type has no semantic-provenance
    field. ``ok=False`` is an operational rejection only — the spine routes it
    to ``rejected_for_correction`` (and, on persistence,
    ``structural-validation-failure``); it is never recorded as a semantic
    dismissal in ``attempted_interpretation``.
    """

    ok: bool
    missing_refs: list[str] = field(default_factory=list)
    feedback: Optional[str] = None   # operational correction text for tool_result(is_error)
    # B0 grounded recovery: the near-miss candidate sets the substrate OFFERED
    # for this rejection's missing refs (``recovery.offer_payload`` dicts).
    # Telemetry-only — the model sees the same candidates inside ``feedback``;
    # the spine records offers onto the outcome's attempted_interpretation so
    # what-was-disclosed is auditable. Never a substitution channel.
    offers: list[dict] = field(default_factory=list)


@dataclass
class PresentedCandidate:
    """A substrate-derived, admissibly-grounded candidate presented back to the
    LLM. ``admissibility_layer`` is substrate-authored (D-086); the LLM
    transcribes it, never asserts it."""

    path_id: str
    admissibility_layer: AdmissibilityLayer
    summary: dict[str, Any] = field(default_factory=dict)   # display fields (archetype/claim_kind/subject)


@dataclass
class RefusalDirective:
    """A substrate-authored refusal instruction; ``route_refusal`` materializes
    it into a :class:`GenerationOutcome`."""

    refusal_kind: RefusalKind
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class IntentResolution:
    """Result of ``resolve_intent`` — the semantic processing of a proposed
    intent (D-086 step processing)."""

    grounded_candidates: list[PresentedCandidate]
    next_action: NextAction
    interpretation_delta: dict[str, Any] = field(default_factory=dict)  # spine STORES into attempted_interpretation
    refusal: Optional[RefusalDirective] = None                          # set iff next_action == REFUSE


@dataclass
class SelectionVerdict:
    """Result of ``accept_selection`` — Layer-B validation of the canonical
    selection."""

    accepted: bool
    interpretation_delta: dict[str, Any] = field(default_factory=dict)
    finding: Optional[RefusalDirective] = None   # Layer-B finding -> refusal


@dataclass
class OutcomeVerdict:
    """Result of ``finalize_outcome`` — Layer-B validation + construction of the
    draft outcome, or a governance override to refusal."""

    outcome: Optional[GenerationOutcome] = None     # validated draft GenerationOutcome
    interpretation_delta: dict[str, Any] = field(default_factory=dict)
    override: Optional[RefusalDirective] = None     # governance override -> refusal
    # Substrate-authored S2 bodies for the draft (an ``emission.EmissionBundle``;
    # typed Any to keep this seam free of an S2 import, matching ``state: Any``).
    # The persister writes them in one Session (D-097.4 / D-099); refs do not
    # exist until post-write. None on refusal/override.
    # D-207: ``emissions`` carries ALL bundles (one per grounded intent, in
    # propose order); ``emission`` stays as the first bundle for single-bundle
    # readers (always ``emissions[0]`` by construction in finalize_outcome).
    emission: Optional[Any] = None
    emissions: Optional[list[Any]] = None


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------

class GovernanceProvider(Protocol):
    """The governance interface the spine drives. Mocked for spine tests;
    governance-core implements it for real."""

    def check_refs_exist(
        self, *, intent_input: dict[str, Any], ctx: ConversationContext
    ) -> RefCheck:
        """Operational Layer-A: do the intent's S1 entity refs exist at the
        pinned ``s1_version_seq``? (D-095.1 / D-087). Operational only."""
        ...

    def resolve_intent(
        self, *, intent_input: dict[str, Any], ctx: ConversationContext, state: Any
    ) -> IntentResolution:
        """Semantic: derive candidates, compute admissibility (substrate-
        authored ``admissibility_layer``), record dismissals; decide
        await-selection / proceed-to-emit / refuse (D-086)."""
        ...

    def accept_selection(
        self, *, selection_input: dict[str, Any], ctx: ConversationContext, state: Any
    ) -> SelectionVerdict:
        """Semantic: Layer-B validation of the canonical selection against the
        substrate's view of the candidates (D-087)."""
        ...

    def finalize_outcome(
        self, *, outcome_input: dict[str, Any], ctx: ConversationContext, state: Any
    ) -> OutcomeVerdict:
        """Semantic: Layer-B validation of the emitted draft + construction of
        the :class:`GenerationOutcome` (D-086 / D-072), or a governance
        override to refusal."""
        ...

    def route_refusal(
        self, *, directive: RefusalDirective, ctx: ConversationContext, state: Any
    ) -> GenerationOutcome:
        """Materialize a substrate-authored refusal directive into a
        :class:`GenerationOutcome` (the refusal router). Used for semantic
        refusals and for spine-detected operational refusals."""
        ...
