"""Tests for the substrate-2 SQLAlchemy table models.

Per SPEC §4.1 + Track A migration
(``alembic/versions/tenant/20260518_1014_create_substrate_2_tables.py``).

Pure model-shape verification — no DB connection. Asserts that
each class declares the columns, primary keys, CHECK constraints,
indexes, enum bindings, JSONB columns, and logical-FK semantics
matching the Track A migration. Catches drift between the
Python-side model and the actual DB schema at PR-review time.
"""
from __future__ import annotations

from typing import Iterable

import pytest
from sqlalchemy import CheckConstraint, Index
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.types import DateTime, Integer, Text

from primeqa.db import Base
from primeqa.test_representation import (
    TestClaim,
    TestClaimCoverage,
    TestProvenance,
    TestRecipe,
    TestRecipeRuntimeState,
    TestRequirementLink,
)


# ---------------------------------------------------------------------------
# Expected schema specification, derived from Track A migration.
# Each entry: (model_class, expected_table_name, expected_pk_cols,
#              expected_columns: {name: type_class})
#
# This is a denormalized restatement of the migration's CREATE TABLE
# bodies; any drift between the migration and the models surfaces as
# a failed assertion below.
# ---------------------------------------------------------------------------

EXPECTED_TABLES = {
    "test_claims",
    "test_recipes",
    "test_provenance",
    "test_claim_coverage",
    "test_recipe_runtime_state",
    "test_requirement_links",
}

# Logical-FK columns must NOT carry a ForeignKey declaration
# (substrate-isolation principle + the recipe→claim case where
# claim.test_id is not unique).
LOGICAL_FK_COLUMNS: list[tuple[type, str]] = [
    (TestRecipe, "claim_test_id"),
    (TestRecipe, "claim_version_seq"),
    (TestProvenance, "claim_test_id"),
    (TestProvenance, "recipe_id"),
    (TestClaimCoverage, "claim_test_id"),
    (TestClaimCoverage, "entity_id"),
    (TestRecipeRuntimeState, "recipe_id"),
    (TestRecipeRuntimeState, "last_run_id"),
    (TestRequirementLink, "test_id"),
]


# ---------------------------------------------------------------------------
# __tablename__ + PK
# ---------------------------------------------------------------------------

class TestTableNames:
    def test_test_claim_tablename(self) -> None:
        assert TestClaim.__tablename__ == "test_claims"

    def test_test_recipe_tablename(self) -> None:
        assert TestRecipe.__tablename__ == "test_recipes"

    def test_test_provenance_tablename(self) -> None:
        assert TestProvenance.__tablename__ == "test_provenance"

    def test_test_claim_coverage_tablename(self) -> None:
        assert TestClaimCoverage.__tablename__ == "test_claim_coverage"

    def test_test_recipe_runtime_state_tablename(self) -> None:
        assert (
            TestRecipeRuntimeState.__tablename__
            == "test_recipe_runtime_state"
        )

    def test_test_requirement_link_tablename(self) -> None:
        assert TestRequirementLink.__tablename__ == "test_requirement_links"


class TestPrimaryKeys:
    """PK column composition matches the migration's
    PrimaryKeyConstraint declarations."""

    @pytest.mark.parametrize("model_class,expected_pk", [
        (TestClaim, ["test_id", "version_seq"]),
        (TestRecipe, ["recipe_id", "version_seq"]),
        (TestProvenance, ["id"]),
        (TestClaimCoverage,
         ["claim_test_id", "entity_id", "reference_kind"]),
        (TestRecipeRuntimeState, ["recipe_id"]),
        (TestRequirementLink,
         ["test_id", "external_system", "external_key", "link_kind"]),
    ])
    def test_pk_columns(
        self, model_class: type, expected_pk: list[str],
    ) -> None:
        pk_cols = [c.name for c in model_class.__table__.primary_key.columns]
        assert pk_cols == expected_pk


