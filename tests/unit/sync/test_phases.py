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
        real_phases = {"Object", "PicklistValueSet", "PicklistValue",
                       "Field", "RecordType", "Layout",
                       "ValidationRule", "Profile",
                       "PermissionSet", "User", "Flow"}
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

    def test_phase_picklist_value_set_populates_svs_metadata_cache(
        self,
    ) -> None:
        """Per corrections-log §9, PVS phase writes each fetched SVS
        Metadata into ctx.svs_metadata_cache keyed by FullName. The
        cache is later consumed by phase_picklist_value to skip
        refetching the 616-record catalog."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_global_value_sets.return_value = []
        ctx.sf_client.fetch_standard_value_sets.return_value = [
            {
                "FullName": "AccountSource",
                "MasterLabel": "Account Source",
                "Metadata": {
                    "standardValue": [
                        {"valueName": "Web"},
                        {"valueName": "Phone"},
                    ],
                },
            },
            {
                "FullName": "LeadSource",
                "MasterLabel": "Lead Source",
                "Metadata": {
                    "standardValue": [{"valueName": "Web"}],
                },
            },
        ]
        conn = MagicMock()
        assert ctx.svs_metadata_cache == {}
        with patch("primeqa.sync.phases.batched_materialize"):
            phase_picklist_value_set(ctx, conn)
        # Cache populated with both SVS records, keyed by FullName,
        # value is the Metadata sub-tree (not full record).
        assert set(ctx.svs_metadata_cache.keys()) == {
            "AccountSource", "LeadSource",
        }
        assert ctx.svs_metadata_cache["AccountSource"] == {
            "standardValue": [
                {"valueName": "Web"},
                {"valueName": "Phone"},
            ],
        }
        assert ctx.svs_metadata_cache["LeadSource"] == {
            "standardValue": [{"valueName": "Web"}],
        }

    def test_phase_picklist_value_set_does_not_cache_gvs(self) -> None:
        """The cache is SVS-only per the §9 design — GVS fetch is
        a single cheap bulk Tooling call, so PV phase happily
        re-fetches it. Don't pollute the cache with GVS records."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_global_value_sets.return_value = [
            {
                "FullName": "MyGVS",
                "Metadata": {
                    "customValue": [{"valueName": "Banking"}],
                },
            },
        ]
        ctx.sf_client.fetch_standard_value_sets.return_value = []
        conn = MagicMock()
        with patch("primeqa.sync.phases.batched_materialize"):
            phase_picklist_value_set(ctx, conn)
        # SVS empty + GVS skipped → cache stays empty
        assert ctx.svs_metadata_cache == {}

    def test_phase_picklist_value_set_cache_uses_empty_dict_when_metadata_missing(
        self,
    ) -> None:
        """SVS record with no Metadata key (or Metadata=None) gets
        stored as {} so PV phase's loop can iterate without None
        checks."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_global_value_sets.return_value = []
        ctx.sf_client.fetch_standard_value_sets.return_value = [
            {"FullName": "NoMetadata"},
            {"FullName": "NullMetadata", "Metadata": None},
        ]
        conn = MagicMock()
        with patch("primeqa.sync.phases.batched_materialize"):
            phase_picklist_value_set(ctx, conn)
        assert ctx.svs_metadata_cache["NoMetadata"] == {}
        assert ctx.svs_metadata_cache["NullMetadata"] == {}


# ----------------------------------------------------------------------
# phase_picklist_value — third real phase; first to derive children
# from already-fetched parent records
# ----------------------------------------------------------------------

from primeqa.sync.phases import phase_picklist_value


class TestPhasePicklistValue:
    def test_phase_picklist_value_fetches_gvs_always_and_falls_back_to_svs_fetch_when_cache_empty(
        self,
    ) -> None:
        '''Cache-empty path (resumed sync or PV-in-isolation test):
        PV refetches BOTH parent streams. Per corrections-log §9
        the normal path consumes ctx.svs_metadata_cache populated
        by PVS phase; this test exercises the fallback. The GVS
        path always refetches (cheap bulk call, no cache).'''
        ctx = _stub_ctx_with_mock_sf()
        assert ctx.svs_metadata_cache == {}  # cache empty
        ctx.sf_client.fetch_global_value_sets.return_value = []
        ctx.sf_client.fetch_standard_value_sets.return_value = []
        conn = MagicMock()
        with patch('primeqa.sync.phases.batched_materialize'):
            phase_picklist_value(ctx, conn)
        ctx.sf_client.fetch_global_value_sets.assert_called_once_with()
        ctx.sf_client.fetch_standard_value_sets.assert_called_once_with(
            labels=None,
        )

    def test_phase_picklist_value_reads_from_cache_when_populated(
        self,
    ) -> None:
        '''Normal path: PVS phase has populated
        ctx.svs_metadata_cache; PV reads from cache and does NOT
        call fetch_standard_value_sets. This is the §9 fix that
        eliminates the ~6 min SVS catalog refetch.'''
        ctx = _stub_ctx_with_mock_sf()
        ctx.svs_metadata_cache = {
            "AccountType": {
                "standardValue": [
                    {"valueName": "Analyst", "label": "Analyst"},
                    {"valueName": "Customer", "label": "Customer"},
                ],
            },
            "LeadSource": {
                "standardValue": [
                    {"valueName": "Web", "label": "Web"},
                ],
            },
        }
        ctx.sf_client.fetch_global_value_sets.return_value = []
        conn = MagicMock()
        with patch('primeqa.sync.phases.batched_materialize') as mock_bm:
            phase_picklist_value(ctx, conn)
        # GVS still fetched (cheap; no cache for GVS)
        ctx.sf_client.fetch_global_value_sets.assert_called_once_with()
        # SVS NOT fetched — cache hit
        ctx.sf_client.fetch_standard_value_sets.assert_not_called()
        # Payloads built from cache
        payloads = mock_bm.call_args.kwargs['raw_payloads']
        assert len(payloads) == 3
        parents = {p["_parent_external_id"] for p in payloads}
        assert parents == {"SVS:AccountType", "SVS:LeadSource"}

    def test_phase_picklist_value_cache_hit_preserves_sort_order_per_parent(
        self,
    ) -> None:
        '''Cache-hit path: _sort_order reflects each value's index
        within its parent's standardValue list — not a global
        index across all cached SVSes.'''
        ctx = _stub_ctx_with_mock_sf()
        ctx.svs_metadata_cache = {
            "AccountType": {
                "standardValue": [
                    {"valueName": "Analyst"},
                    {"valueName": "Customer"},
                ],
            },
            "LeadSource": {
                "standardValue": [
                    {"valueName": "Web"},
                    {"valueName": "Phone"},
                ],
            },
        }
        ctx.sf_client.fetch_global_value_sets.return_value = []
        conn = MagicMock()
        with patch('primeqa.sync.phases.batched_materialize') as mock_bm:
            phase_picklist_value(ctx, conn)
        payloads = mock_bm.call_args.kwargs['raw_payloads']
        # Each parent's children numbered from 0 within their parent
        by_parent: dict[str, list[tuple[int, str]]] = {}
        for p in payloads:
            by_parent.setdefault(
                p["_parent_external_id"], [],
            ).append((p["_sort_order"], p["valueName"]))
        for parent, entries in by_parent.items():
            entries.sort()
            assert entries[0][0] == 0, (
                f"{parent} first child should have _sort_order=0; "
                f"got {entries}"
            )

    def test_phase_picklist_value_extracts_gvs_values(self) -> None:
        '''Each GVS's Metadata.customValue entries become
        PicklistValue raw payloads with the parent_external_id
        unprefixed (GVS contract).'''
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_global_value_sets.return_value = [
            {
                'FullName': 'MyGVS',
                'Metadata': {
                    'customValue': [
                        {'valueName': 'Banking', 'label': 'Banking'},
                        {'valueName': 'Tech', 'label': 'Technology'},
                    ],
                },
            },
        ]
        ctx.sf_client.fetch_standard_value_sets.return_value = []
        conn = MagicMock()
        with patch('primeqa.sync.phases.batched_materialize') as mock_bm:
            phase_picklist_value(ctx, conn)
        mock_bm.assert_called_once()
        payloads = mock_bm.call_args.kwargs['raw_payloads']
        assert len(payloads) == 2
        assert payloads[0]['_parent_external_id'] == 'MyGVS'
        assert payloads[0]['_sort_order'] == 0
        assert payloads[0]['valueName'] == 'Banking'
        assert payloads[1]['_parent_external_id'] == 'MyGVS'
        assert payloads[1]['_sort_order'] == 1

    def test_phase_picklist_value_extracts_svs_values_with_prefix(
        self,
    ) -> None:
        '''SVS values get parent_external_id with the 'SVS:' prefix
        so child external_ids inherit the namespace.'''
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_global_value_sets.return_value = []
        ctx.sf_client.fetch_standard_value_sets.return_value = [
            {
                'FullName': 'AccountType',
                'Metadata': {
                    'standardValue': [
                        {'valueName': 'Analyst', 'label': 'Analyst',
                         'isActive': None, 'default': False},
                    ],
                },
            },
        ]
        conn = MagicMock()
        with patch('primeqa.sync.phases.batched_materialize') as mock_bm:
            phase_picklist_value(ctx, conn)
        payloads = mock_bm.call_args.kwargs['raw_payloads']
        assert len(payloads) == 1
        assert payloads[0]['_parent_external_id'] == 'SVS:AccountType'
        assert payloads[0]['_sort_order'] == 0
        assert payloads[0]['valueName'] == 'Analyst'

    def test_phase_picklist_value_handles_empty_value_lists(self) -> None:
        '''A parent with empty customValue/standardValue contributes
        no PV payloads. Empty parents are common — most uncustomized
        sandbox SVSes return an empty value list.'''
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_global_value_sets.return_value = []
        ctx.sf_client.fetch_standard_value_sets.return_value = [
            {'FullName': 'EmptyVS', 'Metadata': {'standardValue': []}},
            {'FullName': 'NullMetaVS', 'Metadata': None},
            {'FullName': 'MissingMetaVS'},  # no Metadata key at all
        ]
        conn = MagicMock()
        with patch('primeqa.sync.phases.batched_materialize') as mock_bm:
            phase_picklist_value(ctx, conn)
        # All three parents have no values → no payloads → no call
        mock_bm.assert_not_called()

    def test_phase_picklist_value_skips_value_with_missing_value_name(
        self,
    ) -> None:
        '''Defensive: Metadata API occasionally returns placeholder
        entries with empty/missing valueName. The phase function
        filters these so downstream external_id construction
        doesn't fail.'''
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_global_value_sets.return_value = []
        ctx.sf_client.fetch_standard_value_sets.return_value = [
            {
                'FullName': 'PartialVS',
                'Metadata': {
                    'standardValue': [
                        {'valueName': 'Good', 'label': 'Good'},
                        {'valueName': '', 'label': 'Blank'},  # filtered
                        {'label': 'NoValueName'},  # filtered
                        'NotADict',  # filtered
                    ],
                },
            },
        ]
        conn = MagicMock()
        with patch('primeqa.sync.phases.batched_materialize') as mock_bm:
            phase_picklist_value(ctx, conn)
        payloads = mock_bm.call_args.kwargs['raw_payloads']
        assert len(payloads) == 1
        assert payloads[0]['valueName'] == 'Good'

    def test_phase_picklist_value_empty_no_call(self) -> None:
        '''Both fetchers return [] → no batched_materialize call →
        all-zero PhaseResult.'''
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_global_value_sets.return_value = []
        ctx.sf_client.fetch_standard_value_sets.return_value = []
        conn = MagicMock()
        with patch('primeqa.sync.phases.batched_materialize') as mock_bm:
            result = phase_picklist_value(ctx, conn)
        mock_bm.assert_not_called()
        assert result.entity_type == 'PicklistValue'
        assert result.entities_inserted == 0
        assert result.succeeded is True



# ----------------------------------------------------------------------
# phase_field — fourth real phase; FIRST edge-writing phase
# ----------------------------------------------------------------------

from primeqa.sync.phases import phase_field


class TestPhaseField:
    def _ctx_with_objects(self, object_rows):
        """Set up ctx + conn where the Object SELECT returns the given
        rows. Each row is a (id, sf_api_name) namedtuple-style."""
        ctx = _stub_ctx_with_mock_sf()
        conn = MagicMock()
        # The Object SELECT inside phase_field
        conn.execute.return_value.fetchall.return_value = [
            type('R', (), {'id': r[0], 'sf_api_name': r[1]})()
            for r in object_rows
        ]
        return ctx, conn

    def test_phase_field_bulk_fetches_for_all_objects_in_one_call(
        self,
    ) -> None:
        """phase_field reads currently-active Object entities from
        this sync's data and calls fetch_fields_for_objects_bulk ONCE
        with the full list (bulk fetcher chunks at 25 internally —
        see test_sf_client). Per-Object fetch_fields_for_object is
        NOT called by phase_field anymore."""
        ctx, conn = self._ctx_with_objects([
            ('obj-acc', 'Account'),
            ('obj-con', 'Contact'),
        ])
        ctx.sf_client.fetch_fields_for_objects_bulk.return_value = {}
        with patch('primeqa.sync.phases.batched_materialize') as mock_bm, \
             patch('primeqa.sync.phases.materialize_edges_for_entities'):
            phase_field(ctx, conn)
        # Bulk fetcher called exactly once with the list of all
        # object API names. Internal chunking is the fetcher's
        # responsibility (and its own tests cover it).
        ctx.sf_client.fetch_fields_for_objects_bulk.assert_called_once_with(
            ['Account', 'Contact'],
        )
        # Old per-Object call is no longer used by phase_field.
        ctx.sf_client.fetch_fields_for_object.assert_not_called()
        # No fields → no materialize
        mock_bm.assert_not_called()

    def test_phase_field_decorates_fields_with_parent_marker(
        self,
    ) -> None:
        """Each field payload is tagged with _parent_object_api_name
        before going to batched_materialize. Marker drives both
        external_id construction and detail-row FK resolution. The
        bulk fetcher's dict-of-lists is flattened with per-Object
        marker injection."""
        ctx, conn = self._ctx_with_objects([('obj-acc', 'Account')])
        ctx.sf_client.fetch_fields_for_objects_bulk.return_value = {
            'Account': [
                {'name': 'Industry', 'type': 'picklist'},
                {'name': 'Name', 'type': 'string'},
            ],
        }
        with patch('primeqa.sync.phases.batched_materialize') as mock_bm, \
             patch('primeqa.sync.phases.materialize_edges_for_entities'):
            mock_bm.return_value = {
                'Account.Industry': 'fld-1',
                'Account.Name': 'fld-2',
            }
            phase_field(ctx, conn)
        mock_bm.assert_called_once()
        payloads = mock_bm.call_args.kwargs['raw_payloads']
        assert all(
            p['_parent_object_api_name'] == 'Account' for p in payloads
        )
        # return_id_map=True is required (edges need the map)
        assert mock_bm.call_args.kwargs.get('return_id_map') is True

    def test_phase_field_passes_normalized_payloads_to_edge_writer(
        self,
    ) -> None:
        """phase_field re-normalizes the raw payloads before feeding
        them to materialize_edges_for_entities so the edge spec
        extractors see the post-_strip_volatile shape (same view
        substrate-1 uses for derived-edge inference)."""
        ctx, conn = self._ctx_with_objects([('obj-acc', 'Account')])
        ctx.sf_client.fetch_fields_for_objects_bulk.return_value = {
            'Account': [
                {'name': 'OwnerId', 'type': 'reference',
                 'referenceTo': ['User']},
            ],
        }
        with patch('primeqa.sync.phases.batched_materialize') as mock_bm, \
             patch(
                 'primeqa.sync.phases.materialize_edges_for_entities',
             ) as mock_edges, \
             patch(
                 'primeqa.sync.phases.normalize',
                 side_effect=lambda et, p: {**p, '_normalized': True},
             ):
            mock_bm.return_value = {'Account.OwnerId': 'fld-1'}
            phase_field(ctx, conn)
        mock_edges.assert_called_once()
        # Verify shape passed
        kwargs = mock_edges.call_args.kwargs
        assert kwargs['source_entity_type'] == 'Field'
        assert kwargs['entity_id_map'] == {'Account.OwnerId': 'fld-1'}
        # normalized_payloads aligned 1:1 with the input
        assert len(kwargs['normalized_payloads']) == 1
        assert kwargs['normalized_payloads'][0]['_normalized'] is True

    def test_phase_field_handles_per_object_failure_silently(
        self,
    ) -> None:
        """If the bulk fetcher omits a key (e.g., Object's describe
        returned 404 within its batch), the field_payloads list
        simply has no entries for that Object. The phase doesn't
        fail; it materializes what it got. Mirrors the lenient
        fault-tolerance discipline of fetch_standard_value_sets."""
        ctx, conn = self._ctx_with_objects([
            ('obj-acc', 'Account'),
            ('obj-bad', 'Inaccessible__c'),
        ])
        # Bulk fetcher returns only Account; Inaccessible__c omitted
        # (would have been logged at WARN by the fetcher itself)
        ctx.sf_client.fetch_fields_for_objects_bulk.return_value = {
            'Account': [{'name': 'Industry', 'type': 'picklist'}],
        }
        with patch('primeqa.sync.phases.batched_materialize') as mock_bm, \
             patch('primeqa.sync.phases.materialize_edges_for_entities'):
            mock_bm.return_value = {'Account.Industry': 'fld-1'}
            phase_field(ctx, conn)
        # Only Account's field got materialized
        mock_bm.assert_called_once()
        payloads = mock_bm.call_args.kwargs['raw_payloads']
        assert len(payloads) == 1
        assert payloads[0]['name'] == 'Industry'

    def test_phase_field_no_objects_no_work(self) -> None:
        """No Object entities in this sync → bulk fetcher receives
        [] (which short-circuits to {} internally per its own tests)
        and no materialize / edge work happens."""
        ctx, conn = self._ctx_with_objects([])
        # Bulk fetcher is called with [] but returns {}
        ctx.sf_client.fetch_fields_for_objects_bulk.return_value = {}
        with patch('primeqa.sync.phases.batched_materialize') as mock_bm, \
             patch(
                 'primeqa.sync.phases.materialize_edges_for_entities',
             ) as mock_edges:
            result = phase_field(ctx, conn)
        ctx.sf_client.fetch_fields_for_objects_bulk.assert_called_once_with(
            [],
        )
        # Old per-Object fetcher remains unused
        ctx.sf_client.fetch_fields_for_object.assert_not_called()
        mock_bm.assert_not_called()
        mock_edges.assert_not_called()
        assert result.entity_type == 'Field'
        assert result.entities_inserted == 0

    def test_phase_field_returns_phase_result_with_correct_type(
        self,
    ) -> None:
        """Result entity_type is 'Field' regardless of work done."""
        ctx, conn = self._ctx_with_objects([])
        ctx.sf_client.fetch_fields_for_objects_bulk.return_value = {}
        with patch('primeqa.sync.phases.batched_materialize'), \
             patch('primeqa.sync.phases.materialize_edges_for_entities'):
            result = phase_field(ctx, conn)
        assert isinstance(result, PhaseResult)
        assert result.entity_type == 'Field'
        assert result.succeeded is True


# ----------------------------------------------------------------------
# phase_record_type — fifth real phase
# ----------------------------------------------------------------------

from primeqa.sync.phases import phase_record_type


class TestPhaseRecordType:
    def _ok_rt(self, full_name="Account.PartnerAccount", **overrides):
        base = {
            "Id": "012000000000ABC",
            "FullName": full_name,
            "Name": "PartnerAccount",
            "IsActive": True,
            "Metadata": {
                "active": True,
                "label": "Partner Account",
                "description": "Partner-channel accounts",
                "developerName": "PartnerAccount",
            },
        }
        # Tooling response shape mixes top-level + Metadata fields.
        # _to_presentation_record_type reads developerName from the
        # top of the normalized dict; the substrate-1 normalize
        # _strip_volatile preserves whatever shape we send. To match
        # phase_record_type's expectations, surface developerName +
        # active + label at top level (the way Tooling's flat-record
        # SELECT actually returns them in real production).
        base['developerName'] = base['Metadata']['developerName']
        base['active'] = base['Metadata']['active']
        base['label'] = base['Metadata']['label']
        base['description'] = base['Metadata']['description']
        base.update(overrides)
        return base

    def test_phase_record_type_calls_fetch_record_types(self) -> None:
        """phase_record_type delegates Tooling fetching to
        ctx.sf_client.fetch_record_types()."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_record_types.return_value = []
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases._synced_object_api_names",
            return_value=set(),
        ), patch("primeqa.sync.phases.batched_materialize"):
            phase_record_type(ctx, conn)
        ctx.sf_client.fetch_record_types.assert_called_once_with()

    def test_phase_record_type_decorates_parent_marker_from_fullname(
        self,
    ) -> None:
        """Each RT's FullName is split at the first '.' to extract
        the parent Object API name; that name is injected as
        _parent_object_api_name before materialize. The marker
        drives both external_id (which uses FullName directly) AND
        detail-row FK resolution + BELONGS_TO edge target lookup."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_record_types.return_value = [
            self._ok_rt("Account.PartnerAccount"),
            self._ok_rt("Contact.Customer"),
        ]
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases._synced_object_api_names",
            return_value={"Account", "Contact"},
        ), patch("primeqa.sync.phases.batched_materialize") as mock_bm, \
             patch("primeqa.sync.phases.materialize_edges_for_entities"):
            mock_bm.return_value = {
                "Account.PartnerAccount": "rt-1",
                "Contact.Customer": "rt-2",
            }
            phase_record_type(ctx, conn)
        payloads = mock_bm.call_args.kwargs["raw_payloads"]
        assert len(payloads) == 2
        parent_names = {p["_parent_object_api_name"] for p in payloads}
        assert parent_names == {"Account", "Contact"}

    def test_phase_record_type_handles_namespaced_fullname(self) -> None:
        """Namespaced FullName like 'MyNS__Object.RT' splits cleanly:
        parent_object_api_name = 'MyNS__Object'. Same algorithm
        (split at first '.') works for both namespaced and
        non-namespaced; verifying the namespaced case explicitly so
        regressions are loud."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_record_types.return_value = [
            self._ok_rt("sfLma__License__c.Trial"),
        ]
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases._synced_object_api_names",
            return_value={"sfLma__License__c"},
        ), patch("primeqa.sync.phases.batched_materialize") as mock_bm, \
             patch("primeqa.sync.phases.materialize_edges_for_entities"):
            mock_bm.return_value = {"sfLma__License__c.Trial": "rt-1"}
            phase_record_type(ctx, conn)
        payloads = mock_bm.call_args.kwargs["raw_payloads"]
        assert payloads[0]["_parent_object_api_name"] == "sfLma__License__c"

    def test_phase_record_type_calls_batched_materialize_and_edges(
        self,
    ) -> None:
        """phase_record_type calls batched_materialize with
        return_id_map=True and pipes the id_map plus normalized
        payloads to materialize_edges_for_entities for BELONGS_TO
        edge writes."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_record_types.return_value = [
            self._ok_rt("Account.PartnerAccount"),
        ]
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases._synced_object_api_names",
            return_value={"Account"},
        ), patch("primeqa.sync.phases.batched_materialize") as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ) as mock_edges, \
             patch(
                 "primeqa.sync.phases.normalize",
                 side_effect=lambda et, p: {**p, "_normalized": True},
             ):
            mock_bm.return_value = {"Account.PartnerAccount": "rt-1"}
            phase_record_type(ctx, conn)
        # batched_materialize called with return_id_map=True (edges need
        # the source entity_id_map)
        assert mock_bm.call_args.kwargs.get("return_id_map") is True
        assert mock_bm.call_args.kwargs["entity_type"] == "RecordType"
        # edges hook called with the id_map + normalized payloads
        mock_edges.assert_called_once()
        edge_kwargs = mock_edges.call_args.kwargs
        assert edge_kwargs["source_entity_type"] == "RecordType"
        assert edge_kwargs["entity_id_map"] == {
            "Account.PartnerAccount": "rt-1",
        }
        assert len(edge_kwargs["normalized_payloads"]) == 1
        assert edge_kwargs["normalized_payloads"][0]["_normalized"] is True

    def test_phase_record_type_empty_response(self) -> None:
        """Empty fetch (org with 0 RTs — rare but possible) → no
        materialize, no edges, all-zero result."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_record_types.return_value = []
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases._synced_object_api_names",
            return_value=set(),
        ), patch("primeqa.sync.phases.batched_materialize") as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ) as mock_edges:
            result = phase_record_type(ctx, conn)
        mock_bm.assert_not_called()
        mock_edges.assert_not_called()
        assert result.entity_type == "RecordType"
        assert result.entities_inserted == 0
        assert result.succeeded is True

    def test_phase_record_type_filters_unsyncable_parent(self) -> None:
        """RTs whose parent Object isn't in the synced Object set
        are silently dropped (per §18). The filter prevents the
        detail mapper from raising on unresolvable parent FK."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_record_types.return_value = [
            self._ok_rt("Account.Customer"),        # parent synced
            self._ok_rt("sfFma__Internal__c.RT"),   # parent NOT synced
            self._ok_rt("Contact.Standard"),        # parent synced
        ]
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases._synced_object_api_names",
            return_value={"Account", "Contact"},
        ), patch("primeqa.sync.phases.batched_materialize") as mock_bm, \
             patch("primeqa.sync.phases.materialize_edges_for_entities"):
            mock_bm.return_value = {
                "Account.Customer": "rt-1",
                "Contact.Standard": "rt-2",
            }
            phase_record_type(ctx, conn)
        # Only Account + Contact RTs survive the filter
        payloads = mock_bm.call_args.kwargs["raw_payloads"]
        assert len(payloads) == 2
        full_names = {p["FullName"] for p in payloads}
        assert full_names == {"Account.Customer", "Contact.Standard"}

    def test_phase_record_type_proceeds_when_all_parents_syncable(
        self,
    ) -> None:
        """When every RT's parent Object IS in the synced set, no
        RTs are filtered — the filter is a no-op pass-through."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_record_types.return_value = [
            self._ok_rt("Account.RT1"),
            self._ok_rt("Account.RT2"),
        ]
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases._synced_object_api_names",
            return_value={"Account"},
        ), patch("primeqa.sync.phases.batched_materialize") as mock_bm, \
             patch("primeqa.sync.phases.materialize_edges_for_entities"):
            mock_bm.return_value = {
                "Account.RT1": "rt-1",
                "Account.RT2": "rt-2",
            }
            phase_record_type(ctx, conn)
        payloads = mock_bm.call_args.kwargs["raw_payloads"]
        assert len(payloads) == 2

    def test_phase_record_type_all_filtered_no_materialize(self) -> None:
        """If every fetched RT references an unsyncable Object,
        filtered_rts is empty → materialize/edges NOT called →
        all-zero result. No spurious error."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_record_types.return_value = [
            self._ok_rt("sfFma__Internal__c.RT"),
            self._ok_rt("OtherInternal__c.Other"),
        ]
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases._synced_object_api_names",
            return_value={"Account"},  # neither matches
        ), patch("primeqa.sync.phases.batched_materialize") as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ) as mock_edges:
            result = phase_record_type(ctx, conn)
        mock_bm.assert_not_called()
        mock_edges.assert_not_called()
        assert result.entities_inserted == 0


# ----------------------------------------------------------------------
# phase_layout — sixth real phase; first property-bearing edge writer
# ----------------------------------------------------------------------

from primeqa.sync.phases import phase_layout


class TestPhaseLayout:
    def _ctx_with_objects(
        self, object_rows, profile_rows=None,
        recordtype_rows=None, profile_layouts=None,
    ):
        """Build (ctx, conn) for phase_layout tests.

        phase_layout issues THREE entities-table queries:
          - the Object-scope query at phase start
            (entity_type='Object'; rows expose .id + .sf_api_name)
          - the Profile Id→name query inside
            _decorate_layouts_with_profile_assignments
            (entity_type='Profile'; rows expose .sf_id + .sf_api_name)
          - the RecordType Id→entity_id query
            (entity_type='RecordType'; rows expose .sf_id + .id)
        The conn mock dispatches fetchall() by the entity_type
        literal in the SQL.

        object_rows:     list[(entity_id, sf_api_name)]
        profile_rows:    list[(sf_id, sf_api_name)]    — Profile entities
        recordtype_rows: list[(sf_id, entity_id)]       — RecordType entities
        profile_layouts: list[dict] returned by
                         ctx.sf_client.fetch_profile_layouts()
        """
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_profile_layouts.return_value = (
            profile_layouts or []
        )

        obj_result = [
            type('R', (), {'id': r[0], 'sf_api_name': r[1]})()
            for r in object_rows
        ]
        prof_result = [
            type('R', (), {'sf_id': r[0], 'sf_api_name': r[1]})()
            for r in (profile_rows or [])
        ]
        rt_result = [
            type('R', (), {'sf_id': r[0], 'id': r[1]})()
            for r in (recordtype_rows or [])
        ]

        def _execute_side_effect(stmt, params=None):
            sql = str(stmt)
            r = MagicMock()
            if "entity_type = 'Profile'" in sql:
                r.fetchall.return_value = prof_result
            elif "entity_type = 'RecordType'" in sql:
                r.fetchall.return_value = rt_result
            else:
                # Default: the Object-scope query (and any other).
                r.fetchall.return_value = obj_result
            return r

        conn = MagicMock()
        conn.execute.side_effect = _execute_side_effect
        return ctx, conn

    def _layout(self, layout_id="00hF900000ABC", sections=()):
        return {
            "id": layout_id,
            "detailLayoutSections": list(sections),
        }

    def test_phase_layout_fetches_per_object_then_resolves_names(
        self,
    ) -> None:
        """phase_layout calls fetch_layouts_for_object per Object
        AND fetch_layout_names() once for the Id→Name mapping."""
        ctx, conn = self._ctx_with_objects([
            ('obj-acc', 'Account'),
        ])
        ctx.sf_client.fetch_layouts_for_object.return_value = {
            'layouts': [self._layout('00h1')],
        }
        ctx.sf_client.fetch_layout_names.return_value = [
            {'Id': '00h1', 'Name': 'Account Layout',
             'EntityDefinitionId': 'Account', 'LayoutType': 'Standard'},
        ]
        with patch('primeqa.sync.phases.batched_materialize') as mock_bm, \
             patch('primeqa.sync.phases.materialize_edges_for_entities'):
            mock_bm.return_value = {'Account-Account Layout': 'l1'}
            phase_layout(ctx, conn)
        ctx.sf_client.fetch_layouts_for_object.assert_called_once_with(
            'Account',
        )
        ctx.sf_client.fetch_layout_names.assert_called_once_with()

    def test_phase_layout_decorates_layout_with_markers(self) -> None:
        """Each layout gets _parent_object_api_name, _layout_full_name,
        _layout_type, _layout_name_resolved markers injected before
        batched_materialize."""
        ctx, conn = self._ctx_with_objects([('obj-acc', 'Account')])
        ctx.sf_client.fetch_layouts_for_object.return_value = {
            'layouts': [self._layout('00h1')],
        }
        ctx.sf_client.fetch_layout_names.return_value = [
            {'Id': '00h1', 'Name': 'Account Layout',
             'EntityDefinitionId': 'Account', 'LayoutType': 'Standard'},
        ]
        with patch('primeqa.sync.phases.batched_materialize') as mock_bm, \
             patch('primeqa.sync.phases.materialize_edges_for_entities'):
            mock_bm.return_value = {'Account-Account Layout': 'l1'}
            phase_layout(ctx, conn)
        payloads = mock_bm.call_args.kwargs['raw_payloads']
        assert len(payloads) == 1
        layout = payloads[0]
        assert layout['_parent_object_api_name'] == 'Account'
        assert layout['_layout_full_name'] == 'Account-Account Layout'
        assert layout['_layout_type'] == 'Standard'
        assert layout['_layout_name_resolved'] == 'Account Layout'

    def test_phase_layout_filters_global_quick_action_list(self) -> None:
        """LayoutType='GlobalQuickActionList' has EntityDefinitionId='Global'
        which isn't a real sObject → can't satisfy
        layout_details.object_entity_id NOT NULL FK. Skip per §15."""
        ctx, conn = self._ctx_with_objects([('obj-acc', 'Account')])
        ctx.sf_client.fetch_layouts_for_object.return_value = {
            'layouts': [
                self._layout('00h1'),  # will resolve to Standard
                self._layout('00h2'),  # will resolve to GlobalQuickActionList
            ],
        }
        ctx.sf_client.fetch_layout_names.return_value = [
            {'Id': '00h1', 'Name': 'Account Layout',
             'EntityDefinitionId': 'Account', 'LayoutType': 'Standard'},
            {'Id': '00h2', 'Name': 'Global Quick Action List',
             'EntityDefinitionId': 'Global',
             'LayoutType': 'GlobalQuickActionList'},
        ]
        with patch('primeqa.sync.phases.batched_materialize') as mock_bm, \
             patch('primeqa.sync.phases.materialize_edges_for_entities'):
            mock_bm.return_value = {'Account-Account Layout': 'l1'}
            phase_layout(ctx, conn)
        payloads = mock_bm.call_args.kwargs['raw_payloads']
        # Only the Standard layout survives the filter
        assert len(payloads) == 1
        assert payloads[0]['_layout_type'] == 'Standard'

    def test_phase_layout_skips_layouts_missing_from_tooling(self) -> None:
        """Layout Id appears in REST describe/layouts but NOT in
        fetch_layout_names() response (e.g., a sandbox-vs-tooling
        drift) → skip with WARN log. Defensive."""
        ctx, conn = self._ctx_with_objects([('obj-acc', 'Account')])
        ctx.sf_client.fetch_layouts_for_object.return_value = {
            'layouts': [
                self._layout('00h-resolved'),
                self._layout('00h-orphan'),
            ],
        }
        ctx.sf_client.fetch_layout_names.return_value = [
            {'Id': '00h-resolved', 'Name': 'Account Layout',
             'EntityDefinitionId': 'Account', 'LayoutType': 'Standard'},
        ]
        with patch('primeqa.sync.phases.batched_materialize') as mock_bm, \
             patch('primeqa.sync.phases.materialize_edges_for_entities'):
            mock_bm.return_value = {'Account-Account Layout': 'l1'}
            phase_layout(ctx, conn)
        payloads = mock_bm.call_args.kwargs['raw_payloads']
        # Only the resolved one survives
        assert len(payloads) == 1
        assert payloads[0]['id'] == '00h-resolved'

    def test_phase_layout_empty_object_set_short_circuits(self) -> None:
        """No syncable Objects → no fetch calls, no materialize, no
        edges. Defensive."""
        ctx, conn = self._ctx_with_objects([])
        with patch('primeqa.sync.phases.batched_materialize') as mock_bm, \
             patch(
                 'primeqa.sync.phases.materialize_edges_for_entities',
             ) as mock_edges:
            result = phase_layout(ctx, conn)
        ctx.sf_client.fetch_layouts_for_object.assert_not_called()
        ctx.sf_client.fetch_layout_names.assert_not_called()
        mock_bm.assert_not_called()
        mock_edges.assert_not_called()
        assert result.entity_type == 'Layout'
        assert result.entities_inserted == 0

    def test_phase_layout_tolerates_per_object_fetch_failure(self) -> None:
        """fetch_layouts_for_object(X) raises (industry-cloud Object
        with no describe/layouts endpoint) → log warning, continue
        with the other Objects. Mirrors fetch_standard_value_sets
        per-label tolerance."""
        ctx, conn = self._ctx_with_objects([
            ('obj-acc', 'Account'),
            ('obj-bad', 'Inaccessible__c'),
        ])

        def fetch_side_effect(name):
            if name == 'Inaccessible__c':
                raise RuntimeError('404 Not Found')
            return {'layouts': [self._layout('00h1')]}

        ctx.sf_client.fetch_layouts_for_object.side_effect = fetch_side_effect
        ctx.sf_client.fetch_layout_names.return_value = [
            {'Id': '00h1', 'Name': 'Account Layout',
             'EntityDefinitionId': 'Account', 'LayoutType': 'Standard'},
        ]
        with patch('primeqa.sync.phases.batched_materialize') as mock_bm, \
             patch('primeqa.sync.phases.materialize_edges_for_entities'):
            mock_bm.return_value = {'Account-Account Layout': 'l1'}
            phase_layout(ctx, conn)
        # Account's layout survived; Inaccessible__c silently skipped
        payloads = mock_bm.call_args.kwargs['raw_payloads']
        assert len(payloads) == 1
        assert payloads[0]['_parent_object_api_name'] == 'Account'

    # ----- ASSIGNED_TO_PROFILE_RECORDTYPE decoration (§16 resolution) -----

    def _layout_with_rtm(self, layout_id, rtm=()):
        """A describe/layouts layout payload that also carries the
        recordTypeMappings the is_default cross-reference reads."""
        return {
            "id": layout_id,
            "detailLayoutSections": [],
            "recordTypeMappings": list(rtm),
        }

    def test_phase_layout_decorates_profile_layout_assignments(
        self,
    ) -> None:
        """phase_layout fetches ProfileLayout rows and decorates
        each Layout payload with _profile_layout_assignments —
        resolved (profile_name, record_type_entity_id, is_default)
        triples. The marker is injected AFTER batched_materialize
        (edge-extraction input, not Layout entity state), so it
        lands in normalized_payloads but NOT raw_payloads."""
        ctx, conn = self._ctx_with_objects(
            [('obj-acc', 'Account')],
            profile_rows=[('00eAdmin', 'Admin')],
            recordtype_rows=[('012rtA', 'rt-entity-uuid-A')],
            profile_layouts=[
                {"Id": "01G1", "ProfileId": "00eAdmin",
                 "LayoutId": "00h1", "RecordTypeId": "012rtA"},
            ],
        )
        ctx.sf_client.fetch_layouts_for_object.return_value = {
            'layouts': [self._layout_with_rtm("00h1", rtm=[
                {"recordTypeId": "012rtA",
                 "defaultRecordTypeMapping": True},
            ])],
        }
        ctx.sf_client.fetch_layout_names.return_value = [
            {'Id': '00h1', 'Name': 'Account Layout',
             'EntityDefinitionId': 'Account', 'LayoutType': 'Standard'},
        ]
        captured = {}
        with patch('primeqa.sync.phases.batched_materialize') as mock_bm, \
             patch(
                 'primeqa.sync.phases.materialize_edges_for_entities',
             ) as mock_edges, \
             patch(
                 'primeqa.sync.phases.normalize',
                 side_effect=lambda et, p: dict(p),
             ):
            mock_bm.return_value = {'Account-Account Layout': 'l1'}
            phase_layout(ctx, conn)
        ctx.sf_client.fetch_profile_layouts.assert_called_once_with()
        # The marker is NOT on raw_payloads (decorated AFTER
        # batched_materialize — keeps the Layout entity hash
        # decoupled from ProfileLayout churn).
        raw = mock_bm.call_args.kwargs['raw_payloads'][0]
        # batched_materialize ran before decoration — but since the
        # same dict object is mutated in place afterwards, we check
        # the edge path instead: normalized_payloads carries it.
        norm = mock_edges.call_args.kwargs['normalized_payloads'][0]
        assert norm['_profile_layout_assignments'] == [
            {"profile_name": "Admin",
             "record_type_entity_id": "rt-entity-uuid-A",
             "is_default": True},
        ]

    def test_phase_layout_skips_null_recordtype_assignments(
        self,
    ) -> None:
        """ProfileLayout rows with RecordTypeId=NULL are out of
        scope (§16 Decision 1A — the schema's record_type_entity_id
        is required). They're filtered out of the decoration."""
        ctx, conn = self._ctx_with_objects(
            [('obj-acc', 'Account')],
            profile_rows=[('00eAdmin', 'Admin')],
            recordtype_rows=[('012rtA', 'rt-entity-A')],
            profile_layouts=[
                {"Id": "01G1", "ProfileId": "00eAdmin",
                 "LayoutId": "00h1", "RecordTypeId": "012rtA"},
                {"Id": "01G2", "ProfileId": "00eAdmin",
                 "LayoutId": "00h1", "RecordTypeId": None},  # NULL-RT
            ],
        )
        ctx.sf_client.fetch_layouts_for_object.return_value = {
            'layouts': [self._layout_with_rtm("00h1")],
        }
        ctx.sf_client.fetch_layout_names.return_value = [
            {'Id': '00h1', 'Name': 'Account Layout',
             'EntityDefinitionId': 'Account', 'LayoutType': 'Standard'},
        ]
        with patch('primeqa.sync.phases.batched_materialize') as mock_bm, \
             patch(
                 'primeqa.sync.phases.materialize_edges_for_entities',
             ) as mock_edges, \
             patch(
                 'primeqa.sync.phases.normalize',
                 side_effect=lambda et, p: dict(p),
             ):
            mock_bm.return_value = {'Account-Account Layout': 'l1'}
            phase_layout(ctx, conn)
        norm = mock_edges.call_args.kwargs['normalized_payloads'][0]
        # Only the RT-bound assignment survives; NULL-RT dropped
        assert len(norm['_profile_layout_assignments']) == 1
        assert norm['_profile_layout_assignments'][0][
            'record_type_entity_id'] == 'rt-entity-A'

    def test_phase_layout_skips_unresolved_profile_or_recordtype(
        self,
    ) -> None:
        """ProfileLayout rows whose Profile or RecordType isn't in
        the synced entity set are silently skipped (managed-package
        internals etc.)."""
        ctx, conn = self._ctx_with_objects(
            [('obj-acc', 'Account')],
            profile_rows=[('00eAdmin', 'Admin')],  # only Admin synced
            recordtype_rows=[('012rtA', 'rt-entity-A')],  # only rtA synced
            profile_layouts=[
                # resolvable
                {"Id": "01G1", "ProfileId": "00eAdmin",
                 "LayoutId": "00h1", "RecordTypeId": "012rtA"},
                # Profile not synced
                {"Id": "01G2", "ProfileId": "00eUNSYNCED",
                 "LayoutId": "00h1", "RecordTypeId": "012rtA"},
                # RecordType not synced
                {"Id": "01G3", "ProfileId": "00eAdmin",
                 "LayoutId": "00h1", "RecordTypeId": "012UNSYNCED"},
            ],
        )
        ctx.sf_client.fetch_layouts_for_object.return_value = {
            'layouts': [self._layout_with_rtm("00h1")],
        }
        ctx.sf_client.fetch_layout_names.return_value = [
            {'Id': '00h1', 'Name': 'Account Layout',
             'EntityDefinitionId': 'Account', 'LayoutType': 'Standard'},
        ]
        with patch('primeqa.sync.phases.batched_materialize') as mock_bm, \
             patch(
                 'primeqa.sync.phases.materialize_edges_for_entities',
             ) as mock_edges, \
             patch(
                 'primeqa.sync.phases.normalize',
                 side_effect=lambda et, p: dict(p),
             ):
            mock_bm.return_value = {'Account-Account Layout': 'l1'}
            phase_layout(ctx, conn)
        norm = mock_edges.call_args.kwargs['normalized_payloads'][0]
        # Only the fully-resolvable assignment survives
        assert len(norm['_profile_layout_assignments']) == 1
        assert norm['_profile_layout_assignments'][0][
            'profile_name'] == 'Admin'

    def test_phase_layout_is_default_from_recordtype_mappings(
        self,
    ) -> None:
        """is_default is cross-referenced from the describe/layouts
        recordTypeMappings[].defaultRecordTypeMapping flag for the
        (LayoutId, RecordTypeId) pair (§16 Decision 2A)."""
        ctx, conn = self._ctx_with_objects(
            [('obj-acc', 'Account')],
            profile_rows=[('00eAdmin', 'Admin'),
                          ('00eStd', 'Standard')],
            recordtype_rows=[('012rtA', 'rt-A'), ('012rtB', 'rt-B')],
            profile_layouts=[
                {"Id": "01G1", "ProfileId": "00eAdmin",
                 "LayoutId": "00h1", "RecordTypeId": "012rtA"},
                {"Id": "01G2", "ProfileId": "00eStd",
                 "LayoutId": "00h1", "RecordTypeId": "012rtB"},
            ],
        )
        ctx.sf_client.fetch_layouts_for_object.return_value = {
            'layouts': [self._layout_with_rtm("00h1", rtm=[
                {"recordTypeId": "012rtA",
                 "defaultRecordTypeMapping": True},
                {"recordTypeId": "012rtB",
                 "defaultRecordTypeMapping": False},
            ])],
        }
        ctx.sf_client.fetch_layout_names.return_value = [
            {'Id': '00h1', 'Name': 'Account Layout',
             'EntityDefinitionId': 'Account', 'LayoutType': 'Standard'},
        ]
        with patch('primeqa.sync.phases.batched_materialize') as mock_bm, \
             patch(
                 'primeqa.sync.phases.materialize_edges_for_entities',
             ) as mock_edges, \
             patch(
                 'primeqa.sync.phases.normalize',
                 side_effect=lambda et, p: dict(p),
             ):
            mock_bm.return_value = {'Account-Account Layout': 'l1'}
            phase_layout(ctx, conn)
        norm = mock_edges.call_args.kwargs['normalized_payloads'][0]
        by_profile = {
            a['profile_name']: a
            for a in norm['_profile_layout_assignments']
        }
        # rtA mapping is default; rtB is not
        assert by_profile['Admin']['is_default'] is True
        assert by_profile['Standard']['is_default'] is False

    def test_phase_layout_empty_profile_layouts_yields_empty_marker(
        self,
    ) -> None:
        """When fetch_profile_layouts returns nothing, every Layout
        still gets a _profile_layout_assignments key (empty list) —
        the edge extractor then produces no ATPRT edges."""
        ctx, conn = self._ctx_with_objects(
            [('obj-acc', 'Account')],
            profile_layouts=[],
        )
        ctx.sf_client.fetch_layouts_for_object.return_value = {
            'layouts': [self._layout_with_rtm("00h1")],
        }
        ctx.sf_client.fetch_layout_names.return_value = [
            {'Id': '00h1', 'Name': 'Account Layout',
             'EntityDefinitionId': 'Account', 'LayoutType': 'Standard'},
        ]
        with patch('primeqa.sync.phases.batched_materialize') as mock_bm, \
             patch(
                 'primeqa.sync.phases.materialize_edges_for_entities',
             ) as mock_edges, \
             patch(
                 'primeqa.sync.phases.normalize',
                 side_effect=lambda et, p: dict(p),
             ):
            mock_bm.return_value = {'Account-Account Layout': 'l1'}
            phase_layout(ctx, conn)
        norm = mock_edges.call_args.kwargs['normalized_payloads'][0]
        assert norm['_profile_layout_assignments'] == []


