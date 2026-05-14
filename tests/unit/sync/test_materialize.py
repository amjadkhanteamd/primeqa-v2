"""Tests for primeqa.sync.materialize — batched normalize→bucket→
write→enqueue cycle.

Strategy: patch the four batched DB helpers (_batch_read_existing,
_batch_insert_new_entities, _batch_close_superseded,
_batch_touch_existing, _batch_upsert_queue) plus the upstream
normalize / presentation / to_semantic_text / hash_normalized
helpers. Verify which batched helpers run, with which inputs,
and the resulting PhaseResult counter values. The SQL itself is
exercised by the live integration test.
"""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

from primeqa.sync.context import SyncContext
from primeqa.sync.materialize import (
    DEFAULT_CHUNK_SIZE,
    batched_materialize,
)
from primeqa.sync.result import PhaseResult


def _stub_ctx() -> SyncContext:
    return SyncContext(
        sf_client=None,
        engine=MagicMock(),
        sync_run_id="11111111-1111-1111-1111-111111111111",
        connected_org_id="22222222-2222-2222-2222-222222222222",
        tenant_schema="tenant_1",
        logical_version_seq=42,
    )


def _patch_path(name: str) -> str:
    return f"primeqa.sync.materialize.{name}"


def _patch_pipeline(
    *,
    normalize_return=None,
    hash_return="h",
    presentation_return=None,
    semantic_text_return="text",
    existing_return=None,
    insert_return=None,
    close_return=None,
    touch_return=None,
    upsert_return=None,
):
    """Helper to patch the full pipeline in one ContextManager.

    Returns a list of patch context managers to enter via ExitStack
    or nested with.
    """
    return [
        patch(_patch_path("normalize"),
              return_value=normalize_return or {"n": True}),
        patch(_patch_path("hash_normalized"),
              return_value=hash_return),
        patch(_patch_path("to_presentation"),
              return_value=presentation_return or {"label": "X", "name": "X"}),
        patch(_patch_path("to_semantic_text"),
              return_value=semantic_text_return),
        patch(_patch_path("_batch_read_existing"),
              return_value=existing_return or {}),
        patch(_patch_path("_batch_insert_new_entities"),
              return_value=insert_return or []),
        patch(_patch_path("_batch_close_superseded"),
              return_value=close_return),
        patch(_patch_path("_batch_touch_existing"),
              return_value=touch_return),
        patch(_patch_path("_batch_upsert_queue"),
              return_value=upsert_return),
    ]


class TestBatchedMaterializeEmptyInput:
    def test_batched_materialize_empty_input(self) -> None:
        """Empty raw_payloads → no batched helpers called, no
        counter changes."""
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        from contextlib import ExitStack
        with ExitStack() as stack:
            mocks = {
                name: stack.enter_context(p)
                for name, p in zip(
                    ("normalize", "hash", "pres", "text",
                     "read", "insert", "close", "touch", "upsert"),
                    _patch_pipeline(),
                )
            }
            batched_materialize(
                _stub_ctx(), conn, "Object", raw_payloads=[],
                result=result,
            )
        # No batched helpers were called
        assert mocks["read"].call_count == 0
        assert mocks["insert"].call_count == 0
        assert mocks["close"].call_count == 0
        assert mocks["touch"].call_count == 0
        assert mocks["upsert"].call_count == 0
        # Counters unchanged
        assert result.entities_inserted == 0
        assert result.entities_superseded == 0
        assert result.entities_unchanged == 0


class TestBatchedMaterializeAllNew:
    def test_batched_materialize_all_new_entities(self) -> None:
        """All entities are new (empty existing) → _batch_insert
        called, _batch_upsert_queue called, _batch_close +
        _batch_touch NOT called."""
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        from contextlib import ExitStack
        raw_payloads = [{"name": f"Entity_{i}"} for i in range(3)]
        new_ids = ["id-1", "id-2", "id-3"]

        with ExitStack() as stack:
            for p in _patch_pipeline(
                existing_return={},  # nothing exists
                insert_return=new_ids,
            ):
                stack.enter_context(p)
            mock_insert = stack.enter_context(
                patch(_patch_path("_batch_insert_new_entities"),
                      return_value=new_ids)
            )
            mock_close = stack.enter_context(
                patch(_patch_path("_batch_close_superseded"))
            )
            mock_touch = stack.enter_context(
                patch(_patch_path("_batch_touch_existing"))
            )
            mock_upsert = stack.enter_context(
                patch(_patch_path("_batch_upsert_queue"))
            )

            batched_materialize(
                _stub_ctx(), conn, "Object",
                raw_payloads=raw_payloads, result=result,
            )

        # INSERT called once with all 3 entities
        assert mock_insert.call_count == 1
        # UPSERT to queue called once with the 3 new IDs
        mock_upsert.assert_called_once()
        upsert_call_args = mock_upsert.call_args.args
        assert sorted(upsert_call_args[2]) == sorted(new_ids)
        # close + touch NOT called
        mock_close.assert_not_called()
        mock_touch.assert_not_called()
        # Counters
        assert result.entities_inserted == 3
        assert result.entities_superseded == 0
        assert result.entities_unchanged == 0


class TestBatchedMaterializeAllUnchanged:
    def test_batched_materialize_all_unchanged(self) -> None:
        """All entities match existing hash → _batch_touch called,
        no _batch_insert, no _batch_upsert."""
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        from contextlib import ExitStack

        # 3 incoming, all matching the same hash
        raw_payloads = [{"name": f"Entity_{i}"} for i in range(3)]
        existing = {
            "Entity_0": {"id": "old-0", "last_seed_hash": "h"},
            "Entity_1": {"id": "old-1", "last_seed_hash": "h"},
            "Entity_2": {"id": "old-2", "last_seed_hash": "h"},
        }

        with ExitStack() as stack:
            for p in _patch_pipeline(
                hash_return="h",  # matches existing
                existing_return=existing,
            ):
                stack.enter_context(p)
            mock_insert = stack.enter_context(
                patch(_patch_path("_batch_insert_new_entities"))
            )
            mock_close = stack.enter_context(
                patch(_patch_path("_batch_close_superseded"))
            )
            mock_touch = stack.enter_context(
                patch(_patch_path("_batch_touch_existing"))
            )
            mock_upsert = stack.enter_context(
                patch(_patch_path("_batch_upsert_queue"))
            )

            # Override _extract_external_id since "name" is used in
            # the Object branch — match against raw["name"]
            batched_materialize(
                _stub_ctx(), conn, "Object",
                raw_payloads=raw_payloads, result=result,
            )

        # Touch called once with all 3 existing IDs
        mock_touch.assert_called_once()
        touch_ids = mock_touch.call_args.args[1]
        assert sorted(touch_ids) == sorted(["old-0", "old-1", "old-2"])
        # No insert, no upsert
        mock_insert.assert_not_called()
        mock_close.assert_not_called()
        mock_upsert.assert_not_called()
        # Counters
        assert result.entities_inserted == 0
        assert result.entities_superseded == 0
        assert result.entities_unchanged == 3


