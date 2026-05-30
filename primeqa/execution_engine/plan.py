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

from primeqa.test_representation.models.primitives import AssertionPredicate
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
