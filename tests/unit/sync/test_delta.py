"""Unit tests for 1b.2 per-category delta fetch (ValidationRule) + the D-253
full-fetch deletion-reconcile backstop.

Layers, none touching a DB:
  * the allow-list gate (``delta.delta_since_for``),
  * the SF-client delta SOQL + the cheap id-list + the ``_query_all``
    ``require_complete`` fail-closed hardening (D-253), and
  * ``phase_validation_rule`` orchestration — both the delta AND the full-fetch
    path now fetch the id-list and reconcile (D-253 reverses the old
    full-fetch-does-NOT-reconcile rule); the reconcile is gated on a non-empty
    ``present_ids`` (``if present_ids:``) so an empty/partial id-fetch refuses to
    mass-close on EITHER path.

The real-DB SCD-2 supersede + edge close (and the deletion/refusal proofs) live in
tests/integration/semantic/test_delta_reconcile_live.py — this file mocks the
materialize/reconcile boundary and asserts the ORCHESTRATION.
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
# 2b. _query_all require_complete fail-closed (D-253)
# ----------------------------------------------------------------------
class TestQueryAllRequireComplete:
    """``_query_all(require_complete=True)`` RAISES on a malformed cursor instead
    of silently returning a partial; the default (False) is byte-for-byte
    unchanged (zero blast radius on the other callers)."""

    def _client_with_pages(self, pages):
        c = _client()
        resps = []
        for pg in pages:
            r = MagicMock()
            r.json.return_value = pg
            resps.append(r)
        c._request = MagicMock(side_effect=resps)
        return c

    def test_complete_multipage_walk_returns_full_aggregate(self) -> None:
        c = self._client_with_pages([
            {"records": [{"Id": "a"}], "done": False, "nextRecordsUrl": "/n1"},
            {"records": [{"Id": "b"}], "done": True},
        ])
        out = c._query_all("/p", "SELECT Id FROM X", require_complete=True)
        assert [r["Id"] for r in out] == ["a", "b"]      # full walk, no raise

    def test_malformed_cursor_raises_under_require_complete(self) -> None:
        from primeqa.integrations.exceptions import SFIncompletePaginationError
        c = self._client_with_pages([
            {"records": [{"Id": "a"}], "done": False},   # done=False, no cursor
        ])
        with pytest.raises(SFIncompletePaginationError):
            c._query_all("/p", "SELECT Id FROM X", require_complete=True)

    def test_malformed_cursor_default_is_silent_partial(self) -> None:
        # Default require_complete=False unchanged: returns rows-so-far, no raise.
        c = self._client_with_pages([
            {"records": [{"Id": "a"}], "done": False},   # done=False, no cursor
        ])
        out = c._query_all("/p", "SELECT Id FROM X")
        assert [r["Id"] for r in out] == ["a"]           # silent partial preserved


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

    def test_full_path_now_also_reconciles(self) -> None:
        # D-253 backstop #1: the full-fetch path NOW fetches the id-list and
        # reconciles (reverses the old "full-fetch does NOT reconcile").
        ctx, sf, bm, me, rec = self._run(
            delta_since=None,
            fetch_rules_side_effect=[[_vr()]],
            fetch_ids_return={"vr1"},
        )
        sf.fetch_validation_rules.assert_called_once_with()   # no modified_since
        sf.fetch_validation_rule_ids.assert_called_once()     # the gated id-list
        rec.assert_called_once()
        assert rec.call_args[0][3] == {"vr1"}                 # full id-set threaded

    def test_delta_fetch_error_falls_back_to_full_and_reconciles(self) -> None:
        # D-253: delta query raises → full-fetch fallback, which NOW reconciles via
        # the full id-list (a complete set). (Pre-D-253 the reconcile was suppressed
        # on any fallback.)
        ctx, sf, bm, me, rec = self._run(
            delta_since=_T,
            fetch_rules_side_effect=[RuntimeError("tooling 500"), [_vr()]],
            fetch_ids_return={"vr1"},
        )
        assert sf.fetch_validation_rules.call_count == 2       # delta (raised) + full
        assert sf.fetch_validation_rules.call_args_list[1].args == ()
        rec.assert_called_once()                               # full fallback reconciles
        assert rec.call_args[0][3] == {"vr1"}

    def test_id_list_error_on_both_attempts_no_reconcile(self) -> None:
        # delta id-list raises → full fallback → the full id-list ALSO raises →
        # present_ids stays None → no reconcile (fail-safe to NO-reconcile).
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

    def test_empty_id_fetch_refuses_reconcile_on_both_paths(self) -> None:
        # `if present_ids:` refuses set() → NO reconcile (no mass-close), on BOTH
        # the delta and the full-fetch path. The D-253 catastrophic guard AND the
        # latent-1b.2-hole closure (shipped 1b.2 would have mass-closed on empty).
        for ds in (None, _T):
            _c, sf, bm, me, rec = self._run(
                delta_since=ds, fetch_rules_side_effect=[[]], fetch_ids_return=set())
            rec.assert_not_called()