# ---------------------------------------------------------------------------
# Column-by-column type checks
# ---------------------------------------------------------------------------

class TestTestClaimColumns:
    @pytest.mark.parametrize("col_name,type_class", [
        ("test_id", UUID),
        ("version_seq", Integer),
        ("valid_from", DateTime),
        ("valid_to", DateTime),
        ("archetype", ENUM),
        ("claim_kind", ENUM),
        ("asserted_truth", JSONB),
        ("semantic_conditions", JSONB),
        ("identity_hash", Text),
        ("identity_hash_version", Integer),
        ("status", ENUM),
        ("created_at", DateTime),
        ("updated_at", DateTime),
    ])
    def test_column_type(self, col_name: str, type_class: type) -> None:
        col = TestClaim.__table__.columns[col_name]
        assert isinstance(col.type, type_class), (
            f"TestClaim.{col_name} has type "
            f"{type(col.type).__name__}, expected {type_class.__name__}"
        )

    def test_valid_to_nullable(self) -> None:
        assert TestClaim.__table__.columns["valid_to"].nullable is True

    def test_status_has_draft_server_default(self) -> None:
        col = TestClaim.__table__.columns["status"]
        # server_default is a DefaultClause whose .arg is the literal.
        assert col.server_default is not None
        assert "draft" in str(col.server_default.arg).lower()


class TestTestRecipeColumns:
    @pytest.mark.parametrize("col_name,type_class", [
        ("recipe_id", UUID),
        ("version_seq", Integer),
        ("valid_from", DateTime),
        ("valid_to", DateTime),
        ("claim_test_id", UUID),
        ("claim_version_seq", Integer),
        ("trigger_kind", ENUM),
        ("recipe_kind", ENUM),
        ("causal_initiation", JSONB),
        ("observation_realization", JSONB),
        ("execution_environment", JSONB),
        ("priority", Integer),
        ("status", ENUM),
        ("created_at", DateTime),
        ("updated_at", DateTime),
    ])
    def test_column_type(self, col_name: str, type_class: type) -> None:
        col = TestRecipe.__table__.columns[col_name]
        assert isinstance(col.type, type_class)

    def test_priority_default_zero(self) -> None:
        col = TestRecipe.__table__.columns["priority"]
        assert col.server_default is not None
        assert "0" in str(col.server_default.arg)

    def test_status_default_generated_unapproved(self) -> None:
        col = TestRecipe.__table__.columns["status"]
        assert "generated_unapproved" in str(col.server_default.arg)

    def test_claim_version_seq_nullable(self) -> None:
        assert (
            TestRecipe.__table__.columns["claim_version_seq"].nullable
            is True
        )


class TestTestProvenanceColumns:
    @pytest.mark.parametrize("col_name,type_class", [
        ("id", UUID),
        ("claim_test_id", UUID),
        ("recipe_id", UUID),
        ("event_kind", ENUM),
        ("event_data", JSONB),
        ("event_actor", Text),
        ("event_at", DateTime),
    ])
    def test_column_type(self, col_name: str, type_class: type) -> None:
        col = TestProvenance.__table__.columns[col_name]
        assert isinstance(col.type, type_class)

    def test_polymorphic_columns_both_nullable(self) -> None:
        cols = TestProvenance.__table__.columns
        # Both polymorphic targets are NULLABLE — the CHECK
        # constraint enforces exactly-one-non-NULL at row level.
        assert cols["claim_test_id"].nullable is True
        assert cols["recipe_id"].nullable is True


class TestTestClaimCoverageColumns:
    @pytest.mark.parametrize("col_name,type_class", [
        ("claim_test_id", UUID),
        ("entity_type", Text),
        ("entity_id", UUID),
        ("reference_kind", ENUM),
    ])
    def test_column_type(self, col_name: str, type_class: type) -> None:
        col = TestClaimCoverage.__table__.columns[col_name]
        assert isinstance(col.type, type_class)


