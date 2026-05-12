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
        # Real phase implementations live elsewhere and are tested
        # separately; this test covers only the remaining no-op
        # placeholders.
        real_phases = {"Object"}
        for entity_type, phase_fn in PHASE_REGISTRY.items():
            if entity_type in real_phases:
                continue
            result = phase_fn(ctx)
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
        with patch("primeqa.sync.phases.materialize_entity"):
            phase_object(ctx)
        ctx.sf_client.fetch_objects.assert_called_once_with()

    def test_phase_object_filters_non_queryable_objects(self) -> None:
        """Non-queryable raw payloads are NOT passed to
        materialize_entity."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_objects.return_value = [
            _account_raw(),         # passes
            _non_queryable_raw(),   # filtered
        ]
        with patch("primeqa.sync.phases.materialize_entity") as mock_mat:
            phase_object(ctx)
        assert mock_mat.call_count == 1
        # The single call's external_id was 'Account'
        call_kwargs = mock_mat.call_args.kwargs
        assert call_kwargs["external_id"] == "Account"

    def test_phase_object_filters_custom_setting_objects(self) -> None:
        """customSetting=True payloads are NOT passed through."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_objects.return_value = [
            _account_raw(),
            _custom_setting_raw(),
        ]
        with patch("primeqa.sync.phases.materialize_entity") as mock_mat:
            phase_object(ctx)
        assert mock_mat.call_count == 1
        assert mock_mat.call_args.kwargs["external_id"] == "Account"

    def test_phase_object_filters_deprecated_objects(self) -> None:
        """deprecatedAndHidden=True payloads are NOT passed through."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_objects.return_value = [
            _account_raw(),
            _deprecated_raw(),
        ]
        with patch("primeqa.sync.phases.materialize_entity") as mock_mat:
            phase_object(ctx)
        assert mock_mat.call_count == 1
        assert mock_mat.call_args.kwargs["external_id"] == "Account"

    def test_phase_object_materializes_filtered_objects_only(
        self,
    ) -> None:
        """Mixed input — verify exactly the syncable subset reaches
        materialize_entity."""
        ctx = _stub_ctx_with_mock_sf()
        contact_raw = {**_account_raw(), "name": "Contact"}
        ctx.sf_client.fetch_objects.return_value = [
            _account_raw(),          # passes
            _non_queryable_raw(),    # filtered
            contact_raw,             # passes
            _custom_setting_raw(),   # filtered
            _deprecated_raw(),       # filtered
        ]
        with patch("primeqa.sync.phases.materialize_entity") as mock_mat:
            phase_object(ctx)
        assert mock_mat.call_count == 2
        external_ids = [c.kwargs["external_id"] for c in mock_mat.call_args_list]
        assert external_ids == ["Account", "Contact"]
