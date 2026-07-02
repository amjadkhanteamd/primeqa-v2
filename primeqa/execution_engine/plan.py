"""The executable-plan contract — S4-owned, the seam slices 2-4 consume.

Per the S4 SPEC §2.1 / §5 (``docs/architecture/substrate_4_execution``).
The bridge (``bridge.py``) turns an S2 recipe into a
:class:`MetadataInspectionPlan`: the **semantic** (pre-translation) form of a
metadata-inspection run — an ordered sequence of metadata reads + assertions
**in S1-edge vocabulary**.

Two deliberate properties (both from D-108 / SPEC §5):

  - **Semantic, not live.** ``PlannedRead.target_entity`` is a
    :class:`LogicalRef` (resolve-by-name) and ``fields_to_capture`` carries
    S1-edge names (e.g. ``"APPLIES_TO"``), *not* a Salesforce SOQL/Tooling
    query. Translating the edge into a live query is slice 2's executor job
    (the §2.3 edge->live-read translator); the plan stays semantic so it can
    be built + tested without a live org.

  - **S4-owned envelope over S2 primitives.** The plan is a set of frozen
    dataclasses (S4's contract), but it *carries* the S2 Pydantic primitives
    it references verbatim — :class:`LogicalRef` and
    :class:`AssertionPredicate`. The bridge narrows + orders + validates the
    recipe's steps; it does not re-author the reference / predicate types.

The result-model *schema* (how a run's evidence is persisted) is a separate,
later concern (SPEC §4 / slice 3) and intentionally absent here — this is the
*input* contract for the executor, not the output of a run.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Union
from uuid import UUID

from primeqa.test_representation.models.primitives import (
    AssertionPredicate,
    RejectionExpectation,
)
from primeqa.test_representation.models.references import LogicalRef


# ---------------------------------------------------------------------------
# Plan steps — the narrowed, ordered projection of a metadata_read recipe
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PlannedRead:
    """A planned metadata read — the semantic (pre-translation) form.

    Narrowed from the recipe's ``ReadMetadataStep``. ``target_entity`` is a
    :class:`LogicalRef` (never pinned — D-099.3: an inspection re-reads the
    org's *current* state, so the reference resolves by name at run time).
    ``fields_to_capture`` carries S1-edge vocabulary (e.g. ``"APPLIES_TO"``)
    that slice 2's translator turns into a live query.
    """

    step_id: str
    target_entity: LogicalRef
    fields_to_capture: tuple[str, ...]
    kind: Literal["read"] = "read"
    # D-224: the captured edge's FAR endpoint + capability qualifier, when the
    # realization needs them to scope (GRANTS_*_ACCESS / INCLUDES_FIELD).
    # None on every pre-D-224 shape (single-endpoint edge-reads + self-reads).
    edge_target: Optional[LogicalRef] = None
    edge_qualifier: Optional[str] = None


@dataclass(frozen=True)
class PlannedAssertion:
    """A planned assertion over a prior read's captured output.

    Narrowed from the recipe's ``AssertStep``. Carries the S2
    :class:`AssertionPredicate` verbatim (``subject_ref`` + ``predicate`` +
    optional ``value``). ``subject_ref`` is the recipe-internal reference
    (typically a prior read's ``step_id``); the executor resolves it against
    the run's capture map at evaluation time.
    """

    step_id: str
    predicate: AssertionPredicate
    kind: Literal["assert"] = "assert"


PlanStep = Union[PlannedRead, PlannedAssertion]
"""One ordered step of a metadata-inspection plan: a read or an assertion.

In-memory discriminated union (``isinstance`` dispatch; the ``kind`` field is
the explicit label for legibility + future serialization). The plan preserves
the recipe's step order so an assertion can reference a read that precedes
it."""


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MetadataInspectionPlan:
    """The executor's input contract for one metadata-inspection run.

    Produced by :func:`primeqa.execution_engine.bridge.build_metadata_inspection_plan`
    from an S2 ``RecipeRead``; consumed by slice 2's executor (which
    translates each :class:`PlannedRead` to a live query and evaluates each
    :class:`PlannedAssertion`) and slice 4's posture callback (which reports
    the run outcome for ``recipe_id`` at ``recipe_version_seq``).

    Identity is carried, not resolved: ``claim_test_id`` /
    ``claim_version_seq`` reference the claim this recipe verifies, but slice 1
    does not fetch the claim body (that is not needed to run the read + assert,
    and keeps the bridge org-free + DB-free).
    """

    recipe_id: UUID
    recipe_version_seq: int
    claim_test_id: UUID
    claim_version_seq: Optional[int]
    api_choice: Literal["metadata_api", "tooling_api"]
    steps: tuple[PlanStep, ...]


# ---------------------------------------------------------------------------
# Data-recipe behavioral-negative plan (D-110.2) — parallel to the inspection
# plan above, not a generalization (the projected shape differs: a create +
# expect-rejection, not a read + assert). Generalization is deferred to
# recipe-kind-family growth (rule of three).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PlannedCreate:
    """A planned record create.

    Narrowed from the recipe's ``CreateStep``. ``target_object`` is a
    :class:`LogicalRef` (the sobject is referenced by name — operational
    bodies are logical by default, D-058 §5.1). ``field_values`` is carried
    verbatim (the payload the executor POSTs).

    ``expect_rejection`` discriminates the two data verticals:
      - **set** (D-110.2 — the behavioral negative): the executor matches the
        org's rejection against this :class:`RejectionExpectation`;
      - **``None``** (D-115 — the positive create-and-verify): an ordinary
        create the org should *accept*; the executor pads the required fields
        (k16), creates, reads the record back, and grounds ``field == V``.
    """

    step_id: str
    target_object: LogicalRef
    field_values: dict
    expect_rejection: Optional[RejectionExpectation] = None
    expect_acceptance: bool = False
    kind: Literal["create"] = "create"


@dataclass(frozen=True)
class PlannedDataRead:
    """A planned data read-back (D-115 — the positive create-and-verify).

    Narrowed from the recipe's ``ReadStep``. Unlike the metadata vertical's
    :class:`PlannedRead` (S1-edge vocabulary, no live query), a data read carries
    the recipe's raw ``soql`` — including the cross-step ``$<step_id>.id``
    reference the executor resolves against the run's state at run time
    (``refs.resolve_step_refs``) — plus the real field names to capture.
    ``target`` is the FROM object (a :class:`LogicalRef`; resolve-by-name)."""

    step_id: str
    target: LogicalRef
    soql: Optional[str]
    fields_to_capture: tuple[str, ...]
    kind: Literal["read"] = "read"


@dataclass(frozen=True)
class PlannedUpdate:
    """A planned update — REJECTED (D-203, the 2-step behavioral negative)
    or POSITIVE (D-306, the update-then-observe chain).

    Narrowed from a recipe's ``UpdateStep``. ``setup_step_id`` is the
    **positional binding** to the plan's create — the executor mutates the
    record that create step made (no ``$ref`` machinery; the bridge validates
    the pairing). ``field_changes`` is carried verbatim (S1-qualified names;
    the executor bare-ifies at the live boundary). ``expect_rejection`` set =
    the D-203 negative; ``None`` = the D-306 positive update, where
    ``expect_acceptance`` grades a business rejection ``failed`` (the org
    refused a change that must succeed) instead of an indeterminate.
    """

    step_id: str
    target_object: LogicalRef
    field_changes: dict
    expect_rejection: Optional[RejectionExpectation]
    setup_step_id: str
    expect_acceptance: bool = False
    kind: Literal["update"] = "update"


@dataclass(frozen=True)
class PlannedDelete:
    """A planned delete the org should **reject** (D-203) — same positional
    ``setup_step_id`` binding + required ``expect_rejection`` semantics as
    :class:`PlannedUpdate`, minus a payload (a delete carries none)."""

    step_id: str
    target_object: LogicalRef
    expect_rejection: RejectionExpectation
    setup_step_id: str
    kind: Literal["delete"] = "delete"


DataPlanStep = Union[
    PlannedCreate, PlannedDataRead, PlannedAssertion, PlannedUpdate, PlannedDelete]
"""One ordered step of a data-recipe plan: a create, a read-back, an assertion,
or a rejected mutation. The behavioral negative is a single
:class:`PlannedCreate` (``expect_rejection`` set, D-110.2) **or** a setup
:class:`PlannedCreate` followed by a :class:`PlannedUpdate` /
:class:`PlannedDelete` (D-203); the positive create-and-verify (D-115) is a
:class:`PlannedCreate` (no ``expect_rejection``) → :class:`PlannedDataRead` →
:class:`PlannedAssertion`. ``isinstance`` dispatch in the executor; ``kind`` is
the explicit serialization label."""


@dataclass(frozen=True)
class DataRecipePlan:
    """The executor's input contract for one data-recipe run — both data
    verticals (D-110.2 behavioral negative + D-115 positive create-and-verify).

    Produced by :func:`primeqa.execution_engine.bridge.build_data_recipe_plan`;
    consumed by ``execute_data_recipe``, which dispatches on the
    **rejection-bearing step** (S2 guarantees at most one): a flagged create →
    the 1-step behavioral negative (D-110.2); a flagged update/delete → the
    2-step provisioned negative (D-203: setup create → rejected mutation →
    teardown); none → the positive create-and-verify (pad → create → read back
    → ground ``field == V``, D-115). Same identity-carried-not-resolved
    discipline as :class:`MetadataInspectionPlan`. N-step shapes beyond these
    remain deferred (5b-2).
    """

    recipe_id: UUID
    recipe_version_seq: int
    claim_test_id: UUID
    claim_version_seq: Optional[int]
    api_choice: Literal["rest", "bulk", "composite"]
    steps: tuple[DataPlanStep, ...]