class TestBatchedMaterializeMixedBuckets:
    def test_batched_materialize_mixed_buckets(self) -> None:
        """3 entities split 1/1/1 across new/changed/unchanged → all
        three batched writes invoked."""
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        from contextlib import ExitStack

        raw_payloads = [
            {"name": "NewObj"},      # new
            {"name": "ChangedObj"},  # changed
            {"name": "SameObj"},     # unchanged
        ]
        # Hash routing: normalize returns the raw payload (verified
        # below), then hash_normalized returns a per-entity hash.
        # We'll patch hash to return name-derived hashes.
        def hash_side_effect(normalized):
            name = normalized.get("name", "")
            return f"hash_{name}"

        existing = {
            "ChangedObj": {"id": "old-changed",
                          "last_seed_hash": "hash_DIFFERENT"},
            "SameObj": {"id": "old-same",
                       "last_seed_hash": "hash_SameObj"},
            # NewObj absent
        }

        with ExitStack() as stack:
            stack.enter_context(patch(
                _patch_path("normalize"),
                side_effect=lambda et, raw: raw,
            ))
            stack.enter_context(patch(
                _patch_path("hash_normalized"),
                side_effect=hash_side_effect,
            ))
            stack.enter_context(patch(
                _patch_path("to_presentation"),
                side_effect=lambda et, n: {"label": n["name"], "name": n["name"]},
            ))
            stack.enter_context(patch(
                _patch_path("to_semantic_text"),
                return_value="text",
            ))
            stack.enter_context(patch(
                _patch_path("_batch_read_existing"),
                return_value=existing,
            ))
            mock_insert = stack.enter_context(patch(
                _patch_path("_batch_insert_new_entities"),
                side_effect=[["new-1"], ["new-2"]],
            ))
            mock_close = stack.enter_context(patch(
                _patch_path("_batch_close_superseded"),
            ))
            mock_touch = stack.enter_context(patch(
                _patch_path("_batch_touch_existing"),
            ))
            mock_upsert = stack.enter_context(patch(
                _patch_path("_batch_upsert_queue"),
            ))

            batched_materialize(
                _stub_ctx(), conn, "Object",
                raw_payloads=raw_payloads, result=result,
            )

        # _batch_insert_new_entities called TWICE: once for new
        # bucket, once for changed bucket (SCD Type 2 new versions)
        assert mock_insert.call_count == 2
        # close called once for the changed bucket
        mock_close.assert_called_once()
        close_ids = mock_close.call_args.args[2]
        assert close_ids == ["old-changed"]
        # touch called once with the unchanged id
        mock_touch.assert_called_once()
        touch_ids = mock_touch.call_args.args[1]
        assert touch_ids == ["old-same"]
        # upsert called once with new + changed_new ids
        mock_upsert.assert_called_once()
        upsert_ids = mock_upsert.call_args.args[2]
        assert sorted(upsert_ids) == sorted(["new-1", "new-2"])
        # Counters
        assert result.entities_inserted == 1
        assert result.entities_superseded == 1
        assert result.entities_unchanged == 1


class TestBatchedMaterializeChunking:
    def test_batched_materialize_chunks_respect_chunk_size(self) -> None:
        """1100 entities at chunk_size=500 → 3 chunks
        (500 + 500 + 100)."""
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        from contextlib import ExitStack

        raw_payloads = [{"name": f"O_{i}"} for i in range(1100)]

        with ExitStack() as stack:
            stack.enter_context(patch(
                _patch_path("normalize"), side_effect=lambda et, raw: raw,
            ))
            stack.enter_context(patch(
                _patch_path("hash_normalized"), return_value="h",
            ))
            stack.enter_context(patch(
                _patch_path("to_presentation"),
                side_effect=lambda et, n: {"label": n.get("name"), "name": n.get("name")},
            ))
            stack.enter_context(patch(
                _patch_path("to_semantic_text"), return_value="text",
            ))
            mock_read = stack.enter_context(patch(
                _patch_path("_batch_read_existing"), return_value={},
            ))
            stack.enter_context(patch(
                _patch_path("_batch_insert_new_entities"),
                # Each chunk returns its own list of ids
                side_effect=[
                    [f"id-{i}" for i in range(500)],
                    [f"id-{i}" for i in range(500, 1000)],
                    [f"id-{i}" for i in range(1000, 1100)],
                ],
            ))
            stack.enter_context(patch(_patch_path("_batch_close_superseded")))
            stack.enter_context(patch(_patch_path("_batch_touch_existing")))
            stack.enter_context(patch(_patch_path("_batch_upsert_queue")))

            batched_materialize(
                _stub_ctx(), conn, "Object",
                raw_payloads=raw_payloads, result=result,
                chunk_size=500,
            )

        # _batch_read_existing called once per chunk → 3 times
        assert mock_read.call_count == 3
        # Chunk sizes: 500, 500, 100
        chunk_sizes = [
            len(call.args[3])  # external_ids is 4th positional arg
            for call in mock_read.call_args_list
        ]
        assert chunk_sizes == [500, 500, 100]

    def test_batched_materialize_counters_accumulate_across_chunks(
        self,
    ) -> None:
        """Counters accumulate across chunks."""
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        from contextlib import ExitStack

        raw_payloads = [{"name": f"O_{i}"} for i in range(1100)]

        with ExitStack() as stack:
            stack.enter_context(patch(
                _patch_path("normalize"), side_effect=lambda et, raw: raw,
            ))
            stack.enter_context(patch(
                _patch_path("hash_normalized"), return_value="h",
            ))
            stack.enter_context(patch(
                _patch_path("to_presentation"),
                side_effect=lambda et, n: {"label": n.get("name"), "name": n.get("name")},
            ))
            stack.enter_context(patch(
                _patch_path("to_semantic_text"), return_value="text",
            ))
            stack.enter_context(patch(
                _patch_path("_batch_read_existing"), return_value={},
            ))
            stack.enter_context(patch(
                _patch_path("_batch_insert_new_entities"),
                side_effect=[
                    [f"id-{i}" for i in range(500)],
                    [f"id-{i}" for i in range(500, 1000)],
                    [f"id-{i}" for i in range(1000, 1100)],
                ],
            ))
            stack.enter_context(patch(_patch_path("_batch_close_superseded")))
            stack.enter_context(patch(_patch_path("_batch_touch_existing")))
            stack.enter_context(patch(_patch_path("_batch_upsert_queue")))

            batched_materialize(
                _stub_ctx(), conn, "Object",
                raw_payloads=raw_payloads, result=result,
                chunk_size=500,
            )

        assert result.entities_inserted == 1100  # 500 + 500 + 100
        assert result.entities_superseded == 0
        assert result.entities_unchanged == 0
        assert result.embeddings_queued == 1100
        assert result.summaries_queued == 1100


class TestBatchedMaterializeSupersession:
    def test_batched_materialize_supersedes_changed_entities(self) -> None:
        """Changed entities go through SCD Type 2: close-out + new
        INSERT. The new INSERT's returned ids drive the queue UPSERT."""
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        from contextlib import ExitStack

        raw_payloads = [{"name": "Account"}]
        existing = {
            "Account": {"id": "old-id", "last_seed_hash": "OLDHASH"},
        }

        with ExitStack() as stack:
            stack.enter_context(patch(
                _patch_path("normalize"), side_effect=lambda et, raw: raw,
            ))
            stack.enter_context(patch(
                _patch_path("hash_normalized"), return_value="NEWHASH",
            ))
            stack.enter_context(patch(
                _patch_path("to_presentation"),
                side_effect=lambda et, n: {"label": "Account", "name": "Account"},
            ))
            stack.enter_context(patch(
                _patch_path("to_semantic_text"), return_value="text",
            ))
            stack.enter_context(patch(
                _patch_path("_batch_read_existing"), return_value=existing,
            ))
            mock_insert = stack.enter_context(patch(
                _patch_path("_batch_insert_new_entities"),
                return_value=["new-id"],
            ))
            mock_close = stack.enter_context(patch(
                _patch_path("_batch_close_superseded"),
            ))
            mock_upsert = stack.enter_context(patch(
                _patch_path("_batch_upsert_queue"),
            ))
            stack.enter_context(patch(_patch_path("_batch_touch_existing")))

            batched_materialize(
                _stub_ctx(), conn, "Object",
                raw_payloads=raw_payloads, result=result,
            )

        # Close called with prior id; insert called for the new version
        mock_close.assert_called_once()
        close_ids = mock_close.call_args.args[2]
        assert close_ids == ["old-id"]
        # INSERT called exactly once (only the changed bucket; no
        # new bucket members)
        assert mock_insert.call_count == 1
        # Queue UPSERT against NEW entity id, NOT the old prior id
        mock_upsert.assert_called_once()
        upsert_ids = mock_upsert.call_args.args[2]
        assert upsert_ids == ["new-id"]
        # Counter
        assert result.entities_superseded == 1


