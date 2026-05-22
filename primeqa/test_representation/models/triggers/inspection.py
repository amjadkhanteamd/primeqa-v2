"""inspection-trigger body (operational layer).

Per D-099 (reopens D-055's five-kind lock). The sixth trigger kind.
Where the five original kinds each model a causal *event* (inbound
push / DML / UI action / elapsed-time predicate / metadata deploy),
an inspection-trigger models the *other* execution-initiation mode:
execution initiated by explicit inspection of extant state, not by
a causal event. An operator, release gate, verifier, or scheduled
inspection pass chooses to inspect the org's current state.

Per D-099.1, the trigger taxonomy classifies execution-initiation
modes, not exclusively causal events: recipes are either
event-reactive (the five causal kinds) or invariant-inspective
(this kind). Per D-099.3, an inspection-trigger recipe asserts the
org's current state at *execution time* (S4 actively re-inspects);
it is not a frozen snapshot of what S3 once observed at grounding.

Minimal body: a distinct initiation mode carries no event payload,
so there is nothing to model beyond the discriminator. Per D-099.2
this is purely additive — ``identity_hash`` excludes operational
layers (identity_hash.py), so the new kind cannot perturb any
existing claim identity or dedup, and the five event kinds are
unchanged.

Per D-099.4 this is NOT a substitute for the behavioral (event)
triggers: behavioral verification, runtime causality, and effect
observation remain the center of gravity. Per D-099.5 a future
bifurcation (invariant vs observational inspection) is anticipated
but deliberately NOT modeled now.
"""
from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict

from primeqa.test_representation.models.common import BodyBase
from primeqa.test_representation.models.registry import register_body


@register_body("inspection-trigger", 1)
class InspectionTriggerBody(BodyBase):
    """The inspection-trigger body shape (v1).

    A distinct initiation mode (inspection of extant state), not
    the absence of a trigger. Minimal by construction — the kind
    discriminator is the whole semantic content; there is no
    causal event to describe (per D-099.2).
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    body_schema_version: Literal[1] = 1
    kind: Literal["inspection-trigger"] = "inspection-trigger"
