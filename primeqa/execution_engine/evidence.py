"""In-memory run evidence — S4-owned, evidence-first (SPEC §4, F2).

The executor produces a :class:`RunEvidence` per run; **slice 3 persists it**
(the result-model schema is deliberately unlocked until then). Capture is rich
+ honest: the query, the *structured filter* it encoded, the rows + count,
per-step timings, and error surfaces — rich enough that S6 can recover *why* an
outcome came out a given way (e.g. distinguish an **absent object** from a
**present-but-no-VR** subject — both yield a 0-row read) **without S4 inferring
it**. S4 records; S6 interprets (SPEC §4).

Read-only-vertical N/As (before/after state, field diff, artifacts) are
**reserved** in the shape but unused — they fill in with the CRUD / UI verticals.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional, Union
from uuid import UUID


@dataclass(frozen=True)
class ErrorSurface:
    """A captured error — what failed, at which phase, with the raw message.

    S4 *records* it; it does not classify or attribute it (that is S6's job).
    ``error_type`` is the exception class name, not an interpreted category."""

    phase: Literal["translate", "read", "assert"]
    error_type: str
    message: str


@dataclass(frozen=True)
class ReadEvidence:
    """Evidence for one planned read — the query, the filter it encoded, and
    what came back."""

    step_id: str
    ordinal: int
    query: str                      # the SOQL issued (api_request analog)
    sobject: str                    # the Tooling object queried
    edge: str                       # the S1 edge realized
    subject_entity_type: str        # the filter: which entity_type ...
    subject_external_id: str        # ... and which external_id (absences stay legible)
    row_count: int
    rows: tuple[dict, ...]          # the records (api_response analog)
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    kind: Literal["read"] = "read"
    error: Optional[ErrorSurface] = None
    # read-only-vertical N/As, reserved (no mutation in an inspection):
    before_state: None = None
    after_state: None = None
    field_diff: None = None


@dataclass(frozen=True)
class AssertEvidence:
    """Evidence for one planned assertion — what was checked, against which
    read's capture, and whether it held."""

    step_id: str
    ordinal: int
    predicate: str
    subject_ref: str
    evaluated_row_count: int
    held: bool
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    kind: Literal["assert"] = "assert"
    error: Optional[ErrorSurface] = None


StepEvidence = Union[ReadEvidence, AssertEvidence]


@dataclass(frozen=True)
class RunEvidence:
    """One metadata-inspection run's captured truth + grounded outcome.

    ``run_id`` is the run's own identity — minted by the executor at run start
    (the run self-identifies from birth). It becomes the result-store PK
    (slice 3) and S2's ``last_run_id`` at the posture callback (slice 4).

    ``outcome`` is the **run result** — assertion held (`passed`) / didn't hold
    (`failed`) / couldn't be evaluated (`errored`). It is *not* an
    interpretation: nothing here classifies, attributes, or explains *why*."""

    run_id: UUID
    recipe_id: UUID
    recipe_version_seq: int
    claim_test_id: UUID
    claim_version_seq: Optional[int]
    environment_id: int
    api_choice: str
    outcome: Literal["passed", "failed", "errored"]
    started_at: datetime
    finished_at: datetime
    steps: tuple[StepEvidence, ...]
    error: Optional[ErrorSurface] = None    # top-level surface for an errored run
    # read-only-vertical N/As, reserved:
    artifacts: tuple = ()
