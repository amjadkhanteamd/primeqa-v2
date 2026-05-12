"""Tests for primeqa.sync.phases — phase registry + no-op behavior."""
from __future__ import annotations

import pytest

from primeqa.sync.context import SyncContext
from primeqa.sync.fk_assertion import ENTITY_ORDER
from primeqa.sync.phases import (
    PHASE_REGISTRY,
    get_phase_function,
)
from primeqa.sync.result import PhaseResult


def _stub_context() -> SyncContext:
    """Minimal SyncContext for phase smoke-tests. No-op phases don't
    touch any of these, so they can all be sentinels."""
    return SyncContext(
        sf_client=None,
        engine=None,
        sync_run_id="00000000-0000-0000-0000-000000000000",
        connected_org_id="00000000-0000-0000-0000-000000000001",
        tenant_schema="tenant_1",
        logical_version_seq=1,
    )


class TestPhaseRegistry:
    def test_phase_registry_has_function_for_every_entity_order_value(self) -> None:
        """PHASE_REGISTRY keys must exactly match ENTITY_ORDER values.

        Lock against drift: if someone adds an entity type to
        ENTITY_ORDER without adding a phase function (or vice versa),
        this test fails immediately."""
        assert set(PHASE_REGISTRY.keys()) == set(ENTITY_ORDER)
        # Also assert tuple-vs-set ordering: PHASE_REGISTRY's
        # insertion order matches ENTITY_ORDER
        assert tuple(PHASE_REGISTRY.keys()) == ENTITY_ORDER

    def test_noop_phase_returns_empty_phase_result(self) -> None:
        """Every no-op phase returns a PhaseResult with all-zero
        counts and no error_message (succeeded == True).

        Skips entity types whose phase function has been replaced
        with a real implementation (e.g., Object since the Object
        phase cycle). The phase-registry-shape test verifies all
        entity types ARE registered; this test verifies the no-op
        SHAPE behavior of remaining placeholders."""
        ctx = _stub_context()
        from unittest.mock import MagicMock
        stub_conn = MagicMock()
        # Real phase implementations live elsewhere and are tested
        # separately; this test covers only the remaining no-op
        # placeholders.
        real_phases = {"Object", "PicklistValueSet"}
        for entity_type, phase_fn in PHASE_REGISTRY.items():
            if entity_type in real_phases:
                continue
            result = phase_fn(ctx, stub_conn)
            assert isinstance(result, PhaseResult)
            assert result.entity_type == entity_type
            assert result.entities_inserted == 0
            assert result.entities_superseded == 0
            assert result.entities_unchanged == 0
            assert result.edges_inserted == 0
            assert result.edges_superseded == 0
            assert result.embeddings_queued == 0
            assert result.summaries_queued == 0
            assert result.error_message is None
            assert result.succeeded is True

    def test_get_phase_function_raises_keyerror_for_unknown_entity_type(self) -> None:
        """get_phase_function rejects entity types not in the registry."""
        with pytest.raises(KeyError) as excinfo:
            get_phase_function("NotARealEntityType")
        # Error message should reference the bad name
        assert "NotARealEntityType" in str(excinfo.value)


# ----------------------------------------------------------------------
# phase_object — real implementation tests
# ----------------------------------------------------------------------

from unittest.mock import MagicMock, patch

from primeqa.sync.phases import _is_syncable_object, phase_object


def _account_raw() -> dict:
    """Minimal raw fetch_objects payload for a syncable standard
    object."""
    return {
        "name": "Account",
        "label": "Account",
        "queryable": True,
        "searchable": True,
        "custom": False,
        "deprecatedAndHidden": False,
        "customSetting": False,
    }


def _non_queryable_raw() -> dict:
    return {**_account_raw(), "name": "AggregateResult", "queryable": False}


def _non_searchable_raw() -> dict:
    return {**_account_raw(), "name": "OutgoingEmail",
            "searchable": False}


def _deprecated_raw() -> dict:
    return {**_account_raw(), "name": "OldEntity__c",
            "deprecatedAndHidden": True}


def _custom_setting_raw() -> dict:
    return {**_account_raw(), "name": "Config__c",
            "customSetting": True}


class TestIsSyncableObject:
    """Filter logic — Option C strict filter."""

    @pytest.mark.parametrize("raw,expected", [
        (_account_raw(),         True),   # standard syncable
        (_non_queryable_raw(),   False),  # non-queryable rejected
        (_non_searchable_raw(),  False),  # non-searchable rejected
        (_deprecated_raw(),      False),  # deprecated rejected
        (_custom_setting_raw(),  False),  # custom setting rejected
    ])
    def test_is_syncable_object_filter_logic(
        self, raw: dict, expected: bool,
    ) -> None:
        assert _is_syncable_object(raw) is expected


