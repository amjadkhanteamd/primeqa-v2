"""In-memory run evidence — S4-owned, evidence-first (SPEC §4, F2).

The executor produces a :class:`RunEvidence` per run; the result store persists
it (D-108.3+). Capture is rich
+ honest: the query, the *structured filter* it encoded, the rows + count,
per-step timings, and error surfaces — rich enough that S6 can recover *why* an
outcome came out a given way (e.g. distinguish an **absent object** from a
**present-but-no-VR** subject — both yield a 0-row read) **without S4 inferring
it**. S4 records; S6 interprets (SPEC §4).

Some N/As (before/after state, field diff, artifacts) remain **reserved** in
the shape (the UI vertical); the data-mutation vertical's ``created_records``
cleanup audit is live (F6.1, D-196).
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

    phase: Literal["translate", "read", "assert", "create", "construct",
                   "update", "delete"]
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


@dataclass(frozen=True)
class CleanupRecord:
    """The targeted best-effort cleanup of a record an *unexpectedly-successful*
    behavioral-negative create produced (D-110.2, the N-5 minimal-cleanup).

    Only the failing path (the prohibition didn't enforce → a real record was
    created) has anything to clean. ``attempted=False`` is the normal case (the
    create was rejected → nothing created). Best-effort: a failed delete never
    changes the run outcome — it is recorded here, not raised."""

    attempted: bool
    succeeded: Optional[bool] = None     # None when not attempted
    record_id: Optional[str] = None


@dataclass(frozen=True)
class CreateAttemptEvidence:
    """Evidence for one behavioral-negative create attempt (D-110.2).

    Captures the attempted mutation, the org's response (the rejection signal,
    or the unexpected success), whether it *matched* the recipe's
    ``expect_rejection``, and the targeted cleanup. ``before/after_state`` +
    ``field_diff`` stay N/A — a rejected create produces no state, and an
    unexpected success is cleaned up rather than diffed."""

    step_id: str
    ordinal: int
    sobject: str
    field_values: dict              # the attempted payload (api_request analog)
    http_status: Optional[int]      # None when the create couldn't be attempted
    success: bool                   # was the create accepted (2xx)?
    error_code: Optional[str]       # convenience: an errorCode from the body
    message: Optional[str]          # convenience: an error message from the body
    rejection_body: tuple           # the FULL error body (api_response analog)
    matched: Optional[bool]         # did the rejection match expect_rejection? (None if success/errored)
    cleanup: CleanupRecord
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    kind: Literal["create"] = "create"
    error: Optional[ErrorSurface] = None
    # D-225: every field named across the body's error entries — FLS/access
    # denials name the blocked fields; structured here so S6 + the UI never
    # re-parse rejection_body. () when the body names none.
    error_fields: tuple = ()
    # N/As, reserved (a rejected create produces no state; a success is deleted):
    before_state: None = None
    after_state: None = None
    field_diff: None = None


@dataclass(frozen=True)
class DataReadEvidence:
    """Evidence for one data read-back (D-115 — the positive create-and-verify).

    The **resolved** SOQL actually issued (``$<step>.id`` substituted), the object
    queried, the fields captured, and the row(s) returned. A 0-row read is an
    honest "couldn't observe" (no immediate-consistency assumption is baked in) —
    distinguished downstream from a transport ``error``. Distinct from the
    metadata vertical's :class:`ReadEvidence` (S1-edge vocabulary); a data read
    speaks SOQL + real field names."""

    step_id: str
    ordinal: int
    soql: str                       # the resolved SOQL issued (api_request analog)
    sobject: str
    fields_captured: tuple[str, ...]
    row_count: int
    rows: tuple[dict, ...]          # the records (api_response analog)
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    kind: Literal["read"] = "read"
    error: Optional[ErrorSurface] = None
    # D-210: how many read attempts were issued (a side-effect read retries a
    # bounded number of times while the org's automation lands; 1 = first try).
    attempts: int = 1
    # mutation N/As, reserved (a read mutates nothing):
    before_state: None = None
    after_state: None = None
    field_diff: None = None


@dataclass(frozen=True)
class UpdateAttemptEvidence:
    """Evidence for one rejected-update attempt (D-203 — the 2-step
    behavioral negative).

    ``record_id`` names the subject record the mutation targeted (the setup
    create's record). The subject's teardown is reported **once**, on the setup
    create's evidence (the step that created the record — the established
    convention), not here. ``before/after_state`` + ``field_diff`` stay
    reserved (before-state capture is a logged D-203 residual)."""

    step_id: str
    ordinal: int
    sobject: str
    record_id: Optional[str]        # the subject record (from the setup create)
    field_changes: dict             # the attempted mutation (api_request analog)
    http_status: Optional[int]      # None when the update couldn't be attempted
    success: bool                   # was the update accepted (2xx)?
    error_code: Optional[str]       # convenience: an errorCode from the body
    message: Optional[str]          # convenience: an error message from the body
    rejection_body: tuple           # the FULL error body (api_response analog)
    matched: Optional[bool]         # did the rejection match expect_rejection?
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    kind: Literal["update"] = "update"
    error: Optional[ErrorSurface] = None
    # D-225: fields named by the error entries (FLS/access denials name them).
    error_fields: tuple = ()
    # N/As, reserved (before-state capture is a D-203 residual):
    before_state: None = None
    after_state: None = None
    field_diff: None = None


@dataclass(frozen=True)
class DeleteAttemptEvidence:
    """Evidence for one rejected-delete attempt (D-203) — same semantics as
    :class:`UpdateAttemptEvidence`, minus a payload (a delete carries none)."""

    step_id: str
    ordinal: int
    sobject: str
    record_id: Optional[str]        # the subject record (from the setup create)
    http_status: Optional[int]
    success: bool                   # was the delete accepted (2xx)?
    error_code: Optional[str]
    message: Optional[str]
    rejection_body: tuple
    matched: Optional[bool]
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    kind: Literal["delete"] = "delete"
    error: Optional[ErrorSurface] = None
    # D-225: fields named by the error entries (FLS/access denials name them).
    error_fields: tuple = ()
    before_state: None = None
    after_state: None = None
    field_diff: None = None


StepEvidence = Union[
    ReadEvidence, AssertEvidence, CreateAttemptEvidence, DataReadEvidence,
    UpdateAttemptEvidence, DeleteAttemptEvidence]


@dataclass(frozen=True)
class RunEvidence:
    """One run's captured truth + grounded outcome (metadata-inspection or
    data-mutation vertical).

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
    # Records S4 created during this run (positive vertical) — the cleanup audit
    # (F6.1, D-196). Empty for read-only / rejected-create runs. ``CreatedRecord``s
    # from ``provisioning.CreatedRecordTracker``; persisted to s4_created_records.
    created_records: tuple = ()


