"""Substrate-2 claim body models, grouped by archetype.

Per SPEC §3 + D-053 (claim-kind taxonomy, 16 kinds across 5
archetypes). Each archetype has its own subpackage:

  - :mod:`primeqa.test_representation.models.claims.data_behavior`
    (B-β; shipped) — value, state-transition, automation-effect,
    prohibition.
  - Future: ``configuration`` / ``permission`` / ``ui`` /
    ``integration`` arrive in subsequent tracks.

Importing this package triggers ``@register_body`` on every claim
body across every archetype that's shipped to date. Consumers that
need a single archetype can import the archetype subpackage
directly (e.g.,
``from primeqa.test_representation.models.claims.data_behavior
import DataBehaviorClaimBody``); the package-level
:data:`ClaimBody` union is for the cross-archetype dispatch path
the Coordinator uses.

Per the **flat union** decision: ClaimBody is a single
discriminated union over every claim-kind body class, NOT a
nested union of per-archetype unions. The claim-kind discriminator
values are unique across the entire taxonomy per D-053's purity
guardrail, so a flat union dispatches unambiguously. Benefits:
  - Coordinator-side dispatch is one ``TypeAdapter`` call.
  - No reliance on Pydantic's nested-discriminator behavior at
    union-of-unions depth.
  - Per-archetype unions (e.g., :data:`DataBehaviorClaimBody`)
    remain available for code paths that want
    archetype-scoped typing.

Future archetypes extend this flat union; ordering of variants is
not semantically meaningful (the discriminator value picks the
right one).
"""
from __future__ import annotations

from typing import Annotated, Union

from pydantic import Field

# Importing the data_behavior subpackage triggers @register_body
# on each of the four data-behavior claim bodies and exposes the
# archetype-scoped union.
from primeqa.test_representation.models.claims.data_behavior import (
    AutomationEffectClaimBody,
    DataBehaviorClaimBody,
    ProhibitionClaimBody,
    StateTransitionClaimBody,
    ValueClaimBody,
)
# Importing the configuration subpackage triggers @register_body on its
# bodies (the S3 draft-vertical debut archetype, D-098.1).
from primeqa.test_representation.models.claims.configuration import (
    ConfigurationClaimBody,
    ExistenceClaimBody,
    MetadataRelationshipClaimBody,
    PropertyClaimBody,
)


__all__ = [
    # Per-archetype scoped union (kept for archetype-scoped
    # typing in downstream code).
    "DataBehaviorClaimBody",
    "ConfigurationClaimBody",
    # Individual body classes (re-exported for direct typing).
    "AutomationEffectClaimBody",
    "ProhibitionClaimBody",
    "StateTransitionClaimBody",
    "ValueClaimBody",
    "MetadataRelationshipClaimBody",
    "ExistenceClaimBody",
    "PropertyClaimBody",
    # Cross-archetype flat union (the substrate-level
    # dispatch type).
    "ClaimBody",
]


ClaimBody = Annotated[
    Union[
        ValueClaimBody,
        StateTransitionClaimBody,
        AutomationEffectClaimBody,
        ProhibitionClaimBody,
        MetadataRelationshipClaimBody,
        ExistenceClaimBody,
        PropertyClaimBody,
        # Future archetype body classes extend here. Order is
        # not semantically meaningful — Pydantic dispatches by
        # the ``kind`` discriminator value.
    ],
    Field(discriminator="kind"),
]
"""Cross-archetype discriminated union over every shipped claim
body.

Per SPEC §4.7.2 + D-053. Pydantic dispatches by the universal
``kind`` literal declared on :class:`BodyBase`. Every claim-kind
value is unique across the taxonomy (D-053 purity guardrail), so
this flat union is unambiguous.

This is the substrate's primary claim-body dispatch type — the
Coordinator + read path use it to decode persisted claim bodies
without first resolving which archetype the kind belongs to.
"""
