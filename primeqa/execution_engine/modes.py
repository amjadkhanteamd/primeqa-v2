"""Declared dispatch modes per recipe kind (D6 / SAD A3; LLD 3A-2 §a).

The read-only/mutating classification at ``_authorize_dispatch`` is an
EXPLICIT per-kind property declared here — never inferred from kind names
(the inference this replaces carried its own docstring warning that a
future metadata-write kind would silently break it). EVERY registered
recipe kind MUST have a row; a kind absent from the table is REFUSED at
dispatch (fail closed), and a drift-guard test pins table ↔ enum equality
in both directions so a kind cannot register without declaring its mode.
"""
from __future__ import annotations

from primeqa.execution_engine.errors import PolicyError

READ_ONLY = "READ_ONLY"
MUTATING = "MUTATING"

# The declared table — grounds per kind in LLD 3A-2 §a:
RECIPE_MODES: dict[str, str] = {
    # the bridge already enforces mode == metadata_read; the declaration
    # makes explicit what the old inference assumed
    "metadata-recipe": READ_ONLY,
    # creates/updates/deletes org records by design
    "data-recipe": MUTATING,
    # Mode B inheritance: ordered click/type steps mutate application state
    "ui-recipe": MUTATING,
    # subscribes/observes platform events; performs no org write
    "event-subscription-recipe": READ_ONLY,
    # conservative: interception alters runtime behaviour; unclear ⇒ MUTATING
    "callout-intercept-recipe": MUTATING,
    # SAD A3 verbatim: ui-inspection = read-only (browser-plane scan)
    "ui-inspection": READ_ONLY,
}


def mode_for(recipe_kind: str) -> str:
    """The declared mode for ``recipe_kind`` — REFUSES undeclared kinds
    (fail closed; declaration is a registration requirement, D6)."""
    mode = RECIPE_MODES.get(recipe_kind)
    if mode is None:
        raise PolicyError(
            f"recipe_kind={recipe_kind!r} has no declared dispatch mode "
            "(RECIPE_MODES, D6/SAD A3) — undeclared kinds are refused, "
            "never inferred")
    return mode
