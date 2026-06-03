"""The S6 interpretation store — persists a run's interpretation (D-111.2).

One per-tenant table ``s6_interpretations``: typed identity / semantic columns
(``run_id`` PK, ``recipe_id``, ``claim_test_id``, ``outcome``, ``verdict`` —
queryable) + a ``detail`` JSONB carrying the rich part (``attribution`` prose,
``evidence_refs``, ``cause``). The precise mirror of the S4 result store
(``result_store.py`` / ``persist_run_evidence``), one substrate up: S4 persists
the captured truth, S6 persists the *meaning* of it. The DDL is in
``alembic/versions/tenant/20260527_0020_s6_interpretations.py``; this module is
the Python projection + the persister.

**Persistence boundary.** The interpreter + attributer stay pure (produce an
in-memory :class:`~primeqa.interpretation.model.Interpretation`, no DB import).
:func:`persist_interpretation` is the only writer — it maps the in-memory
interpretation to a row and persists it on a caller-provided session (add +
flush, **no commit** — the caller owns the transaction, the substrate
convention; mirrors ``persist_run_evidence``). The run-path wraps the call in a
savepoint so a persist failure never rolls back the S4 run truth (D-111.2,
best-effort).
"""
from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import Column, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from primeqa.db import Base
from primeqa.execution_engine.result_store import RUN_OUTCOME_ENUM
from primeqa.interpretation.model import Cause, EvidenceRef, Interpretation


class S6Interpretation(Base):
    """One S6 interpretation of one S4 run — the captured truth's *meaning*.

    Per-tenant (no ``tenant_id`` — isolation by schema). Identity + the semantic
    axes (outcome carried verbatim from S4, verdict) are typed columns; the rich
    part (attribution prose, evidence refs, deeper cause) lives in ``detail``
    JSONB. ``run_id`` is the PK — one interpretation per run (the logical 1:1 to
    ``s4_execution_runs.run_id``, not a DB-enforced FK)."""

    __tablename__ = "s6_interpretations"
    __test__ = False  # pytest collection: not a test class

    run_id = Column(UUID(as_uuid=True), primary_key=True, nullable=False)
    recipe_id = Column(UUID(as_uuid=True), nullable=False)
    claim_test_id = Column(UUID(as_uuid=True), nullable=False)
    # carried verbatim from S4 (reused enum) — S6 never recomputes the outcome.
    outcome = Column(RUN_OUTCOME_ENUM, nullable=False)
    # the semantic verdict (TEXT — the Python Verdict Literal is the source of
    # truth; the taxonomy grows with recipe kinds).
    verdict = Column(Text, nullable=False)
    # promoted from detail->'cause' for cross-run clustering (D-116) — the
    # queryable axes (recurring causes, the same VR across runs). NULL when the
    # interpretation has no deeper cause (``cause`` is Optional).
    cause_kind = Column(Text, nullable=True)
    vr_name = Column(Text, nullable=True)
    # the rich part: {attribution, evidence_refs, cause}.
    detail = Column(JSONB, nullable=False)
    # the LLM-phrased presentation layer (D-117) — {headline, explanation, model,
    # prompt_version, generated_at}, or NULL until phrased on-demand. Written by
    # the v1 enricher via set_phrasing (the LLM stays out of interpretation/).
    phrasing = Column(JSONB, nullable=True)


def persist_interpretation(session, interpretation: Interpretation):
    """Persist one :class:`Interpretation` as an ``s6_interpretations`` row.

    Maps the in-memory interpretation to typed columns + a ``detail`` JSONB,
    adds + flushes on the caller's ``session`` (no commit — the caller owns the
    transaction, the substrate convention), and returns the ``run_id``. Mirrors
    :func:`primeqa.execution_engine.result_store.persist_run_evidence`.
    """
    cause = interpretation.cause
    row = S6Interpretation(
        run_id=interpretation.run_id,
        recipe_id=interpretation.recipe_id,
        claim_test_id=interpretation.claim_test_id,
        outcome=interpretation.outcome,
        verdict=interpretation.verdict,
        # promoted cause axes for clustering (D-116); None when no cause.
        cause_kind=(cause.cause_kind if cause is not None else None),
        vr_name=(cause.vr_name if cause is not None else None),
        detail=_detail(interpretation),
    )
    session.add(row)
    session.flush()
    return interpretation.run_id


def set_phrasing(session, run_id, phrasing: dict) -> None:
    """Cache the LLM-phrased presentation (D-117) onto an existing
    ``s6_interpretations`` row — a pure ``UPDATE`` (no LLM, no commit; the caller
    owns the transaction). The phrasing dict is produced by the v1 enricher
    (``intelligence.interpretation_phrasing``); this writer keeps the LLM out of
    ``interpretation/`` — the substrate owns its table's persistence while the
    presentation layer lives in v1."""
    session.query(S6Interpretation).filter(
        S6Interpretation.run_id == run_id).update(
        {S6Interpretation.phrasing: phrasing}, synchronize_session="fetch")


