"""Operational trigger body models (Track B-γ).

Per SPEC §3 + D-055. Five trigger-kinds, locked taxonomy:

  - :class:`InboundTriggerBody` — external system pushes payload
    into Salesforce (runtime plane).
  - :class:`DataMutationTriggerBody` — DML on records inside
    Salesforce (runtime plane).
  - :class:`UITriggerBody` — user-driven UI action (runtime plane).
  - :class:`TimeTriggerBody` — elapsed-time predicate fires
    automation (runtime plane).
  - :class:`ConfigurationTriggerBody` — metadata deploy as causal
    initiation (model plane).

Importing this package triggers ``@register_body`` on each of the
five body modules. Consumers who need only one body can import its
submodule directly to avoid pulling in the others, but typical
usage imports this package so the :data:`TriggerBody` discriminated
union is constructed with all five variants known.

Per D-055 §"Trigger-kind identity nuance": trigger-kind is
operational-by-default — references inside trigger bodies use
:data:`OperationalRef`, not :class:`IdentityBearingRef`. If the
trigger mechanism is *semantically asserted* in a claim, the
Coordinator routes that assertion to the conditions body (which
is identity-bearing); the trigger body itself stays operational.
"""
from __future__ import annotations

from typing import Annotated, Union

from pydantic import Field

from primeqa.test_representation.models.triggers.configuration import (
    ConfigurationTriggerBody,
)
from primeqa.test_representation.models.triggers.data_mutation import (
    DataMutationTriggerBody,
)
from primeqa.test_representation.models.triggers.inbound import (
    InboundTriggerBody,
)
from primeqa.test_representation.models.triggers.time import (
    TimeTriggerBody,
)
from primeqa.test_representation.models.triggers.ui import (
    UITriggerBody,
)


__all__ = [
    "ConfigurationTriggerBody",
    "DataMutationTriggerBody",
    "InboundTriggerBody",
    "TimeTriggerBody",
    "TriggerBody",
    "UITriggerBody",
]


TriggerBody = Annotated[
    Union[
        InboundTriggerBody,
        DataMutationTriggerBody,
        UITriggerBody,
        TimeTriggerBody,
        ConfigurationTriggerBody,
    ],
    Field(discriminator="kind"),
]
"""On-the-wire discriminated union over the five trigger bodies.

Per SPEC §4.7.2: bodies share the universal ``kind`` discriminator
declared on :class:`BodyBase`. Pydantic dispatches by the literal
value: ``"inbound-trigger"`` → :class:`InboundTriggerBody`,
``"data-mutation-trigger"`` → :class:`DataMutationTriggerBody`,
``"ui-trigger"`` → :class:`UITriggerBody`,
``"time-trigger"`` → :class:`TimeTriggerBody`,
``"configuration-trigger"`` → :class:`ConfigurationTriggerBody`.
"""
