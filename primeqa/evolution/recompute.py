"""S8 S1-sync recompute trigger (D-143) — recompute grounding-validity over a
tenant's current claims when its S1 advances, refreshing the store.

Three layers:
  - :func:`load_current_artifacts` — the S2 read: the tenant's current claims +
    their data-recipes, decoded into :class:`Artifact`s.
  - :func:`recompute_grounding` — the testable orchestration: per artifact,
    **freshness** (a store row at the current S1 seq) gates the work; stale /
    ungrounded artifacts are grounded (up to ``cap``) + persisted.
  - :func:`recompute_tenant_grounding` / :func:`run_s8_grounding_tick` — the
    production wrappers (a tenant-scoped connection + the tri-port reader; then
    per-tenant fan-out for the scheduler).

**Freshness, not a watermark (D-143).** A claim is *fresh* iff its stored verdict's
``evaluated_at_version_seq`` equals the current S1 seq — derived from the store, so
no watermark table. This is cap-correct: a partial recompute leaves stale claims
un-fresh, so the next tick resumes them. When S1 advances, every claim is stale →
re-grounded (recompute-all; the change→impact reverse index that would narrow this
is deferred).
"""
from __future__ import annotations

import logging
from collections import namedtuple
from dataclasses import dataclass

from sqlalchemy import text

from primeqa.evolution.grounding_validity import Artifact, grounding_validity
from primeqa.evolution.result_store import (
    persist_grounding_validity,
    read_grounding_validity,
)
from primeqa.evolution.s1_reader import S8S1Reader
from primeqa.test_representation.coordinator import SemanticTransactionCoordinator
from primeqa.test_representation.models.recipes.data_recipe import DataRecipeBody

log = logging.getLogger(__name__)

_DEFAULT_CAP = 100
_coordinator = SemanticTransactionCoordinator()  # stateless; reuse for the process.

ArtifactRef = namedtuple("ArtifactRef", ["test_id", "version_seq", "artifact"])


@dataclass(frozen=True)
class RecomputeResult:
    """The outcome of one recompute pass: how many claims were (re)grounded +
    persisted, how many were already fresh (skipped), and how many stale claims
    were deferred past the per-tick ``cap`` (to be resumed next tick)."""

    grounded: int
    skipped_fresh: int
    remaining: int


def load_current_artifacts(session) -> list[ArtifactRef]:
    """Read the tenant's **current** claims (``valid_to IS NULL``) + their active
    data-recipes, decoded into :class:`Artifact`s. Non-data recipes are dropped
    (the recipe legs are data-recipe-only). There is no ready S2 "list current
    claims" API, so the test_id/version_seq enumeration is a direct read; the
    body decode goes through the coordinator (the S2 read surface)."""
    rows = session.execute(text(
        "SELECT test_id, version_seq FROM test_claims "
        "WHERE valid_to IS NULL ORDER BY test_id")).all()
    refs: list[ArtifactRef] = []
    for test_id, version_seq in rows:
        claim = _coordinator.get_latest_claim(session, test_id)
        if claim is None:
            continue
        recipes = tuple(
            r.observation_realization
            for r in _coordinator.list_active_recipes(session, test_id)
            if isinstance(r.observation_realization, DataRecipeBody))
        refs.append(ArtifactRef(
            test_id, version_seq,
            Artifact(claim=claim.asserted_truth, recipes=recipes)))
    return refs


def recompute_grounding(
    session, artifact_refs, *, s1, at_seq: int, cap: int = _DEFAULT_CAP,
) -> RecomputeResult:
    """Recompute + persist grounding-validity for the stale artifacts among
    ``artifact_refs`` (the testable core — ``s1`` is the injected tri-port).

    Per ref: **fresh** (a store row at ``at_seq``) → skip; **stale / absent** →
    ground via :func:`grounding_validity` (``s1`` serves all three ports) +
    :func:`persist_grounding_validity`, up to ``cap`` groundings. Stale refs left
    past the cap are counted ``remaining`` (resumed next tick) — never silently
    dropped."""
    grounded = skipped_fresh = remaining = 0
    for ref in artifact_refs:
        existing = read_grounding_validity(session, ref.test_id, ref.version_seq)
        if existing is not None and existing.evaluated_at_version_seq == at_seq:
            skipped_fresh += 1
            continue
        if grounded >= cap:
            remaining += 1
            continue
        validity = grounding_validity(
            ref.artifact, subjects=s1, vrs=s1, picklists=s1)
        persist_grounding_validity(
            session, test_id=ref.test_id, version_seq=ref.version_seq,
            evaluated_at_version_seq=at_seq, validity=validity)
        grounded += 1
    if remaining:
        log.info("s8 recompute: %d grounded, %d deferred past cap=%d",
                 grounded, remaining, cap)
    return RecomputeResult(grounded, skipped_fresh, remaining)


def recompute_tenant_grounding(tenant_id: int, *, cap: int = _DEFAULT_CAP) -> int:
    """The production wrapper: open a tenant-scoped connection, build the tri-port
    reader pinned at the current S1 seq, and recompute. A tenant with no S1
    versions yet (``VersionNotFoundError``) has nothing to ground → 0. Returns the
    number (re)grounded. The connection context owns the commit."""
    from sqlalchemy.orm import Session

    from primeqa.semantic.connection import get_tenant_connection
    from primeqa.semantic.query import SemanticOrgModel, VersionNotFoundError

    with get_tenant_connection(tenant_id) as conn:
        model = SemanticOrgModel(conn)
        try:
            at_seq = model.current_version_seq()
        except VersionNotFoundError:
            return 0
        s1 = S8S1Reader(model, at_seq=at_seq)
        session = Session(bind=conn)
        refs = load_current_artifacts(session)
        result = recompute_grounding(session, refs, s1=s1, at_seq=at_seq, cap=cap)
        return result.grounded


def run_s8_grounding_tick(tenant_ids, *, cap: int = _DEFAULT_CAP) -> dict:
    """The scheduler fan-out (mirrors ``run_s3_reaper_tick``): recompute each
    tenant with per-tenant isolation (one tenant's failure never starves the
    others). Returns ``{tenant_id: grounded_count}``."""
    out: dict = {}
    for tid in tenant_ids:
        try:
            out[tid] = recompute_tenant_grounding(tid, cap=cap)
        except Exception as exc:
            log.warning("s8_grounding_tick: tenant %s failed: %s", tid, exc)
            out[tid] = 0
    return out
