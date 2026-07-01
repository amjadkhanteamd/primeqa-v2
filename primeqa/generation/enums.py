"""Substrate-3 (Generation Engine) vocabulary enums.

The locked substrate-3 controlled vocabulary, per the Phase 1 design
(``docs/architecture/DECISIONS_LOG.md`` D-070 through D-094). Bounded,
governed, extended only through deliberate design cycles (the same
discipline substrate-2 applies to ``claim_kind`` per D-052/D-076).

Pure data contracts: no behavior. String-valued enums following
substrate-2's :class:`ArraySemantics` idiom (``class X(str, Enum)``),
so values serialize as their string form in JSONB and in the PG enum
columns the Slice-0 migration creates.

Slice 0 scope: vocabulary only. The orchestration, admissibility, tool
surface, and refusal-routing logic that *consume* this vocabulary are
later slices.
"""
from __future__ import annotations

from enum import Enum


class OutcomeKind(str, Enum):
    """The binary outcome discriminator per D-072.

    Every requirement in a request resolves to exactly one outcome — a
    draft (one or more claims emitted, possibly mixed with dedup
    matches) or a refusal. There is no third kind; dedup-against-existing
    is a form of draft (D-072), not a separate outcome.
    """

    DRAFT = "draft"
    REFUSAL = "refusal"


class AdmissibilityLayer(str, Enum):
    """Grounding-rigor layer surfaced at artifact top level per D-083 (e).

    ``layer_1`` — the constraint exists and is active (e.g., a validation
    rule applies; its formula is not parsed). ``layer_2`` — the
    constraint's semantic content confirms the asserted outcome. The
    marker is artifact-prominent so reviewers see the verification depth.
    """

    LAYER_1 = "layer_1"
    LAYER_2 = "layer_2"


class CaveatKind(str, Enum):
    """Typed epistemic-qualification class for a caveated emission (D-101.3).

    The caveat the substrate stamps on an emitted artifact records *why* the
    emission is epistemically qualified — a posture class, not a warning flag.
    Persisted at emission as the registry verdict snapshot (D-101.3); the
    semantic-completeness registry (D-097.3) remains the sole authority of the
    decision. ``caveat_kind`` is an enum, not a boolean, because the substrate
    models qualification *classes*: one value at v1; future causes (partial
    grounding, runtime approximation, ...) enter as distinct kinds.
    """

    # The deeper semantic-verification layer (Layer 2) is defined but its
    # formula parser is unbuilt (D-096.2 / D-100): grounding proved the
    # constraint EXISTS, not that its formula enforces the claimed outcome.
    DEEPER_VERIFICATION_LAYER_UNPARSED = "deeper_verification_layer_unparsed"


class RefusalKind(str, Enum):
    """The ten refusal kinds across three categories.

    Refusals are first-class outputs (D-070). Five invalidity kinds
    (D-073, Theme 1), three policy kinds (D-073 / D-083; D-293 behaviour-
    incomplete), two operational kinds (D-088 budget; D-105 emission-deferred).
    Each carries a typed feedback payload — deferred to the governance-core
    slice; this enum is the discriminator only. The category axis is documented
    inline, not modelled as a separate enum at this slice.
    """

    # invalidity — content / structure quality
    UNDERSPECIFIED_REQUIREMENT = "underspecified-requirement"
    NO_RELEVANT_CONTEXT = "no-relevant-context"
    AMBIGUOUS_REFERENCE = "ambiguous-reference"
    UNGROUNDED_CLAIM = "ungrounded-claim"
    STRUCTURAL_VALIDATION_FAILURE = "structural-validation-failure"
    # policy — substrate-deliberate restraint
    LOW_GENERATION_CONFIDENCE = "low-generation-confidence"
    NO_ADMISSIBLE_NEGATIVE_SCENARIO_FOUND = "no-admissible-negative-scenario-found"
    # D-293: a prohibition behaviour instance with no derivable behavioural reject
    # recipe (non-numeric VR; delete/share/transfer) — refuse, never degrade to
    # the caveated inspection.
    BEHAVIOUR_INCOMPLETE = "behaviour-incomplete"
    # operational — substrate-runtime constraint
    OPERATIONAL_BUDGET_EXHAUSTED = "operational-budget-exhausted"
    EMISSION_DEFERRED = "emission-deferred"   # D-105: groundable, but emission for this kind isn't built