class TestBatchedMaterializeQueueReEnqueue:
    def test_batched_materialize_re_enqueues_changed_via_upsert(
        self,
    ) -> None:
        """Changed entities trigger queue UPSERT — the ON CONFLICT
        clause in the SQL handles the case where a prior queue row
        exists (resets to pending). Here we verify the UPSERT call
        happens for changed entities; the SQL's ON CONFLICT
        semantics are exercised by the live test."""
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        from contextlib import ExitStack

        raw_payloads = [{"name": "Account"}, {"name": "Contact"}]
        existing = {
            "Account": {"id": "id-A", "last_seed_hash": "OLD"},
            "Contact": {"id": "id-C", "last_seed_hash": "OLD"},
        }

        with ExitStack() as stack:
            stack.enter_context(patch(
                _patch_path("normalize"), side_effect=lambda et, raw: raw,
            ))
            stack.enter_context(patch(
                _patch_path("hash_normalized"), return_value="NEW",
            ))
            stack.enter_context(patch(
                _patch_path("to_presentation"),
                side_effect=lambda et, n: {"label": n["name"], "name": n["name"]},
            ))
            stack.enter_context(patch(
                _patch_path("to_semantic_text"), return_value="text",
            ))
            stack.enter_context(patch(
                _patch_path("_batch_read_existing"), return_value=existing,
            ))
            stack.enter_context(patch(
                _patch_path("_batch_insert_new_entities"),
                return_value=["new-A", "new-C"],
            ))
            stack.enter_context(patch(_patch_path("_batch_close_superseded")))
            stack.enter_context(patch(_patch_path("_batch_touch_existing")))
            mock_upsert = stack.enter_context(patch(
                _patch_path("_batch_upsert_queue"),
            ))

            batched_materialize(
                _stub_ctx(), conn, "Object",
                raw_payloads=raw_payloads, result=result,
            )

        # UPSERT called once with both new entity_ids (NOT prior ids)
        mock_upsert.assert_called_once()
        upsert_ids = mock_upsert.call_args.args[2]
        assert sorted(upsert_ids) == ["new-A", "new-C"]
        # embeddings + summaries each queued for both
        assert result.embeddings_queued == 2
        assert result.summaries_queued == 2


# ----------------------------------------------------------------------
# _extract_external_id — per-entity-type external_id routing
# ----------------------------------------------------------------------

import pytest

from primeqa.sync.materialize import _extract_external_id


class TestExtractExternalId:
    """Verifies the per-entity-type router. Each new phase cycle
    adds a branch here; the test parametrizes the known mappings."""

    @pytest.mark.parametrize("entity_type,raw,expected", [
        ("Object", {"name": "Account"}, "Account"),
        ("Object", {"name": "MyCustom__c"}, "MyCustom__c"),
        # PicklistValueSet defaults to GVS shape when no _source
        # marker (backward compat with pre-SVS-cycle fixtures).
        ("PicklistValueSet",
         {"FullName": "RegionVS", "MasterLabel": "Region"},
         "RegionVS"),
        ("PicklistValueSet",
         {"FullName": "MyNamespace__MyVS"},
         "MyNamespace__MyVS"),
        # _source='GlobalValueSet' explicit — same as default.
        ("PicklistValueSet",
         {"_source": "GlobalValueSet", "FullName": "RegionVS"},
         "RegionVS"),
        # _source='StandardValueSet' — namespaced with SVS: prefix
        # per corrections-log §8 addendum (collision avoidance).
        ("PicklistValueSet",
         {"_source": "StandardValueSet", "FullName": "AccountSource"},
         "SVS:AccountSource"),
        ("PicklistValueSet",
         {"_source": "StandardValueSet",
          "FullName": "Industry"},  # collision-prone catalog name
         "SVS:Industry"),
        # PicklistValue: composite external_id, parent prefix from
        # _parent_external_id marker (which already inherits SVS:
        # prefix for StandardValueSet sources).
        ("PicklistValue",
         {"_parent_external_id": "SVS:AccountType",
          "valueName": "Analyst"},
         "SVS:AccountType.Analyst"),
        ("PicklistValue",
         {"_parent_external_id": "MyCustomGVS",
          "valueName": "Banking"},
         "MyCustomGVS.Banking"),
        # RecordType: external_id = Salesforce-provided FullName.
        # Same canonical-identifier pattern as GVS — no composite
        # construction needed since the Tooling fetcher hands us
        # the identifier directly.
        ("RecordType",
         {"FullName": "Account.PartnerAccount",
          "developerName": "PartnerAccount"},
         "Account.PartnerAccount"),
        ("RecordType",
         {"FullName": "MyNS__License__c.Trial"},
         "MyNS__License__c.Trial"),
        # Layout: composite from phase-injected _layout_full_name
        # marker (Object-Name with HYPHEN separator per substrate-1
        # Metadata API convention). Phase function sets this after
        # Tooling Name resolution.
        ("Layout",
         {"_layout_full_name": "Account-Account Layout"},
         "Account-Account Layout"),
        ("Layout",
         {"_layout_full_name": "Contact-Contact (Customer)"},
         "Contact-Contact (Customer)"),
        # ValidationRule: external_id = Salesforce-provided FullName
        # (post-P5 fetcher enhancement). Same canonical-identifier
        # pattern as RecordType — Tooling Phase 2 hands us the
        # identifier; no composite construction needed.
        ("ValidationRule",
         {"FullName": "Account.AmountPositive",
          "ValidationName": "AmountPositive"},
         "Account.AmountPositive"),
        ("ValidationRule",
         {"FullName": "MyNS__Object.RequiredField"},
         "MyNS__Object.RequiredField"),
        # Profile: external_id = Tooling FullName (which equals the
        # Profile name itself — org-level entity, no composition).
        ("Profile",
         {"FullName": "System Administrator", "Name": "System Administrator"},
         "System Administrator"),
        ("Profile",
         {"FullName": "Read Only"},
         "Read Only"),
        # PermissionSet: external_id = Tooling Name (FIELDS(STANDARD)
        # doesn't return FullName for PermissionSet; Name is the
        # API-name identifier and is unique per org).
        ("PermissionSet",
         {"Id": "0PS000000000001", "Name": "MyCustomPS",
          "Type": "Regular"},
         "MyCustomPS"),
        ("PermissionSet",
         {"Id": "0PS000000000002", "Name": "ScaleCenterUsers",
          "Type": "Group"},
         "ScaleCenterUsers"),
        # User: external_id = Username (unique per org;
        # human-readable). Same idiom as PermissionSet (Name) and
        # Profile (FullName=Name) — single-key identifier from
        # the SOQL response.
        ("User",
         {"Id": "005F900000000001",
          "Username": "alice@example.com",
          "UserType": "Standard"},
         "alice@example.com"),
        ("User",
         {"Id": "005F900000000002",
          "Username": "autoproc@00dip0000008mzpmai",
          "UserType": "AutomatedProcess"},
         "autoproc@00dip0000008mzpmai"),
        # Flow: external_id = the parent FlowDefinition's
        # DeveloperName, injected by phase_flow as the
        # _developer_name marker (per §20: one Flow entity per
        # stable DeveloperName, versioning via bitemporal
        # supersession — NOT per-version FullName).
        ("Flow",
         {"Id": "301F90000000abc", "DefinitionId": "300F900000xyz",
          "VersionNumber": 3, "_developer_name": "My_Record_Flow"},
         "My_Record_Flow"),
        ("Flow",
         {"Id": "301F90000000def", "VersionNumber": 1,
          "_developer_name": "MHolt__Org_Expiration_Notification"},
         "MHolt__Org_Expiration_Notification"),
    ])
    def test_extract_external_id_known_types(
        self, entity_type: str, raw: dict, expected: str,
    ) -> None:
        assert _extract_external_id(entity_type, raw) == expected

    def test_extract_external_id_profile_raises_when_fullname_missing(
        self,
    ) -> None:
        """Profile without FullName → ValueError. Tooling Phase 2 is
        the canonical source; if FullName is missing, deeper failure
        upstream."""
        with pytest.raises(ValueError) as excinfo:
            _extract_external_id(
                "Profile",
                {"Name": "System Administrator"},
            )
        assert "FullName" in str(excinfo.value)

    def test_extract_external_id_permission_set_raises_when_name_missing(
        self,
    ) -> None:
        """PermissionSet without Name → ValueError. FIELDS(STANDARD)
        always returns Name; missing indicates a deeper failure
        upstream (e.g., malformed response)."""
        with pytest.raises(ValueError) as excinfo:
            _extract_external_id(
                "PermissionSet",
                {"Id": "0PS000000000001", "Type": "Regular"},
            )
        assert "Name" in str(excinfo.value)

    def test_extract_external_id_user_raises_when_username_missing(
        self,
    ) -> None:
        """User without Username → ValueError. Data API SOQL always
        returns Username for User; missing means malformed upstream."""
        with pytest.raises(ValueError) as excinfo:
            _extract_external_id(
                "User",
                {"Id": "005F900000000001", "UserType": "Standard"},
            )
        assert "Username" in str(excinfo.value)

    def test_extract_external_id_flow_raises_when_marker_missing(
        self,
    ) -> None:
        """Flow without the _developer_name marker → ValueError.
        phase_flow must decorate each flow-version payload with
        _developer_name from the parent FlowDefinition before
        materialize; a missing marker means a phase-function bug."""
        with pytest.raises(ValueError) as excinfo:
            _extract_external_id(
                "Flow",
                {"Id": "301F90000000abc", "VersionNumber": 3},
            )
        assert "_developer_name" in str(excinfo.value)

    def test_extract_external_id_layout_raises_when_marker_missing(
        self,
    ) -> None:
        """Layout without _layout_full_name → ValueError. REST
        describe/layouts doesn't expose names; the phase function
        must inject the marker after Tooling resolution."""
        with pytest.raises(ValueError) as excinfo:
            _extract_external_id("Layout", {"id": "00h..."})
        assert "_layout_full_name" in str(excinfo.value)

    def test_extract_external_id_validation_rule_raises_when_full_name_missing(
        self,
    ) -> None:
        """ValidationRule without FullName → ValueError. Post-P5
        substrate-1 fetcher enhancement, FullName is always present
        in Phase 2 output; missing indicates a deeper failure."""
        with pytest.raises(ValueError) as excinfo:
            _extract_external_id(
                "ValidationRule",
                {"ValidationName": "AmountPositive"},
            )
        assert "FullName" in str(excinfo.value)

    def test_extract_external_id_record_type_raises_when_fullname_missing(
        self,
    ) -> None:
        """RecordType without FullName → ValueError. Without the
        Tooling-provided identifier we have no way to compose one
        (developerName alone isn't unique across Objects)."""
        with pytest.raises(ValueError) as excinfo:
            _extract_external_id(
                "RecordType",
                {"developerName": "PartnerAccount"},
            )
        assert "FullName" in str(excinfo.value)

    def test_extract_external_id_unknown_type_raises(self) -> None:
        with pytest.raises(KeyError) as excinfo:
            _extract_external_id("NotAnEntity", {"name": "X"})
        msg = str(excinfo.value)
        assert "NotAnEntity" in msg
        assert "_extract_external_id" in msg

    def test_extract_external_id_picklist_value_missing_parent_raises(
        self,
    ) -> None:
        """PicklistValue without _parent_external_id marker — fail
        loudly with a message naming both missing fields."""
        with pytest.raises(ValueError) as excinfo:
            _extract_external_id(
                "PicklistValue", {"valueName": "Analyst"},
            )
        msg = str(excinfo.value)
        assert "_parent_external_id" in msg
        assert "valueName" in msg


