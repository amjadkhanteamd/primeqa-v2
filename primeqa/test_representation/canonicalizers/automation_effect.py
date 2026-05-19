"""Custom canonicalizer for :class:`AutomationEffectClaimBody`.

Parallel of :mod:`state_transition` per D-059 §6.3.3 + §6.3.4.
:class:`AutomationEffectClaimBody.expected_effect` can be a
:class:`FieldChangeEffect` whose ``changes`` is a
:class:`StateDescriptor` keyed by ``external_id``; this
canonicalizer re-keys that StateDescriptor on ``entity_id`` using
the body's ``affected_fields`` list.

For non-:class:`FieldChangeEffect` variants
(:class:`BlockedOperationEffect`, :class:`SideEffect`) there's no
StateDescriptor to re-key, so the generic walk handles the
``expected_effect`` field directly. The body's other fields walk
generically too.

The ontology check mirrors the state-transition canonicalizer:
every external_id appearing in ``expected_effect.changes.field_values``
MUST appear in ``affected_fields``, otherwise
:class:`OntologyViolationError` fires.
"""
from __future__ import annotations

from typing import Any

from primeqa.test_representation.canonicalization import (
    _canonicalize_value,
)
from primeqa.test_representation.canonicalizers import (
    register_canonicalizer,
)
from primeqa.test_representation.errors import OntologyViolationError
from primeqa.test_representation.models.claims.data_behavior.automation_effect_claim import (
    AutomationEffectClaimBody,
)
from primeqa.test_representation.models.primitives import (
    FieldChangeEffect,
)


@register_canonicalizer(
    AutomationEffectClaimBody,
    1,
    description=(
        "C1-B resolution per D-059 §6.3.3 (parallel of "
        "StateTransitionClaimBody): when expected_effect is a "
        "FieldChangeEffect, re-key its nested StateDescriptor's "
        "field_values on entity_id (from affected_fields) rather "
        "than external_id. Non-FieldChangeEffect variants "
        "(BlockedOperationEffect, SideEffect) walk generically. "
        "Raises OntologyViolationError if a field_values key isn't "
        "declared in affected_fields."
    ),
)
def canonicalize_automation_effect(
    body: AutomationEffectClaimBody,
) -> dict[str, Any]:
    """Custom canonicalizer for :class:`AutomationEffectClaimBody`."""
    body_class_name = type(body).__name__
    body_class = type(body)

    # external_id → entity_id resolution from affected_fields.
    ext_to_entity: dict[str, str] = {
        ref.external_id: str(ref.entity_id) for ref in body.affected_fields
    }

    result: dict[str, Any] = {}
    for field_name, field_info in body_class.model_fields.items():
        if field_name == "body_schema_version":
            continue
        if field_name == "expected_effect":
            effect = getattr(body, field_name)
            result[field_name] = _canonicalize_effect(
                effect, ext_to_entity, body_class_name, field_name,
            )
        else:
            value = getattr(body, field_name)
            result[field_name] = _canonicalize_value(
                value, field_info, body_class_name, field_name,
            )

    return result


def _canonicalize_effect(
    effect: Any,
    ext_to_entity: dict[str, str],
    body_class_name: str,
    path: str,
) -> dict[str, Any]:
    """Canonicalize an :class:`EffectDescriptor` instance.

    For :class:`FieldChangeEffect`, re-key the inner
    StateDescriptor's ``field_values`` dict on ``entity_id`` via
    ``ext_to_entity``. Other effect kinds fall back to the
    generic walk."""
    if not isinstance(effect, FieldChangeEffect):
        # Non-field-change effects (BlockedOperationEffect /
        # SideEffect) carry no entity-id-keyed dict; the generic
        # walk handles them.
        return _canonicalize_value(effect, None, body_class_name, path)

    # FieldChangeEffect: rebuild the nested StateDescriptor's
    # field_values dict keyed by entity_id.
    re_keyed: dict[str, Any] = {}
    for external_id, expected_value in effect.changes.field_values.items():
        if external_id not in ext_to_entity:
            raise OntologyViolationError(
                f"{body_class_name}.{path}.changes.field_values "
                f"references field external_id {external_id!r} not "
                f"declared in affected_fields; ontology requires "
                f"every effect-touched field to appear in "
                f"affected_fields per D-058 §5.4",
                field_path=(
                    f"{path}.changes.field_values.{external_id}"
                ),
                referent=body_class_name,
            )
        entity_id = ext_to_entity[external_id]
        re_keyed[entity_id] = _canonicalize_value(
            expected_value,
            None,
            body_class_name,
            f"{path}.changes.field_values.{external_id}",
        )

    sorted_field_values = {
        k: re_keyed[k] for k in sorted(re_keyed.keys())
    }
    return {
        "kind": effect.kind,
        "changes": {"field_values": sorted_field_values},
    }
