"""configuration-trigger body (operational layer, model plane).

Per SPEC §3 + D-055. A configuration-trigger represents a metadata
deploy as causal initiation — flipping an activation flag,
changing a metadata property, creating/deleting a metadata entity,
or granting permission. Distinct from the four runtime-plane
triggers: configuration-triggers mutate the *org model itself*,
not records or runtime behavior.

Per D-055: configuration-triggers carry test-runtime risk (can
break unrelated tests by changing shared rules), require
shared-org coordination, and are architecturally adjacent to S8
(Evolution) work. They are operational at the layer level — refs
default to logical — but the change_description sub-union is
strongly typed because the four configuration-change shapes are
structurally distinct.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from primeqa.test_representation.models.common import BodyBase
from primeqa.test_representation.models.references import OperationalRef
from primeqa.test_representation.models.registry import register_body


# ---------------------------------------------------------------------------
# ConfigurationChange — discriminated union of 4 shape variants
# ---------------------------------------------------------------------------

class ActivationChange(BaseModel):
    """Activate or deactivate a configuration artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["activation"] = "activation"
    new_state: Literal["active", "inactive"]


class PropertyChange(BaseModel):
    """Change a named property on a configuration artifact.

    ``property_name`` is a free-form string at v1; the Coordinator
    enforces that the property is meaningful for the
    ``affected_entity`` type. ``new_value`` is ``Any`` because
    property value types span string / int / bool / nested struct
    depending on the metadata type.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["property"] = "property"
    property_name: str
    new_value: Any


class EntityLifecycleChange(BaseModel):
    """Create or delete a metadata entity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["entity_lifecycle"] = "entity_lifecycle"
    lifecycle_event: Literal["created", "deleted"]


class PermissionGrantChange(BaseModel):
    """Grant a permission on a subject (Profile / Permission Set /
    User).

    ``permission_value`` is a loose dict at v1 because permission
    shapes span CRUDS flags, FLS read/write pairs, named entity
    grants, etc. Structured per-permission-type representation
    deferred to a future body schema version.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["permission_grant"] = "permission_grant"
    subject_type: Literal["profile", "permission_set", "user"]
    permission_value: dict[str, Any]


ConfigurationChange = Annotated[
    Union[
        ActivationChange,
        PropertyChange,
        EntityLifecycleChange,
        PermissionGrantChange,
    ],
    Field(discriminator="kind"),
]
"""Discriminated union over the four configuration-change shape
variants. Pydantic dispatches by the ``kind`` literal."""


# ---------------------------------------------------------------------------
# ConfigurationTriggerBody
# ---------------------------------------------------------------------------

@register_body("configuration-trigger", 1)
class ConfigurationTriggerBody(BodyBase):
    """The configuration-trigger body shape (v1).

    Note on the deploy_target / change_description relationship:
    the two fields are independent at the schema level — a
    cross-field validator coupling specific deploy_target values
    to specific change_description kinds is intentionally NOT
    added at v1. The substrate models the structural shape; the
    Coordinator (later track) enforces semantic consistency where
    needed. This keeps the schema permissive of edge cases that
    arise in practice (e.g., a permission grant deployment that
    also flips an activation flag, classified loosely under
    ``permission_grant`` as the dominant deploy target).
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    body_schema_version: Literal[1] = 1
    kind: Literal["configuration-trigger"] = "configuration-trigger"

    deploy_target: Literal[
        "activation_flag",
        "property_change",
        "entity_create_or_delete",
        "permission_grant",
    ]
    """High-level categorisation of what's being deployed."""

    affected_entity: OperationalRef
    """The S1 entity the deploy targets. Required because every
    configuration-trigger has a specific deploy target;
    operational-layer ref."""

    change_description: ConfigurationChange
    """The structured change shape. Discriminated by ``kind``
    over the four variants."""
