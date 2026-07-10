"""Unit: the S3 persister's identity dedup excludes DEPRECATED claims.

Deprecation is a human governance signal (D-ε-1) that a test is withdrawn; the
documented deprecate-then-regenerate procedure expects the regen to mint FRESH
(that is how a defective recipe — outside identity, D-110.3 — gets superseded).
Caught live on req-315: the deprecated 125%-witness VR08 claim captured the
post-P1 same-hash regen as a no-op, so the 25.01 minimally-violating witness
could never mint. Pure/offline — fake Coordinator, stubbed hash, no DB.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

import pytest

from primeqa.generation import persistence as P

pytestmark = pytest.mark.unit


def _persister(equivalents):
    persister = P.LedgerPersister.__new__(P.LedgerPersister)
    coord = mock.MagicMock()
    coord.query_equivalent_claims.return_value = equivalents
    coord.write_claim.return_value = SimpleNamespace(
        test_id=uuid4(), version_seq=1, was_noop=False)
    coord.write_recipe.return_value = SimpleNamespace(
        recipe_id=uuid4(), version_seq=1)
    persister._coordinator = coord
    return persister, coord


def _emission():
    return SimpleNamespace(
        archetype="data_behavior", claim_kind="prohibition-claim",
        asserted_truth={}, semantic_conditions={},
        trigger_kind="data-mutation-trigger", recipe_kind="data-recipe",
        causal_initiation=None, observation_realization=None,
        execution_environment=None, secondary_recipes=())


def _outcome():
    return SimpleNamespace(claims_written=None, equivalent_existing=None,
                           recipes_written=None, requirement_ref={})


def _claim(status):
    return SimpleNamespace(test_id=uuid4(), status=status)


def test_deprecated_equivalent_is_not_a_dedup_match(monkeypatch):
    # Sole same-hash match is deprecated -> mint FRESH (test_id=None).
    monkeypatch.setattr(P, "compute_identity_hash", lambda *a, **k: "h")
    persister, coord = _persister([_claim("deprecated")])
    persister._write_emission(mock.MagicMock(), _outcome(), _emission())
    assert coord.write_claim.call_args.kwargs["test_id"] is None


def test_live_equivalent_still_dedups(monkeypatch):
    # A draft same-hash match keeps the no-op re-version path (SPEC §7.7).
    monkeypatch.setattr(P, "compute_identity_hash", lambda *a, **k: "h")
    live = _claim("draft")
    persister, coord = _persister([live])
    persister._write_emission(mock.MagicMock(), _outcome(), _emission())
    assert coord.write_claim.call_args.kwargs["test_id"] == live.test_id


def test_deprecated_filtered_before_first_pick(monkeypatch):
    # deprecated sorts first by test_id -> must be skipped, the LIVE one picked.
    monkeypatch.setattr(P, "compute_identity_hash", lambda *a, **k: "h")
    dep, live = _claim("deprecated"), _claim("approved")
    persister, coord = _persister([dep, live])
    persister._write_emission(mock.MagicMock(), _outcome(), _emission())
    assert coord.write_claim.call_args.kwargs["test_id"] == live.test_id
