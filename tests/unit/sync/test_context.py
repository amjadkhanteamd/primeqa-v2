"""Unit tests for SyncContext dataclass.

Covers the basic required fields + the svs_metadata_cache field
added by the §9 PV-phase optimization cycle.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from primeqa.sync.context import SyncContext


def _stub_ctx(**overrides) -> SyncContext:
    """Construct a SyncContext with placeholder fields, allowing
    per-test field overrides via keyword args."""
    defaults = dict(
        sf_client=MagicMock(),
        engine=MagicMock(),
        sync_run_id="11111111-1111-1111-1111-111111111111",
        connected_org_id="22222222-2222-2222-2222-222222222222",
        tenant_schema="tenant_1",
        logical_version_seq=42,
    )
    defaults.update(overrides)
    return SyncContext(**defaults)


class TestSyncContextRequiredFields:
    def test_constructs_with_required_kwargs(self) -> None:
        ctx = _stub_ctx()
        assert ctx.sync_run_id == "11111111-1111-1111-1111-111111111111"
        assert ctx.tenant_schema == "tenant_1"
        assert ctx.logical_version_seq == 42


class TestSyncContextSvsMetadataCache:
    """Cache field defaults to an empty dict per default_factory.

    Per corrections-log §9, the cache stores SVS Metadata sub-trees
    keyed by FullName. PVS phase populates it; PV phase consumes
    it instead of re-fetching the 616-record SVS catalog.
    """

    def test_default_is_empty_dict(self) -> None:
        ctx = _stub_ctx()
        assert ctx.svs_metadata_cache == {}

    def test_default_is_independent_per_instance(self) -> None:
        """default_factory=dict creates a fresh dict per
        construction. Catches a regression where someone used
        =dict() (shared mutable default) instead of
        =field(default_factory=dict)."""
        ctx1 = _stub_ctx()
        ctx2 = _stub_ctx()
        ctx1.svs_metadata_cache["AccountSource"] = {
            "standardValue": [{"valueName": "Web"}],
        }
        assert ctx2.svs_metadata_cache == {}

    def test_cache_is_mutable(self) -> None:
        """PVS phase populates the cache by mutating it directly.
        Verifies the field is mutable (no frozen=True trap)."""
        ctx = _stub_ctx()
        ctx.svs_metadata_cache["AccountType"] = {
            "standardValue": [{"valueName": "Analyst"}],
        }
        assert ctx.svs_metadata_cache == {
            "AccountType": {
                "standardValue": [{"valueName": "Analyst"}],
            },
        }

    def test_cache_can_be_pre_populated_via_kwarg(self) -> None:
        """Tests that construct a context with a pre-populated
        cache (the cache-hit path) use this kwarg pattern."""
        pre_cache = {
            "AccountSource": {
                "standardValue": [{"valueName": "Web"}],
            },
        }
        ctx = _stub_ctx(svs_metadata_cache=pre_cache)
        assert ctx.svs_metadata_cache == pre_cache
