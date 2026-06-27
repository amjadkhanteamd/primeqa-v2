"""Unit: the S3 ledger persister threads the bundle's strategy_kind into
write_claim (D-288, 4f.2-prep). Pure/offline — a fake Coordinator + a stubbed
identity hash, no DB, no Salesforce.

LedgerPersister._write_emission reads ``emission.strategy_kind`` and passes it to
``coordinator.write_claim(..., strategy_kind=...)``. Today every authoring path
leaves it None → write_claim persists NULL → the router/decision read None →
single (byte-identical with pre-D-288). The future bva-authoring helper (4f.2b)
stamps 'bva'; this proves the wire carries whatever the bundle holds.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

import pytest

from primeqa.generation import persistence as P

pytestmark = pytest.mark.unit


def _persister():
    # skip __init__ (it builds a real Coordinator) — _write_emission only touches
    # self._coordinator, which we replace with a mock.
    persister = P.LedgerPersister.__new__(P.LedgerPersister)
    coord = mock.MagicMock()
    coord.query_equivalent_claims.return_value = []           # no dedup match
    coord.write_claim.return_value = SimpleNamespace(
        test_id=uuid4(), version_seq=1, was_noop=False)
    coord.write_recipe.return_value = SimpleNamespace(
        recipe_id=uuid4(), version_seq=1)
    persister._coordinator = coord
    return persister, coord


def _outcome():
    return SimpleNamespace(claims_written=None, equivalent_existing=None,
                           recipes_written=None, requirement_ref={})


def _emission(**over):
    base = dict(
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth={}, semantic_conditions={},
        trigger_kind="data-mutation-trigger", recipe_kind="data-recipe",
        causal_initiation=None, observation_realization=None,
        execution_environment=None, secondary_recipes=())
    base.update(over)
    return SimpleNamespace(**base)


def test_write_emission_passes_strategy_kind_through(monkeypatch):
    monkeypatch.setattr(P, "compute_identity_hash", lambda *a, **k: "h")
    persister, coord = _persister()
    persister._write_emission(mock.MagicMock(), _outcome(),
                              _emission(strategy_kind="bva"))
    assert coord.write_claim.call_args.kwargs["strategy_kind"] == "bva"


def test_write_emission_passes_none_when_slot_is_none(monkeypatch):
    monkeypatch.setattr(P, "compute_identity_hash", lambda *a, **k: "h")
    persister, coord = _persister()
    persister._write_emission(mock.MagicMock(), _outcome(),
                              _emission(strategy_kind=None))
    assert coord.write_claim.call_args.kwargs["strategy_kind"] is None


def test_write_emission_defaults_none_for_legacy_bundle_without_slot(monkeypatch):
    # a hand-built / pre-D-288 bundle with NO strategy_kind attribute still works:
    # getattr(emission, "strategy_kind", None) yields None → NULL.
    monkeypatch.setattr(P, "compute_identity_hash", lambda *a, **k: "h")
    persister, coord = _persister()
    emission = _emission()                 # base seeds no strategy_kind attr at all
    assert not hasattr(emission, "strategy_kind")
    persister._write_emission(mock.MagicMock(), _outcome(), emission)
    assert coord.write_claim.call_args.kwargs["strategy_kind"] is None


def test_write_emission_noop_path_does_not_write_strategy_kind_recipe(monkeypatch):
    # same-hash regeneration: write_claim returns was_noop=True → the persister
    # returns before any recipe write, and write_claim still received strategy_kind.
    monkeypatch.setattr(P, "compute_identity_hash", lambda *a, **k: "h")
    persister, coord = _persister()
    coord.write_claim.return_value = SimpleNamespace(
        test_id=uuid4(), version_seq=2, was_noop=True)
    persister._write_emission(mock.MagicMock(), _outcome(),
                              _emission(strategy_kind="bva"))
    assert coord.write_claim.call_args.kwargs["strategy_kind"] == "bva"
    coord.write_recipe.assert_not_called()