# ----------------------------------------------------------------------
# phase_validation_rule — seventh real phase
# ----------------------------------------------------------------------

from primeqa.sync.phases import phase_validation_rule


class TestPhaseValidationRule:
    def _ok_vr(self, full_name="Account.AmountPositive", **overrides):
        base = {
            "Id": "03dF9000000ABC",
            "FullName": full_name,
            "ValidationName": "AmountPositive",
            "Active": True,
            "ErrorMessage": "Amount must be positive",
            "ErrorDisplayField": "Amount",
            "Description": "Ensures amount is non-negative",
            "EntityDefinitionId": "01IF9000001CNEB",
            "Metadata": {
                "errorConditionFormula": "Amount <= 0",
                "active": True,
            },
        }
        base.update(overrides)
        return base

    def test_phase_validation_rule_calls_fetch_validation_rules(self) -> None:
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_validation_rules.return_value = []
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases._synced_object_api_names",
            return_value=set(),
        ), patch("primeqa.sync.phases.batched_materialize"):
            phase_validation_rule(ctx, conn)
        ctx.sf_client.fetch_validation_rules.assert_called_once_with()

    def test_phase_validation_rule_decorates_parent_marker_from_fullname(
        self,
    ) -> None:
        """Each VR's FullName is split at the first '.' to extract
        the parent Object api_name; that name is injected as
        _parent_object_api_name before materialize. Same algorithm
        as phase_record_type."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_validation_rules.return_value = [
            self._ok_vr("Account.AmountPositive"),
            self._ok_vr("Opportunity.StatusValid"),
        ]
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases._synced_object_api_names",
            return_value={"Account", "Opportunity"},
        ), patch("primeqa.sync.phases.batched_materialize") as mock_bm, \
             patch("primeqa.sync.phases.materialize_edges_for_entities"):
            mock_bm.return_value = {
                "Account.AmountPositive": "vr-1",
                "Opportunity.StatusValid": "vr-2",
            }
            phase_validation_rule(ctx, conn)
        payloads = mock_bm.call_args.kwargs["raw_payloads"]
        parent_names = {p["_parent_object_api_name"] for p in payloads}
        assert parent_names == {"Account", "Opportunity"}

    def test_phase_validation_rule_handles_namespaced_fullname(
        self,
    ) -> None:
        """Managed-package object: 'MyNS__Object.RuleName' splits
        cleanly to 'MyNS__Object'."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_validation_rules.return_value = [
            self._ok_vr("sfLma__License__c.MustHaveOwner"),
        ]
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases._synced_object_api_names",
            return_value={"sfLma__License__c"},
        ), patch("primeqa.sync.phases.batched_materialize") as mock_bm, \
             patch("primeqa.sync.phases.materialize_edges_for_entities"):
            mock_bm.return_value = {
                "sfLma__License__c.MustHaveOwner": "vr-1",
            }
            phase_validation_rule(ctx, conn)
        payloads = mock_bm.call_args.kwargs["raw_payloads"]
        assert payloads[0]["_parent_object_api_name"] == "sfLma__License__c"

    def test_phase_validation_rule_calls_batched_materialize_and_edges(
        self,
    ) -> None:
        """Verifies batched_materialize is called with
        return_id_map=True and materialize_edges_for_entities gets
        the id_map + normalized payloads for VR-source edges
        (BELONGS_TO + APPLIES_TO)."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_validation_rules.return_value = [
            self._ok_vr("Account.AmountPositive"),
        ]
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases._synced_object_api_names",
            return_value={"Account"},
        ), patch("primeqa.sync.phases.batched_materialize") as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ) as mock_edges, \
             patch(
                 "primeqa.sync.phases.normalize",
                 side_effect=lambda et, p: {**p, "_normalized": True},
             ):
            mock_bm.return_value = {"Account.AmountPositive": "vr-1"}
            phase_validation_rule(ctx, conn)
        assert mock_bm.call_args.kwargs.get("return_id_map") is True
        assert mock_bm.call_args.kwargs["entity_type"] == "ValidationRule"
        mock_edges.assert_called_once()
        edge_kwargs = mock_edges.call_args.kwargs
        assert edge_kwargs["source_entity_type"] == "ValidationRule"
        assert edge_kwargs["entity_id_map"] == {
            "Account.AmountPositive": "vr-1",
        }

    def test_phase_validation_rule_empty_response(self) -> None:
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_validation_rules.return_value = []
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases._synced_object_api_names",
            return_value=set(),
        ), patch("primeqa.sync.phases.batched_materialize") as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ) as mock_edges:
            result = phase_validation_rule(ctx, conn)
        mock_bm.assert_not_called()
        mock_edges.assert_not_called()
        assert result.entity_type == "ValidationRule"
        assert result.entities_inserted == 0
        assert result.succeeded is True

    def test_phase_validation_rule_filters_unsyncable_parent(self) -> None:
        """VRs whose parent Object isn't in the synced Object set
        are silently dropped (per §18 — surfaced live by sandbox's
        sfFma__FeatureParameterBoolean__c VR)."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_validation_rules.return_value = [
            self._ok_vr("Account.AmountPositive"),       # synced
            self._ok_vr("sfFma__Internal__c.RuleName"),  # NOT synced
            self._ok_vr("Opportunity.HasOwner"),         # synced
        ]
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases._synced_object_api_names",
            return_value={"Account", "Opportunity"},
        ), patch("primeqa.sync.phases.batched_materialize") as mock_bm, \
             patch("primeqa.sync.phases.materialize_edges_for_entities"):
            mock_bm.return_value = {
                "Account.AmountPositive": "vr-1",
                "Opportunity.HasOwner": "vr-2",
            }
            phase_validation_rule(ctx, conn)
        payloads = mock_bm.call_args.kwargs["raw_payloads"]
        assert len(payloads) == 2
        full_names = {p["FullName"] for p in payloads}
        assert full_names == {
            "Account.AmountPositive", "Opportunity.HasOwner",
        }

    def test_phase_validation_rule_proceeds_when_all_parents_syncable(
        self,
    ) -> None:
        """No-op filter pass-through: every VR's parent in synced
        set → all VRs survive."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_validation_rules.return_value = [
            self._ok_vr("Account.V1"),
            self._ok_vr("Account.V2"),
        ]
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases._synced_object_api_names",
            return_value={"Account"},
        ), patch("primeqa.sync.phases.batched_materialize") as mock_bm, \
             patch("primeqa.sync.phases.materialize_edges_for_entities"):
            mock_bm.return_value = {
                "Account.V1": "vr-1", "Account.V2": "vr-2",
            }
            phase_validation_rule(ctx, conn)
        payloads = mock_bm.call_args.kwargs["raw_payloads"]
        assert len(payloads) == 2

    def test_phase_validation_rule_all_filtered_no_materialize(self) -> None:
        """All VRs reference unsyncable parents → empty filtered set
        → no materialize, no edges, all-zero result."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_validation_rules.return_value = [
            self._ok_vr("sfFma__Internal__c.RuleA"),
            self._ok_vr("OtherInternal__c.RuleB"),
        ]
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases._synced_object_api_names",
            return_value={"Account"},  # neither matches
        ), patch("primeqa.sync.phases.batched_materialize") as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ) as mock_edges:
            result = phase_validation_rule(ctx, conn)
        mock_bm.assert_not_called()
        mock_edges.assert_not_called()
        assert result.entities_inserted == 0