# ----------------------------------------------------------------------
# resolve_entity_id_by_external_id + make_parent_resolver
# ----------------------------------------------------------------------

from primeqa.sync.materialize import (
    make_parent_resolver,
    resolve_entity_id_by_external_id,
)


class _RowStub:
    def __init__(self, id_):
        self.id = id_


class TestResolveEntityIdByExternalId:
    def test_returns_id_when_found(self) -> None:
        ctx = _stub_ctx()
        conn = MagicMock()
        conn.execute.return_value.first.return_value = _RowStub("uuid-abc")
        result = resolve_entity_id_by_external_id(
            conn, ctx, "PicklistValueSet", "SVS:AccountType",
        )
        assert result == "uuid-abc"
        # Verify the bound parameters were passed through
        call_kwargs = conn.execute.call_args[0][1]
        assert call_kwargs["entity_type"] == "PicklistValueSet"
        assert call_kwargs["external_id"] == "SVS:AccountType"
        assert call_kwargs["org_id"] == ctx.connected_org_id

    def test_returns_none_when_not_found(self) -> None:
        ctx = _stub_ctx()
        conn = MagicMock()
        conn.execute.return_value.first.return_value = None
        result = resolve_entity_id_by_external_id(
            conn, ctx, "PicklistValueSet", "SVS:NeverHeardOfIt",
        )
        assert result is None


class TestMakeParentResolver:
    def test_caches_repeated_lookups(self) -> None:
        """A resolver for one chunk should issue at most one query
        per (entity_type, external_id) pair, regardless of call
        count. Critical for the PicklistValue chunk shape (many
        children share each parent)."""
        ctx = _stub_ctx()
        conn = MagicMock()
        conn.execute.return_value.first.return_value = _RowStub("uuid-parent")

        resolver = make_parent_resolver(conn, ctx)
        # Same parent called 5 times → 1 query
        for _ in range(5):
            r = resolver(
                entity_type="PicklistValueSet",
                external_id="SVS:AccountType",
            )
            assert r == "uuid-parent"
        assert conn.execute.call_count == 1

    def test_distinct_lookups_issue_distinct_queries(self) -> None:
        """Distinct (entity_type, external_id) pairs each get their
        own query. Cache is a memoizer, not a single-result lock."""
        ctx = _stub_ctx()
        conn = MagicMock()
        # Different rows on subsequent calls
        conn.execute.return_value.first.side_effect = [
            _RowStub("uuid-a"), _RowStub("uuid-b"), _RowStub("uuid-c"),
        ]

        resolver = make_parent_resolver(conn, ctx)
        a = resolver(entity_type="PicklistValueSet", external_id="P1")
        b = resolver(entity_type="PicklistValueSet", external_id="P2")
        c = resolver(entity_type="PicklistValueSet", external_id="P3")
        assert (a, b, c) == ("uuid-a", "uuid-b", "uuid-c")
        assert conn.execute.call_count == 3

    def test_caches_none_results(self) -> None:
        """Resolver should also memoize None — re-querying an
        unresolvable external_id within a chunk is wasted IO."""
        ctx = _stub_ctx()
        conn = MagicMock()
        conn.execute.return_value.first.return_value = None

        resolver = make_parent_resolver(conn, ctx)
        for _ in range(3):
            r = resolver(
                entity_type="PicklistValueSet",
                external_id="SVS:Ghost",
            )
            assert r is None
        assert conn.execute.call_count == 1


# ----------------------------------------------------------------------
# Detail-table integration with _materialize_chunk
# ----------------------------------------------------------------------


class TestMaterializeChunkDetailRows:
    """Verify _materialize_chunk routes through detail_mappers when
    the entity_type has a registered mapper, and skips otherwise."""

    def test_calls_batch_insert_details_for_object(self) -> None:
        """Object has a registered mapper; new entities → detail
        rows inserted in the Object detail table."""
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        from contextlib import ExitStack
        with ExitStack() as stack:
            # Pipeline patches
            mocks = {
                name: stack.enter_context(p)
                for name, p in zip(
                    ("normalize", "hash", "pres", "text",
                     "read", "insert", "close", "touch", "upsert"),
                    _patch_pipeline(
                        normalize_return={"name": "Account", "custom": False,
                                          "queryable": True},
                        presentation_return={"name": "Account",
                                              "label": "Account"},
                        existing_return={},  # all-new
                        insert_return=["entity-uuid-1"],
                    ),
                )
            }
            # Patch the detail-table INSERT helper
            mock_details = stack.enter_context(
                patch(_patch_path("_batch_insert_details")),
            )
            batched_materialize(
                _stub_ctx(), conn, "Object",
                raw_payloads=[{"name": "Account"}],
                result=result,
            )
        mock_details.assert_called_once()
        # Verify call shape: conn, table_name, rows
        call_args = mock_details.call_args[0]
        assert call_args[1] == "object_details"
        rows = call_args[2]
        assert len(rows) == 1
        assert rows[0]["entity_id"] == "entity-uuid-1"

    def test_skips_detail_writes_for_picklist_value_set(self) -> None:
        """PicklistValueSet has no detail-table mapper — _batch_
        insert_details must NOT be called even when entities are
        inserted."""
        result = PhaseResult(entity_type="PicklistValueSet")
        conn = MagicMock()
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch(
                _patch_path("get_detail_mapper"),
                return_value=None,
            ))
            mocks = {
                name: stack.enter_context(p)
                for name, p in zip(
                    ("normalize", "hash", "pres", "text",
                     "read", "insert", "close", "touch", "upsert"),
                    _patch_pipeline(
                        existing_return={},
                        insert_return=["e1"],
                    ),
                )
            }
            mock_details = stack.enter_context(
                patch(_patch_path("_batch_insert_details")),
            )
            batched_materialize(
                _stub_ctx(), conn, "PicklistValueSet",
                raw_payloads=[{"FullName": "X"}],
                result=result,
            )
        mock_details.assert_not_called()

    def test_detail_writes_cover_changed_bucket_too(self) -> None:
        """Changed-bucket entities also need fresh detail rows for
        their new entity_ids. The mapper is called once per (new +
        changed) row total."""
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        # Two payloads: one new, one changed
        from contextlib import ExitStack

        def fake_normalize(et, raw):
            return {"name": raw["name"], "custom": False}

        def fake_hash(n):
            # Different hash than the "existing" one to force changed
            return f"h_{n['name']}"

        with ExitStack() as stack:
            stack.enter_context(patch(_patch_path("normalize"),
                                      side_effect=fake_normalize))
            stack.enter_context(patch(_patch_path("hash_normalized"),
                                      side_effect=fake_hash))
            stack.enter_context(patch(_patch_path("to_presentation"),
                                      return_value={"name": "X", "label": "X"}))
            stack.enter_context(patch(_patch_path("to_semantic_text"),
                                      return_value="text"))
            # Account exists with old hash; Contact is new
            stack.enter_context(patch(
                _patch_path("_batch_read_existing"),
                return_value={
                    "Account": {"id": "existing-uuid-1",
                                "last_seed_hash": "old_hash"},
                },
            ))
            stack.enter_context(patch(
                _patch_path("_batch_insert_new_entities"),
                # Called once for new (Contact), once for changed (Account)
                side_effect=[["new-uuid-contact"], ["new-uuid-account"]],
            ))
            stack.enter_context(patch(_patch_path("_batch_close_superseded")))
            stack.enter_context(patch(_patch_path("_batch_touch_existing")))
            stack.enter_context(patch(_patch_path("_batch_upsert_queue")))
            mock_details = stack.enter_context(
                patch(_patch_path("_batch_insert_details")),
            )
            batched_materialize(
                _stub_ctx(), conn, "Object",
                raw_payloads=[
                    {"name": "Account"},  # changed
                    {"name": "Contact"},  # new
                ],
                result=result,
            )
        mock_details.assert_called_once()
        rows = mock_details.call_args[0][2]
        # 1 new + 1 changed = 2 detail rows
        assert len(rows) == 2
        entity_ids = {r["entity_id"] for r in rows}
        assert entity_ids == {"new-uuid-contact", "new-uuid-account"}