def _stub_ctx_with_mock_sf() -> SyncContext:
    return SyncContext(
        sf_client=MagicMock(),
        engine=MagicMock(),
        sync_run_id="11111111-1111-1111-1111-111111111111",
        connected_org_id="22222222-2222-2222-2222-222222222222",
        tenant_schema="tenant_1",
        logical_version_seq=42,
    )


class TestPhaseObject:
    def test_phase_object_calls_fetch_objects_on_client(self) -> None:
        """phase_object delegates Salesforce fetching to
        ctx.sf_client.fetch_objects()."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_objects.return_value = []
        conn = MagicMock()
        with patch("primeqa.sync.phases.batched_materialize"):
            phase_object(ctx, conn)
        ctx.sf_client.fetch_objects.assert_called_once_with()

    def test_phase_object_filters_non_queryable_objects(self) -> None:
        """Non-queryable raw payloads are NOT passed to
        batched_materialize."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_objects.return_value = [
            _account_raw(),         # passes
            _non_queryable_raw(),   # filtered
        ]
        conn = MagicMock()
        with patch("primeqa.sync.phases.batched_materialize") as mock_bm:
            phase_object(ctx, conn)
        # batched_materialize called once with the filtered list
        mock_bm.assert_called_once()
        raw_payloads = mock_bm.call_args.kwargs["raw_payloads"]
        names = [r["name"] for r in raw_payloads]
        assert names == ["Account"]

    def test_phase_object_filters_custom_setting_objects(self) -> None:
        """customSetting=True payloads are NOT passed through."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_objects.return_value = [
            _account_raw(),
            _custom_setting_raw(),
        ]
        conn = MagicMock()
        with patch("primeqa.sync.phases.batched_materialize") as mock_bm:
            phase_object(ctx, conn)
        mock_bm.assert_called_once()
        names = [r["name"] for r in mock_bm.call_args.kwargs["raw_payloads"]]
        assert names == ["Account"]

    def test_phase_object_filters_deprecated_objects(self) -> None:
        """deprecatedAndHidden=True payloads are NOT passed through."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_objects.return_value = [
            _account_raw(),
            _deprecated_raw(),
        ]
        conn = MagicMock()
        with patch("primeqa.sync.phases.batched_materialize") as mock_bm:
            phase_object(ctx, conn)
        mock_bm.assert_called_once()
        names = [r["name"] for r in mock_bm.call_args.kwargs["raw_payloads"]]
        assert names == ["Account"]

    def test_phase_object_materializes_filtered_objects_only(
        self,
    ) -> None:
        """Mixed input — verify exactly the syncable subset reaches
        batched_materialize."""
        ctx = _stub_ctx_with_mock_sf()
        contact_raw = {**_account_raw(), "name": "Contact"}
        ctx.sf_client.fetch_objects.return_value = [
            _account_raw(),          # passes
            _non_queryable_raw(),    # filtered
            contact_raw,             # passes
            _custom_setting_raw(),   # filtered
            _deprecated_raw(),       # filtered
        ]
        conn = MagicMock()
        with patch("primeqa.sync.phases.batched_materialize") as mock_bm:
            phase_object(ctx, conn)
        mock_bm.assert_called_once()
        names = [r["name"] for r in mock_bm.call_args.kwargs["raw_payloads"]]
        assert names == ["Account", "Contact"]


# ----------------------------------------------------------------------
# phase_picklist_value_set — GVS source, second real phase
# ----------------------------------------------------------------------

from primeqa.sync.phases import phase_picklist_value_set