# ----------------------------------------------------------------------
# _synced_object_api_names — bulk-fetcher parent-scope helper per §18
# ----------------------------------------------------------------------

from primeqa.sync.phases import _synced_object_api_names


class TestSyncedObjectApiNames:
    def test_returns_set_of_api_names(self) -> None:
        """Helper returns a set keyed by sf_api_name from
        currently-active Object entities for this sync's org."""
        ctx = _stub_ctx_with_mock_sf()
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            type("R", (), {"sf_api_name": "Account"})(),
            type("R", (), {"sf_api_name": "Contact"})(),
            type("R", (), {"sf_api_name": "MyNS__Custom__c"})(),
        ]
        result = _synced_object_api_names(ctx, conn)
        assert result == {"Account", "Contact", "MyNS__Custom__c"}

    def test_empty_when_no_objects(self) -> None:
        """Empty entities table → empty set. Bulk-fetcher phases
        treat empty set as 'filter everything out' which is the
        correct conservative behavior."""
        ctx = _stub_ctx_with_mock_sf()
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        result = _synced_object_api_names(ctx, conn)
        assert result == set()

    def test_scopes_to_org_and_active(self) -> None:
        """Query filters by last_synced_from_org_id (current
        sync's connected_org) AND entity_type='Object' AND
        valid_to_seq IS NULL. Verifies the SQL contains these
        scoping conditions."""
        ctx = _stub_ctx_with_mock_sf()
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        _synced_object_api_names(ctx, conn)
        sql_text = str(conn.execute.call_args[0][0])
        assert "entity_type = 'Object'" in sql_text
        assert "valid_to_seq IS NULL" in sql_text
        assert "last_synced_from_org_id = :org_id" in sql_text
        params = conn.execute.call_args[0][1]
        assert params["org_id"] == ctx.connected_org_id


