"""Data-behavior archetype claim bodies (Track B-β).

Per SPEC §3 + D-053 (+ D-305). Five claim-kinds:

  - :class:`ValueClaimBody` — "this field has this value"
  - :class:`StateTransitionClaimBody` — "this record moves from
    state A to state B when triggered"
  - :class:`AutomationEffectClaimBody` — "this automation produces
    this effect when triggered"
  - :class:`ProhibitionClaimBody` — "the platform rejects this
    operation against this target"
  - :class:`AcceptanceClaimBody` — "the platform ACCEPTS this
    operation under this business state" (the prohibition's
    mirror, D-305)

Importing this package triggers ``@register_body`` decoration on
each of the four body modules (the imports below). Consumers who
need a single specific body can import the submodule directly to
avoid pulling in the other three, but the typical pattern is to
import this package so the
:data:`DataBehaviorClaimBody` discriminated union is constructed
with all four variants known.
"""
from __future__ import annotations

from typing import Annotated, Union

from pydantic import Field

from primeqa.test_representation.models.claims.data_behavior.acceptance_claim import (
    AcceptanceClaimBody,
)
from primeqa.test_representation.models.claims.data_behavior.automation_effect_claim import (
    AutomationEffectClaimBody,
)
from primeqa.test_representation.models.claims.data_behavior.prohibition_claim import (
    ProhibitionClaimBody,
)
from primeqa.test_representation.models.claims.data_behavior.state_transition_claim import (
    StateTransitionClaimBody,
)
from primeqa.test_representation.models.claims.data_behavior.value_claim import (
    ValueClaimBody,
)


__all__ = [
    "AcceptanceClaimBody",
    "AutomationEffectClaimBody",
    "DataBehaviorClaimBody",
    "ProhibitionClaimBody",
    "StateTransitionClaimBody",
    "ValueClaimBody",
]


DataBehaviorClaimBody = Annotated[
    Union[
        ValueClaimBody,
        StateTransitionClaimBody,
        AutomationEffectClaimBody,
        ProhibitionClaimBody,
        AcceptanceClaimBody,
    ],
    Field(discriminator="kind"),
]
"""On-the-wire discriminated union over the four data-behavior
claim bodies.

Per SPEC §4.7.2: bodies share the universal ``kind`` discriminator
declared on :class:`BodyBase`. Pydantic dispatches on the literal
value: ``"value-claim"`` → :class:`ValueClaimBody`,
``"state-transition-claim"`` → :class:`StateTransitionClaimBody`,
``"automation-effect-claim"`` → :class:`AutomationEffectClaimBody`,
``"prohibition-claim"`` → :class:`ProhibitionClaimBody`,
``"acceptance-claim"`` → :class:`AcceptanceClaimBody`.

The five archetype-level unions (data_behavior, configuration,
permission, ui, integration) are intentionally NOT combined into a
single top-level union. Archetype is itself a discriminator column
on the substrate's storage envelope (per D-052 + SPEC §4.3); the
read path resolves to an archetype-scoped union before Pydantic
dispatches by ``kind``. This keeps the union sizes manageable and
the dispatch unambiguous (kind values are unique across
archetypes, but the substrate models archetype separately for
operational reasons)."""
