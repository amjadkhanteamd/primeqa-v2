"""The S8 grounding-validity store (D-142) — persists + reads a claim version's
grounding-validity verdict.

One per-tenant table ``s8_grounding_validity``: typed identity (``test_id`` +
``version_seq`` + ``connected_org_id`` — the claim version PER ORG; grounding
depends on one org's model, so the verdict is org-keyed since 3f Slices 2-3) +
the contemporaneous S1 pin (``evaluated_at_version_seq``) + the composed
verdict (``overall`` / ``claim_verdict``), with the rich per-recipe part in a
``detail`` JSONB. The precise mirror of the S6 interpretation store
(``interpretation/result_store.py``) one substrate over: S6 persists a run's
*meaning*, S8 persists a claim version's *grounding validity*. The DDL is in
``alembic/versions/tenant/20260603_0030_s8_grounding_validity.py`` (+ the
org re-key in ``20260707_0010_s8_grounding_org.py``); this module is the
Python projection + the persister + the read API.

**The thin mechanics (D-142).** Persist + read only — NO sync trigger / reverse
index / orchestration (those stay fenced; the recompute trigger is slice 5). The
pure core (the legs + ``grounding_validity``) stays DB-free — only this module
imports ``Base``.

**Persistence boundary.** :func:`persist_grounding_validity` is the only writer —
an **UPSERT** on ``(test_id, version_seq, connected_org_id)`` (re-grounding a
claim version at a later S1 seq refreshes the row), add/update + flush, **no
commit** (the caller owns the transaction — the substrate convention).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import Column, Integer, Text, case
from sqlalchemy.dialects.postgresql import JSONB, UUID

from primeqa.db import Base
from primeqa.evolution.grounding_validity import GroundingValidity

_LIST_HARD_CAP = 500  # the substrate list-bound convention.


class S8GroundingValidity(Base):
    """One S8 grounding-validity verdict for one grounded claim version in one
    org.

    Per-tenant (no ``tenant_id`` — isolation by schema). Identity + the composed
    verdict are typed columns; the per-recipe rich part lives in ``detail``
    JSONB. PK ``(test_id, version_seq, connected_org_id)`` — one verdict per
    claim version PER ORG (the logical link to ``test_claims``, not a
    DB-enforced FK)."""

    __tablename__ = "s8_grounding_validity"
    __test__ = False  # pytest collection: not a test class

    test_id = Column(UUID(as_uuid=True), primary_key=True, nullable=False)
    version_seq = Column(Integer, primary_key=True, nullable=False)
    connected_org_id = Column(UUID(as_uuid=True), primary_key=True,
                              nullable=False)
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
    validity: GroundingValidity, connected_org_id,
):
    """Persist one :class:`GroundingValidity` as an ``s8_grounding_validity`` row.

    An **UPSERT** on ``(test_id, version_seq, connected_org_id)``: if the claim
    version already has a verdict for that org (a prior grounding), the row is
    **refreshed** in place (new ``evaluated_at_version_seq`` + verdicts +
    ``detail``) rather than colliding — the recompute path (slice 5) re-grounds
    the same claim version at a later S1 seq. ``connected_org_id`` is REQUIRED:
    a verdict without an org identity is exactly the blend D-265 refused.
    add/update + flush, **no commit** (the caller owns the transaction)."""
    org = uuid.UUID(str(connected_org_id))
    detail = _detail(validity)
    row = (session.query(S8GroundingValidity)
           .filter(S8GroundingValidity.test_id == test_id,
                   S8GroundingValidity.version_seq == version_seq,
                   S8GroundingValidity.connected_org_id == org)
           .one_or_none())
    if row is None:
        row = S8GroundingValidity(
            test_id=test_id, version_seq=version_seq,
            connected_org_id=org,
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
    + the org it grounds against + the contemporaneous S1 pin + the rich
    ``detail``."""

    test_id: uuid.UUID
    version_seq: int
    evaluated_at_version_seq: int
    overall: str
    claim_verdict: str
    detail: dict
    connected_org_id: Optional[uuid.UUID] = None


# Worst-first severity for the org-blind read: broken < drifted < intact.
_OVERALL_SEVERITY = case(
    (S8GroundingValidity.overall == "broken", 0),
    (S8GroundingValidity.overall == "drifted", 1),
    else_=2,
)


def read_grounding_validity(
    session, test_id, version_seq, connected_org_id=None,
) -> Optional[GroundingValidityRead]:
    """Read one verdict by ``(test_id, version_seq)`` on the caller's
    tenant-scoped session, or None when absent.

    ``connected_org_id`` selects that org's verdict. ``None`` (the org-blind
    callers) returns the WORST verdict across orgs (broken < drifted < intact)
    — pessimistic is the safe read for a release gate, and with one org's rows
    it is exactly that org's verdict. (The pre-org ``one_or_none`` would raise
    ``MultipleResultsFound`` the moment two orgs have rows.)"""
    q = (session.query(S8GroundingValidity)
         .filter(S8GroundingValidity.test_id == test_id,
                 S8GroundingValidity.version_seq == version_seq))
    if connected_org_id is not None:
        row = q.filter(S8GroundingValidity.connected_org_id
                       == uuid.UUID(str(connected_org_id))).one_or_none()
    else:
        row = q.order_by(_OVERALL_SEVERITY,
                         S8GroundingValidity.connected_org_id).first()
    return _row_to_read(row) if row is not None else None


def list_grounding_validity(
    session, *, test_id=None, overall=None, connected_org_id=None,
    limit: int = 200,
) -> list[GroundingValidityRead]:
    """List verdicts on the caller's tenant-scoped session, optionally scoped by
    ``test_id`` (all versions of a test), ``overall`` (e.g. all ``drifted`` /
    ``broken`` — the standing-verdict consumer query) or ``connected_org_id``
    (one org's verdicts). Ordered by ``(test_id, version_seq,
    connected_org_id)`` (deterministic) and bounded by ``min(limit, 500)``
    (the substrate list-bound convention)."""
    q = session.query(S8GroundingValidity)
    if test_id is not None:
        q = q.filter(S8GroundingValidity.test_id == test_id)
    if overall is not None:
        q = q.filter(S8GroundingValidity.overall == overall)
    if connected_org_id is not None:
        q = q.filter(S8GroundingValidity.connected_org_id
                     == uuid.UUID(str(connected_org_id)))
    rows = (q.order_by(S8GroundingValidity.test_id,
                       S8GroundingValidity.version_seq,
                       S8GroundingValidity.connected_org_id)
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
        detail=row.detail, connected_org_id=row.connected_org_id)
