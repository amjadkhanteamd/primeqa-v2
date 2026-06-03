"""S8 field-value-validity leg (D-140) — do a recipe payload's field values still
exist in the current org?

The third leg of the S8 grounding-validity predicate (SPEC §2). It closes a
structural blind spot of the recipe-grounding leg (D-113): ``formula.evaluate``
returns ``True`` on a *removed* picklist value (the formula string-compares
fine), so recipe-grounding reports a false ``intact`` for a value Salesforce
would now reject for being **invalid**, not for **violating the rule**. This leg
asks the orthogonal question — does each payload picklist value still exist in
the field's current active value set?

**Realized (D-140).** Picklist *values* are synced — the production adapter
resolves the Field, follows ``field_details.picklist_value_set_entity_id``, and
reads ``get_picklist_values`` (the active ``value_api_name``s). The leg itself is
pure: it asks its :class:`PicklistReader` port ``active_values(object, field)``
per payload key and checks membership.

**Parallel-siblings (D-112).** S8 reads through its OWN port; the production
adapter lands with the composition (slice 3). No S6 import.

Faculty-first / produce-only. Object-level / behavioral-negative-only v1 (the
negative create step's ``field_values``), the same scope as recipe-grounding.
Multi-select (semicolon-delimited) picklist values are a v1 simplification —
single-value membership first; the split is a tracked follow-up.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional, Protocol

from primeqa.evolution.recipe_grounding import _negative_create_step
from primeqa.test_representation.models.recipes.data_recipe import DataRecipeBody

FieldValueVerdict = Literal["intact", "broken"]


@dataclass(frozen=True)
class FieldValueGroundingResult:
    """The field-value-validity verdict. ``reason`` (``"picklist_value_removed"``)
    and ``invalid`` are set only on ``broken`` and are **load-bearing** —
    ``invalid`` carries the ``(field_api, value)`` pairs whose value no longer
    exists in the field's active value set."""

    verdict: FieldValueVerdict
    reason: Optional[str] = None
    invalid: tuple[tuple[str, Any], ...] = ()


class PicklistReader(Protocol):
    """Read-port for a field's current accepted picklist values. Returns the
    active ``value_api_name``s as a frozenset, or **None when the field is not a
    constrained picklist** (the leg then skips it — non-picklist values are not
    this leg's concern). The production adapter (Field → set → values) lands in
    slice 3; tests inject a stub. S8's OWN port (parallel-siblings, D-112)."""

    def active_values(
        self, object_external_id: str, field_api: str,
    ) -> Optional[frozenset]:
        ...


def field_value_grounding_validity(
    payload: dict[str, Any], object_external_id: str, *, s1: PicklistReader,
) -> FieldValueGroundingResult:
    """Do all of ``payload``'s picklist field values still exist on
    ``object_external_id``?

    Per payload key: a ``None`` value (no membership concern) and a non-picklist
    field (the port returns ``None``) are skipped; a present value absent from the
    field's active set is collected. ``broken`` / ``picklist_value_removed`` iff
    any value is invalid (the ``invalid`` tuple names which), else ``intact``."""
    invalid: list[tuple[str, Any]] = []
    for field_api, value in payload.items():
        if value is None:
            continue
        active = s1.active_values(object_external_id, field_api)
        if active is None:
            continue                          # not a constrained picklist — skip
        if value not in active:
            invalid.append((field_api, value))
    if invalid:
        return FieldValueGroundingResult(
            "broken", reason="picklist_value_removed", invalid=tuple(invalid))
    return FieldValueGroundingResult("intact")


def field_value_grounding_validity_for_recipe(
    recipe: DataRecipeBody, *, s1: PicklistReader,
) -> FieldValueGroundingResult:
    """Adapter: extract the behavioral-negative create step's stored payload +
    target object from a :class:`DataRecipeBody` and check its field values.

    Reuses ``recipe_grounding._negative_create_step`` (a private intra-package
    import; shared-helper extraction is tracked adjacent work). Raises
    ``ValueError`` when the recipe carries no behavioral-negative create step —
    the leg is defined for behavioral negatives."""
    step = _negative_create_step(recipe)
    if step is None:
        raise ValueError(
            "recipe has no behavioral-negative create step (expect_rejection)")
    return field_value_grounding_validity(
        step.field_values, step.target_object.external_id, s1=s1)
