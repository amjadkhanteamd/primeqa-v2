"""Unit tests for 1b.2 per-category delta fetch (ValidationRule).

Three layers, none touching a DB:
  * the allow-list gate (``delta.delta_since_for``),
  * the SF-client delta SOQL + the cheap id-list, and
  * ``phase_validation_rule`` orchestration — that the delta path fetches with
    the watermark, ALWAYS reconciles (even on an empty add/mod delta), the full
    path does neither, and any SF error in the delta+id-list unit falls back to a
    full fetch with NO reconcile (fail toward full-fetch).

The real-DB SCD-2 supersede + edge close (and the red-on-disable deletion proof)
live in tests/integration/semantic/test_delta_reconcile_live.py — this file mocks
the materialize/reconcile boundary and asserts the ORCHESTRATION.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock
from unittest.mock import MagicMock

import pytest

from primeqa.sync import delta
from primeqa.sync.delta import delta_since_for, DELTA_SAFE_ENTITY_TYPES

pytestmark = pytest.mark.unit

_T = datetime(2026, 6, 19, 12, 0, 0, tzinfo=timezone.utc)


# ----------------------------------------------------------------------
# 1. allow-list gate
# ----------------------------------------------------------------------
class TestDeltaSinceFor:
    def test_vr_with_window_returns_the_watermark(self) -> None:
        ctx = SimpleNamespace(delta_since=_T)
        assert delta_since_for(ctx, "ValidationRule") == _T

    def test_off_allow_list_is_always_none_even_with_window(self) -> None:
        # Object is NOT delta-safe — the gate funnels it to full-fetch regardless
        # of a present window.
        ctx = SimpleNamespace(delta_since=_T)
        assert delta_since_for(ctx, "Object") is None
        assert "Object" not in DELTA_SAFE_ENTITY_TYPES

    def test_no_window_is_none_for_a_delta_safe_type(self) -> None:
        # Resume / no watermark → ctx.delta_since is None → full-fetch.
        assert delta_since_for(SimpleNamespace(delta_since=None), "ValidationRule") is None

    def test_missing_attr_is_none(self) -> None:
        # Defensive: a context without the attribute at all → None (full-fetch).
        assert delta_since_for(SimpleNamespace(), "ValidationRule") is None

    def test_allow_list_is_vr_only_this_slice(self) -> None:
        assert DELTA_SAFE_ENTITY_TYPES == frozenset({"ValidationRule"})


# ----------------------------------------------------------------------
# 2. SF-client delta SOQL + cheap id-list
# ----------------------------------------------------------------------
def _client() -> "object":
    from primeqa.integrations.sf_client import SalesforceClient
    return SalesforceClient(
        instance_url="https://test.salesforce.com",
        client_id="x", client_secret="y", refresh_token="z",
    )


class TestFetchValidationRulesDelta:
    def test_full_fetch_has_no_where(self) -> None:
        c = _client()
        c._query_all = MagicMock(return_value=[])  # phase-1 empty → no phase-2
        c.fetch_validation_rules()
        soql = c._query_all.call_args[0][1]
        assert "FROM ValidationRule" in soql
        assert "WHERE" not in soql

    def test_delta_fetch_adds_lastmodifieddate_where(self) -> None:
        c = _client()
        c._query_all = MagicMock(return_value=[])
        c.fetch_validation_rules(modified_since=_T)
        soql = c._query_all.call_args[0][1]
        # the filter column + a `>` comparison against the SOQL Z-literal
        assert "WHERE LastModifiedDate >" in soql
        assert "2026-06-19T12:00:00Z" in soql
        # LastModifiedDate is also projected (harmless; volatile-stripped)
        assert "LastModifiedDate " in soql.split("WHERE")[0]

    def test_fetch_validation_rule_ids_is_cheap_id_only(self) -> None:
        c = _client()
        c._query_all = MagicMock(return_value=[{"Id": "a1"}, {"Id": "b2"}, {"Id": None}])
        ids = c.fetch_validation_rule_ids()
        assert ids == {"a1", "b2"}            # None filtered out
        assert c._query_all.call_args[0][1] == "SELECT Id FROM ValidationRule"


# ----------------------------------------------------------------------
# 3. phase_validation_rule orchestration
# ----------------------------------------------------------------------
def _ctx(delta_since):
    sf = MagicMock(name="sf_client")
    return SimpleNamespace(sf_client=sf, delta_since=delta_since), sf


def _vr(full_name="Account.Foo", _id="vr1"):
    return {"Id": _id, "FullName": full_name, "ValidationName": full_name.split(".")[-1],
            "Active": True, "EntityDefinitionId": "01I", "Metadata": {}}


class TestPhaseValidationRuleOrchestration:
    """Patch the materialize/reconcile boundary + the synced-object scope; assert
    which SF calls fire and whether the reconcile runs. No DB."""

    def _run(self, *, delta_since, fetch_rules_side_effect=None,
             fetch_ids_return=None):
        from primeqa.sync import phases
        ctx, sf = _ctx(delta_since)
        if fetch_rules_side_effect is not None:
            sf.fetch_validation_rules.side_effect = fetch_rules_side_effect
        if fetch_ids_return is not None:
            sf.fetch_validation_rule_ids.return_value = fetch_ids_return
        conn = MagicMock()
        with mock.patch.object(phases, "_synced_object_api_names",
                               return_value={"Account"}), \
             mock.patch.object(phases, "batched_materialize",
                               return_value={"Account.Foo": "eid1"}) as bm, \
             mock.patch.object(phases, "materialize_edges_for_entities") as me, \
             mock.patch.object(phases, "reconcile_deletions_by_sf_id",
                               return_value=0) as rec:
            phases.phase_validation_rule(ctx, conn)
        return ctx, sf, bm, me, rec

    def test_delta_path_fetches_with_watermark_and_reconciles(self) -> None:
        ctx, sf, bm, me, rec = self._run(
            delta_since=_T,
            fetch_rules_side_effect=[[_vr()]],
            fetch_ids_return={"vr1"},
        )
        sf.fetch_validation_rules.assert_called_once_with(modified_since=_T)
        sf.fetch_validation_rule_ids.assert_called_once()
        bm.assert_called_once()
        assert bm.call_args.kwargs["raw_payloads"] == [
            {**_vr(), "_parent_object_api_name": "Account"}]
        rec.assert_called_once()
        assert rec.call_args[0][3] == {"vr1"}     # present_ids threaded

    def test_empty_delta_still_reconciles(self) -> None:
        # The no-early-return fix: zero adds/mods but the reconcile MUST still run
        # (a deletion is invisible to the delta and only the reconcile catches it).
        ctx, sf, bm, me, rec = self._run(
            delta_since=_T,
            fetch_rules_side_effect=[[]],          # nothing modified
            fetch_ids_return={"kept"},
        )
        bm.assert_not_called()                     # nothing to materialize
        me.assert_not_called()
        rec.assert_called_once()                   # ...but deletions reconciled
        assert rec.call_args[0][3] == {"kept"}

    def test_full_path_never_deltas_or_reconciles(self) -> None:
        ctx, sf, bm, me, rec = self._run(
            delta_since=None,
            fetch_rules_side_effect=[[_vr()]],
        )
        sf.fetch_validation_rules.assert_called_once_with()   # no modified_since
        sf.fetch_validation_rule_ids.assert_not_called()
        rec.assert_not_called()

    def test_delta_fetch_error_falls_back_to_full_no_reconcile(self) -> None:
        # delta query raises → full-fetch fallback, reconcile suppressed.
        ctx, sf, bm, me, rec = self._run(
            delta_since=_T,
            fetch_rules_side_effect=[RuntimeError("tooling 500"), [_vr()]],
        )
        assert sf.fetch_validation_rules.call_count == 2       # delta (raised) + full
        assert sf.fetch_validation_rules.call_args_list[1].args == ()
        rec.assert_not_called()                                # never a partial delta

    def test_id_list_error_falls_back_to_full_no_reconcile(self) -> None:
        # delta query OK but the id-list raises → full-fetch fallback, no reconcile.
        from primeqa.sync import phases
        ctx, sf = _ctx(_T)
        sf.fetch_validation_rules.side_effect = [[_vr()], [_vr()]]
        sf.fetch_validation_rule_ids.side_effect = RuntimeError("id list 500")
        conn = MagicMock()
        with mock.patch.object(phases, "_synced_object_api_names",
                               return_value={"Account"}), \
             mock.patch.object(phases, "batched_materialize",
                               return_value={"Account.Foo": "eid1"}), \
             mock.patch.object(phases, "materialize_edges_for_entities"), \
             mock.patch.object(phases, "reconcile_deletions_by_sf_id") as rec:
            phases.phase_validation_rule(ctx, conn)
        assert sf.fetch_validation_rules.call_count == 2
        rec.assert_not_called()
