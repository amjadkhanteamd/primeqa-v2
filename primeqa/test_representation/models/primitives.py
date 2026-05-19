"""Shared value-typed primitives for claim body shapes.

Per SPEC §3 (archetype representation — claim bodies are
archetype-specific semantic forms) + §4.7.2 (Pydantic patterns) +
D-058 §5.4 (coverage extraction walks IdentityBearingRef instances).

The primitives in this module compose into the four data-behavior
claim bodies (value-claim, state-transition-claim,
automation-effect-claim, prohibition-claim). They're factored out
because:

  - **ExpectedValue** appears in :class:`ValueClaimBody` directly and
    inside :class:`StateDescriptor.field_values`. One source of truth
    for "the value a field should hold."
  - **StateDescriptor** describes a record-shape state (what fields
    have what values). Used by :class:`StateTransitionClaimBody`
    (from_state / to_state) and inside :class:`FieldChangeEffect`.
  - **EventDescriptor** describes the causal action that triggers
    the claim's evaluation. Used by both
    :class:`StateTransitionClaimBody` and
    :class:`AutomationEffectClaimBody`.
  - **EffectDescriptor** describes what an automation produced.
    Used by :class:`AutomationEffectClaimBody`.
  - **RejectionSignal** describes how a prohibited operation was
    rejected. Used by :class:`ProhibitionClaimBody`.

Per the B-α empirical finding: any field declared with type
``IdentityBearingRef`` is constructed by Pydantic as an
``IdentityBearingRef`` instance during validation (not a plain
``PinnedRef``). The discriminator value ``"pinned"`` is shared
between the two; the Python type is the discriminator.

All primitives are frozen post-construction per D-061 §7.2 — once
a claim body has passed validation, its embedded primitives are
content-addressable and must not mutate.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from primeqa.test_representation.models.references import IdentityBearingRef


# ---------------------------------------------------------------------------
# ExpectedValue — discriminated union over literal vs null
#
# Per SPEC §3 / §6.3.2: the canonical form of a "what value should
# this field hold" assertion. Concrete kinds:
#   - LiteralValue: an explicit value (any JSON-serializable type)
#   - NullValue: the field should be NULL / unset
#
# A ComputedValue variant (e.g., references another field, applies a
# transform) is intentionally deferred — adding it later is additive
# on the discriminator and does not change existing payload shapes.
# ---------------------------------------------------------------------------


class LiteralValue(BaseModel):
    """An explicit literal value expected for a field.

    The ``value`` is intentionally typed ``Any``: Pydantic accepts
    any JSON-shaped scalar (str / int / float / bool) and rejects
    nothing structurally. Type-correctness against the field's
    Salesforce type is enforced at the Coordinator (D-060 §4.7.6),
    not here.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["literal"] = "literal"
    """Discriminator. See :data:`ExpectedValue`."""

    value: Any
    """The expected value. ``Any`` because callers know the
    Salesforce field type; the substrate intentionally does not
    enforce SF-type/Python-type alignment here — that's the
    Coordinator's job."""


class NullValue(BaseModel):
    """The field is expected to be NULL / unset / absent.

    Distinct from ``LiteralValue(value=None)``: a NullValue is an
    explicit assertion about absence, whereas a LiteralValue with
    a Python ``None`` would be a literal value that happens to be
    None. The semantic distinction matters for read-back and
    canonicalization.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["null"] = "null"


ExpectedValue = Annotated[
    Union[LiteralValue, NullValue],
    Field(discriminator="kind"),
]
"""Discriminated union over the two expected-value variants.

Future-extensible: adding a ``ComputedValue(kind="computed", ...)``
later is additive on the discriminator. Payloads that round-trip
through the substrate at v1 continue to work after a v2 schema
extension because the discriminator value space only grows."""


# ---------------------------------------------------------------------------
# StateDescriptor — a snapshot of (field → expected_value) pairs
#
# Per SPEC §3 (data-behavior archetype): records carry state as
# field-value bindings. A StateDescriptor describes one such
# snapshot — typically a "from" state and a "to" state in a
# transition, or the post-effect state after an automation fires.
#
# Design note on the ``field_values`` key shape: the key is the
# field's external_id (a string), not the IdentityBearingRef
# itself. Dict-with-model-keys works in Pydantic but creates a
# painful on-the-wire shape (JSON dicts only allow string keys, so
# round-tripping a model-keyed dict needs custom serializers). The
# trade-off: the containing claim body carries an explicit
# ``subject_fields`` list of IdentityBearingRef for D-058 §5.4
# coverage extraction. The duplication is small (a handful of
# fields per state) and localized.
# ---------------------------------------------------------------------------


class StateDescriptor(BaseModel):
    """A set of (field_external_id → expected_value) bindings
    describing a record's state.

    The keys are the field's external_id (Salesforce API name) as
    a string; the values are :data:`ExpectedValue` instances.
    Empty dicts are permitted — represents "no field-level
    constraint on this state" — though in practice empty states
    are rare.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    field_values: dict[str, ExpectedValue]
    """Field external_id → expected value. The containing claim
    body carries an explicit list of IdentityBearingRef for these
    fields (so coverage extraction per D-058 §5.4 can walk them);
    the dict key here is a flat string for clean JSON
    round-tripping."""