def failure_signature(evidence: RunEvidence) -> tuple[Optional[str], Optional[str]]:
    """Derive the run's ``(failure_category, sf_error_code)`` from its top-level
    error surface — the ONE classification both the result store (at persist,
    :func:`primeqa.execution_engine.result_store._run_failure`) and S6 (at
    interpret, the ``not_evaluated`` path) consume, so a run reads the SAME
    category in both places (incl. retroactively from the captured evidence,
    which carries ``error_type`` even on rows persisted before the failure_category
    column existed).

    Pure; reuses the shared taxonomy (``integrations.failure_taxonomy.category_for``).
    Returns ``(None, None)`` for a run with no error surface — ``passed`` /
    assertion-``failed`` / a business rejection carry no ``evidence.error``. The SF
    ``errorCode`` is read from the first erroring step's evidence (a read-path
    ``SFRequestError`` has no captured code, so it classifies by exception class
    only — a documented coarseness)."""
    from primeqa.integrations.failure_taxonomy import category_for

    err = evidence.error
    if err is None:
        return None, None
    sf_error_code = None
    for step in evidence.steps:
        if getattr(step, "error", None) is not None:
            code = getattr(step, "error_code", None)
            if isinstance(code, str) and code:
                sf_error_code = code
                break
    return category_for(err.error_type, sf_error_code), sf_error_code
