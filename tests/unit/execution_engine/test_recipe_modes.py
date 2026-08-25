"""D6 declared dispatch modes (LLD 3A-2 §a) — pure, merge-gate.

(1) drift-guard: RECIPE_MODES ↔ RECIPE_KIND_ENUM equality BOTH directions;
(2) undeclared kinds are refused with the declared error, never inferred;
(3) _authorize_dispatch: ui-inspection passes as READ_ONLY (read_only env +
non-admin production); data-recipe still requires mutation policy.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from primeqa.core.authz import Tier
from primeqa.execution_engine.errors import PolicyError
from primeqa.execution_engine.modes import (
    MUTATING, READ_ONLY, RECIPE_MODES, mode_for)
from primeqa.execution_engine.run import _authorize_dispatch
from primeqa.test_representation.models_db import RECIPE_KIND_ENUM

pytestmark = pytest.mark.unit


def _recipe(kind):
    return SimpleNamespace(recipe_kind=kind)


def test_drift_guard_table_equals_enum_both_directions():
    assert set(RECIPE_MODES.keys()) == set(RECIPE_KIND_ENUM.enums)


def test_declared_modes_match_the_lld_table():
    assert RECIPE_MODES == {
        "metadata-recipe": READ_ONLY,
        "data-recipe": MUTATING,
        "ui-recipe": MUTATING,
        "event-subscription-recipe": READ_ONLY,
        "callout-intercept-recipe": MUTATING,
        "ui-inspection": READ_ONLY,
    }


def test_undeclared_kind_is_refused_with_the_declared_error():
    with pytest.raises(PolicyError) as ei:
        mode_for("teleport-recipe")
    assert "no declared dispatch mode" in str(ei.value)
    with pytest.raises(PolicyError) as ei2:
        _authorize_dispatch(_recipe("teleport-recipe"),
                            execution_policy="full", is_production=False,
                            caller_tier=int(Tier.ADMIN))
    assert "no declared dispatch mode" in str(ei2.value)


def test_ui_inspection_passes_authorize_as_read_only():
    # read_only env: permitted
    _authorize_dispatch(_recipe("ui-inspection"), execution_policy="read_only",
                        is_production=False, caller_tier=int(Tier.MEMBER))
    # production, non-admin: permitted (read-only inspection bypass)
    _authorize_dispatch(_recipe("ui-inspection"), execution_policy="full",
                        is_production=True, caller_tier=int(Tier.MEMBER))


def test_data_recipe_still_requires_mutation_policy():
    from primeqa.execution_engine.errors import ExecutionEngineError
    with pytest.raises(PolicyError):
        _authorize_dispatch(_recipe("data-recipe"), execution_policy="read_only",
                            is_production=False, caller_tier=int(Tier.ADMIN))
    with pytest.raises(Exception) as ei:   # AuthorizationError family
        _authorize_dispatch(_recipe("data-recipe"), execution_policy="full",
                            is_production=True, caller_tier=int(Tier.MEMBER))
    assert "Admin" in str(ei.value)


def test_metadata_recipe_posture_unchanged():
    _authorize_dispatch(_recipe("metadata-recipe"), execution_policy="read_only",
                        is_production=True, caller_tier=int(Tier.MEMBER))
