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
                       "ValidationRule"}
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


# ----------------------------------------------------------------------
# phase_picklist_value — third real phase; first to derive children
# from already-fetched parent records
# ----------------------------------------------------------------------

from primeqa.sync.phases import phase_picklist_value


class TestPhasePicklistValue:
    def test_phase_picklist_value_calls_both_parent_fetchers(self) -> None:
        '''No fresh SF call exclusive to PV — values come nested
        inside GVS + SVS records. Phase re-fetches via the same
        methods PVS phase used.'''
        ctx = _stub_ctx_with_mock_sf()
        ctx.sf_client.fetch_global_value_sets.return_value = []
        ctx.sf_client.fetch_standard_value_sets.return_value = []
        conn = MagicMock()
        with patch('primeqa.sync.phases.batched_materialize'):
            phase_picklist_value(ctx, conn)
        ctx.sf_client.fetch_global_value_sets.assert_called_once_with()
        ctx.sf_client.fetch_standard_value_sets.assert_called_once_with(
            labels=None,
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
        with patch("primeqa.sync.phases.batched_materialize"):
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
        with patch("primeqa.sync.phases.batched_materialize") as mock_bm, \
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
        with patch("primeqa.sync.phases.batched_materialize") as mock_bm, \
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
        with patch("primeqa.sync.phases.batched_materialize") as mock_bm, \
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
        with patch("primeqa.sync.phases.batched_materialize") as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ) as mock_edges:
            result = phase_record_type(ctx, conn)
        mock_bm.assert_not_called()
        mock_edges.assert_not_called()
        assert result.entity_type == "RecordType"
        assert result.entities_inserted == 0
        assert result.succeeded is True


# ----------------------------------------------------------------------
# phase_layout — sixth real phase; first property-bearing edge writer
# ----------------------------------------------------------------------

from primeqa.sync.phases import phase_layout


class TestPhaseLayout:
    def _ctx_with_objects(self, object_rows):
        ctx = _stub_ctx_with_mock_sf()
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            type('R', (), {'id': r[0], 'sf_api_name': r[1]})()
            for r in object_rows
        ]
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
        with patch("primeqa.sync.phases.batched_materialize"):
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
        with patch("primeqa.sync.phases.batched_materialize") as mock_bm, \
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
        with patch("primeqa.sync.phases.batched_materialize") as mock_bm, \
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
        with patch("primeqa.sync.phases.batched_materialize") as mock_bm, \
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
        with patch("primeqa.sync.phases.batched_materialize") as mock_bm, \
             patch(
                 "primeqa.sync.phases.materialize_edges_for_entities",
             ) as mock_edges:
            result = phase_validation_rule(ctx, conn)
        mock_bm.assert_not_called()
        mock_edges.assert_not_called()
        assert result.entity_type == "ValidationRule"
        assert result.entities_inserted == 0
        assert result.succeeded is True