from primeqa.sync.materialize import _batch_insert_details


class TestBatchInsertDetails:
    def test_empty_input_is_noop(self) -> None:
        """No detail rows → no SQL executed (guard against malformed
        INSERT with empty VALUES list)."""
        conn = MagicMock()
        _batch_insert_details(conn, "object_details", [])
        conn.execute.assert_not_called()

    def test_builds_multi_row_insert(self) -> None:
        """Multiple rows → one INSERT with VALUES (...), (...), ...
        and bound parameters keyed by col_index."""
        conn = MagicMock()
        rows = [
            {"entity_id": "u1", "is_custom": True},
            {"entity_id": "u2", "is_custom": False},
        ]
        _batch_insert_details(conn, "object_details", rows)
        conn.execute.assert_called_once()
        # First positional arg is the text() clause; second is params
        params = conn.execute.call_args[0][1]
        assert params["entity_id_0"] == "u1"
        assert params["is_custom_0"] is True
        assert params["entity_id_1"] == "u2"
        assert params["is_custom_1"] is False


# ----------------------------------------------------------------------
# Edge writes — property-less variant
# ----------------------------------------------------------------------

from primeqa.sync.materialize import (
    _batch_close_superseded_edges,
    _batch_insert_new_edges,
    _batch_read_existing_edges_for_sources,
    _lookup_edge_category,
    batched_materialize_edges,
    materialize_edges_for_entities,
)


class TestLookupEdgeCategory:
    def test_returns_structural_for_belongs_to(self) -> None:
        """BELONGS_TO is STRUCTURAL per substrate-1's TIER_1_EDGES."""
        assert _lookup_edge_category("BELONGS_TO") == "STRUCTURAL"

    def test_returns_structural_for_has_relationship_to(self) -> None:
        """HAS_RELATIONSHIP_TO is STRUCTURAL."""
        assert _lookup_edge_category("HAS_RELATIONSHIP_TO") == "STRUCTURAL"

    def test_raises_for_unknown_edge_type(self) -> None:
        """Lookup is strict — unknown edge_type raises KeyError from
        TIER_1_EDGES (not silently returns a default)."""
        import pytest as _pytest
        with _pytest.raises(KeyError):
            _lookup_edge_category("NOT_A_REAL_EDGE")


class TestBatchReadExistingEdgesForSources:
    def test_empty_source_list_returns_empty_set(self) -> None:
        """Empty input short-circuits — no DB call."""
        conn = MagicMock()
        result = _batch_read_existing_edges_for_sources(
            conn, "BELONGS_TO", [],
        )
        assert result == set()
        conn.execute.assert_not_called()

    def test_returns_set_of_source_target_tuples(self) -> None:
        """Rows from the SELECT are reshaped into a set of
        (source_id, target_id) tuples for set-difference bucketing."""
        conn = MagicMock()
        row1 = type("R", (), {"source_entity_id": "s1",
                              "target_entity_id": "t1"})()
        row2 = type("R", (), {"source_entity_id": "s2",
                              "target_entity_id": "t2"})()
        conn.execute.return_value.fetchall.return_value = [row1, row2]
        result = _batch_read_existing_edges_for_sources(
            conn, "BELONGS_TO", ["s1", "s2"],
        )
        assert result == {("s1", "t1"), ("s2", "t2")}


class TestBatchInsertNewEdges:
    def test_empty_pairs_is_noop(self) -> None:
        """Defensive empty-input check — no malformed VALUES list."""
        conn = MagicMock()
        ctx = _stub_ctx()
        _batch_insert_new_edges(conn, ctx, "BELONGS_TO", "STRUCTURAL", [])
        conn.execute.assert_not_called()

    def test_builds_multi_row_insert_with_uuid_casts(self) -> None:
        """Bound parameters keyed by source_{i}/target_{i}/props_{i}/
        hash_{i}. SQL uses CAST(... AS uuid) for source+target and
        CAST(... AS JSONB) for properties. Property-less rows pass
        {} for props and None for hash."""
        conn = MagicMock()
        ctx = _stub_ctx()
        rows = [
            ("s1", "t1", {}, None),
            ("s2", "t2", {}, None),
        ]
        _batch_insert_new_edges(
            conn, ctx, "BELONGS_TO", "STRUCTURAL", rows,
        )
        conn.execute.assert_called_once()
        sql_text = str(conn.execute.call_args[0][0])
        assert "CAST(:source_0 AS uuid)" in sql_text
        assert "CAST(:target_1 AS uuid)" in sql_text
        assert "CAST(:props_0 AS JSONB)" in sql_text
        params = conn.execute.call_args[0][1]
        assert params["source_0"] == "s1"
        assert params["target_1"] == "t2"
        assert params["edge_type"] == "BELONGS_TO"
        assert params["edge_category"] == "STRUCTURAL"
        assert params["valid_from_seq"] == ctx.logical_version_seq
        # Property-less: empty JSON object + NULL hash
        assert params["props_0"] == "{}"
        assert params["hash_0"] is None

    def test_builds_multi_row_insert_with_properties(self) -> None:
        """Property-bearing edges: props gets serialized JSON, hash
        non-None. Hash is opaque (caller computed; we just store it)."""
        conn = MagicMock()
        ctx = _stub_ctx()
        rows = [
            ("s1", "t1",
             {"section_name": "Info", "section_order": 0, "row": 0,
              "column": 0, "is_required": False, "is_readonly": False},
             "abc123"),
        ]
        _batch_insert_new_edges(
            conn, ctx, "INCLUDES_FIELD", "CONFIG", rows,
        )
        conn.execute.assert_called_once()
        params = conn.execute.call_args[0][1]
        # JSON-serialized properties dict (key order may vary; just
        # check the content survives the round-trip)
        import json as _json
        decoded = _json.loads(params["props_0"])
        assert decoded["section_name"] == "Info"
        assert decoded["row"] == 0
        assert params["hash_0"] == "abc123"
        assert params["edge_type"] == "INCLUDES_FIELD"
        assert params["edge_category"] == "CONFIG"


