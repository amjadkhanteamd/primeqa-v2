"""Cross-run clustering over the S6 interpretation store (D-116).

Deterministic aggregation across many runs' interpretations into release-level
patterns: **recurring causes**, the **same validation rule** implicated across
runs, and **flapping** (alternating-outcome) claim_tests. Pure substrate —
read-only SELECTs over the per-tenant ``s6_interpretations`` table (the cause
axes promoted to typed columns by the D-116 migration), no LLM, no writes.

Mirrors the result-store's function style (no class). Each function takes a
caller-provided tenant-scoped ``session`` (isolation by schema — the substrate
convention) + an optional ``recipe_id`` filter; the grain is per-cause /
per-VR / per-claim (S6-Q-007). Release-grain is deferred — there is no
release→runs key on ``s6_interpretations`` yet.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from sqlalchemy import text


@dataclass(frozen=True)
class CauseCluster:
    """A ``cause_kind`` recurring across runs (e.g. repeated ``enforcement_gap``
    / ``vr_formula_drift``) — the recurring non-enforcement signal."""

    cause_kind: str
    count: int
    run_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class VrCluster:
    """A single validation rule implicated across runs, with the distinct
    outcomes it was implicated under — "the same VR across runs"."""

    vr_name: str
    count: int
    outcomes: tuple[str, ...]
    run_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class FlappingCluster:
    """A claim_test whose runs do not agree — more than one distinct outcome
    across the runs (passed *and* failed, etc.): the flapping signal."""

    claim_test_id: UUID
    outcomes: tuple[str, ...]
    run_ids: tuple[UUID, ...]


def cluster_recurring_causes(
    session, *, recipe_id: Optional[UUID] = None, min_runs: int = 2,
) -> list[CauseCluster]:
    """Group interpretations by deeper ``cause_kind`` (the promoted column),
    keeping kinds that recur across ``>= min_runs`` runs. Optionally scoped to a
    ``recipe_id``. Ordered most-frequent first."""
    sql = (
        "SELECT cause_kind, COUNT(*) AS n, "
        "       array_agg(run_id ORDER BY run_id) AS run_ids "
        "FROM s6_interpretations "
        "WHERE cause_kind IS NOT NULL "
        + ("AND recipe_id = CAST(:recipe_id AS uuid) " if recipe_id is not None else "")
        + "GROUP BY cause_kind "
        "HAVING COUNT(*) >= :min_runs "
        "ORDER BY n DESC, cause_kind"
    )
    params: dict = {"min_runs": min_runs}
    if recipe_id is not None:
        params["recipe_id"] = str(recipe_id)
    rows = session.execute(text(sql), params).mappings().all()
    return [CauseCluster(
        cause_kind=r["cause_kind"], count=r["n"],
        run_ids=tuple(r["run_ids"])) for r in rows]


def cluster_by_vr(
    session, *, recipe_id: Optional[UUID] = None, min_runs: int = 2,
) -> list[VrCluster]:
    """Group interpretations by the implicated validation rule (``vr_name``),
    keeping VRs implicated across ``>= min_runs`` runs, with the distinct
    outcomes under which each was implicated. Optionally scoped to a
    ``recipe_id``. Ordered most-frequent first."""
    sql = (
        "SELECT vr_name, COUNT(*) AS n, "
        "       array_agg(DISTINCT outcome::text ORDER BY outcome::text) AS outcomes, "
        "       array_agg(run_id ORDER BY run_id) AS run_ids "
        "FROM s6_interpretations "
        "WHERE vr_name IS NOT NULL "
        + ("AND recipe_id = CAST(:recipe_id AS uuid) " if recipe_id is not None else "")
        + "GROUP BY vr_name "
        "HAVING COUNT(*) >= :min_runs "
        "ORDER BY n DESC, vr_name"
    )
    params: dict = {"min_runs": min_runs}
    if recipe_id is not None:
        params["recipe_id"] = str(recipe_id)
    rows = session.execute(text(sql), params).mappings().all()
    return [VrCluster(
        vr_name=r["vr_name"], count=r["n"], outcomes=tuple(r["outcomes"]),
        run_ids=tuple(r["run_ids"])) for r in rows]


def cluster_flapping(
    session, *, recipe_id: Optional[UUID] = None,
) -> list[FlappingCluster]:
    """Claim_tests whose runs disagree — more than one distinct ``outcome``
    across the runs (the flapping signal). Uses the typed ``outcome`` column.
    Optionally scoped to a ``recipe_id``. Ordered by claim_test_id."""
    sql = (
        "SELECT claim_test_id, "
        "       array_agg(DISTINCT outcome::text ORDER BY outcome::text) AS outcomes, "
        "       array_agg(run_id ORDER BY run_id) AS run_ids "
        "FROM s6_interpretations "
        + ("WHERE recipe_id = CAST(:recipe_id AS uuid) " if recipe_id is not None else "")
        + "GROUP BY claim_test_id "
        "HAVING COUNT(DISTINCT outcome) > 1 "
        "ORDER BY claim_test_id"
    )
    params: dict = {}
    if recipe_id is not None:
        params["recipe_id"] = str(recipe_id)
    rows = session.execute(text(sql), params).mappings().all()
    return [FlappingCluster(
        claim_test_id=r["claim_test_id"], outcomes=tuple(r["outcomes"]),
        run_ids=tuple(r["run_ids"])) for r in rows]
