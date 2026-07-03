"""Tests for primeqa.sync.fk_assertion — ENTITY_ORDER vs schema FKs."""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from primeqa.sync.exceptions import EntityOrderViolation
from primeqa.sync.fk_assertion import (
    ENTITY_ORDER,
    assert_entity_order_respects_schema_fks,
)


class TestEntityOrderComposition:
    """Locks the ENTITY_ORDER membership decisions that survey
    cycles resolved — so future drift fails loudly here."""

    def test_entity_order_omits_flow_definition_per_spec_section_9(
        self,
    ) -> None:
        """FlowDefinition is NOT in ENTITY_ORDER. SPEC.md §9 lists
        exactly 10 Tier-1 entity types and Flow is the
        flow-related one — FlowDefinition is not an entity.
        Substrate-1's bitemporal supersession provides Flow's
        versioning natively (each version deployment supersedes
        the prior Flow record). fetch_flow_definitions() remains
        a Tooling fetcher supplying fetch-time parent context to
        the Flow phase, but FlowDefinition is not itself a phase.
        See corrections-log §20."""
        assert "FlowDefinition" not in ENTITY_ORDER
        # Flow IS a phase — it's the last entity-materializing
        # phase, and the flow-related Tier-1 entity per SPEC §9.
        assert "Flow" in ENTITY_ORDER

    def test_entity_order_is_twelve_entity_materializing_phases(
        self,
    ) -> None:
        """ENTITY_ORDER is 11 entries: the 10 SPEC §9 Tier-1
        detail-table entity types PLUS PicklistValueSet (an
        entity-materializing phase that intentionally has no
        detail table — its shape lives in entities.attributes
        JSONB per the Object-cycle corrections). Catches an
        accidental re-addition of FlowDefinition or any other
        non-entity phase."""
        assert ENTITY_ORDER == (
            "Object",
            "PicklistValueSet",
            "PicklistValue",
            "Field",
            "RecordType",
            "Layout",
            "ValidationRule",
            "Profile",
            "PermissionSet",
            "User",
            "Flow",
            "ApprovalProcess",                     # D-308
        )
        assert len(ENTITY_ORDER) == 12                 # D-308: + ApprovalProcess


class TestFKAssertion:
    def test_fk_assertion_passes_when_entity_order_respects_fks(self) -> None:
        """When schema's FKs go from later-phase tables to earlier-phase
        tables, assertion passes silently.

        Simulates a hypothetical per-type-table schema where Field
        depends on Object (correct ordering).
        """
        mock_inspector = MagicMock()

        # Route get_foreign_keys per table:
        # - 'objects' has no FKs to other entity tables
        # - 'fields' has an FK to 'objects' (correct ordering: Field
        #   depends on Object, which is earlier in ENTITY_ORDER)
        def fk_side_effect(table_name, schema=None):
            if table_name == "fields":
                return [
                    {
                        "constrained_columns": ["object_id"],
                        "referred_table": "objects",
                        "referred_columns": ["id"],
                    },
                ]
            return []

        mock_inspector.get_foreign_keys.side_effect = fk_side_effect

        with patch(
            "primeqa.sync.fk_assertion.inspect",
            return_value=mock_inspector,
        ):
            with patch.dict(
                "primeqa.sync.fk_assertion.ENTITY_TYPE_TO_TABLE",
                {"Object": "objects", "Field": "fields"},
                clear=True,
            ):
                # No raise = pass
                assert_entity_order_respects_schema_fks(
                    engine=MagicMock(), tenant_schema="tenant_1",
                )

    def test_fk_assertion_raises_when_a_later_phase_fk_to_earlier_phase(self) -> None:
        """When the schema declares an FK from an earlier-phase table
        to a later-phase table, EntityOrderViolation is raised.

        Per design doc §4 — the assertion catches schema drift
        loudly.

        Simulated case: Object has an FK to Field (reverse of
        ENTITY_ORDER), which is a violation.
        """
        mock_inspector = MagicMock()
        # The 'objects' table has an FK to 'fields' — ENTITY_ORDER
        # places Object (idx 0) before Field (idx 3), so this FK
        # points the wrong way: an earlier-phase table depends on
        # a later-phase table.
        def fk_side_effect(table_name, schema=None):
            if table_name == "objects":
                return [
                    {
                        "constrained_columns": ["lead_field_id"],
                        "referred_table": "fields",
                        "referred_columns": ["id"],
                    },
                ]
            return []

        mock_inspector.get_foreign_keys.side_effect = fk_side_effect

        with patch(
            "primeqa.sync.fk_assertion.inspect",
            return_value=mock_inspector,
        ):
            with patch.dict(
                "primeqa.sync.fk_assertion.ENTITY_TYPE_TO_TABLE",
                {"Object": "objects", "Field": "fields"},
                clear=True,
            ):
                with pytest.raises(EntityOrderViolation) as excinfo:
                    assert_entity_order_respects_schema_fks(
                        engine=MagicMock(), tenant_schema="tenant_1",
                    )
                msg = str(excinfo.value)
                assert "Object" in msg
                assert "Field" in msg
                # Error message should clearly identify the offending FK
                assert "objects" in msg
                assert "fields" in msg

    def test_fk_assertion_handles_table_with_no_fks(self) -> None:
        """If a mapped entity-type table exists in the schema but
        declares no FKs, the assertion proceeds without error."""
        mock_inspector = MagicMock()
        mock_inspector.get_foreign_keys.return_value = []

        with patch(
            "primeqa.sync.fk_assertion.inspect",
            return_value=mock_inspector,
        ):
            with patch.dict(
                "primeqa.sync.fk_assertion.ENTITY_TYPE_TO_TABLE",
                {"Object": "objects"},
                clear=True,
            ):
                # No raise = pass
                assert_entity_order_respects_schema_fks(
                    engine=MagicMock(), tenant_schema="tenant_1",
                )

    def test_fk_assertion_warning_on_no_per_type_tables(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Substrate-1 case: ENTITY_TYPE_TO_TABLE is empty (single
        `entities` table). Assertion logs a warning and returns
        without raising."""
        # ENTITY_TYPE_TO_TABLE is empty by default in this codebase
        with caplog.at_level(logging.WARNING, logger="primeqa.sync.fk_assertion"):
            assert_entity_order_respects_schema_fks(
                engine=MagicMock(), tenant_schema="tenant_1",
            )
        # Verify the warning was logged
        warning_messages = [
            r.getMessage() for r in caplog.records
            if r.levelname == "WARNING"
        ]
        assert any(
            "single-entities-table" in m or "logically only" in m
            for m in warning_messages
        ), f"Expected warning about single-entities-table; got: {warning_messages}"
