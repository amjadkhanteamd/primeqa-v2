"""Custom canonicalizer for :class:`StateTransitionClaimBody`.

Implements the **C1-B resolution** per D-059 §6.3.3 + §6.3.4:
re-key the body's ``from_state.field_values`` and
``to_state.field_values`` dicts on the field's ``entity_id``
rather than its ``external_id`` before hashing.

Rationale (from D-059 §6.3.3):
  - ``entity_id`` is the canonical identity of an S1 entity.
  - ``external_id`` is informational — it's the human-readable
    Salesforce API name, which can change (objects renamed,
    fields relabeled) without the entity being structurally
    different.

The author-friendly form of a StateDescriptor's ``field_values``
keys by ``external_id`` (a string like ``"Opportunity.StageName"``)
because that's what generators and humans naturally write. This
canonicalizer translates the author-friendly form to the
canonical entity-id-keyed form at hash time, so:

  1. Renaming a field's ``external_id`` (without changing
     ``entity_id``) preserves the body's hash — the C1-B
     **drift-preservation** invariant.
  2. Two bodies with the same ``entity_id`` set in
     ``subject_fields`` but different ``external_id`` naming
     hash identically.

Ontology check (per D-058 §5.7): every key in
``from_state.field_values`` / ``to_state.field_values`` MUST
correspond to an entity declared in ``subject_fields``. If a
state dict references a field external_id not present in
``subject_fields``, the canonicalizer raises
:class:`OntologyViolationError` — the body is malformed and
should not be hashed.
"""
from __future__ import annotations

from typing import Any

from primeqa.test_representation.canonicalization import (
    _generic_canonicalize_body,
    _canonicalize_value,
    canonical_serialize,
)
from primeqa.test_representation.canonicalizers import (
    register_canonicalizer,
)
from primeqa.test_representation.errors import OntologyViolationError
from primeqa.test_representation.models.claims.data_behavior.state_transition_claim import (
    StateTransitionClaimBody,
)
from primeqa.test_representation.models.primitives import StateDescriptor


@register_canonicalizer(
    StateTransitionClaimBody,
    1,
    description=(
        "C1-B resolution per D-059 §6.3.3: re-key from_state and "
        "to_state field_values on entity_id (from subject_fields) "
        "rather than external_id. External_id renames preserve "
        "the body's hash; the entity's canonical identity drives "
        "equality. Raises OntologyViolationError if a state dict "
        "references a field external_id absent from subject_fields."
    ),
)
def canonicalize_state_transition(
    body: StateTransitionClaimBody,
) -> dict[str, Any]:
    """Custom canonicalizer for :class:`StateTransitionClaimBody`."""
    body_class_name = type(body).__name__

    # Step 1: build the external_id → entity_id map from
    # subject_fields. Authors declare every state-bearing field
    # here so the canonicalizer (and coverage extraction per
    # D-058 §5.4) can resolve them by identity.
    ext_to_entity: dict[str, str] = {}
    for ref in body.subject_fields:
        ext_to_entity[ref.external_id] = str(ref.entity_id)

    # Step 2: produce the body's canonical form. Most fields walk
    # generically; ``from_state`` and ``to_state`` get re-keyed.
    result: dict[str, Any] = {}
    body_class = type(body)

    for field_name, field_info in body_class.model_fields.items():
        if field_name == "body_schema_version":
            continue
        if field_name in ("from_state", "to_state"):
            state_value = getattr(body, field_name)
            result[field_name] = _canonicalize_state_descriptor(
                state_value,
                ext_to_entity,
                body_class_name,
                f"{field_name}",
            )
        else:
            value = getattr(body, field_name)
            result[field_name] = _canonicalize_value(
                value, field_info, body_class_name, field_name,
            )

    return result


def _canonicalize_state_descriptor(
    state: StateDescriptor,
    ext_to_entity: dict[str, str],
    body_class_name: str,
    path: str,
) -> dict[str, Any]:
    """Canonicalize a :class:`StateDescriptor` by re-keying its
    ``field_values`` dict on ``entity_id`` resolved via
    ``ext_to_entity``.

    The output dict's keys are entity-id strings sorted
    alphabetically; values are canonicalized recursively via the
    generic walk.
    """
    re_keyed: dict[str, Any] = {}
    field_values = state.field_values

    for external_id, expected_value in field_values.items():
        if external_id not in ext_to_entity:
            raise OntologyViolationError(
                f"{body_class_name}.{path}.field_values references "
                f"field external_id {external_id!r} not declared in "
                f"subject_fields; ontology requires every "
                f"state-bearing field to appear in subject_fields "
                f"per D-058 §5.4",
                field_path=f"{path}.field_values.{external_id}",
                referent=body_class_name,
            )
        entity_id = ext_to_entity[external_id]
        # The expected_value is a discriminated union (ExpectedValue);
        # canonicalize it generically via _canonicalize_value with
        # field_info=None — its concrete type is what matters and
        # the generic walk handles BaseModel instances.
        re_keyed[entity_id] = _canonicalize_value(
            expected_value,
            None,
            body_class_name,
            f"{path}.field_values.{external_id}",
        )

    # Build the output StateDescriptor-shaped dict: the
    # field_values dict is the entity-id-keyed version, sorted
    # alphabetically by the new key.
    sorted_field_values = {
        k: re_keyed[k] for k in sorted(re_keyed.keys())
    }
    return {"field_values": sorted_field_values}
