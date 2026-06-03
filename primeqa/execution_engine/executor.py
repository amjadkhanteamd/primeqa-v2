"""The metadata-inspection executor (SPEC §5 slice 2).

Walks a :class:`MetadataInspectionPlan` against a live org: translate each read
→ run it via the injected Tooling client → evaluate each assertion → render the
**grounded run outcome** (`passed` / `failed` / `errored`) + an in-memory
:class:`RunEvidence`.

The outcome is the **run result**, not an interpretation: S4 records what held /
didn't hold / couldn't be evaluated; it never classifies, attributes, or infers
*why* (S6's job — SPEC §4). Two failure modes are distinct:
  - a **read transport failure** (org-side) → the assertion can't be evaluated
    → `errored` (caught, recorded in evidence);
  - an **unsupported predicate / unresolvable subject_ref / untranslatable
    edge** (a representation or plan defect, code-side) → **fail loud** (raise),
    never silently coerced into an outcome.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from primeqa.execution_engine.errors import AssertionResolutionError
from primeqa.execution_engine.errors import UnsupportedPredicateError
from primeqa.execution_engine.evidence import (
    AssertEvidence,
    ErrorSurface,
    ReadEvidence,
    RunEvidence,
    StepEvidence,
)
from primeqa.execution_engine.plan import (
    MetadataInspectionPlan,
    PlannedAssertion,
    PlannedRead,
)
from primeqa.execution_engine.translator import translate_read
from primeqa.integrations.exceptions import SFClientError

# Predicates the executor can faithfully evaluate. `exists` (row presence) grounds
# existence / metadata-relationship; `equals` / `is_null` (a captured column value)
# ground property (D-128). Others are deferred (fail-loud until built).
_SUPPORTED_PREDICATES = frozenset({"exists", "equals", "is_null"})


def execute_metadata_inspection(
    plan: MetadataInspectionPlan, *, client, environment_id: int,
) -> RunEvidence:
    """Execute a metadata-inspection plan against ``client``.

    ``client`` is anything with ``.query(soql) -> list[dict]`` (injected, so
    unit tests drive a stub with no org / no PG). ``environment_id`` is the
    env the client is bound to — recorded as evidence context.

    Returns a :class:`RunEvidence` carrying the grounded outcome + per-step
    evidence. Reads precede the assertions that reference them; a read
    transport failure stops the walk (downstream asserts can't be evaluated).
    """
    run_id = uuid4()        # the run self-identifies from birth (slice 3 PK)
    started = _now()
    steps: list[StepEvidence] = []
    captures: dict[str, list[dict]] = {}            # read step_id -> rows
    capture_columns: dict[str, Optional[str]] = {}  # read step_id -> value column (D-128)
    outcome = "passed"
    top_error: Optional[ErrorSurface] = None

    for ordinal, step in enumerate(plan.steps):
        if isinstance(step, PlannedRead):
            ev, err = _run_read(step, ordinal, client, captures, capture_columns)
            steps.append(ev)
            if err is not None:
                # The read couldn't be performed → the assertion can't be
                # evaluated → errored. Stop: downstream asserts depend on it.
                outcome = "errored"
                top_error = err
                break
        else:  # PlannedAssertion (plan carries only the two kinds)
            ev = _run_assert(step, ordinal, captures, capture_columns)
            steps.append(ev)
            if not ev.held:
                outcome = "failed"

    finished = _now()
    return RunEvidence(
        run_id=run_id,
        recipe_id=plan.recipe_id,
        recipe_version_seq=plan.recipe_version_seq,
        claim_test_id=plan.claim_test_id,
        claim_version_seq=plan.claim_version_seq,
        environment_id=environment_id,
        api_choice=plan.api_choice,
        outcome=outcome,
        started_at=started,
        finished_at=finished,
        steps=tuple(steps),
        error=top_error,
    )


def _run_read(
    step: PlannedRead, ordinal: int, client, captures: dict, capture_columns: dict,
) -> tuple[ReadEvidence, Optional[ErrorSurface]]:
    """Translate + run one read. Returns (evidence, error|None). An
    untranslatable edge / unmapped property raises (fail-loud, before any client
    call); a transport failure is captured + returned as an error (→ errored
    outcome). On success, records the read's value column (``capture_column``,
    None for an existence/edge read) so a downstream value-predicate can read it."""
    query = translate_read(step)        # Unsupported{Edge,Property}Error → fail-loud
    start = _now()
    try:
        rows = client.query(query.soql)
    except SFClientError as e:
        end = _now()
        err = ErrorSurface(
            phase="read", error_type=type(e).__name__, message=str(e))
        ev = ReadEvidence(
            step_id=step.step_id, ordinal=ordinal, query=query.soql,
            sobject=query.sobject, edge=query.edge,
            subject_entity_type=query.subject_entity_type,
            subject_external_id=query.subject_external_id,
            row_count=0, rows=(), started_at=start, finished_at=end,
            duration_ms=_ms(start, end), error=err)
        return ev, err

    end = _now()
    captures[step.step_id] = rows
    capture_columns[step.step_id] = query.capture_column
    ev = ReadEvidence(
        step_id=step.step_id, ordinal=ordinal, query=query.soql,
        sobject=query.sobject, edge=query.edge,
        subject_entity_type=query.subject_entity_type,
        subject_external_id=query.subject_external_id,
        row_count=len(rows), rows=tuple(rows), started_at=start,
        finished_at=end, duration_ms=_ms(start, end))
    return ev, None


def _run_assert(
    step: PlannedAssertion, ordinal: int, captures: dict, capture_columns: dict,
) -> AssertEvidence:
    """Evaluate one assertion against a prior read's capture.

    - ``exists`` — the run holds iff the referenced read returned ≥1 row.
    - ``equals`` / ``is_null`` (D-128) — read the read's captured column value
      from its single row and compare to the predicate's ``value``. The value
      column was recorded by the read (a property self-read); a value predicate
      over a presence-only read (no captured column) fails loud.

    An unsupported predicate or an unresolvable ``subject_ref`` fails loud
    (raises) — a representation/plan defect, not a run outcome."""
    predicate = step.predicate.predicate
    if predicate not in _SUPPORTED_PREDICATES:
        raise UnsupportedPredicateError(
            f"assertion {step.step_id!r} uses predicate {predicate!r}; "
            f"the executor evaluates only {sorted(_SUPPORTED_PREDICATES)}")

    subject = step.predicate.subject_ref
    if subject not in captures:
        raise AssertionResolutionError(
            f"assertion {step.step_id!r} subject_ref {subject!r} does not "
            f"resolve to a prior read step (captured: {sorted(captures)})")

    start = _now()
    rows = captures[subject]
    if predicate == "exists":
        held = len(rows) > 0
    else:
        # equals / is_null — a value comparison over the read's captured column.
        column = capture_columns.get(subject)
        if column is None:
            raise AssertionResolutionError(
                f"assertion {step.step_id!r} predicate {predicate!r} needs a "
                f"value column, but read {subject!r} captured none (a value "
                f"predicate over a presence-only read)")
        # A value predicate needs an observed row; 0 rows (the subject didn't
        # surface) can't confirm equality OR null-ness → not held (a grounded
        # `failed`, distinct from the existence read's own outcome).
        if not rows:
            held = False
        elif predicate == "is_null":
            held = rows[0].get(column) is None
        else:  # equals
            held = _value_eq(rows[0].get(column), step.predicate.value)
    end = _now()
    return AssertEvidence(
        step_id=step.step_id, ordinal=ordinal, predicate=predicate,
        subject_ref=subject, evaluated_row_count=len(rows), held=held,
        started_at=start, finished_at=end, duration_ms=_ms(start, end))


def _value_eq(observed, expected) -> bool:
    """Equality with a representational-coercion fallback: a faithful match
    (``observed == expected``) OR — when both are present but differently typed
    (Tooling JSON may render a number as a string) — a string-equal fallback.
    Never coerces a None / absent value into a match."""
    if observed == expected:
        return True
    if observed is None or expected is None:
        return False
    return str(observed) == str(expected)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ms(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds() * 1000)
