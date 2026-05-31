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

from sqlalchemy import Column, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from primeqa.db import Base
from primeqa.execution_engine.result_store import RUN_OUTCOME_ENUM
from primeqa.interpretation.model import Interpretation


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
    # the rich part: {attribution, evidence_refs, cause}.
    detail = Column(JSONB, nullable=False)


def persist_interpretation(session, interpretation: Interpretation):
    """Persist one :class:`Interpretation` as an ``s6_interpretations`` row.

    Maps the in-memory interpretation to typed columns + a ``detail`` JSONB,
    adds + flushes on the caller's ``session`` (no commit — the caller owns the
    transaction, the substrate convention), and returns the ``run_id``. Mirrors
    :func:`primeqa.execution_engine.result_store.persist_run_evidence`.
    """
    row = S6Interpretation(
        run_id=interpretation.run_id,
        recipe_id=interpretation.recipe_id,
        claim_test_id=interpretation.claim_test_id,
        outcome=interpretation.outcome,
        verdict=interpretation.verdict,
        detail=_detail(interpretation),
    )
    session.add(row)
    session.flush()
    return interpretation.run_id


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