class TestTestRecipeRuntimeStateColumns:
    @pytest.mark.parametrize("col_name,type_class", [
        ("recipe_id", UUID),
        ("last_run_id", UUID),
        ("last_run_at", DateTime),
        ("last_run_outcome", ENUM),
        ("last_run_recipe_version_seq", Integer),
        ("last_pass_at", DateTime),
        ("last_failure_at", DateTime),
        ("updated_at", DateTime),
    ])
    def test_column_type(self, col_name: str, type_class: type) -> None:
        col = TestRecipeRuntimeState.__table__.columns[col_name]
        assert isinstance(col.type, type_class)


class TestTestRequirementLinkColumns:
    @pytest.mark.parametrize("col_name,type_class", [
        ("test_id", UUID),
        ("external_system", ENUM),
        ("external_key", Text),
        ("external_version", Text),
        ("link_kind", ENUM),
        ("linked_at", DateTime),
        ("linked_by", Text),
    ])
    def test_column_type(self, col_name: str, type_class: type) -> None:
        col = TestRequirementLink.__table__.columns[col_name]
        assert isinstance(col.type, type_class)


# ---------------------------------------------------------------------------
# CHECK constraints
# ---------------------------------------------------------------------------

def _check_constraint_names(model_class: type) -> set[str]:
    return {
        c.name for c in model_class.__table__.constraints
        if isinstance(c, CheckConstraint)
    }


class TestCheckConstraints:
    def test_test_claims_check_constraints(self) -> None:
        names = _check_constraint_names(TestClaim)
        assert "ck_test_claims_asserted_truth_keys" in names
        assert "ck_test_claims_semantic_conditions_keys" in names

    def test_test_recipes_check_constraints(self) -> None:
        names = _check_constraint_names(TestRecipe)
        assert "ck_test_recipes_causal_initiation_keys" in names
        assert "ck_test_recipes_observation_realization_keys" in names
        assert "ck_test_recipes_execution_environment_keys" in names

    def test_test_provenance_polymorphic_check_constraint(self) -> None:
        names = _check_constraint_names(TestProvenance)
        assert "ck_test_provenance_polymorphic_target" in names

    def test_boundary_tables_have_no_check_constraints(self) -> None:
        """test_claim_coverage, test_recipe_runtime_state, and
        test_requirement_links don't declare CHECK constraints in
        the migration. The DB enforces constraints via NOT NULL +
        enum membership; no row-level invariants beyond that."""
        for cls in (
            TestClaimCoverage, TestRecipeRuntimeState, TestRequirementLink,
        ):
            assert _check_constraint_names(cls) == set()


# ---------------------------------------------------------------------------
# Indexes (including partial-index postgresql_where)
# ---------------------------------------------------------------------------

def _indexes_by_name(model_class: type) -> dict[str, Index]:
    return {idx.name: idx for idx in model_class.__table__.indexes}


class TestTestClaimIndexes:
    def test_current_version_partial_index(self) -> None:
        indexes = _indexes_by_name(TestClaim)
        idx = indexes["idx_test_claims_test_id_current"]
        assert [c.name for c in idx.expressions] == ["test_id"]
        # postgresql_where is stored under dialect_options['postgresql']['where']
        where = idx.dialect_options["postgresql"].get("where")
        assert where is not None
        assert "valid_to IS NULL" in str(where)

    def test_identity_hash_index(self) -> None:
        indexes = _indexes_by_name(TestClaim)
        idx = indexes["idx_test_claims_identity_hash"]
        cols = [c.name for c in idx.expressions]
        assert cols == ["identity_hash", "identity_hash_version"]


