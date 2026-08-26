"""Pre-enqueue executability gate (D-223; G-1 from the matrix audit).

S3's ``EMITTABLE`` declares what can be *authored*; S4's runtime fail-louds
know what can be *executed*. Between them, an approved-but-unexecutable claim
(today: capability-claim / layout-claim, whose ``GRANTS_*_ACCESS`` /
``INCLUDES_FIELD`` captures have no translator entry) enqueues cleanly and
dies later as dogfood noise. This module moves that refusal to the enqueue
door.

**No shadow registry.** The check dry-runs S4's own pure machinery — the
recipe→plan bridge plus (for metadata plans) the per-read translator. The
single source of truth is the code that will execute the recipe; the gate
cannot drift from reality by construction. The static
``EXPECTED_EXECUTABILITY`` map lives in the drift-guard TEST, not here.
"""
from __future__ import annotations

from uuid import UUID

from primeqa.execution_engine.bridge import (
    build_data_recipe_plan,
    build_metadata_inspection_plan,
)
from primeqa.execution_engine.errors import (
    ExecutionEngineError,
    PlanTranslationError,
    UnexecutableClaimError,
)
from primeqa.execution_engine.plan import PlannedRead
from primeqa.execution_engine.translator import translate_read

# The select_recipe_for_execution status commitment (coordinator) — the gate
# evaluates exactly the recipes the runtime would consider.
_ELIGIBLE_STATUSES = ("active", "approved")


def check_recipe_executability(recipe) -> None:
    """Raise the S4 shape error ``recipe`` would die with at run time.

    Pure: dry-runs the bridge projection and, for metadata plans, the per-read
    translator (the two layers whose fail-louds define executability). A clean
    return means S4's *current* machinery can plan and translate the recipe —
    runtime outcomes (credentials, org state, predicates) stay runtime-owned.
    """
    if recipe.recipe_kind == "metadata-recipe":
        plan = build_metadata_inspection_plan(recipe)
        for step in plan.steps:
            if isinstance(step, PlannedRead):
                translate_read(step)
    elif recipe.recipe_kind == "data-recipe":
        build_data_recipe_plan(recipe)
    elif recipe.recipe_kind == "ui-inspection":
        # Browser-plane kind (LLD 3A-2 §a): executed via the s4_ui_*
        # manifest queue, never by the in-process S4 verticals. Honest
        # refusal here; the browser-plane enqueue path wires in 3A-3.
        raise PlanTranslationError(
            "ui-inspection is a browser-plane kind; no in-process S4 "
            "vertical executes it (dispatch via the UI manifest queue)",
            recipe_id=recipe.recipe_id,
        )
    else:
        raise PlanTranslationError(
            f"no S4 vertical executes recipe_kind={recipe.recipe_kind!r}",
            recipe_id=recipe.recipe_id,
        )


def gate_enqueue(session, test_id: UUID) -> None:
    """The D-223 enqueue gate for one claim.

    Loads the claim's current (``valid_to IS NULL``) recipes in the
    selection-eligible statuses and raises :class:`UnexecutableClaimError`
    when at least one exists and ALL fail :func:`check_recipe_executability`.
    Returns silently when ≥1 recipe passes — and when ZERO status-eligible
    recipes exist: status outcomes are runtime-owned (the run path's
    ``no-eligible-recipe`` result is already first-class), the gate owns
    *shape* only.
    """
    from primeqa.test_representation.coordinator import (
        SemanticTransactionCoordinator,
    )

    coord = SemanticTransactionCoordinator()
    eligible = [r for r in coord.list_active_recipes(session, test_id)
                if r.status in _ELIGIBLE_STATUSES]
    if not eligible:
        return
    reasons = []
    for recipe in eligible:
        try:
            check_recipe_executability(recipe)
            return
        except ExecutionEngineError as exc:
            reasons.append(f"{recipe.recipe_id}: {exc}")
    raise UnexecutableClaimError(test_id=test_id, reasons=reasons)


__all__ = ["check_recipe_executability", "gate_enqueue"]
