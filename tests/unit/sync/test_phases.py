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
        counts and no error_message (succeeded == True)."""
        ctx = _stub_context()
        for entity_type, phase_fn in PHASE_REGISTRY.items():
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