# ----------------------------------------------------------------------
# phase_profile — eighth real phase; first org-level entity phase
# ----------------------------------------------------------------------

from primeqa.sync.phases import phase_profile


class TestPhaseProfile:
    def _ok_profile(self, name="Read Only", **overrides):
        base = {
            "Id": "00eF9000000ABC",
            "Name": name,
            "Description": None,
            "FullName": name,
            "Metadata": {
                "custom": False,
                "userLicense": "Salesforce",
                "objectPermissions": [
                    {"object": "Account", "allowRead": True},
                ],
                "fieldPermissions": [
                    {"field": "Account.Industry",
                     "readable": True, "editable": False},
                ],
            },
        }
        base.update(overrides)
        return base

    def test_phase_profile_calls_fetch_profiles(self) -> None:
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_profiles.return_value = []
        conn = MagicMock()
        with patch("primeqa.sync.phases.batched_materialize"):
            phase_profile(ctx, conn)
        ctx.sf_client.fetch_profiles.assert_called_once_with()

    def test_phase_profile_does_not_inject_parent_marker(self) -> None:
        """Profile is org-level. Unlike RT/VR/Field/Layout phases,
        phase_profile does NOT inject _parent_object_api_name or
        any parent marker on payloads — Profile has no parent."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_profiles.return_value = [
            self._ok_profile("Read Only"),
            self._ok_profile("System Administrator"),
        ]
        conn = MagicMock()
        with patch("primeqa.sync.phases.batched_materialize") as mock_bm, \
             patch("primeqa.sync.phases.materialize_edges_for_entities"):
            mock_bm.return_value = {
                "Read Only": "prof-1",
                "System Administrator": "prof-2",
            }
            phase_profile(ctx, conn)
        payloads = mock_bm.call_args.kwargs["raw_payloads"]
        for p in payloads:
            assert "_parent_object_api_name" not in p

    def test_phase_profile_calls_batched_materialize_and_edges(
        self,
    ) -> None:
        """phase_profile materializes Profile entities and writes
        GRANTS_OBJECT_ACCESS + GRANTS_FIELD_ACCESS edges via
        materialize_edges_for_entities."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_profiles.return_value = [
            self._ok_profile("Read Only"),
        ]
        conn = MagicMock()
        with patch("primeqa.sync.phases.batched_materialize") as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ) as mock_edges, \
             patch(
                 "primeqa.sync.phases.normalize",
                 side_effect=lambda et, p: {**p, "_normalized": True},
             ):
            mock_bm.return_value = {"Read Only": "prof-1"}
            phase_profile(ctx, conn)
        assert mock_bm.call_args.kwargs.get("return_id_map") is True
        assert mock_bm.call_args.kwargs["entity_type"] == "Profile"
        mock_edges.assert_called_once()
        edge_kwargs = mock_edges.call_args.kwargs
        assert edge_kwargs["source_entity_type"] == "Profile"
        assert edge_kwargs["entity_id_map"] == {"Read Only": "prof-1"}

    def test_phase_profile_empty_response(self) -> None:
        """Org with 0 Profiles (impossible in practice but defensive).
        No materialize, no edges, all-zero result."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_profiles.return_value = []
        conn = MagicMock()
        with patch("primeqa.sync.phases.batched_materialize") as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ) as mock_edges:
            result = phase_profile(ctx, conn)
        mock_bm.assert_not_called()
        mock_edges.assert_not_called()
        assert result.entity_type == "Profile"
        assert result.entities_inserted == 0
        assert result.succeeded is True

    def test_phase_profile_does_not_filter_by_synced_objects(self) -> None:
        """Profile is org-level (not child-of-Object). §18 parent-
        filter pattern does NOT apply — phase_profile must NOT
        call _synced_object_api_names, and must NOT pre-filter
        payloads. (Edge target resolution skips unsyncable
        targets at the materialize layer's level instead.)"""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_profiles.return_value = [
            self._ok_profile("Read Only"),
        ]
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases._synced_object_api_names",
        ) as mock_filter, \
             patch("primeqa.sync.phases.batched_materialize") as mock_bm, \
             patch("primeqa.sync.phases.materialize_edges_for_entities"):
            mock_bm.return_value = {"Read Only": "prof-1"}
            phase_profile(ctx, conn)
        # Profile phase doesn't need the parent-scope filter
        mock_filter.assert_not_called()