class TestTestRecipeIndexes:
    def test_current_recipe_id_partial_index(self) -> None:
        indexes = _indexes_by_name(TestRecipe)
        idx = indexes["idx_test_recipes_recipe_id_current"]
        assert [c.name for c in idx.expressions] == ["recipe_id"]
        where = idx.dialect_options["postgresql"].get("where")
        assert "valid_to IS NULL" in str(where)

    def test_current_claim_test_id_partial_index(self) -> None:
        indexes = _indexes_by_name(TestRecipe)
        idx = indexes["idx_test_recipes_claim_test_id_current"]
        assert [c.name for c in idx.expressions] == ["claim_test_id"]
        where = idx.dialect_options["postgresql"].get("where")
        assert "valid_to IS NULL" in str(where)


class TestTestProvenanceIndexes:
    """Track A added 2 partial indexes beyond SPEC §4.1 literal
    text. The model includes them to match the actual DB
    schema."""

    def test_claim_test_id_partial_index(self) -> None:
        indexes = _indexes_by_name(TestProvenance)
        idx = indexes["idx_test_provenance_claim_test_id"]
        assert [c.name for c in idx.expressions] == ["claim_test_id"]
        where = idx.dialect_options["postgresql"].get("where")
        assert "claim_test_id IS NOT NULL" in str(where)

    def test_recipe_id_partial_index(self) -> None:
        indexes = _indexes_by_name(TestProvenance)
        idx = indexes["idx_test_provenance_recipe_id"]
        assert [c.name for c in idx.expressions] == ["recipe_id"]
        where = idx.dialect_options["postgresql"].get("where")
        assert "recipe_id IS NOT NULL" in str(where)


class TestTestClaimCoverageIndexes:
    def test_entity_lookup_index(self) -> None:
        indexes = _indexes_by_name(TestClaimCoverage)
        idx = indexes["idx_test_claim_coverage_entity"]
        cols = [c.name for c in idx.expressions]
        assert cols == ["entity_id", "entity_type"]


class TestTestRecipeRuntimeStateIndexes:
    def test_outcome_index(self) -> None:
        indexes = _indexes_by_name(TestRecipeRuntimeState)
        idx = indexes["idx_test_recipe_runtime_state_outcome"]
        assert [c.name for c in idx.expressions] == ["last_run_outcome"]

    def test_last_run_at_index(self) -> None:
        indexes = _indexes_by_name(TestRecipeRuntimeState)
        idx = indexes["idx_test_recipe_runtime_state_last_run_at"]
        assert [c.name for c in idx.expressions] == ["last_run_at"]


class TestTestRequirementLinkIndexes:
    def test_external_key_lookup_index(self) -> None:
        indexes = _indexes_by_name(TestRequirementLink)
        idx = indexes["idx_test_requirement_links_external_key"]
        cols = [c.name for c in idx.expressions]
        assert cols == ["external_system", "external_key"]


# ---------------------------------------------------------------------------
# Enums declared with create_type=False
# ---------------------------------------------------------------------------

def _enum_columns(model_class: type) -> Iterable[tuple[str, ENUM]]:
    for col in model_class.__table__.columns:
        if isinstance(col.type, ENUM):
            yield (col.name, col.type)


class TestEnumsCreateTypeFalse:
    """All ENUM-typed columns must declare ``create_type=False``
    so SQLAlchemy doesn't attempt to recreate the PG enum types
    that Track A already created."""

    def test_all_enum_columns_create_type_false(self) -> None:
        seen_enum_names: set[str] = set()
        for cls in (
            TestClaim, TestRecipe, TestProvenance, TestClaimCoverage,
            TestRecipeRuntimeState, TestRequirementLink,
        ):
            for col_name, enum_type in _enum_columns(cls):
                assert enum_type.create_type is False, (
                    f"{cls.__name__}.{col_name} (enum "
                    f"{enum_type.name!r}) has create_type=True; "
                    f"Track A migration already created it"
                )
                seen_enum_names.add(enum_type.name)
        # The substrate-2 bodies use 11 distinct enum types; some
        # are reused across columns (e.g., a status enum on
        # test_claims AND test_recipes is two different enums,
        # but trigger_kind shows up only on test_recipes). Verify
        # all 11 names are reachable via these models.
        assert seen_enum_names == {
            "archetype", "claim_kind", "trigger_kind", "recipe_kind",
            "claim_status", "recipe_status", "provenance_event_kind",
            "reference_kind", "run_outcome", "external_system",
            "link_kind",
        }


