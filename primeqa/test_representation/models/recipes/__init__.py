"""Recipe body models — observation-realization layer (Track B-δ).

Per SPEC §3 + D-054. Five recipe-kinds, locked taxonomy:

  - :class:`DataRecipeBody` — observes/asserts via REST/SOAP/Bulk DML.
  - :class:`MetadataRecipeBody` — observes/asserts via the
    Metadata or Tooling API (read or write mode).
  - :class:`UIRecipeBody` — observes/asserts by driving the
    Lightning UI.
  - :class:`EventSubscriptionRecipeBody` — observes/asserts via
    durable / ephemeral event subscriptions (platform events,
    outbound messages, CDC).
  - :class:`CalloutInterceptRecipeBody` — observes/asserts by
    standing in as the target of an outbound callout.

Importing this package triggers ``@register_body`` on each of the
five body modules. Consumers who need only one body can import its
submodule directly; typical usage imports this package so the
:data:`ObservationRealizationBody` discriminated union is
constructed with all five variants known.

Per D-055: this layer was renamed "observation realization"
(formerly "execution realization") to better reflect that
recipes observe and assert; they don't execute arbitrary work.
"""
from __future__ import annotations

from typing import Annotated, Union

from pydantic import Field

from primeqa.test_representation.models.recipes.callout_intercept import (
    CalloutInterceptRecipeBody,
)
from primeqa.test_representation.models.recipes.data_recipe import (
    DataRecipeBody,
)
from primeqa.test_representation.models.recipes.event_subscription import (
    EventSubscriptionRecipeBody,
)
from primeqa.test_representation.models.recipes.metadata_recipe import (
    MetadataRecipeBody,
)
from primeqa.test_representation.models.recipes.ui_recipe import (
    UIRecipeBody,
)


__all__ = [
    "CalloutInterceptRecipeBody",
    "DataRecipeBody",
    "EventSubscriptionRecipeBody",
    "MetadataRecipeBody",
    "ObservationRealizationBody",
    "UIRecipeBody",
]


ObservationRealizationBody = Annotated[
    Union[
        DataRecipeBody,
        MetadataRecipeBody,
        UIRecipeBody,
        EventSubscriptionRecipeBody,
        CalloutInterceptRecipeBody,
    ],
    Field(discriminator="kind"),
]
"""On-the-wire discriminated union over the five recipe bodies.

Per SPEC §4.7.2 + D-054. Pydantic dispatches by the universal
``kind`` literal:
  - ``"data-recipe"`` → :class:`DataRecipeBody`
  - ``"metadata-recipe"`` → :class:`MetadataRecipeBody`
  - ``"ui-recipe"`` → :class:`UIRecipeBody`
  - ``"event-subscription-recipe"`` → :class:`EventSubscriptionRecipeBody`
  - ``"callout-intercept-recipe"`` → :class:`CalloutInterceptRecipeBody`
"""
