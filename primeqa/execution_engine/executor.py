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
from primeqa.execution_engine.read_only_client import ReadOnlyClient
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
from primeqa.execution_engine.translator import (
    LayoutMembershipProbe,
    _soql_literal,
    translate_read,
)
from primeqa.integrations.exceptions import SFClientError

# Predicates the executor can faithfully evaluate. `exists` (row presence) grounds
# existence / metadata-relationship; `equals` / `is_null` (a captured column value)
# ground property (D-128). Others are deferred (fail-loud until built).
_SUPPORTED_PREDICATES = frozenset({"exists", "equals", "is_null"})


def execute_metadata_inspection(
    plan: MetadataInspectionPlan, *, client: ReadOnlyClient, environment_id: int,
) -> RunEvidence:
    """Execute a metadata-inspection plan against ``client``.

    ``client`` is a :class:`ReadOnlyClient` (L1, D-245) — SOQL reads only, no
    mutation verb. ``ToolingReadClient`` satisfies it; a ``DataMutationClient``
    does not (a type error), so no write is reachable from this path. Injected,
    so unit tests drive a stub with no org / no PG. ``environment_id`` is the
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
    realization = translate_read(step)  # Unsupported{Edge,Property}Error → fail-loud
    if isinstance(realization, LayoutMembershipProbe):
        return _run_layout_probe(realization, step, ordinal, client,
                                 captures, capture_columns)
    query = realization
    start = _now()
    try:
        # D-224: ObjectPermissions/FieldPermissions live on the Data-API
        # query endpoint; everything else stays Tooling.
        rows = (client.query_data(query.soql) if query.api == "data"
                else client.query(query.soql))
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


def _walk_layout_items(metadata: dict) -> list[str]:
    """Every field api name placed on a layout Metadata blob —
    layoutSections→layoutRows→layoutItems→layoutComponents, components of
    type 'Field' (the same structure S1's sync walks)."""
    fields: list[str] = []
    for section in (metadata.get("layoutSections") or ()):
        if not isinstance(section, dict):
            continue
        for row in (section.get("layoutRows") or ()):
            if not isinstance(row, dict):
                continue
            for item in (row.get("layoutItems") or ()):
                if not isinstance(item, dict):
                    continue
                for comp in (item.get("layoutComponents") or ()):
                    if (isinstance(comp, dict)
                            and comp.get("type") == "Field"
                            and comp.get("value")):
                        fields.append(comp["value"])
    return fields


def _run_layout_probe(
    probe: LayoutMembershipProbe, step: PlannedRead, ordinal: int,
    client, captures: dict, capture_columns: dict,
) -> tuple[ReadEvidence, Optional[ErrorSurface]]:
    """Run the INCLUDES_FIELD membership probe (D-224): layout name → Id
    (Tooling SOQL), Id → Metadata blob (single-row Tooling read), walk the
    items for the field. Synthesizes ``[{"field": …}]``/``[]`` so the recipe's
    ``exists`` assert evaluates unchanged. 0 layout matches is an honest
    absent-subject read (0 rows), not an error."""
    id_soql = (
        "SELECT Id, EntityDefinitionId FROM Layout "
        f"WHERE Name = '{_soql_literal(probe.layout_name)}'"
    )
    start = _now()

    def _evidence(rows, *, query: str, error=None):
        return ReadEvidence(
            step_id=step.step_id, ordinal=ordinal, query=query,
            sobject=probe.sobject, edge=probe.edge,
            subject_entity_type=probe.subject_entity_type,
            subject_external_id=probe.layout_external_id,
            row_count=len(rows), rows=tuple(rows), started_at=start,
            finished_at=_now(), duration_ms=_ms(start, _now()), error=error)

    try:
        candidates = client.query(id_soql)
        # Prefer the layout owned by the probe's object (standard objects
        # carry the api name in EntityDefinitionId); fall back to a unique
        # name match.
        matched = [r for r in candidates
                   if r.get("EntityDefinitionId") == probe.object_api]
        if not matched and len(candidates) == 1:
            matched = candidates
        if not matched:
            captures[step.step_id] = []
            capture_columns[step.step_id] = None
            return _evidence([], query=id_soql), None

        meta_soql = (f"SELECT Metadata FROM Layout "
                     f"WHERE Id = '{matched[0]['Id']}'")
        meta_rows = client.query(meta_soql)
        placed = _walk_layout_items(
            (meta_rows[0].get("Metadata") or {}) if meta_rows else {})
        rows = ([{"field": probe.field_api}]
                if probe.field_api in placed else [])
        captures[step.step_id] = rows
        capture_columns[step.step_id] = None
        return _evidence(rows, query=f"{id_soql}; {meta_soql}"), None
    except SFClientError as e:
        err = ErrorSurface(
            phase="read", error_type=type(e).__name__, message=str(e))
        return _evidence([], query=id_soql, error=err), err


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