# ---------------------------------------------------------------------------
# EventDescriptor — what causal initiation kicks off the claim's
# evaluation
#
# Per D-055: trigger-kind taxonomy is locked at 5 kinds. An
# EventDescriptor names one of them; the human-readable description
# is for diagnostics and is NOT identity-bearing (per D-055 §
# "Trigger-kind identity nuance" — trigger-kind itself is operational
# by default). Recipe bodies in B-γ/δ will carry richer trigger
# content; here we capture only what's necessary for the claim's
# semantic.
# ---------------------------------------------------------------------------


class EventDescriptor(BaseModel):
    """The causal action that initiates the claim's evaluation.

    Per D-055, ``trigger_kind`` is operational by default and not
    identity-bearing. However, per D-051's discipline rule, if the
    trigger mechanism is *semantically asserted* in the claim
    ("when external system sends via synchronous REST..."), the
    mechanism becomes part of semantic conditions and IS
    identity-bearing. The Coordinator decides which slot this
    EventDescriptor lives in.

    The ``precise_trigger`` slot is reserved for future structured
    trigger content (e.g., the specific Apex trigger node, the UI
    button id). B-β keeps it implicit; B-γ/δ may extend.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    trigger_kind: Literal[
        "inbound-trigger",
        "data-mutation-trigger",
        "ui-trigger",
        "time-trigger",
        "configuration-trigger",
    ]
    """One of the five trigger-kinds locked by D-055."""

    description: str
    """Human-readable description of what kicks off the scenario
    (e.g., "user inserts a new Opportunity record"). Diagnostic,
    not identity-bearing — semantic content sits in the
    surrounding claim's other fields."""

    # NOTE: ``precise_trigger`` reserved for future structured
    # trigger content; see SPEC §3 "Causal initiation" layer.


# ---------------------------------------------------------------------------
# EffectDescriptor — what an automation produced
#
# Per SPEC §3 (automation-effect-claim semantic form): an automation
# can:
#   - change record state (FieldChangeEffect)
#   - block an operation (BlockedOperationEffect)
#   - produce a side effect outside the record (SideEffect)
#
# Discriminated union over ``kind``.
# ---------------------------------------------------------------------------