class TestBatchCloseSupersededEdges:
    def test_empty_pairs_is_noop(self) -> None:
        conn = MagicMock()
        ctx = _stub_ctx()
        _batch_close_superseded_edges(conn, ctx, "BELONGS_TO", [])
        conn.execute.assert_not_called()

    def test_uses_tuple_in_with_uuid_casts(self) -> None:
        """UPDATE close uses Postgres tuple-IN with explicit UUID
        casts on each side of each pair."""
        conn = MagicMock()
        ctx = _stub_ctx()
        pairs = [("s1", "t1"), ("s2", "t2")]
        _batch_close_superseded_edges(
            conn, ctx, "BELONGS_TO", pairs,
        )
        conn.execute.assert_called_once()
        sql_text = str(conn.execute.call_args[0][0])
        assert "(source_entity_id, target_entity_id) IN" in sql_text
        assert "CAST(:source_0 AS uuid)" in sql_text
        params = conn.execute.call_args[0][1]
        assert params["close_seq"] == ctx.logical_version_seq
        assert params["edge_type"] == "BELONGS_TO"


class TestBatchedMaterializeEdgesPropertyLess:
    def _setup(self, existing_pairs, incoming):
        """Common setup: ctx, conn with _batch_read returning
        existing_pairs as a set; existing_pairs is what's currently
        in the DB; incoming is what this sync wants."""
        ctx = _stub_ctx()
        conn = MagicMock()
        result = PhaseResult(entity_type="Field")
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch(
            _patch_path("_batch_read_existing_edges_for_sources"),
            return_value=existing_pairs,
        ))
        mock_insert = stack.enter_context(patch(
            _patch_path("_batch_insert_new_edges"),
        ))
        mock_close = stack.enter_context(patch(
            _patch_path("_batch_close_superseded_edges"),
        ))
        return ctx, conn, result, mock_insert, mock_close, stack

    def test_inserts_only_new_pairs(self) -> None:
        """Pairs in incoming but not in existing → INSERT; pairs in
        both → no-op (unchanged). Property-less branch uses {} for
        properties and None for hash (TIER_1_EDGES BELONGS_TO has
        properties_schema=None)."""
        existing = {("s1", "t1")}
        incoming = [
            ("s1", "t1", "BELONGS_TO", "STRUCTURAL", {}),
            ("s2", "t2", "BELONGS_TO", "STRUCTURAL", {}),
        ]
        ctx, conn, result, mock_insert, mock_close, stack = self._setup(
            existing, incoming,
        )
        with stack:
            batched_materialize_edges(
                ctx, conn, incoming, result,
            )
        mock_insert.assert_called_once()
        # 5-tuple (s, t, props, hash) per row in new_rows
        new_rows = mock_insert.call_args[0][4]
        new_pairs = {(r[0], r[1]) for r in new_rows}
        assert new_pairs == {("s2", "t2")}
        # Property-less rows carry {} properties + None hash
        for r in new_rows:
            assert r[2] == {}
            assert r[3] is None
        mock_close.assert_not_called()
        assert result.edges_inserted == 1
        assert result.edges_superseded == 0

    def test_closes_only_removed_pairs(self) -> None:
        """Pairs in existing but not in incoming → close. Pairs in
        incoming but not in existing → INSERT. Property-less branch."""
        existing = {("s1", "t1"), ("s2", "t2")}
        incoming = [
            ("s1", "t1", "BELONGS_TO", "STRUCTURAL", {}),
            ("s3", "t3", "BELONGS_TO", "STRUCTURAL", {}),
        ]
        ctx, conn, result, mock_insert, mock_close, stack = self._setup(
            existing, incoming,
        )
        with stack:
            batched_materialize_edges(
                ctx, conn, incoming, result,
            )
        # New: (s3, t3) only — extract pairs from 4-tuples
        new_rows = mock_insert.call_args[0][4]
        assert {(r[0], r[1]) for r in new_rows} == {("s3", "t3")}
        # Superseded: (s2, t2) only — close still takes 2-tuples
        assert set(mock_close.call_args[0][3]) == {("s2", "t2")}
        assert result.edges_inserted == 1
        assert result.edges_superseded == 1

    def test_idempotent_when_incoming_equals_existing(self) -> None:
        """All pairs in incoming match existing → unchanged set is
        full → no INSERT, no UPDATE. Idempotency for repeated syncs."""
        existing = {("s1", "t1"), ("s2", "t2")}
        incoming = [
            ("s1", "t1", "BELONGS_TO", "STRUCTURAL", {}),
            ("s2", "t2", "BELONGS_TO", "STRUCTURAL", {}),
        ]
        ctx, conn, result, mock_insert, mock_close, stack = self._setup(
            existing, incoming,
        )
        with stack:
            batched_materialize_edges(
                ctx, conn, incoming, result,
            )
        mock_insert.assert_not_called()
        mock_close.assert_not_called()
        assert result.edges_inserted == 0
        assert result.edges_superseded == 0

    def test_groups_by_edge_type(self) -> None:
        """Multiple edge_types in incoming → bucketed per edge_type
        independently; each gets its own existing-set lookup.
        Property-less edges (BELONGS_TO + HAS_RELATIONSHIP_TO) both
        route through the property-less branch."""
        existing_calls = []

        def fake_read(conn, edge_type, source_ids):
            existing_calls.append(edge_type)
            return set()
        conn = MagicMock()
        ctx = _stub_ctx()
        result = PhaseResult(entity_type="Field")
        incoming = [
            ("s1", "t1", "BELONGS_TO", "STRUCTURAL", {}),
            ("s1", "tref", "HAS_RELATIONSHIP_TO", "STRUCTURAL", {}),
        ]
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch(
                _patch_path("_batch_read_existing_edges_for_sources"),
                side_effect=fake_read,
            ))
            stack.enter_context(patch(
                _patch_path("_batch_insert_new_edges"),
            ))
            batched_materialize_edges(
                ctx, conn, incoming, result,
            )
        # Both edge_types' existing sets were independently queried
        assert set(existing_calls) == {"BELONGS_TO",
                                        "HAS_RELATIONSHIP_TO"}


