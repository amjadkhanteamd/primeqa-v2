"""3A-3 pure merge-gate tests — applicability matrix (fail-closed),
refusal texts, the coordinator event_context default, and drift guards
against the migration CHECKs."""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from primeqa.generation.enumeration import (
    APPLICABLE,
    HUMAN_REVIEW,
    NOT_APPLICABLE,
    STALE_RELEASE_MSG,
    EnumerationRefusal,
    applicability_for,
)
from primeqa.test_representation.coordinator import (
    SemanticTransactionCoordinator,
)

pytestmark = pytest.mark.unit

_MIGRATION = (Path(__file__).parents[2] / ".." / "alembic" / "versions" /
              "tenant" / "20260825_0020_3a3_inventory_claim_sets.py").resolve()


def test_applicability_matrix():
    assert applicability_for("AUTO") == (APPLICABLE, True)
    # AUTO_WITH_ACTION: enumerated, visible, NOT executable until Mode B
    assert applicability_for("AUTO_WITH_ACTION") == (APPLICABLE, False)
    assert applicability_for("HUMAN_WITH_CANDIDATE") == (HUMAN_REVIEW, False)
    assert applicability_for("HUMAN_ONLY") == (HUMAN_REVIEW, False)


def test_applicability_fails_closed_on_unknown_capability():
    with pytest.raises(EnumerationRefusal) as ei:
        applicability_for("TELEPATHIC")
    assert "refused, never inferred" in str(ei.value)


def test_applicability_covers_exactly_the_062_capability_domain():
    # Drift guard: the four capability values in migration 062's CHECK.
    for cap in ("AUTO", "AUTO_WITH_ACTION", "HUMAN_WITH_CANDIDATE",
                "HUMAN_ONLY"):
        applicability_for(cap)          # must not raise


def test_applicability_values_match_the_migration_check():
    ddl = _MIGRATION.read_text(encoding="utf-8")
    for value in (APPLICABLE, NOT_APPLICABLE, HUMAN_REVIEW):
        assert f"'{value}'" in ddl


def test_stale_release_message_names_the_remedy():
    msg = STALE_RELEASE_MSG.format(rule_id="PLM-A11Y-001", recorded=1,
                                   current=2)
    assert "cut a new catalogue release" in msg


def test_promote_event_context_defaults_to_none():
    # Existing callers stay byte-identical: the new parameter is
    # keyword-optional with default None on BOTH promotes.
    for fn in (SemanticTransactionCoordinator.promote_claim_to_approved,
               SemanticTransactionCoordinator.promote_recipe_to_approved):
        param = inspect.signature(fn).parameters["event_context"]
        assert param.default is None