# ----------------------------------------------------------------------
# phase_permission_set — ninth real phase (Category 4 fetcher;
# Type='Profile' filter; ParentId JOIN; PSG decoration via
# psg_components + Id→Name map; license_id_to_label resolution)
# ----------------------------------------------------------------------


from primeqa.sync.phases import phase_permission_set


class TestPhasePermissionSet:
    def _ok_fetch_result(
        self,
        permission_sets=None,
        object_permissions=None,
        field_permissions=None,
        psg_components=None,
        license_id_to_label=None,
    ):
        """Build a five-key fetch_permission_sets-shaped dict."""
        return {
            "permission_sets": permission_sets or [],
            "object_permissions": object_permissions or [],
            "field_permissions": field_permissions or [],
            "psg_components": psg_components or [],
            "license_id_to_label": license_id_to_label or {},
        }

    def _regular_ps(
        self, ps_id="0PS001", name="MyPS", license_id=None,
    ):
        return {
            "Id": ps_id, "Name": name, "Label": name,
            "Type": "Regular", "IsCustom": True,
            "Description": "Test PS",
            "LicenseId": license_id,
        }

    def _profile_synth_ps(self, ps_id="0PS018", name="X00e00001"):
        """Type='Profile' auto-synth duplicate that the phase MUST
        filter out per substrate-1 §5."""
        return {
            "Id": ps_id, "Name": name, "Type": "Profile",
            "IsCustom": False, "LicenseId": None,
        }

    def test_phase_permission_set_calls_fetch_permission_sets(
        self,
    ) -> None:
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_permission_sets.return_value = (
            self._ok_fetch_result()
        )
        conn = MagicMock()
        with patch("primeqa.sync.phases.batched_materialize"):
            phase_permission_set(ctx, conn)
        ctx.sf_client.fetch_permission_sets.assert_called_once_with()

    def test_phase_permission_set_filters_type_profile_rows(
        self,
    ) -> None:
        """Type='Profile' rows are dropped per substrate-1 §5 —
        they're auto-synth duplicates of Profile entities and
        materializing them would double-count permissions."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_permission_sets.return_value = (
            self._ok_fetch_result(
                permission_sets=[
                    self._regular_ps(ps_id="0PS001", name="Reg1"),
                    self._profile_synth_ps(),
                    self._regular_ps(ps_id="0PS002", name="Reg2"),
                ],
            )
        )
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases.batched_materialize",
        ) as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ):
            mock_bm.return_value = {"Reg1": "id1", "Reg2": "id2"}
            phase_permission_set(ctx, conn)
        payloads = mock_bm.call_args.kwargs["raw_payloads"]
        names = [p["Name"] for p in payloads]
        # X00e00001 (Type='Profile') filtered; Reg1 + Reg2 kept
        assert names == ["Reg1", "Reg2"]
        assert "X00e00001" not in names

    def test_phase_permission_set_joins_child_permissions_by_parent_id(
        self,
    ) -> None:
        """ObjectPermissions / FieldPermissions are flat lists with
        ParentId; phase must JOIN them into each PS's record as
        top-level `objectPermissions` / `fieldPermissions` lists
        (matching substrate-1's _normalize_permission_set
        expectation of Profile-shape payload)."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_permission_sets.return_value = (
            self._ok_fetch_result(
                permission_sets=[self._regular_ps(ps_id="0PS001")],
                object_permissions=[
                    {"ParentId": "0PS001",
                     "SobjectType": "Account",
                     "PermissionsRead": True},
                    {"ParentId": "0PS001",
                     "SobjectType": "Contact",
                     "PermissionsRead": True},
                    # Should NOT land on 0PS001 (different parent)
                    {"ParentId": "0PSOther",
                     "SobjectType": "Lead",
                     "PermissionsRead": True},
                ],
                field_permissions=[
                    {"ParentId": "0PS001",
                     "Field": "Account.Industry",
                     "PermissionsRead": True},
                ],
            )
        )
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases.batched_materialize",
        ) as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ):
            mock_bm.return_value = {"MyPS": "id1"}
            phase_permission_set(ctx, conn)
        payloads = mock_bm.call_args.kwargs["raw_payloads"]
        assert len(payloads) == 1
        ps = payloads[0]
        # Only the 2 OPs sharing ParentId=0PS001 landed
        assert len(ps["objectPermissions"]) == 2
        sob_types = {op["SobjectType"] for op in ps["objectPermissions"]}
        assert sob_types == {"Account", "Contact"}
        # FPs joined
        assert len(ps["fieldPermissions"]) == 1
        assert ps["fieldPermissions"][0]["Field"] == "Account.Industry"

    def test_phase_permission_set_resolves_license_label(self) -> None:
        """LicenseId → MasterLabel via license_id_to_label map.
        The mapper reads `_license_label` (not LicenseId) so the
        resolved label lands in permission_set_details.license_type."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_permission_sets.return_value = (
            self._ok_fetch_result(
                permission_sets=[
                    self._regular_ps(
                        ps_id="0PS001", name="WithLicense",
                        license_id="0PL_xxx",
                    ),
                    self._regular_ps(
                        ps_id="0PS002", name="NoLicense",
                        license_id=None,
                    ),
                    self._regular_ps(
                        ps_id="0PS003", name="UnknownLicense",
                        license_id="0PL_unmapped",
                    ),
                ],
                license_id_to_label={"0PL_xxx": "Salesforce Platform"},
            )
        )
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases.batched_materialize",
        ) as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ):
            mock_bm.return_value = {}
            phase_permission_set(ctx, conn)
        payloads = mock_bm.call_args.kwargs["raw_payloads"]
        labels = {p["Name"]: p["_license_label"] for p in payloads}
        # Resolved
        assert labels["WithLicense"] == "Salesforce Platform"
        # Null LicenseId → sentinel
        assert labels["NoLicense"] == "(no license)"
        # Unmapped LicenseId → sentinel
        assert labels["UnknownLicense"] == "(no license)"

    def test_phase_permission_set_decorates_psg_with_member_ps_names(
        self,
    ) -> None:
        """Type='Group' PSes (PSGs) get `_member_ps_names` injected
        by joining psg_components to permission_sets via the
        Salesforce schema's two-step lookup:
        - PermissionSet(Type='Group').PermissionSetGroupId (0PG...)
          matches PermissionSetGroupComponent.PermissionSetGroupId
        - PermissionSetGroupComponent.PermissionSetId (0PS...)
          matches PermissionSet.Id of the member.

        The PSG shadow row's own `Id` is 0PS-prefixed and is NOT
        used for component lookup — that was the bug the live test
        caught (initial cycle used Id directly; INHERITS edges
        always came out empty)."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_permission_sets.return_value = (
            self._ok_fetch_result(
                permission_sets=[
                    # PSG shadow: Id is 0PS-prefixed; the actual PSG's
                    # Id is in PermissionSetGroupId (0PG-prefixed).
                    {"Id": "0PSShadowGroup",
                     "PermissionSetGroupId": "0PGReal",
                     "Name": "MyPSG",
                     "Type": "Group", "IsCustom": True},
                    {"Id": "0PS001", "Name": "MemberA",
                     "Type": "Regular", "IsCustom": True},
                    {"Id": "0PS002", "Name": "MemberB",
                     "Type": "Regular", "IsCustom": True},
                ],
                psg_components=[
                    # Components reference the 0PG-prefixed PSG Id
                    {"PermissionSetGroupId": "0PGReal",
                     "PermissionSetId": "0PS001"},
                    {"PermissionSetGroupId": "0PGReal",
                     "PermissionSetId": "0PS002"},
                ],
            )
        )
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases.batched_materialize",
        ) as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ):
            mock_bm.return_value = {}
            phase_permission_set(ctx, conn)
        payloads = mock_bm.call_args.kwargs["raw_payloads"]
        by_name = {p["Name"]: p for p in payloads}
        # PSG decorated with sorted member names
        assert by_name["MyPSG"]["_member_ps_names"] == [
            "MemberA", "MemberB",
        ]
        # Non-Group PSes get NO marker
        assert "_member_ps_names" not in by_name["MemberA"]
        assert "_member_ps_names" not in by_name["MemberB"]

    def test_phase_permission_set_psg_without_permission_set_group_id_no_marker(
        self,
    ) -> None:
        """Defensive: a Type='Group' shadow without a
        PermissionSetGroupId field (malformed Salesforce response)
        gets `_member_ps_names = []` rather than crashing. The
        INHERITS extractor returns no edges for that PSG."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_permission_sets.return_value = (
            self._ok_fetch_result(
                permission_sets=[
                    {"Id": "0PSGroup", "Name": "MalformedPSG",
                     # PermissionSetGroupId MISSING
                     "Type": "Group", "IsCustom": True},
                ],
            )
        )
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases.batched_materialize",
        ) as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ):
            mock_bm.return_value = {}
            phase_permission_set(ctx, conn)
        ps = mock_bm.call_args.kwargs["raw_payloads"][0]
        # Marker present but empty — extractor will return []
        assert ps["_member_ps_names"] == []

    def test_phase_permission_set_calls_materialize_and_edges(
        self,
    ) -> None:
        """phase_permission_set materializes PS entities then writes
        edges via materialize_edges_for_entities — same pattern as
        Profile cycle."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_permission_sets.return_value = (
            self._ok_fetch_result(
                permission_sets=[self._regular_ps()],
            )
        )
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases.batched_materialize",
        ) as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ) as mock_edges, \
             patch(
                 "primeqa.sync.phases.normalize",
                 side_effect=lambda et, p: {**p, "_normalized": True},
             ):
            mock_bm.return_value = {"MyPS": "ps-id"}
            phase_permission_set(ctx, conn)
        assert mock_bm.call_args.kwargs.get("return_id_map") is True
        assert mock_bm.call_args.kwargs["entity_type"] == "PermissionSet"
        mock_edges.assert_called_once()
        edge_kwargs = mock_edges.call_args.kwargs
        assert edge_kwargs["source_entity_type"] == "PermissionSet"
        assert edge_kwargs["entity_id_map"] == {"MyPS": "ps-id"}

    def test_phase_permission_set_empty_after_filter(self) -> None:
        """Org returning only Type='Profile' rows → after filtering,
        nothing to materialize → no materialize call, all-zero
        result."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_permission_sets.return_value = (
            self._ok_fetch_result(
                permission_sets=[
                    self._profile_synth_ps(ps_id="0PS001",
                                            name="X00e0001"),
                    self._profile_synth_ps(ps_id="0PS002",
                                            name="X00e0002"),
                ],
            )
        )
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases.batched_materialize",
        ) as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ) as mock_edges:
            result = phase_permission_set(ctx, conn)
        mock_bm.assert_not_called()
        mock_edges.assert_not_called()
        assert result.entity_type == "PermissionSet"
        assert result.entities_inserted == 0
        assert result.succeeded is True

    def test_phase_permission_set_does_not_filter_by_synced_objects(
        self,
    ) -> None:
        """PermissionSet is org-level. §18 parent-filter pattern
        does NOT apply. Edge target resolution skips unsyncable
        Objects/Fields via the §19 skip-counter instead."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_permission_sets.return_value = (
            self._ok_fetch_result(
                permission_sets=[self._regular_ps()],
            )
        )
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases._synced_object_api_names",
        ) as mock_filter, \
             patch("primeqa.sync.phases.batched_materialize") as mock_bm, \
             patch("primeqa.sync.phases.materialize_edges_for_entities"):
            mock_bm.return_value = {}
            phase_permission_set(ctx, conn)
        mock_filter.assert_not_called()

    def test_phase_permission_set_pre_sorts_lists_for_determinism(
        self,
    ) -> None:
        """ObjectPermissions / FieldPermissions are pre-sorted by
        PS-shape identity keys (SobjectType / Field) before
        normalize. Substrate-1's normalize-time sort uses
        Profile-shape keys (`field` lowercase) which resolve to
        "" against PS data → no-op sort. Pre-sorting here gives
        the lists deterministic order so two consecutive syncs
        produce the same hash."""
        ctx = _stub_ctx_with_mock_sf()
        # Pass child permissions in REVERSE order; verify they
        # land in alphabetical order on the PS payload.
        ctx.sf_client.fetch_permission_sets.return_value = (
            self._ok_fetch_result(
                permission_sets=[self._regular_ps()],
                object_permissions=[
                    {"ParentId": "0PS001", "SobjectType": "Zebra"},
                    {"ParentId": "0PS001", "SobjectType": "Account"},
                    {"ParentId": "0PS001", "SobjectType": "Mongoose"},
                ],
                field_permissions=[
                    {"ParentId": "0PS001", "Field": "Zebra.Stripe"},
                    {"ParentId": "0PS001", "Field": "Account.Name"},
                ],
            )
        )
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases.batched_materialize",
        ) as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ):
            mock_bm.return_value = {}
            phase_permission_set(ctx, conn)
        ps = mock_bm.call_args.kwargs["raw_payloads"][0]
        # Sorted alphabetically by SobjectType
        op_order = [op["SobjectType"] for op in ps["objectPermissions"]]
        assert op_order == ["Account", "Mongoose", "Zebra"]
        # Sorted alphabetically by Field
        fp_order = [fp["Field"] for fp in ps["fieldPermissions"]]
        assert fp_order == ["Account.Name", "Zebra.Stripe"]


