"""The S8 grounding-validity store (D-142) — persists + reads a claim version's
grounding-validity verdict.

One per-tenant table ``s8_grounding_validity``: typed identity (``test_id`` +
``version_seq`` — the claim version) + the contemporaneous S1 pin
(``evaluated_at_version_seq``) + the composed verdict (``overall`` /
``claim_verdict``), with the rich per-recipe part in a ``detail`` JSONB. The
precise mirror of the S6 interpretation store (``interpretation/result_store.py``)
one substrate over: S6 persists a run's *meaning*, S8 persists a claim version's
*grounding validity*. The DDL is in
``alembic/versions/tenant/20260603_0030_s8_grounding_validity.py``; this module is
the Python projection + the persister + the read API.

**The thin mechanics (D-142).** Persist + read only — NO sync trigger / reverse
index / orchestration (those stay fenced; the recompute trigger is slice 5). The
pure core (the legs + ``grounding_validity``) stays DB-free — only this module
imports ``Base``.

**Persistence boundary.** :func:`persist_grounding_validity` is the only writer —
an **UPSERT** on ``(test_id, version_seq)`` (re-grounding a claim version at a
later S1 seq refreshes the row), add/update + flush, **no commit** (the caller
owns the transaction — the substrate convention).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import Column, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from primeqa.db import Base
from primeqa.evolution.grounding_validity import GroundingValidity

_LIST_HARD_CAP = 500  # the substrate list-bound convention.


class S8GroundingValidity(Base):
    """One S8 grounding-validity verdict for one grounded claim version.

    Per-tenant (no ``tenant_id`` — isolation by schema). Identity + the composed
    verdict are typed columns; the per-recipe rich part lives in ``detail``
    JSONB. PK ``(test_id, version_seq)`` — one verdict per claim version (the
    logical link to ``test_claims``, not a DB-enforced FK)."""

    __tablename__ = "s8_grounding_validity"
    __test__ = False  # pytest collection: not a test class

    test_id = Column(UUID(as_uuid=True), primary_key=True, nullable=False)
    version_seq = Column(Integer, primary_key=True, nullable=False)
    # The S1 seq the verdict was computed against (the snapshot axis, S8-Q-007).
    evaluated_at_version_seq = Column(Integer, nullable=False)
    # The composed verdict (TEXT — the Python GroundingVerdict Literal is the
    # source of truth).
    overall = Column(Text, nullable=False)
    claim_verdict = Column(Text, nullable=False)
    # The rich part: claim-grounding + per-recipe leg verdicts + roll-ups.
    detail = Column(JSONB, nullable=False)


def persist_grounding_validity(
    session, *, test_id, version_seq, evaluated_at_version_seq,
    validity: GroundingValidity,
):
    """Persist one :class:`GroundingValidity` as an ``s8_grounding_validity`` row.

    An **UPSERT** on ``(test_id, version_seq)``: if the claim version already has
    a verdict (a prior grounding), the row is **refreshed** in place (new
    ``evaluated_at_version_seq`` + verdicts + ``detail``) rather than colliding —
    the recompute path (slice 5) re-grounds the same claim version at a later S1
    seq. add/update + flush, **no commit** (the caller owns the transaction)."""
    detail = _detail(validity)
    row = (session.query(S8GroundingValidity)
           .filter(S8GroundingValidity.test_id == test_id,
                   S8GroundingValidity.version_seq == version_seq)
           .one_or_none())
    if row is None:
        row = S8GroundingValidity(
            test_id=test_id, version_seq=version_seq,
            evaluated_at_version_seq=evaluated_at_version_seq,
            overall=validity.overall,
            claim_verdict=validity.claim_grounding.verdict,
            detail=detail)
        session.add(row)
    else:
        row.evaluated_at_version_seq = evaluated_at_version_seq
        row.overall = validity.overall
        row.claim_verdict = validity.claim_grounding.verdict
        row.detail = detail
    session.flush()
    return test_id


@dataclass(frozen=True)
class GroundingValidityRead:
    """The read-side projection of an ``s8_grounding_validity`` row — the verdict
    + the contemporaneous S1 pin + the rich ``detail``."""

    test_id: uuid.UUID
    version_seq: int
    evaluated_at_version_seq: int
    overall: str
    claim_verdict: str
    detail: dict


def read_grounding_validity(
    session, test_id, version_seq,
) -> Optional[GroundingValidityRead]:
    """Read one verdict by ``(test_id, version_seq)`` (the PK) on the caller's
    tenant-scoped session, or None when absent."""
    row = (session.query(S8GroundingValidity)
           .filter(S8GroundingValidity.test_id == test_id,
                   S8GroundingValidity.version_seq == version_seq)
           .one_or_none())
    return _row_to_read(row) if row is not None else None


def list_grounding_validity(
    session, *, test_id=None, overall=None, limit: int = 200,
) -> list[GroundingValidityRead]:
    """List verdicts on the caller's tenant-scoped session, optionally scoped by
    ``test_id`` (all versions of a test) or ``overall`` (e.g. all ``drifted`` /
    ``broken`` — the standing-verdict consumer query). Ordered by
    ``(test_id, version_seq)`` (deterministic) and bounded by ``min(limit, 500)``
    (the substrate list-bound convention)."""
    q = session.query(S8GroundingValidity)
    if test_id is not None:
        q = q.filter(S8GroundingValidity.test_id == test_id)
    if overall is not None:
        q = q.filter(S8GroundingValidity.overall == overall)
    rows = (q.order_by(S8GroundingValidity.test_id, S8GroundingValidity.version_seq)
            .limit(min(int(limit), _LIST_HARD_CAP)).all())
    return [_row_to_read(r) for r in rows]


def _detail(v: GroundingValidity) -> dict:
    """Build the ``detail`` JSONB — the rich part not promoted to a typed column:
    the claim-grounding reason/unresolved + each recipe's leg verdicts. (Recipe
    bodies are NOT serialized — only their verdicts; payload values are already
    JSON-safe by S2 construction.)"""
    return {
        "claim_grounding": {
            "verdict": v.claim_grounding.verdict,
            "reason": v.claim_grounding.reason,
            "unresolved": [list(p) for p in v.claim_grounding.unresolved],
        },
        "recipe_verdicts": [
            {
                "recipe_grounding": {
                    "verdict": rv.recipe_grounding.verdict,
                    "reason": rv.recipe_grounding.reason,
                },
                "field_value": {
                    "verdict": rv.field_value.verdict,
                    "reason": rv.field_value.reason,
                    "invalid": [list(x) for x in rv.field_value.invalid],
                },
                "rolled_up": rv.rolled_up,
            }
            for rv in v.recipe_verdicts
        ],
    }


def _row_to_read(row) -> GroundingValidityRead:
    return GroundingValidityRead(
        test_id=row.test_id, version_seq=row.version_seq,
        evaluated_at_version_seq=row.evaluated_at_version_seq,
        overall=row.overall, claim_verdict=row.claim_verdict,
        detail=row.detail)