class TestMaterializeEdgesForEntities:
    def _normalized_field(self, name, parent="Account",
                           reference_to=None) -> dict:
        n = {"name": name, "_parent_object_api_name": parent}
        if reference_to is not None:
            n["referenceTo"] = list(reference_to)
        return n

    def test_no_edges_when_no_specs_for_entity_type(self) -> None:
        """If get_edge_specs returns [] for the source entity_type,
        no edge work happens at all (no parent_resolver creation,
        no batched_materialize_edges call)."""
        ctx = _stub_ctx()
        conn = MagicMock()
        result = PhaseResult(entity_type="Object")
        from contextlib import ExitStack
        with ExitStack() as stack:
            mock_bm = stack.enter_context(patch(
                _patch_path("batched_materialize_edges"),
            ))
            materialize_edges_for_entities(
                ctx, conn, "Object",
                entity_id_map={"Account": "obj-acc"},
                normalized_payloads=[{"name": "Account"}],
                result=result,
            )
        mock_bm.assert_not_called()

    def test_writes_belongs_to_for_every_field(self) -> None:
        """Each Field with a resolved parent Object produces one
        BELONGS_TO edge_write tuple."""
        ctx = _stub_ctx()
        conn = MagicMock()
        result = PhaseResult(entity_type="Field")
        normalized_payloads = [
            self._normalized_field("Industry"),
            self._normalized_field("Name"),
        ]
        entity_id_map = {
            "Account.Industry": "fld-001",
            "Account.Name": "fld-002",
        }

        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch(
                _patch_path("make_parent_resolver"),
                return_value=lambda **_kw: "obj-account",
            ))
            mock_bm = stack.enter_context(patch(
                _patch_path("batched_materialize_edges"),
            ))
            materialize_edges_for_entities(
                ctx, conn, "Field",
                entity_id_map=entity_id_map,
                normalized_payloads=normalized_payloads,
                result=result,
            )
        mock_bm.assert_called_once()
        edge_writes = mock_bm.call_args[0][2]
        # 2 fields × 1 BELONGS_TO each = 2 edges
        # (no referenceTo → 0 HAS_RELATIONSHIP_TO)
        belongs_to = [e for e in edge_writes if e[2] == "BELONGS_TO"]
        assert len(belongs_to) == 2

    def test_writes_has_relationship_to_for_reference_field(self) -> None:
        """A reference field with referenceTo=['User'] produces a
        HAS_RELATIONSHIP_TO edge to the User Object entity."""
        ctx = _stub_ctx()
        conn = MagicMock()
        result = PhaseResult(entity_type="Field")
        normalized_payloads = [
            self._normalized_field("OwnerId", reference_to=["User"]),
        ]
        entity_id_map = {"Account.OwnerId": "fld-owner"}

        # Resolver: Account → obj-account, User → obj-user
        def resolver(*, entity_type, external_id):
            return {
                ("Object", "Account"): "obj-account",
                ("Object", "User"): "obj-user",
            }.get((entity_type, external_id))

        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch(
                _patch_path("make_parent_resolver"),
                return_value=resolver,
            ))
            mock_bm = stack.enter_context(patch(
                _patch_path("batched_materialize_edges"),
            ))
            materialize_edges_for_entities(
                ctx, conn, "Field",
                entity_id_map=entity_id_map,
                normalized_payloads=normalized_payloads,
                result=result,
            )
        edge_writes = mock_bm.call_args[0][2]
        # 1 BELONGS_TO + 1 HAS_RELATIONSHIP_TO
        assert len(edge_writes) == 2
        edge_types = {e[2] for e in edge_writes}
        assert edge_types == {"BELONGS_TO", "HAS_RELATIONSHIP_TO"}

    def test_skips_edges_to_unresolvable_targets(self) -> None:
        """If parent_resolver returns None for a target (target Object
        was filtered out or not yet synced), skip the edge silently.
        Don't write a dangling edge; don't fail the whole sync."""
        ctx = _stub_ctx()
        conn = MagicMock()
        result = PhaseResult(entity_type="Field")
        # Reference to an Object that isn't materialized
        normalized_payloads = [
            self._normalized_field(
                "MysteryRef", reference_to=["UnmappedObject"],
            ),
        ]
        entity_id_map = {"Account.MysteryRef": "fld-mystery"}

        def resolver(*, entity_type, external_id):
            return {
                ("Object", "Account"): "obj-account",
                # No UnmappedObject — resolver returns None
            }.get((entity_type, external_id))

        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch(
                _patch_path("make_parent_resolver"),
                return_value=resolver,
            ))
            mock_bm = stack.enter_context(patch(
                _patch_path("batched_materialize_edges"),
            ))
            materialize_edges_for_entities(
                ctx, conn, "Field",
                entity_id_map=entity_id_map,
                normalized_payloads=normalized_payloads,
                result=result,
            )
        edge_writes = mock_bm.call_args[0][2]
        # Only BELONGS_TO survives; HAS_RELATIONSHIP_TO skipped
        assert len(edge_writes) == 1
        assert edge_writes[0][2] == "BELONGS_TO"
        # Per corrections-log §19, the skip should be observable.
        assert result.edges_skipped_by_type == {
            "HAS_RELATIONSHIP_TO": 1,
        }

    def test_materialize_edges_records_skipped_when_target_unresolvable(
        self,
    ) -> None:
        """Each skip increments
        PhaseResult.edges_skipped_by_type[edge_type]. Multiple
        skips on the same edge_type accumulate. Behavior introduced
        per corrections-log §19 (observability without
        behavior change)."""
        ctx = _stub_ctx()
        conn = MagicMock()
        result = PhaseResult(entity_type="Field")
        # 3 polymorphic ref fields where the parent Object resolves
        # but ALL referenceTo targets are unsyncable → 3 skips on
        # HAS_RELATIONSHIP_TO. BELONGS_TO still writes for each.
        normalized_payloads = [
            self._normalized_field(
                "RefA", reference_to=["UnsyncableA"],
            ),
            self._normalized_field(
                "RefB", reference_to=["UnsyncableB"],
            ),
            self._normalized_field(
                "RefC", reference_to=["UnsyncableC"],
            ),
        ]
        entity_id_map = {
            "Account.RefA": "fld-a",
            "Account.RefB": "fld-b",
            "Account.RefC": "fld-c",
        }

        def resolver(*, entity_type, external_id):
            # Only the parent Object resolves; ref targets are None.
            return {
                ("Object", "Account"): "obj-account",
            }.get((entity_type, external_id))

        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch(
                _patch_path("make_parent_resolver"),
                return_value=resolver,
            ))
            stack.enter_context(patch(
                _patch_path("batched_materialize_edges"),
            ))
            materialize_edges_for_entities(
                ctx, conn, "Field",
                entity_id_map=entity_id_map,
                normalized_payloads=normalized_payloads,
                result=result,
            )
        assert result.edges_skipped_by_type == {
            "HAS_RELATIONSHIP_TO": 3,
        }

    def test_materialize_edges_skips_grouped_by_edge_type(self) -> None:
        """edges_skipped_by_type is a dict grouped by edge_type —
        different edge_types accumulate in distinct buckets, none
        leak into each other. Uses two Field payloads whose
        polymorphic references include one resolvable + one
        unresolvable each, so HAS_RELATIONSHIP_TO accumulates skip
        count but BELONGS_TO doesn't."""
        ctx = _stub_ctx()
        conn = MagicMock()
        result = PhaseResult(entity_type="Field")
        normalized_payloads = [
            self._normalized_field(
                "WhoId", reference_to=["Contact", "UnmappedTarget"],
            ),
            self._normalized_field(
                "AnotherRef", reference_to=["UnmappedTarget"],
            ),
        ]
        entity_id_map = {
            "Account.WhoId": "fld-1",
            "Account.AnotherRef": "fld-2",
        }

        def resolver(*, entity_type, external_id):
            return {
                ("Object", "Account"): "obj-account",
                ("Object", "Contact"): "obj-contact",
                # UnmappedTarget unresolvable → 2 skips
            }.get((entity_type, external_id))

        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch(
                _patch_path("make_parent_resolver"),
                return_value=resolver,
            ))
            stack.enter_context(patch(
                _patch_path("batched_materialize_edges"),
            ))
            materialize_edges_for_entities(
                ctx, conn, "Field",
                entity_id_map=entity_id_map,
                normalized_payloads=normalized_payloads,
                result=result,
            )
        # 2 skips on HAS_RELATIONSHIP_TO (Account.WhoId →
        # UnmappedTarget, Account.AnotherRef → UnmappedTarget);
        # no other edge_type appears in the skip dict.
        assert result.edges_skipped_by_type == {
            "HAS_RELATIONSHIP_TO": 2,
        }
        assert "BELONGS_TO" not in result.edges_skipped_by_type


# ----------------------------------------------------------------------
# batched_materialize return_id_map
# ----------------------------------------------------------------------


class TestBatchedMaterializeReturnIdMap:
    def test_default_returns_none(self) -> None:
        """When return_id_map=False (default), batched_materialize
        returns None — the caller pays no accumulation cost."""
        from contextlib import ExitStack
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        with ExitStack() as stack:
            for _name, p in zip(
                ("normalize", "hash", "pres", "text",
                 "read", "insert", "close", "touch", "upsert"),
                _patch_pipeline(),
            ):
                stack.enter_context(p)
            stack.enter_context(patch(_patch_path("_batch_insert_details")))
            ret = batched_materialize(
                _stub_ctx(), conn, "Object",
                raw_payloads=[{"name": "Account"}],
                result=result,
            )
        assert ret is None

    def test_returns_dict_when_enabled(self) -> None:
        """return_id_map=True → returns {external_id: entity_id} for
        every input payload (new + changed + unchanged)."""
        from contextlib import ExitStack
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        with ExitStack() as stack:
            for _name, p in zip(
                ("normalize", "hash", "pres", "text",
                 "read", "insert", "close", "touch", "upsert"),
                _patch_pipeline(
                    normalize_return={"name": "Account", "custom": False},
                    existing_return={},  # all new
                    insert_return=["entity-uuid-1"],
                ),
            ):
                stack.enter_context(p)
            stack.enter_context(patch(_patch_path("_batch_insert_details")))
            ret = batched_materialize(
                _stub_ctx(), conn, "Object",
                raw_payloads=[{"name": "Account"}],
                result=result,
                return_id_map=True,
            )
        assert ret == {"Account": "entity-uuid-1"}


# ----------------------------------------------------------------------
# Hash + property-bearing edge supersession
# ----------------------------------------------------------------------

from primeqa.sync.materialize import (
    _batch_read_existing_edges_with_hash,
    _edge_type_has_properties,
    _hash_edge_properties,
)