# ----------------------------------------------------------------------
# phase_user — tenth real phase. Two-pass materialization for
# HasPermissionSetProperties.assigned_by_user_entity_id forward
# reference resolution.
# ----------------------------------------------------------------------


from primeqa.sync.phases import phase_user


def _make_user_conn(profile_map=None, ps_map=None) -> MagicMock:
    """Build a MagicMock conn whose execute().fetchall() returns
    Profile and PermissionSet entity rows that phase_user reads to
    build its Id → external_id maps.

    phase_user runs TWO `SELECT attributes->>'Id', sf_api_name FROM
    entities WHERE entity_type=...` queries — one for Profile, one
    for PermissionSet. The side_effect dispatches on the WHERE
    clause's entity_type literal."""
    profile_map = profile_map or {}
    ps_map = ps_map or {}

    def _make_row(sf_id, ext_id):
        m = MagicMock()
        m.sf_id = sf_id
        m.sf_api_name = ext_id
        return m

    profile_rows = [_make_row(i, e) for i, e in profile_map.items()]
    ps_rows = [_make_row(i, e) for i, e in ps_map.items()]

    def _side_effect(stmt, params=None):
        sql = str(stmt)
        r = MagicMock()
        if "entity_type = 'Profile'" in sql:
            r.fetchall.return_value = profile_rows
        elif "entity_type = 'PermissionSet'" in sql:
            r.fetchall.return_value = ps_rows
        else:
            r.fetchall.return_value = []
        return r

    conn = MagicMock()
    conn.execute.side_effect = _side_effect
    return conn