def _detail(interpretation: Interpretation) -> dict:
    """Build the ``detail`` JSONB from the in-memory interpretation — the rich
    part not promoted to a typed column: the attribution prose, the evidence
    refs (pointers back into ``RunEvidence``), and the structured deeper
    ``cause`` (or None). Dataclasses → plain dicts (JSONB-safe; no datetimes or
    UUIDs appear inside these)."""
    return {
        "attribution": interpretation.attribution,
        "evidence_refs": [dataclasses.asdict(ref)
                          for ref in interpretation.evidence_refs],
        "cause": (dataclasses.asdict(interpretation.cause)
                  if interpretation.cause is not None else None),
    }


# ---------------------------------------------------------------------------
# The read API (D-137) — pure, tenant-scoped reads; the consumer surface over
# the write-only store. No LLM, no v1 DB.
# ---------------------------------------------------------------------------

_LIST_HARD_CAP = 500  # the substrate list-bound convention (cf. core.repository)


@dataclass(frozen=True)
class InterpretationRead:
    """The read-side projection of an ``s6_interpretations`` row — the hydrated
    interpretation plus the persisted ``phrasing`` presentation layer (which the
    produce-side :class:`Interpretation` does not carry). Returned by
    :func:`read_interpretation` / :func:`list_interpretations`."""

    run_id: uuid.UUID
    recipe_id: uuid.UUID
    claim_test_id: uuid.UUID
    outcome: str
    verdict: str
    attribution: str
    evidence_refs: tuple[EvidenceRef, ...] = field(default_factory=tuple)
    cause: Optional[Cause] = None
    phrasing: Optional[dict] = None


def read_interpretation(session, run_id) -> Optional[InterpretationRead]:
    """Read one interpretation by ``run_id`` (the PK) on the caller's
    tenant-scoped session, or None when absent."""
    row = (session.query(S6Interpretation)
           .filter(S6Interpretation.run_id == run_id).one_or_none())
    return _row_to_read(row) if row is not None else None


def list_interpretations(session, *, recipe_id=None, claim_test_id=None,
                         limit: int = 200) -> list[InterpretationRead]:
    """List interpretations on the caller's tenant-scoped session, optionally
    scoped by ``recipe_id`` / ``claim_test_id``. Ordered by ``run_id``
    (deterministic — the row carries no timestamp axis) and bounded by
    ``min(limit, 500)`` (the substrate list-bound convention)."""
    q = session.query(S6Interpretation)
    if recipe_id is not None:
        q = q.filter(S6Interpretation.recipe_id == recipe_id)
    if claim_test_id is not None:
        q = q.filter(S6Interpretation.claim_test_id == claim_test_id)
    rows = (q.order_by(S6Interpretation.run_id)
            .limit(min(int(limit), _LIST_HARD_CAP)).all())
    return [_row_to_read(r) for r in rows]


def _row_to_interpretation(row) -> Interpretation:
    """Hydrate an ``s6_interpretations`` row back into the in-memory
    :class:`Interpretation` (the inverse of :func:`persist_interpretation` /
    :func:`_detail`): rebuild the evidence refs + the structured cause from the
    ``detail`` JSONB. ``outcome`` / ``verdict`` come from the typed columns."""
    detail = row.detail or {}
    refs = tuple(
        EvidenceRef(step_id=r.get("step_id"), detail=r.get("detail", ""))
        for r in detail.get("evidence_refs", []))
    cause_d = detail.get("cause")
    cause = (Cause(cause_kind=cause_d.get("cause_kind"),
                   vr_name=cause_d.get("vr_name"),
                   detail=cause_d.get("detail"))
             if cause_d else None)
    return Interpretation(
        run_id=row.run_id, recipe_id=row.recipe_id,
        claim_test_id=row.claim_test_id, outcome=row.outcome,
        verdict=row.verdict, attribution=detail.get("attribution", ""),
        evidence_refs=refs, cause=cause)


def _row_to_read(row) -> InterpretationRead:
    """Project a row into the read DTO — the hydrated interpretation plus the
    persisted ``phrasing`` (the presentation layer)."""
    interp = _row_to_interpretation(row)
    return InterpretationRead(
        run_id=interp.run_id, recipe_id=interp.recipe_id,
        claim_test_id=interp.claim_test_id, outcome=interp.outcome,
        verdict=interp.verdict, attribution=interp.attribution,
        evidence_refs=interp.evidence_refs, cause=interp.cause,
        phrasing=row.phrasing)
