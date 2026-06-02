"""Taxonomy contract drift-guard (D-121, Phase 1).

S3 (generation) and S4 (execution) breadth depend on Substrate-2's claim /
recipe / trigger taxonomies being complete and stable — the kinds are
*parameters* to `write_claim` / `write_recipe` / `select_recipe_for_execution`,
not per-kind code. These assertions pin the exact kind sets so a future edit
that drops, renames, or silently adds a kind the breadth contract relies on
fails loud HERE, rather than breaking generation/execution downstream.

Pure introspection of the SQLAlchemy ENUM types — no DB.
"""
from __future__ import annotations

from primeqa.test_representation.models_db import (
    CLAIM_KIND_ENUM,
    RECIPE_KIND_ENUM,
    TRIGGER_KIND_ENUM,
)

# The 16 claim-kinds S3 emits across all 5 archetypes (the Phase-2 breadth set).
_CLAIM_KINDS = {
    "value-claim", "state-transition-claim", "automation-effect-claim",
    "prohibition-claim", "existence-claim", "property-claim",
    "metadata-relationship-claim", "capability-claim", "sharing-rule-claim",
    "element-state-claim", "navigation-claim", "layout-claim",
    "platform-event-claim", "outbound-message-claim", "callout-claim",
    "inbound-effect-claim",
}
# The 5 recipe verticals S4 executes.
_RECIPE_KINDS = {
    "data-recipe", "metadata-recipe", "ui-recipe",
    "event-subscription-recipe", "callout-intercept-recipe",
}
# The 6 trigger kinds.
_TRIGGER_KINDS = {
    "inbound-trigger", "data-mutation-trigger", "ui-trigger",
    "time-trigger", "configuration-trigger", "inspection-trigger",
}


def test_claim_kind_taxonomy_is_the_16_breadth_kinds():
    assert len(_CLAIM_KINDS) == 16          # guard the expected set itself
    assert set(CLAIM_KIND_ENUM.enums) == _CLAIM_KINDS


def test_recipe_kind_taxonomy_is_the_5_verticals():
    assert len(_RECIPE_KINDS) == 5
    assert set(RECIPE_KIND_ENUM.enums) == _RECIPE_KINDS


def test_trigger_kind_taxonomy_is_the_6_kinds():
    assert len(_TRIGGER_KINDS) == 6
    assert set(TRIGGER_KIND_ENUM.enums) == _TRIGGER_KINDS