class FieldChangeEffect(BaseModel):
    """An automation fired and the target record's field values
    changed as a result.

    The ``changes`` StateDescriptor describes the post-effect
    state, NOT the delta. Reading the pre-effect state is the
    caller's responsibility (recipes step from state A to state B;
    the claim asserts that B is what we get after the automation
    fires).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["field_change"] = "field_change"
    changes: StateDescriptor


class BlockedOperationEffect(BaseModel):
    """The automation blocked a user/system action (e.g., a
    validation rule prevents a save).

    Per D-053: distinct from prohibition-claim (which centers
    *that* an operation is rejected and *how* it's signaled). This
    effect type covers the automation-effect-claim case where
    blocking is the *behavior* of the automation rather than the
    asserted prohibition.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["blocked_operation"] = "blocked_operation"
    reason: Optional[str] = None
    """Human-readable reason (e.g., the validation rule's error
    message text). Optional — some blocks surface no diagnostic."""


class SideEffect(BaseModel):
    """The automation produced an effect outside the target
    record's own field values — sending an email, enqueueing a
    job, posting to Chatter, etc.

    The ``description`` is intentionally free-form because
    side-effect taxonomy is open-ended; structured side-effect
    discrimination is deferred to a future body schema version.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["side_effect"] = "side_effect"
    description: str


EffectDescriptor = Annotated[
    Union[FieldChangeEffect, BlockedOperationEffect, SideEffect],
    Field(discriminator="kind"),
]
"""Discriminated union over the three effect-shape variants."""


# ---------------------------------------------------------------------------
# RejectionSignal — how a prohibited operation surfaces its
# rejection
#
# Per SPEC §3 (prohibition-claim semantic form) + D-058 §5.4 (refs
# walked for coverage): a prohibition is the claim that an
# operation is rejected; the RejectionSignal pins down *how* the
# rejection manifests so the recipe can verify it.
#
# The validator enforces "at least one signal is specified" — an
# empty RejectionSignal would be a poorly-specified claim
# ("rejected somehow, we don't know how"). The Coordinator could
# enforce this at write time too; doing it at the Pydantic layer
# is strictly cheaper.
# ---------------------------------------------------------------------------


class RejectionSignal(BaseModel):
    """How a prohibited operation surfaces its rejection.

    At least one of (error_code, error_message_pattern,
    error_field) MUST be non-None — an empty signal can't be
    verified by a recipe and would represent a poorly-specified
    claim.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    error_code: Optional[str] = None
    """Salesforce error code (e.g., ``"FIELD_CUSTOM_VALIDATION_EXCEPTION"``,
    ``"DUPLICATE_VALUE"``). Diagnostic + machine-checkable."""

    error_message_pattern: Optional[str] = None
    """Regex pattern that the surfaced error message must match.
    Diagnostic-only at this layer; the recipe applies it at
    execution time."""

    error_field: Optional[IdentityBearingRef] = None
    """If the error is field-scoped (e.g., a field-level validation
    error), the field reference. Pinned because the field's
    identity matters for the claim's meaning per D-058 §5."""

    @model_validator(mode="after")
    def _at_least_one_signal(self) -> "RejectionSignal":
        if (
            self.error_code is None
            and self.error_message_pattern is None
            and self.error_field is None
        ):
            raise ValueError(
                "RejectionSignal requires at least one of "
                "error_code, error_message_pattern, error_field; "
                "an empty signal cannot be verified by a recipe."
            )
        return self


# ---------------------------------------------------------------------------
# AssertionPredicate — used inside recipe AssertStep variants
#
# Per SPEC §3 (recipes observe and assert) + D-054 (recipe-kinds
# classify observability semantics). An AssertionPredicate is a
# value-level check applied to a prior step's output during recipe
# execution. Distinct from :class:`Condition` (semantic conditions
# layer):
#   - Condition.subject is an IdentityBearingRef to an S1 entity;
#     conditions describe the claim's scoping.
#   - AssertionPredicate.subject_ref is a free-form within-recipe
#     reference (typically "<step_id>" or "<step_id>.<field>"); the
#     assertion checks recipe-internal state.
#
# Cross-step graph validity (does "step_3.Id" actually point at a
# prior step's captured Id?) is a Coordinator-layer concern per
# D-060 §4.7.6 — NOT enforced at the Pydantic layer. The substrate
# treats subject_ref as opaque text here; the Coordinator walks
# the recipe's step list at write time to verify the graph.
# ---------------------------------------------------------------------------


# Predicates that require ``value`` to be provided.
_ASSERTION_VALUE_BEARING_PREDICATES = {
    "equals", "not_equals", "matches_pattern",
}
# Predicates that require ``value`` to be absent.
_ASSERTION_VALUE_FREE_PREDICATES = {"exists", "is_null"}


class AssertionPredicate(BaseModel):
    """A predicate evaluated against a prior step's captured
    output during recipe execution.

    The ``subject_ref`` is intentionally free-form text — the
    substrate doesn't parse it into a structured reference. The
    Coordinator (per D-060) walks the recipe's step graph at
    write time to verify that subject_ref resolves to a real
    prior step's output. Doing the same check here would couple
    the Pydantic body-validation layer to the multi-step
    recipe-graph layer, which is an architectural smell.

    The ``(predicate, value)`` coupling is enforced because the
    coupling is structural (boolean-arity), not graph-related:
    ``exists`` / ``is_null`` carry no comparison value;
    ``equals`` / ``not_equals`` / ``matches_pattern`` require one.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject_ref: str
    """Free-form recipe-internal reference (e.g., ``"step_1"``,
    ``"create_account.Id"``). Coordinator validates the graph at
    write time."""

    predicate: Literal[
        "equals",
        "not_equals",
        "exists",
        "is_null",
        "matches_pattern",
    ]
    """The assertion predicate. Closed taxonomy at v1."""

    value: Optional[Any] = None
    """Comparison value. Required for the value-bearing
    predicates; forbidden for the value-free ones."""

    @model_validator(mode="after")
    def _check_value_predicate_coupling(self) -> "AssertionPredicate":
        if self.predicate in _ASSERTION_VALUE_BEARING_PREDICATES:
            if self.value is None:
                raise ValueError(
                    f"predicate {self.predicate!r} requires a "
                    f"non-None ``value``"
                )
        elif self.predicate in _ASSERTION_VALUE_FREE_PREDICATES:
            if self.value is not None:
                raise ValueError(
                    f"predicate {self.predicate!r} forbids a "
                    f"``value``; got {self.value!r}"
                )
        return self
