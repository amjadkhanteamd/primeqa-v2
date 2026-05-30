"""The finalize step — persist run evidence, then report posture to S2.

Per SPEC §5 slice 4 + D-108.3. Closes the first vertical's spine: a run's
captured truth (the slice-3 result store) and its grounded outcome (S2's
runtime-state surface) are written **atomically on one session**.

`finalize_run`:
  1. ``persist_run_evidence(session, evidence)`` — the durable S4 result-store
     row (`s4_execution_runs`).
  2. ``report_run_outcome(session, actor='s4', …)`` — the S2 posture callback
     (`test_recipe_runtime_state`; coverage freshness).

Both calls ``flush()`` (never ``commit()``) on the **same caller-provided
session**, so persist + posture are one atomic unit — the caller owns the
commit (the substrate D-β contract).

Idempotency is **two layers** (D-108.3):
  - **persist (fail-loud):** ``s4_execution_runs`` has a ``run_id`` PK, so a
    true re-finalize of the same run raises ``IntegrityError`` — runs are never
    silently duplicated. Each run mints a fresh ``run_id``, so a double-finalize
    is a bug, caught not swallowed.
  - **posture (no-op):** ``report_run_outcome`` is first-write-wins on
    ``last_run_id``, so a posture-only retry (re-reporting an already-recorded
    run) is a safe no-op.

This is the **write** side of the S4↔S2 read-through boundary (the read side —
`select_recipe_for_execution` — is D-108.1). The `SemanticTransactionCoordinator`
is injectable (default-constructed) so it can be spied in unit tests without a
DB. This module is the finalize **seam**, not the production trigger — the
full-spine wiring (a worker/route running resolve → plan → execute → finalize
against a real recipe + live org) is a separate, deferred concern (D-108.3).
"""
from __future__ import annotations

from primeqa.execution_engine.evidence import RunEvidence
from primeqa.execution_engine.result_store import persist_run_evidence
from primeqa.test_representation import SemanticTransactionCoordinator


def finalize_run(session, evidence: RunEvidence, *, coordinator=None):
    """Persist ``evidence`` and report its outcome as posture to S2.

    Args:
      session: active SQLAlchemy session — both writes join the caller's
        transaction (flush, not commit); the caller owns the commit boundary.
      evidence: the in-memory :class:`RunEvidence` the executor produced.
      coordinator: optional :class:`SemanticTransactionCoordinator` (injected
        for tests); default-constructed when omitted.

    Returns the :class:`RecipeRuntimeState` ``report_run_outcome`` produced —
    the post-callback runtime-state snapshot for the recipe.
    """
    persist_run_evidence(session, evidence)

    coord = coordinator or SemanticTransactionCoordinator()
    return coord.report_run_outcome(
        session,
        actor="s4",
        recipe_id=evidence.recipe_id,
        last_run_id=evidence.run_id,
        last_run_at=evidence.finished_at,
        last_run_outcome=evidence.outcome,
        last_run_recipe_version_seq=evidence.recipe_version_seq,
    )