class TestPhaseUser:
    def _ok_fetch_result(
        self,
        users=None,
        permission_set_assignments=None,
    ):
        """Build the 2-key fetch_users-shaped dict (User cycle).
        Profile + PermissionSet Id → external_id maps are built
        by phase_user from the entities table at sync time, not
        from fetch_users."""
        return {
            "users": users or [],
            "permission_set_assignments":
                permission_set_assignments or [],
        }

    def _user(
        self, user_id="005U001", username="alice@example.com",
        profile_id="00eA", **overrides,
    ):
        base = {
            "Id": user_id,
            "Username": username,
            "Name": "Alice Test",
            "Email": username,
            "IsActive": True,
            "UserType": "Standard",
            "ProfileId": profile_id,
        }
        base.update(overrides)
        return base

    def test_phase_user_calls_fetch_users(self) -> None:
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_users.return_value = (
            self._ok_fetch_result()
        )
        conn = MagicMock()
        with patch("primeqa.sync.phases.batched_materialize"):
            phase_user(ctx, conn)
        ctx.sf_client.fetch_users.assert_called_once_with()

    def test_phase_user_empty_users_returns_zero_result(self) -> None:
        """Org with no users → no materialize call, all-zero
        PhaseResult."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_users.return_value = (
            self._ok_fetch_result()
        )
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases.batched_materialize",
        ) as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ) as mock_edges:
            result = phase_user(ctx, conn)
        mock_bm.assert_not_called()
        mock_edges.assert_not_called()
        assert result.entities_inserted == 0
        assert result.succeeded is True

    def test_phase_user_decorates_profile_name_from_entities_table(
        self,
    ) -> None:
        """Each User payload gets `_profile_name` injected via the
        Profile Id → external_id map that phase_user builds from
        the entities table (Profile entities materialized by an
        earlier phase per ENTITY_ORDER). Reading from entities
        sidesteps the SOQL Tooling-vs-Data Name asymmetry on
        Profile."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_users.return_value = (
            self._ok_fetch_result(
                users=[
                    self._user(profile_id="00eA"),
                    self._user(user_id="005U002",
                                username="bob@example.com",
                                profile_id="00eB"),
                ],
            )
        )
        conn = _make_user_conn(
            profile_map={
                # Note: external_id IS Tooling FullName (e.g.,
                # 'Admin' for 'System Administrator') because
                # that's what Profile entity sf_api_name carries.
                "00eA": "Admin",
                "00eB": "Standard",
            },
        )
        with patch(
            "primeqa.sync.phases.batched_materialize",
        ) as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ):
            mock_bm.return_value = {
                "alice@example.com": "u1",
                "bob@example.com": "u2",
            }
            phase_user(ctx, conn)
        payloads = mock_bm.call_args.kwargs["raw_payloads"]
        by_username = {p["Username"]: p for p in payloads}
        assert by_username["alice@example.com"]["_profile_name"] == (
            "Admin"
        )
        assert by_username["bob@example.com"]["_profile_name"] == (
            "Standard"
        )

    def test_phase_user_filters_users_with_unresolvable_profile(
        self,
    ) -> None:
        """User.ProfileId missing from synced Profile entities →
        the User is SKIPPED (silent skip + INFO log). Salesforce
        hides some system profiles (e.g., Automated Process for
        AutomatedProcess users); the affected Users can't be
        materialized because user_details.profile_entity_id is
        NOT NULL FK with no Profile entity to reference."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_users.return_value = (
            self._ok_fetch_result(
                users=[
                    self._user(user_id="005U001",
                                username="alice@example.com",
                                profile_id="00eA"),       # resolves
                    self._user(user_id="005U002",
                                username="autoproc",
                                profile_id="00eUNKNOWN"),  # unresolvable
                ],
            )
        )
        conn = _make_user_conn(profile_map={"00eA": "Admin"})
        with patch(
            "primeqa.sync.phases.batched_materialize",
        ) as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ):
            mock_bm.return_value = {"alice@example.com": "u1"}
            phase_user(ctx, conn)
        payloads = mock_bm.call_args.kwargs["raw_payloads"]
        usernames = [p["Username"] for p in payloads]
        # autoproc filtered; alice retained
        assert usernames == ["alice@example.com"]

    def test_phase_user_all_users_filtered_returns_zero_result(
        self,
    ) -> None:
        """If EVERY User has an unresolvable ProfileId (no Profile
        entities materialized at all, or all User ProfileIds
        misalign — defensive case), no materialize call, all-zero
        result."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_users.return_value = (
            self._ok_fetch_result(
                users=[self._user(profile_id="00eUNKNOWN")],
            )
        )
        conn = _make_user_conn(profile_map={})  # no Profiles
        with patch(
            "primeqa.sync.phases.batched_materialize",
        ) as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ) as mock_edges:
            result = phase_user(ctx, conn)
        mock_bm.assert_not_called()
        mock_edges.assert_not_called()
        assert result.succeeded is True
        assert result.entities_inserted == 0

    def test_phase_user_joins_psas_by_assignee_id(self) -> None:
        """Each User gets a `_ps_assignments` list, joined by
        AssigneeId. PSAs for other users don't leak into this
        user's list. PSA.PermissionSetId is resolved to PS
        external_id via the entities-table-derived map (the
        SOQL related-query approach declined HTTP 400 in v66.0)."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_users.return_value = (
            self._ok_fetch_result(
                users=[
                    self._user(user_id="005U001",
                                username="alice@example.com"),
                    self._user(user_id="005U002",
                                username="bob@example.com"),
                ],
                permission_set_assignments=[
                    {"Id": "0Pa1", "AssigneeId": "005U001",
                     "PermissionSetId": "0PSmkt",
                     "PermissionSetGroupId": None,
                     "SystemModstamp": "2026-01-01",
                     "ExpirationDate": None},
                    {"Id": "0Pa2", "AssigneeId": "005U002",
                     "PermissionSetId": "0PSsvc",
                     "PermissionSetGroupId": None,
                     "SystemModstamp": "2026-02-01",
                     "ExpirationDate": None},
                ],
            )
        )
        conn = _make_user_conn(
            profile_map={"00eA": "Admin"},
            ps_map={"0PSmkt": "MarketingPS", "0PSsvc": "ServicePS"},
        )
        with patch(
            "primeqa.sync.phases.batched_materialize",
        ) as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ):
            mock_bm.return_value = {
                "alice@example.com": "u1",
                "bob@example.com": "u2",
            }
            phase_user(ctx, conn)
        payloads = mock_bm.call_args.kwargs["raw_payloads"]
        by_username = {p["Username"]: p for p in payloads}
        alice_psas = by_username["alice@example.com"][
            "_ps_assignments"
        ]
        bob_psas = by_username["bob@example.com"][
            "_ps_assignments"
        ]
        assert len(alice_psas) == 1
        assert alice_psas[0]["permission_set_name"] == "MarketingPS"
        assert alice_psas[0]["assigned_at"] == "2026-01-01"
        assert len(bob_psas) == 1
        assert bob_psas[0]["permission_set_name"] == "ServicePS"

    def test_phase_user_drops_psas_with_unresolvable_permission_set(
        self,
    ) -> None:
        """PSA whose PermissionSetId is NOT in the
        entities-derived permission_set_id_to_external_id map →
        dropped silently (PermissionSet was filtered out,
        Type='Profile' shadow, or never synced; nothing to write
        a HAS_PERMISSION_SET edge for)."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_users.return_value = (
            self._ok_fetch_result(
                users=[self._user()],
                permission_set_assignments=[
                    {"Id": "0Pa1", "AssigneeId": "005U001",
                     "PermissionSetId": "0PSknown",
                     "PermissionSetGroupId": None,
                     "SystemModstamp": "2026-01-01",
                     "ExpirationDate": None},
                    {"Id": "0Pa2", "AssigneeId": "005U001",
                     "PermissionSetId": "0PSunknown",
                     "PermissionSetGroupId": None,
                     "SystemModstamp": "2026-01-01",
                     "ExpirationDate": None},
                ],
            )
        )
        conn = _make_user_conn(
            profile_map={"00eA": "Admin"},
            ps_map={"0PSknown": "MyPS"},  # 0PSunknown absent
        )
        with patch(
            "primeqa.sync.phases.batched_materialize",
        ) as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ):
            mock_bm.return_value = {"alice@example.com": "u1"}
            phase_user(ctx, conn)
        psas = mock_bm.call_args.kwargs["raw_payloads"][0][
            "_ps_assignments"
        ]
        # Only the resolvable PS remains
        assert len(psas) == 1
        assert psas[0]["permission_set_name"] == "MyPS"

    def test_phase_user_filters_psg_derived_psas_in_python(
        self,
    ) -> None:
        """PSG-derived PSAs (PermissionSetGroupId IS NOT NULL) are
        filtered OUT in Python. The SOQL `WHERE PermissionSetGroupId
        = NULL` syntax returns HTTP 400 in v66.0; we filter in
        Python instead. PSG memberships are already captured via
        INHERITS_PERMISSION_SET edges from the PermissionSet
        cycle."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_users.return_value = (
            self._ok_fetch_result(
                users=[self._user()],
                permission_set_assignments=[
                    {"Id": "0Pa1", "AssigneeId": "005U001",
                     "PermissionSetId": "0PS1",
                     "PermissionSetGroupId": None,           # direct
                     "SystemModstamp": "2026-01-01",
                     "ExpirationDate": None},
                    {"Id": "0Pa2", "AssigneeId": "005U001",
                     "PermissionSetId": "0PS2",
                     "PermissionSetGroupId": "0PGsome",      # PSG-derived
                     "SystemModstamp": "2026-01-01",
                     "ExpirationDate": None},
                ],
            )
        )
        conn = _make_user_conn(
            profile_map={"00eA": "Admin"},
            ps_map={"0PS1": "DirectPS", "0PS2": "PSGDerivedPS"},
        )
        with patch(
            "primeqa.sync.phases.batched_materialize",
        ) as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ):
            mock_bm.return_value = {"alice@example.com": "u1"}
            phase_user(ctx, conn)
        psas = mock_bm.call_args.kwargs["raw_payloads"][0][
            "_ps_assignments"
        ]
        # Only the direct PSA remains; PSG-derived is filtered
        names = [a["permission_set_name"] for a in psas]
        assert names == ["DirectPS"]

    def test_phase_user_two_pass_logic_no_op_when_created_by_id_missing(
        self,
    ) -> None:
        """V1 reality: CreatedById is restricted on PSA in v66.0,
        so the SOQL doesn't fetch it. The two-pass logic still
        runs (architectural placeholder) but always sees
        _created_by_user_id=None → no resolution → no
        assigned_by_user_entity_id on any assignment. Pydantic
        schema's Optional field handles the absence gracefully."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_users.return_value = (
            self._ok_fetch_result(
                users=[self._user()],
                permission_set_assignments=[
                    # No CreatedById key — matches the v66.0 SOQL
                    {"Id": "0Pa1", "AssigneeId": "005U001",
                     "PermissionSetId": "0PS1",
                     "PermissionSetGroupId": None,
                     "SystemModstamp": "2026-01-01",
                     "ExpirationDate": None},
                ],
            )
        )
        conn = _make_user_conn(
            profile_map={"00eA": "Admin"},
            ps_map={"0PS1": "MyPS"},
        )
        with patch(
            "primeqa.sync.phases.batched_materialize",
        ) as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ):
            mock_bm.return_value = {"alice@example.com": "u1"}
            phase_user(ctx, conn)
        assignment = mock_bm.call_args.kwargs[
            "raw_payloads"
        ][0]["_ps_assignments"][0]
        # No assigned_by_user_entity_id (CreatedById was absent
        # → pass 2 had nothing to resolve)
        assert "assigned_by_user_entity_id" not in assignment
        # Phase-internal marker popped
        assert "_created_by_user_id" not in assignment

    def test_phase_user_psas_sorted_for_determinism(self) -> None:
        """Per-User PSA list is sorted by PermissionSet name so
        the entity hash is stable across syncs regardless of
        SOQL-result ordering. Sort happens in the decoration
        step (after PS Id→Name resolution), not at normalize time."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_users.return_value = (
            self._ok_fetch_result(
                users=[self._user()],
                permission_set_assignments=[
                    {"Id": "0Pa1", "AssigneeId": "005U001",
                     "PermissionSetId": "0PSz",
                     "PermissionSetGroupId": None,
                     "SystemModstamp": "2026-01-01"},
                    {"Id": "0Pa2", "AssigneeId": "005U001",
                     "PermissionSetId": "0PSa",
                     "PermissionSetGroupId": None,
                     "SystemModstamp": "2026-01-01"},
                    {"Id": "0Pa3", "AssigneeId": "005U001",
                     "PermissionSetId": "0PSm",
                     "PermissionSetGroupId": None,
                     "SystemModstamp": "2026-01-01"},
                ],
            )
        )
        conn = _make_user_conn(
            profile_map={"00eA": "Admin"},
            ps_map={
                "0PSz": "Zebra",
                "0PSa": "Account",
                "0PSm": "Mongoose",
            },
        )
        with patch(
            "primeqa.sync.phases.batched_materialize",
        ) as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ):
            mock_bm.return_value = {"alice@example.com": "u1"}
            phase_user(ctx, conn)
        psas = mock_bm.call_args.kwargs["raw_payloads"][0][
            "_ps_assignments"
        ]
        names = [a["permission_set_name"] for a in psas]
        assert names == ["Account", "Mongoose", "Zebra"]

    def test_phase_user_calls_materialize_and_edges(self) -> None:
        """phase_user materializes Users with return_id_map=True
        (needed for two-pass assigner resolution), then writes
        edges via materialize_edges_for_entities."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_users.return_value = (
            self._ok_fetch_result(users=[self._user()])
        )
        conn = _make_user_conn(profile_map={"00eA": "Admin"})
        with patch(
            "primeqa.sync.phases.batched_materialize",
        ) as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ) as mock_edges, \
             patch(
                 "primeqa.sync.phases.normalize",
                 side_effect=lambda et, p: {**p, "_norm": True},
             ):
            mock_bm.return_value = {"alice@example.com": "u1"}
            phase_user(ctx, conn)
        # Materialize called with return_id_map=True (pass 1)
        assert mock_bm.call_args.kwargs.get("return_id_map") is True
        assert mock_bm.call_args.kwargs["entity_type"] == "User"
        # Edges call (pass 2 post-resolution)
        mock_edges.assert_called_once()
        edge_kwargs = mock_edges.call_args.kwargs
        assert edge_kwargs["source_entity_type"] == "User"
        assert edge_kwargs["entity_id_map"] == {
            "alice@example.com": "u1",
        }

    def test_phase_user_does_not_filter_by_synced_objects(self) -> None:
        """User is org-level; §18 parent-filter pattern does NOT
        apply. Edge target resolution skips unsyncable
        Profile/PermissionSet via the §19 observability counter."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_users.return_value = (
            self._ok_fetch_result(users=[self._user()])
        )
        conn = _make_user_conn(profile_map={"00eA": "Admin"})
        with patch(
            "primeqa.sync.phases._synced_object_api_names",
        ) as mock_filter, \
             patch("primeqa.sync.phases.batched_materialize") as mock_bm, \
             patch("primeqa.sync.phases.materialize_edges_for_entities"):
            mock_bm.return_value = {}
            phase_user(ctx, conn)
        mock_filter.assert_not_called()


# ----------------------------------------------------------------------
# phase_flow — the final entity phase (11/11). Coordinates
# fetch_flow_definitions (parent context) + fetch_flow_versions
# (version data); composes Flow entities keyed by DeveloperName
# with the active version's payload.
# ----------------------------------------------------------------------


from primeqa.sync.phases import phase_flow


class TestPhaseFlow:
    def _fd(self, fd_id="300A", developer_name="My_Flow",
            active_version_id="301A", latest_version_id="301A",
            manageable_state="unmanaged"):
        """Build a FlowDefinition record."""
        return {
            "Id": fd_id,
            "DeveloperName": developer_name,
            "ActiveVersionId": active_version_id,
            "LatestVersionId": latest_version_id,
            "ManageableState": manageable_state,
        }

    def _version(self, version_id="301A", definition_id="300A",
                 version_number=1, status="Active",
                 process_type="AutoLaunchedFlow", metadata=None):
        """Build a Flow version record."""
        return {
            "Id": version_id,
            "DefinitionId": definition_id,
            "VersionNumber": version_number,
            "Status": status,
            "ProcessType": process_type,
            "Metadata": metadata if metadata is not None else {},
        }

    def test_phase_flow_calls_both_fetchers(self) -> None:
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_flow_definitions.return_value = []
        ctx.sf_client.fetch_flow_versions.return_value = []
        conn = MagicMock()
        with patch("primeqa.sync.phases.batched_materialize"):
            phase_flow(ctx, conn)
        ctx.sf_client.fetch_flow_definitions.assert_called_once_with()
        # fetch_flow_versions is only called when there ARE
        # FlowDefinitions — here there are none, so it short-circuits.
        ctx.sf_client.fetch_flow_versions.assert_not_called()

    def test_phase_flow_empty_definitions_returns_zero(self) -> None:
        """Org with no FlowDefinitions → no materialize, all-zero
        result."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_flow_definitions.return_value = []
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases.batched_materialize",
        ) as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ) as mock_edges:
            result = phase_flow(ctx, conn)
        mock_bm.assert_not_called()
        mock_edges.assert_not_called()
        assert result.entity_type == "Flow"
        assert result.entities_inserted == 0
        assert result.succeeded is True

    def test_phase_flow_picks_active_version_by_active_version_id(
        self,
    ) -> None:
        """The chosen version is the one whose Id ==
        FlowDefinition.ActiveVersionId. _is_active marker True."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_flow_definitions.return_value = [
            self._fd(fd_id="300A", developer_name="My_Flow",
                     active_version_id="301A_v2"),
        ]
        ctx.sf_client.fetch_flow_versions.return_value = [
            self._version(version_id="301A_v1", definition_id="300A",
                          version_number=1, status="Obsolete"),
            self._version(version_id="301A_v2", definition_id="300A",
                          version_number=2, status="Active"),
        ]
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases.batched_materialize",
        ) as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ):
            mock_bm.return_value = {"My_Flow": "flow-1"}
            phase_flow(ctx, conn)
        payloads = mock_bm.call_args.kwargs["raw_payloads"]
        assert len(payloads) == 1
        p = payloads[0]
        # The v2 (active) version was chosen
        assert p["Id"] == "301A_v2"
        assert p["VersionNumber"] == 2
        # Decorated with FD's stable identity markers
        assert p["_developer_name"] == "My_Flow"
        assert p["_is_active"] is True
        assert p["_manageable_state"] == "unmanaged"

    def test_phase_flow_falls_back_to_latest_when_no_active(
        self,
    ) -> None:
        """FlowDefinition with no ActiveVersionId (deactivated
        flow) → fall back to LatestVersionId; _is_active=False."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_flow_definitions.return_value = [
            self._fd(fd_id="300B", developer_name="Deactivated_Flow",
                     active_version_id=None,
                     latest_version_id="301B_v3"),
        ]
        ctx.sf_client.fetch_flow_versions.return_value = [
            self._version(version_id="301B_v2", definition_id="300B",
                          version_number=2, status="Obsolete"),
            self._version(version_id="301B_v3", definition_id="300B",
                          version_number=3, status="Draft"),
        ]
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases.batched_materialize",
        ) as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ):
            mock_bm.return_value = {"Deactivated_Flow": "flow-2"}
            phase_flow(ctx, conn)
        p = mock_bm.call_args.kwargs["raw_payloads"][0]
        # Latest version chosen (v3)
        assert p["Id"] == "301B_v3"
        # Marked NOT active (no running version)
        assert p["_is_active"] is False

    def test_phase_flow_skips_definition_with_no_versions(
        self,
    ) -> None:
        """A FlowDefinition with zero fetched versions is skipped
        with a warning — no Flow entity materialized for it."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_flow_definitions.return_value = [
            self._fd(fd_id="300C", developer_name="Has_Version",
                     active_version_id="301C"),
            self._fd(fd_id="300D", developer_name="No_Versions",
                     active_version_id=None,
                     latest_version_id=None),
        ]
        ctx.sf_client.fetch_flow_versions.return_value = [
            self._version(version_id="301C", definition_id="300C"),
            # nothing for 300D
        ]
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases.batched_materialize",
        ) as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ):
            mock_bm.return_value = {"Has_Version": "flow-3"}
            phase_flow(ctx, conn)
        payloads = mock_bm.call_args.kwargs["raw_payloads"]
        names = [p["_developer_name"] for p in payloads]
        # Has_Version materialized; No_Versions skipped
        assert names == ["Has_Version"]

    def test_phase_flow_groups_versions_by_definition_id(self) -> None:
        """Versions are grouped by DefinitionId; a version for a
        different FD doesn't leak into another FD's lookup."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_flow_definitions.return_value = [
            self._fd(fd_id="300E", developer_name="Flow_E",
                     active_version_id="301E"),
            self._fd(fd_id="300F", developer_name="Flow_F",
                     active_version_id="301F"),
        ]
        ctx.sf_client.fetch_flow_versions.return_value = [
            self._version(version_id="301E", definition_id="300E",
                          version_number=5),
            self._version(version_id="301F", definition_id="300F",
                          version_number=9),
        ]
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases.batched_materialize",
        ) as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ):
            mock_bm.return_value = {"Flow_E": "e", "Flow_F": "f"}
            phase_flow(ctx, conn)
        payloads = mock_bm.call_args.kwargs["raw_payloads"]
        by_name = {p["_developer_name"]: p for p in payloads}
        assert by_name["Flow_E"]["VersionNumber"] == 5
        assert by_name["Flow_F"]["VersionNumber"] == 9

    def test_phase_flow_calls_materialize_and_edges(self) -> None:
        """phase_flow materializes with return_id_map=True then
        writes TRIGGERS_ON edges via materialize_edges_for_entities."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_flow_definitions.return_value = [
            self._fd(),
        ]
        ctx.sf_client.fetch_flow_versions.return_value = [
            self._version(),
        ]
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases.batched_materialize",
        ) as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ) as mock_edges, \
             patch(
                 "primeqa.sync.phases.normalize",
                 side_effect=lambda et, p: {**p, "_norm": True},
             ):
            mock_bm.return_value = {"My_Flow": "flow-id"}
            phase_flow(ctx, conn)
        assert mock_bm.call_args.kwargs.get("return_id_map") is True
        assert mock_bm.call_args.kwargs["entity_type"] == "Flow"
        mock_edges.assert_called_once()
        edge_kwargs = mock_edges.call_args.kwargs
        assert edge_kwargs["source_entity_type"] == "Flow"
        assert edge_kwargs["entity_id_map"] == {"My_Flow": "flow-id"}

    def test_phase_flow_does_not_filter_by_synced_objects(self) -> None:
        """Flow is org-level; §18 parent-filter pattern does NOT
        apply. TRIGGERS_ON target Object resolution skips
        unsyncable targets via the §19 observability counter."""
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_flow_definitions.return_value = [self._fd()]
        ctx.sf_client.fetch_flow_versions.return_value = [self._version()]
        conn = MagicMock()
        with patch(
            "primeqa.sync.phases._synced_object_api_names",
        ) as mock_filter, \
             patch("primeqa.sync.phases.batched_materialize") as mock_bm, \
             patch("primeqa.sync.phases.materialize_edges_for_entities"):
            mock_bm.return_value = {}
            phase_flow(ctx, conn)
        mock_filter.assert_not_called()
