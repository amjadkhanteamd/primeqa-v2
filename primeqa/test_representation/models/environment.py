"""Execution environment body — operational layer.

Per SPEC §3 (Execution environment as capability assumptions). The
execution-environment body describes the *set of capability
assumptions* a recipe requires from its target runtime — NOT a
specific environment row. Per §3:

  "A UI recipe assumes a browser is available. A run-as permission
  recipe assumes the executor can impersonate users. A CRUD recipe
  assumes write permissions on target objects."

This drives S4's recipe-selection logic: the substrate matches a
recipe's declared capability assumptions against the actual
environment's capabilities, picking recipes that "fit."

Structurally less specific than other bodies because capability
assumptions are inherently varied. Three lists of typed-variant
assumption shapes (feature / auth / infrastructure) capture the
common categorisations seen in practice; richer per-category
representations are deferred to a future body schema version.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from primeqa.test_representation.models.common import BodyBase
from primeqa.test_representation.models.registry import register_body


# ---------------------------------------------------------------------------
# Assumption shapes
# ---------------------------------------------------------------------------

class FeatureAssumption(BaseModel):
    """A Salesforce feature (license, edition feature, enabled
    setting) the recipe assumes is available.

    ``feature_name`` is free-form at v1 (e.g.,
    ``"Person Accounts"``, ``"Advanced Approvals"``,
    ``"CDC enabled for Account"``). ``required=True`` means the
    recipe will not run without it; ``required=False`` means the
    recipe degrades gracefully and the assumption is informational.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    feature_name: str
    required: bool
    description: Optional[str] = None


class AuthAssumption(BaseModel):
    """A credential / impersonation capability the recipe assumes
    is available in the target environment.

    The ``auth_kind`` taxonomy maps directly to the five recipe
    kinds' typical credential needs: data recipes assume
    ``data_api_user``, metadata recipes assume
    ``metadata_api_user``, UI recipes assume ``ui_session``,
    callout-intercept recipes assume named-credential override
    capability, event-subscription recipes assume the relevant
    push/durable-event credential.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    auth_kind: Literal[
        "data_api_user",
        "metadata_api_user",
        "ui_session",
        "named_credential",
        "event_subscription_credential",
    ]
    details: Optional[str] = None
    """Free-form details (specific user alias, named-credential
    label, permission-set name)."""


class InfrastructureAssumption(BaseModel):
    """An out-of-Salesforce capability the recipe assumes is
    available in the environment.

    Examples: a mocked HTTP endpoint for callout interception, a
    browser binary for UI recipes, an SMTP catcher for inbound
    email tests, credentials reaching an external system for
    integration tests.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    infra_kind: Literal[
        "mock_endpoint",
        "browser",
        "email_gateway_simulator",
        "external_system_credential",
    ]
    details: Optional[str] = None


# ---------------------------------------------------------------------------
# ExecutionEnvironmentBody
# ---------------------------------------------------------------------------

@register_body("execution_environment", 1)
class ExecutionEnvironmentBody(BodyBase):
    """The execution-environment body shape (v1).

    Per SPEC §3: this is the recipe's set of capability
    assumptions — what it expects the target environment to
    provide. The substrate matches these against the actual
    environment at run-selection time; non-matches surface as
    "this recipe won't run here" rather than as run-time errors.

    Note on org_kind / salesforce_edition: both are Optional
    because some recipes are environment-agnostic at this layer
    (they assume capabilities, not a specific org shape). When
    specified, they participate in S4's match algorithm.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    body_schema_version: Literal[1] = 1
    kind: Literal["execution_environment"] = "execution_environment"

    org_kind: Optional[Literal[
        "sandbox",
        "production",
        "scratch_org",
        "dev_hub",
    ]] = None
    """If the recipe constrains itself to a class of orgs, the
    constraint. ``None`` means "any org kind"."""

    salesforce_edition: Optional[Literal[
        "enterprise",
        "unlimited",
        "developer",
        "performance",
    ]] = None
    """If the recipe requires a specific edition (e.g., features
    only in Unlimited), the requirement. ``None`` means "any
    edition"."""

    feature_assumptions: list[FeatureAssumption] = []
    auth_assumptions: list[AuthAssumption] = []
    infrastructure_assumptions: list[InfrastructureAssumption] = []