class TestHashEdgeProperties:
    def test_canonical_json_sorted_keys(self) -> None:
        """Hash is deterministic regardless of key order — same
        properties dict with different insertion order yields same
        hash."""
        h1 = _hash_edge_properties(
            {"a": 1, "b": 2, "c": 3},
        )
        h2 = _hash_edge_properties(
            {"c": 3, "a": 1, "b": 2},
        )
        assert h1 == h2

    def test_different_properties_yield_different_hashes(self) -> None:
        h1 = _hash_edge_properties({"section_order": 0, "row": 0})
        h2 = _hash_edge_properties({"section_order": 1, "row": 0})
        assert h1 != h2

    def test_empty_dict_has_deterministic_hash(self) -> None:
        """Empty properties (property-less edges in practice never
        call this, but defensive) → consistent hex digest."""
        h = _hash_edge_properties({})
        assert isinstance(h, str)
        assert len(h) == 64  # SHA-256 hex digest length

    def test_hash_is_64_char_hex(self) -> None:
        """SHA-256 always produces 64 lowercase hex chars."""
        h = _hash_edge_properties({"foo": "bar"})
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


class TestEdgeTypeHasProperties:
    def test_belongs_to_is_property_less(self) -> None:
        """BELONGS_TO has properties_schema=None in TIER_1_EDGES."""
        assert _edge_type_has_properties("BELONGS_TO") is False

    def test_has_relationship_to_is_property_less(self) -> None:
        assert _edge_type_has_properties("HAS_RELATIONSHIP_TO") is False

    def test_includes_field_is_property_bearing(self) -> None:
        """INCLUDES_FIELD has IncludesFieldProperties schema."""
        assert _edge_type_has_properties("INCLUDES_FIELD") is True


class TestBatchReadExistingEdgesWithHash:
    def test_empty_sources_returns_empty_dict(self) -> None:
        """Defensive empty-input check."""
        conn = MagicMock()
        result = _batch_read_existing_edges_with_hash(
            conn, "INCLUDES_FIELD", [],
        )
        assert result == {}
        conn.execute.assert_not_called()

    def test_returns_dict_keyed_by_source_target(self) -> None:
        """Returns {(source_id, target_id): {id, hash}} for each
        active edge — the dict form lets the materialize layer do
        the hash-compare three-bucket sort."""
        conn = MagicMock()
        row1 = type("R", (), {
            "id": "e1", "source_entity_id": "s1",
            "target_entity_id": "t1", "last_seed_hash": "h1",
        })()
        row2 = type("R", (), {
            "id": "e2", "source_entity_id": "s2",
            "target_entity_id": "t2", "last_seed_hash": "h2",
        })()
        conn.execute.return_value.fetchall.return_value = [row1, row2]
        result = _batch_read_existing_edges_with_hash(
            conn, "INCLUDES_FIELD", ["s1", "s2"],
        )
        assert result == {
            ("s1", "t1"): {"id": "e1", "hash": "h1"},
            ("s2", "t2"): {"id": "e2", "hash": "h2"},
        }


class TestBatchedMaterializeEdgesPropertyBearing:
    """Cover the property-bearing branch via batched_materialize_edges
    dispatch. Uses INCLUDES_FIELD (real property-bearing edge type)
    so TIER_1_EDGES lookup routes correctly."""

    def _valid_props(self, **overrides) -> dict:
        base = {
            "section_name": "Info", "section_order": 0,
            "row": 0, "column": 0,
            "is_required": False, "is_readonly": False,
        }
        base.update(overrides)
        return base

    def test_all_new_takes_new_path(self) -> None:
        """Property-bearing edge type with empty existing → all
        incoming → new bucket → batched insert called once with
        property-bearing rows (props + hash). No close call."""
        ctx = _stub_ctx()
        conn = MagicMock()
        result = PhaseResult(entity_type="Layout")
        incoming = [
            ("s1", "t1", "INCLUDES_FIELD", "CONFIG",
             self._valid_props()),
        ]
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch(
                _patch_path("_batch_read_existing_edges_with_hash"),
                return_value={},  # all new
            ))
            mock_insert = stack.enter_context(patch(
                _patch_path("_batch_insert_new_edges"),
            ))
            mock_close = stack.enter_context(patch(
                _patch_path("_batch_close_superseded_edges_by_id"),
            ))
            batched_materialize_edges(ctx, conn, incoming, result)
        mock_insert.assert_called_once()
        rows = mock_insert.call_args[0][4]
        assert len(rows) == 1
        # Row tuple: (source, target, props, hash)
        assert rows[0][0] == "s1"
        assert rows[0][1] == "t1"
        # Properties round-tripped through Pydantic schema validation
        assert rows[0][2]["section_name"] == "Info"
        # Hash is non-None (property-bearing → computed)
        assert rows[0][3] is not None
        assert len(rows[0][3]) == 64  # SHA-256 hex
        mock_close.assert_not_called()
        assert result.edges_inserted == 1
        assert result.edges_superseded == 0

    def test_changed_hash_triggers_close_and_insert(self) -> None:
        """Existing edge's hash differs from incoming → SCD Type 2:
        close old by id, insert new. Counters: 1 inserted + 1
        superseded."""
        ctx = _stub_ctx()
        conn = MagicMock()
        result = PhaseResult(entity_type="Layout")
        # Incoming properties (will hash to X)
        new_props = self._valid_props(row=1)
        incoming = [
            ("s1", "t1", "INCLUDES_FIELD", "CONFIG", new_props),
        ]
        # Existing has a different hash ("old_hash")
        existing = {
            ("s1", "t1"): {"id": "edge-old", "hash": "old_hash"},
        }
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch(
                _patch_path("_batch_read_existing_edges_with_hash"),
                return_value=existing,
            ))
            mock_insert = stack.enter_context(patch(
                _patch_path("_batch_insert_new_edges"),
            ))
            mock_close = stack.enter_context(patch(
                _patch_path("_batch_close_superseded_edges_by_id"),
            ))
            batched_materialize_edges(ctx, conn, incoming, result)
        # Both close and insert called
        mock_close.assert_called_once()
        assert mock_close.call_args[0][2] == ["edge-old"]
        mock_insert.assert_called_once()
        new_rows = mock_insert.call_args[0][4]
        assert len(new_rows) == 1
        assert new_rows[0][2]["row"] == 1  # the new props
        # Both counters incremented (SCD-type-2: old superseded,
        # new inserted)
        assert result.edges_inserted == 1
        assert result.edges_superseded == 1

    def test_unchanged_hash_is_noop(self) -> None:
        """Existing edge's hash matches incoming → no-op. Neither
        close nor insert called for this pair."""
        ctx = _stub_ctx()
        conn = MagicMock()
        result = PhaseResult(entity_type="Layout")
        props = self._valid_props()
        # Compute the hash that the actual code will compute
        computed_hash = _hash_edge_properties(props)
        incoming = [
            ("s1", "t1", "INCLUDES_FIELD", "CONFIG", props),
        ]
        existing = {
            ("s1", "t1"): {"id": "edge-1", "hash": computed_hash},
        }
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch(
                _patch_path("_batch_read_existing_edges_with_hash"),
                return_value=existing,
            ))
            mock_insert = stack.enter_context(patch(
                _patch_path("_batch_insert_new_edges"),
            ))
            mock_close = stack.enter_context(patch(
                _patch_path("_batch_close_superseded_edges_by_id"),
            ))
            batched_materialize_edges(ctx, conn, incoming, result)
        mock_insert.assert_not_called()
        mock_close.assert_not_called()
        assert result.edges_inserted == 0
        assert result.edges_superseded == 0

    def test_superseded_pair_closes_existing(self) -> None:
        """Pair in existing but not in incoming → close (set
        valid_to_seq). No insert; counter records supersession."""
        ctx = _stub_ctx()
        conn = MagicMock()
        result = PhaseResult(entity_type="Layout")
        # Incoming has one pair, existing has TWO
        incoming = [
            ("s1", "t1", "INCLUDES_FIELD", "CONFIG",
             self._valid_props()),
        ]
        props_hash = _hash_edge_properties(self._valid_props())
        existing = {
            ("s1", "t1"): {"id": "edge-keep", "hash": props_hash},
            ("s1", "t-removed"): {"id": "edge-stale",
                                   "hash": "old_hash"},
        }
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch(
                _patch_path("_batch_read_existing_edges_with_hash"),
                return_value=existing,
            ))
            mock_insert = stack.enter_context(patch(
                _patch_path("_batch_insert_new_edges"),
            ))
            mock_close = stack.enter_context(patch(
                _patch_path("_batch_close_superseded_edges_by_id"),
            ))
            batched_materialize_edges(ctx, conn, incoming, result)
        # No INSERT (the incoming pair is unchanged); one close for
        # the stale pair only
        mock_insert.assert_not_called()
        mock_close.assert_called_once()
        assert mock_close.call_args[0][2] == ["edge-stale"]
        assert result.edges_superseded == 1