class DismissalCategory(str, Enum):
    """Locked category axis for :class:`DismissalReason` per D-076.

    Five categories. CONFIDENCE is reserved with no v1 members — the
    architectural slot is held open for future evolution (D-076: confidence
    dismissals enter as the ``low-generation-confidence`` refusal kind in v1,
    not as an alternative-dismissal reason). The category is a property of
    each ``DismissalReason`` entry, "not an external mapping" (D-076), so it
    is modelled structurally rather than as an inline comment.
    """

    TOPOLOGY = "topology"
    ONTOLOGY_INVALIDITY = "ontology_invalidity"
    RANKING = "ranking"
    GOVERNANCE = "governance"
    CONFIDENCE = "confidence"  # reserved per D-076; no v1 reasons


class DismissalReason(str, Enum):
    """The substrate-3 reasoning vocabulary per D-076.

    The bounded set of reasons a candidate path is dismissed during the
    interpretation / grounding / governance phases (D-077). Participates
    in ``explanation_hash`` canonicalization (D-075) and lives inside the
    ``attempted_interpretation`` artifact (D-087 b) — not as a top-level
    ledger column. Eight values across five categories; the fifth
    (CONFIDENCE) is reserved with no v1 entries per D-076.

    Each member carries its locked :class:`DismissalCategory` as a genuine
    per-member property — D-076: the category is "a property of the entry,
    not an external mapping". The tuple-member form leaves ``_value_`` the
    bare reason string, so JSONB / PG-enum serialization is unaffected
    (``.value`` still round-trips to the string).
    """

    category: DismissalCategory

    def __new__(cls, value: str, category: DismissalCategory) -> "DismissalReason":
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.category = category
        return obj

    # TOPOLOGY — missing / insufficient S1 grounding
    INSUFFICIENT_GROUNDING = ("insufficient_grounding", DismissalCategory.TOPOLOGY)
    NO_GRANT_SUPPORTS_CAPABILITY = ("no_grant_supports_capability", DismissalCategory.TOPOLOGY)
    NO_CONSTRAINT_SUPPORTS_NEGATIVE = ("no_constraint_supports_negative", DismissalCategory.TOPOLOGY)
    # ONTOLOGY_INVALIDITY — substrate-taxonomy violation
    TYPE_INCOMPATIBILITY = ("type_incompatibility", DismissalCategory.ONTOLOGY_INVALIDITY)
    ARCHETYPE_MISMATCH = ("archetype_mismatch", DismissalCategory.ONTOLOGY_INVALIDITY)
    # RANKING — alternative preferred / no clean disambiguation
    AMBIGUOUS_TARGET_RESOLUTION = ("ambiguous_target_resolution", DismissalCategory.RANKING)
    LOWER_SPECIFICITY = ("lower_specificity", DismissalCategory.RANKING)
    # GOVERNANCE — would-be valid, below policy threshold
    POLICY_THRESHOLD_NOT_MET = ("policy_threshold_not_met", DismissalCategory.GOVERNANCE)


class RegenerationKind(str, Enum):
    """Typed regeneration-lineage discriminator per D-071.

    Six values across two categories. Semantic-continuity edges migrate
    into substrate-2 provenance when ``get_provenance`` ships; operational
    edges stay substrate-3-adjacent. Types the regeneration ``deltas``
    payload (structural at this slice).
    """

    # semantic-continuity edges
    CLARIFICATION = "clarification"
    GROUNDING_EVOLUTION = "grounding_evolution"
    REQUIREMENT_CHANGE = "requirement_change"
    # operational edges
    MODEL_EXPERIMENTATION = "model_experimentation"
    EVAL_REPLAY = "eval_replay"
    FAILURE_RECOVERY = "failure_recovery"


class LlmCallOutcome(str, Enum):
    """Operational outcome of a single LLM tool call per D-087 (b).

    Lives on the ``llm_calls`` operational-telemetry table — distinct from
    semantic provenance. ``rejected_for_correction`` covers Layer-A
    schema / vocabulary violations corrected mid-generation (D-088 a).
    """

    SUCCESS = "success"
    TRANSIENT_FAILURE = "transient_failure"
    OPERATIONAL_ERROR = "operational_error"
    REJECTED_FOR_CORRECTION = "rejected_for_correction"
