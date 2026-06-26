"""S6 batch reader + completeness gate (Slice 4c.1, D-282) — DORMANT.

Reads a claim's most-recent run-all batch and decides COMPLETENESS as a
MEMBERSHIP set-compare against the persisted manifest (`s4_runall_batch_manifests`,
D-281). On a COMPLETE batch it builds the ``list[ProbeResult]`` the ``bva``
strategy arm grades (strict-AND, D-279); on an INCOMPLETE batch (an expected
probe has no row) it short-circuits to not-Verified WITHOUT touching the arm.

LOCKED decisions (Slice 4c recon):
  * **A (C2):** the expected set is read from the persisted manifest — NEVER
    recomputed via ``select_recipes_for_execution`` at read time. Completeness is
    therefore drift-immune (the live recipe/claim state can change after the batch
    ran without affecting the recorded expectation).
  * **B (short-circuit):** INCOMPLETE is decided HERE → not-Verified, WITHOUT
    building ProbeResults or calling the bva arm — the arm only ever sees COMPLETE
    sets (preserving the "trusts the set is complete" contract, D-279). An
    errored-row-PRESENT probe is COMPLETE-but-indeterminate (the row exists; the arm
    returns not-Verified on the indeterminate). A row-ABSENT probe is INCOMPLETE.
  * **C (latest-is-truth):** the most-recent batch by ``max(finished_at)`` over its
    rows — NOT by ``batch_id`` (uuid4 is non-chronological). A newer incomplete
    batch is not-Verified / re-run; there is NO fallback to an older complete batch.

The reader does NOT call :func:`apply_strategy`: it returns ``{complete, probes}``;
Slice 4d does ``incomplete → not-Verified`` / ``complete → apply_strategy('bva',
probes)``. The strict-AND stays in the arm.

DORMANT: no caller in ``primeqa/`` until 4d wires it into the decision engine's bva
branch. Read-only — never writes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from sqlalchemy import text

from primeqa.interpretation.strategy import ProbeResult, probe_result_from_outcome

_RUNALL_SOURCE = "runall_probe"


@dataclass(frozen=True)
class BatchCompleteness:
    """The reader's verdict-input for a claim's most-recent run-all batch.

    - ``complete`` — every EXPECTED probe (per the manifest) has a batch row.
    - ``probes``   — the ProbeResults for the bva arm, ONLY when ``complete``;
      ``None`` when incomplete / no batch / no manifest (the arm is never built or
      called — Decision B).
    - ``batch_id`` — the resolved most-recent batch (traceability), or ``None``.
    - ``reason``   — a short machine tag: ``complete`` | ``incomplete`` |
      ``no_batch`` | ``no_manifest``.
    """

    complete: bool
    probes: Optional[tuple[ProbeResult, ...]]
    batch_id: Optional[UUID] = None
    reason: str = ""


# --- SQL — persisted batch + manifest data ONLY (no live recompute) ----------

# The most-recent batch ORDERED BY max(finished_at) of its rows — NOT batch_id.
_MOST_RECENT_BATCH_SQL = text(
    "SELECT batch_id, max(finished_at) AS last_at "
    "FROM s4_execution_runs "
    "WHERE claim_test_id = :tid AND source = :src AND batch_id IS NOT NULL "
    "GROUP BY batch_id ORDER BY last_at DESC LIMIT 1")

_MANIFEST_SQL = text(
    "SELECT expected_recipe_ids FROM s4_runall_batch_manifests "
    "WHERE batch_id = :bid")

# A batch's probe rows + (best-effort) the recipe body for polarity + the s6
# verdict for diagnostics. The OUTCOME for the verdict comes from
# s4_execution_runs.outcome (what the bva adapter consumes); the recipe body +
# s6 verdict are carried for fidelity/explainability, not the verdict.
_BATCH_ROWS_SQL = text(
    "SELECT r.run_id, r.recipe_id, r.outcome, "
    "       rec.observation_realization AS body, i.verdict AS s6_verdict "
    "FROM s4_execution_runs r "
    "LEFT JOIN test_recipes rec "
    "  ON rec.recipe_id = r.recipe_id AND rec.version_seq = r.recipe_version_seq "
    "LEFT JOIN s6_interpretations i ON i.run_id = r.run_id "
    "WHERE r.batch_id = :bid AND r.source = :src AND r.claim_test_id = :tid")


def select_most_recent_batch(session, claim_test_id) -> Optional[UUID]:
    """The claim's most-recent run-all ``batch_id``, ordered by ``max(finished_at)``
    over the batch's rows (Decision C) — NOT by ``batch_id`` (uuid4 is
    non-chronological). ``None`` when the claim has no run-all batch."""
    row = session.execute(
        _MOST_RECENT_BATCH_SQL,
        {"tid": str(claim_test_id), "src": _RUNALL_SOURCE}).first()
    return row[0] if row is not None else None


def _extract_expect_rejection(body) -> bool:
    """Whether a recipe body (``observation_realization`` JSONB → dict) carries a
    behavioral-negative step (some ``steps[].expect_rejection`` is non-null). NOT
    load-bearing for the bva verdict (the arm reads only ``outcome``) — carried on
    the ProbeResult for polarity / diagnostics. Tolerant: a missing / non-dict body
    → ``False``."""
    if not isinstance(body, dict):
        return False
    for step in body.get("steps") or []:
        if isinstance(step, dict) and step.get("expect_rejection") is not None:
            return True
    return False


def _assess(expected_recipe_ids, batch_rows) -> tuple[bool, Optional[tuple]]:
    """PURE completeness decision (Decision A membership + B short-circuit).

    ``expected_recipe_ids`` — from the persisted manifest. ``batch_rows`` — the
    batch's rows as mappings ``{recipe_id, outcome, body, ...}``. COMPLETE iff
    every expected recipe has a row (``expected ⊆ present`` — ``⊆`` not ``==`` so an
    extra row never makes it incomplete; the load-bearing direction is "every
    expected probe has a row"). **INCOMPLETE → ``(False, None)``: NO ProbeResults
    are built and the arm is never reached.** COMPLETE → ``(True, probes)``: one
    ProbeResult per EXPECTED recipe, in the manifest's order (an ``errored`` row →
    ``indeterminate`` flows to the arm, which then returns not-Verified)."""
    expected = [str(r) for r in expected_recipe_ids]
    by_recipe = {str(row["recipe_id"]): row for row in batch_rows}
    if not all(rid in by_recipe for rid in expected):     # row-ABSENT ⇒ INCOMPLETE
        return False, None
    probes = tuple(
        probe_result_from_outcome(
            by_recipe[rid]["outcome"],
            expect_rejection=_extract_expect_rejection(by_recipe[rid].get("body")))
        for rid in expected)
    return True, probes


def read_batch_completeness(session, claim_test_id) -> BatchCompleteness:
    """Read the claim's most-recent run-all batch and decide completeness.

    The completeness path reads ONLY persisted batch + manifest data — it NEVER
    calls ``select_recipes_for_execution`` and NEVER resolves the live environment
    (the drift-immunity guarantee). Returns a :class:`BatchCompleteness`; the
    caller (4d) treats ``complete=False`` as not-Verified and grades
    ``complete=True`` via ``apply_strategy('bva', result.probes)``."""
    batch_id = select_most_recent_batch(session, claim_test_id)
    if batch_id is None:
        return BatchCompleteness(complete=False, probes=None, reason="no_batch")

    mrow = session.execute(_MANIFEST_SQL, {"bid": batch_id}).first()
    if mrow is None or mrow[0] is None:
        # No manifest for a batch that has rows shouldn't happen (4c.0 commits it
        # early), but if it does, completeness is not knowable → not-Verified (the
        # safe default, same as row-absent).
        return BatchCompleteness(complete=False, probes=None, batch_id=batch_id,
                                 reason="no_manifest")
    expected_recipe_ids = list(mrow[0])

    rows = session.execute(
        _BATCH_ROWS_SQL,
        {"bid": batch_id, "src": _RUNALL_SOURCE, "tid": str(claim_test_id)},
    ).mappings().all()
    complete, probes = _assess(expected_recipe_ids, [dict(r) for r in rows])
    return BatchCompleteness(
        complete=complete, probes=probes, batch_id=batch_id,
        reason="complete" if complete else "incomplete")