class TestPhasePicklistValueSet:
    def test_phase_picklist_value_set_calls_both_fetchers(
        self,
    ) -> None:
        """phase_picklist_value_set delegates fetching to BOTH
        fetch_global_value_sets() and fetch_standard_value_sets()
        per the unified GVS + SVS source design (corrections-log §8
        addendum). Object fetcher remains untouched (different phase).
        """
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_global_value_sets.return_value = []
        ctx.sf_client.fetch_standard_value_sets.return_value = []
        conn = MagicMock()
        with patch("primeqa.sync.phases.batched_materialize"):
            phase_picklist_value_set(ctx, conn)
        ctx.sf_client.fetch_global_value_sets.assert_called_once_with()
        ctx.sf_client.fetch_standard_value_sets.assert_called_once_with(
            labels=None,
        )
        ctx.sf_client.fetch_objects.assert_not_called()

    def test_phase_picklist_value_set_combines_gvs_and_svs_streams(
        self,
    ) -> None:
        """Both fetchers' returns are tagged with the appropriate
        `_source` marker and concatenated (GVS first, SVS second)
        before being passed to batched_materialize."""
        ctx = _stub_ctx_with_mock_sf()
        raw_gvs = [
            {"Id": "0Nt000000000001", "FullName": "RegionVS",
             "MasterLabel": "Region", "Description": "Sales regions"},
        ]
        raw_svs = [
            {"Id": "00X000000000001", "FullName": "AccountSource",
             "MasterLabel": "Account Source",
             "Metadata": {"standardValue": []}},
            {"Id": "00X000000000002", "FullName": "CaseOrigin",
             "MasterLabel": "Case Origin",
             "Metadata": {"standardValue": []}},
        ]
        ctx.sf_client.fetch_global_value_sets.return_value = raw_gvs
        ctx.sf_client.fetch_standard_value_sets.return_value = raw_svs
        conn = MagicMock()
        with patch("primeqa.sync.phases.batched_materialize") as mock_bm:
            result = phase_picklist_value_set(ctx, conn)

        mock_bm.assert_called_once()
        call_kwargs = mock_bm.call_args.kwargs
        assert call_kwargs["entity_type"] == "PicklistValueSet"
        assert call_kwargs["conn"] is conn

        combined = call_kwargs["raw_payloads"]
        # 1 GVS + 2 SVS = 3 records total, in GVS-first order.
        assert len(combined) == 3
        assert combined[0]["FullName"] == "RegionVS"
        assert combined[0]["_source"] == "GlobalValueSet"
        assert combined[1]["FullName"] == "AccountSource"
        assert combined[1]["_source"] == "StandardValueSet"
        assert combined[2]["FullName"] == "CaseOrigin"
        assert combined[2]["_source"] == "StandardValueSet"

        assert result.entity_type == "PicklistValueSet"

    def test_phase_picklist_value_set_handles_empty_both_streams(
        self,
    ) -> None:
        """When BOTH fetchers return [], batched_materialize is NOT
        called and result reports all zeros. (Note: in practice the
        SVS catalog is 616 entries pinned to v66.0, so both-empty is
        rare outside fully-mocked tests.)"""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_global_value_sets.return_value = []
        ctx.sf_client.fetch_standard_value_sets.return_value = []
        conn = MagicMock()
        with patch("primeqa.sync.phases.batched_materialize") as mock_bm:
            result = phase_picklist_value_set(ctx, conn)
        mock_bm.assert_not_called()
        assert result.entity_type == "PicklistValueSet"
        assert result.entities_inserted == 0
        assert result.entities_superseded == 0
        assert result.entities_unchanged == 0
        assert result.succeeded is True

    def test_phase_picklist_value_set_gvs_only(self) -> None:
        """GVS records present, SVS empty — combined list contains
        only GVS rows (each tagged) and batched_materialize is
        called exactly once."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_global_value_sets.return_value = [
            {"FullName": "GVS1", "MasterLabel": "GVS 1"},
        ]
        ctx.sf_client.fetch_standard_value_sets.return_value = []
        conn = MagicMock()
        with patch("primeqa.sync.phases.batched_materialize") as mock_bm:
            phase_picklist_value_set(ctx, conn)
        mock_bm.assert_called_once()
        combined = mock_bm.call_args.kwargs["raw_payloads"]
        assert len(combined) == 1
        assert combined[0]["_source"] == "GlobalValueSet"

    def test_phase_picklist_value_set_svs_only(self) -> None:
        """SVS records present, GVS empty (typical sandbox case
        after this cycle) — combined list contains only SVS rows
        (each tagged) and batched_materialize is called once."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_global_value_sets.return_value = []
        ctx.sf_client.fetch_standard_value_sets.return_value = [
            {"FullName": "AccountSource", "MasterLabel": "Account Source",
             "Metadata": {"standardValue": []}},
        ]
        conn = MagicMock()
        with patch("primeqa.sync.phases.batched_materialize") as mock_bm:
            phase_picklist_value_set(ctx, conn)
        mock_bm.assert_called_once()
        combined = mock_bm.call_args.kwargs["raw_payloads"]
        assert len(combined) == 1
        assert combined[0]["_source"] == "StandardValueSet"