# ---------------------------------------------------------------------------
# Logical-FK columns must not carry ForeignKey()
# ---------------------------------------------------------------------------

class TestLogicalFkColumnsHaveNoForeignKey:
    """Per A6 + D-060 §4.7.6: cross-substrate FKs and intra-substrate
    refs to non-unique columns are LOGICAL-only. The Coordinator
    validates at write time; the DB enforces nothing. The
    SQLAlchemy model reflects this by declaring the columns
    without a ForeignKey()."""

    @pytest.mark.parametrize("model_class,col_name", LOGICAL_FK_COLUMNS)
    def test_no_foreign_keys(
        self, model_class: type, col_name: str,
    ) -> None:
        col = model_class.__table__.columns[col_name]
        assert col.foreign_keys == set(), (
            f"{model_class.__name__}.{col_name} carries a "
            f"ForeignKey ({col.foreign_keys!r}); the substrate's "
            f"logical-FK contract requires no DB-level enforcement"
        )


# ---------------------------------------------------------------------------
# JSONB columns are raw JSONB (no TypeDecorator wrapping)
# ---------------------------------------------------------------------------

JSONB_COLUMNS: list[tuple[type, str]] = [
    (TestClaim, "asserted_truth"),
    (TestClaim, "semantic_conditions"),
    (TestRecipe, "causal_initiation"),
    (TestRecipe, "observation_realization"),
    (TestRecipe, "execution_environment"),
    (TestProvenance, "event_data"),
]


class TestJsonbColumnsAreRaw:
    """Body JSONB columns must be raw :class:`JSONB`. Pydantic
    serialize/deserialize is the Coordinator's responsibility per
    D-060 §4.7.1; wrapping JSONB in a TypeDecorator would couple
    the persistence layer to Pydantic in a way the substrate
    intentionally avoids."""

    @pytest.mark.parametrize("model_class,col_name", JSONB_COLUMNS)
    def test_column_type_is_raw_jsonb(
        self, model_class: type, col_name: str,
    ) -> None:
        col = model_class.__table__.columns[col_name]
        # Exact type (no TypeDecorator subclass).
        assert type(col.type) is JSONB, (
            f"{model_class.__name__}.{col_name} type is "
            f"{type(col.type).__name__}; expected raw JSONB"
        )


# ---------------------------------------------------------------------------
# Base.metadata contains all six tables (no accidental extras
# attributable to substrate-2)
# ---------------------------------------------------------------------------

class TestBaseMetadata:
    def test_all_six_tables_in_metadata(self) -> None:
        present = set(Base.metadata.tables.keys())
        missing = EXPECTED_TABLES - present
        assert not missing, (
            f"substrate-2 tables missing from Base.metadata: {missing}"
        )

    def test_table_columns_match_class_columns(self) -> None:
        """Defensive: each class's __table__ is the same object
        reachable from Base.metadata. Guards against accidental
        re-declarations under a different Base."""
        for cls in (
            TestClaim, TestRecipe, TestProvenance, TestClaimCoverage,
            TestRecipeRuntimeState, TestRequirementLink,
        ):
            assert cls.__table__ is Base.metadata.tables[cls.__tablename__]


# ---------------------------------------------------------------------------
# Top-level package re-exports
# ---------------------------------------------------------------------------

class TestTopLevelExports:
    @pytest.mark.parametrize("name", [
        "TestClaim",
        "TestClaimCoverage",
        "TestProvenance",
        "TestRecipe",
        "TestRecipeRuntimeState",
        "TestRequirementLink",
    ])
    def test_exported_from_top_level(self, name: str) -> None:
        import primeqa.test_representation as pkg
        assert name in pkg.__all__
        assert hasattr(pkg, name)
