"""Tests for :class:`ExecutionEnvironmentBody` and the three
assumption shapes.

Per SPEC §3 (capability assumptions). Covers:

  - Each assumption shape's discriminator values.
  - Body constructs with optional org_kind / edition; with each of
    the assumption lists empty or populated.
  - Universal body keys + registry registration.
  - Round-trip preserves nested assumption types.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from primeqa.test_representation.models.environment import (
    AuthAssumption,
    ExecutionEnvironmentBody,
    FeatureAssumption,
    InfrastructureAssumption,
)
from primeqa.test_representation.models.registry import (
    default_registry,
    get_body_model,
)


# ---------------------------------------------------------------------------
# Assumption shapes
# ---------------------------------------------------------------------------

class TestFeatureAssumption:
    def test_minimal(self) -> None:
        fa = FeatureAssumption(feature_name="Person Accounts", required=True)
        assert fa.required is True
        assert fa.description is None

    def test_with_description(self) -> None:
        fa = FeatureAssumption(
            feature_name="CDC", required=False,
            description="Change Data Capture enabled for Account",
        )
        assert fa.description == "Change Data Capture enabled for Account"

    def test_frozen(self) -> None:
        fa = FeatureAssumption(feature_name="x", required=True)
        with pytest.raises(ValidationError):
            fa.required = False  # type: ignore[misc]

    def test_rejects_extras(self) -> None:
        with pytest.raises(ValidationError):
            FeatureAssumption(  # type: ignore[call-arg]
                feature_name="x", required=True, surprise=True,
            )


class TestAuthAssumption:
    @pytest.mark.parametrize("auth_kind", [
        "data_api_user", "metadata_api_user", "ui_session",
        "named_credential", "event_subscription_credential",
    ])
    def test_all_kinds_accepted(self, auth_kind: str) -> None:
        a = AuthAssumption(auth_kind=auth_kind)  # type: ignore[arg-type]
        assert a.auth_kind == auth_kind

    def test_unknown_kind_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AuthAssumption(auth_kind="oauth_token")  # type: ignore[arg-type]

    def test_details_optional(self) -> None:
        a = AuthAssumption(auth_kind="ui_session")
        assert a.details is None


class TestInfrastructureAssumption:
    @pytest.mark.parametrize("infra_kind", [
        "mock_endpoint", "browser",
        "email_gateway_simulator", "external_system_credential",
    ])
    def test_all_kinds_accepted(self, infra_kind: str) -> None:
        a = InfrastructureAssumption(
            infra_kind=infra_kind,  # type: ignore[arg-type]
        )
        assert a.infra_kind == infra_kind

    def test_unknown_kind_rejected(self) -> None:
        with pytest.raises(ValidationError):
            InfrastructureAssumption(
                infra_kind="kubernetes_cluster",  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# ExecutionEnvironmentBody
# ---------------------------------------------------------------------------

class TestExecutionEnvironmentBody:
    def test_empty_body_is_valid(self) -> None:
        body = ExecutionEnvironmentBody()
        assert body.body_schema_version == 1
        assert body.kind == "execution_environment"
        assert body.org_kind is None
        assert body.salesforce_edition is None
        assert body.feature_assumptions == []

    @pytest.mark.parametrize("org_kind", [
        "sandbox", "production", "scratch_org", "dev_hub",
    ])
    def test_all_org_kinds_accepted(self, org_kind: str) -> None:
        body = ExecutionEnvironmentBody(
            org_kind=org_kind,  # type: ignore[arg-type]
        )
        assert body.org_kind == org_kind

    @pytest.mark.parametrize("edition", [
        "enterprise", "unlimited", "developer", "performance",
    ])
    def test_all_editions_accepted(self, edition: str) -> None:
        body = ExecutionEnvironmentBody(
            salesforce_edition=edition,  # type: ignore[arg-type]
        )
        assert body.salesforce_edition == edition

    def test_unknown_org_kind_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ExecutionEnvironmentBody(
                org_kind="qa_box",  # type: ignore[arg-type]
            )

    def test_full_assumption_set_round_trip(self) -> None:
        body = ExecutionEnvironmentBody(
            org_kind="sandbox",
            salesforce_edition="enterprise",
            feature_assumptions=[
                FeatureAssumption(feature_name="CDC", required=True),
                FeatureAssumption(
                    feature_name="Person Accounts", required=False,
                    description="optional",
                ),
            ],
            auth_assumptions=[
                AuthAssumption(
                    auth_kind="data_api_user", details="integration_user",
                ),
                AuthAssumption(auth_kind="ui_session"),
            ],
            infrastructure_assumptions=[
                InfrastructureAssumption(
                    infra_kind="browser", details="playwright chromium",
                ),
                InfrastructureAssumption(infra_kind="mock_endpoint"),
            ],
        )
        roundtrip = ExecutionEnvironmentBody.model_validate(
            body.model_dump(),
        )
        assert roundtrip == body
        assert len(roundtrip.feature_assumptions) == 2
        assert isinstance(
            roundtrip.feature_assumptions[0], FeatureAssumption,
        )

    def test_universal_body_keys_present(self) -> None:
        body = ExecutionEnvironmentBody()
        dumped = body.model_dump()
        assert dumped["body_schema_version"] == 1
        assert dumped["kind"] == "execution_environment"

    def test_kind_locked(self) -> None:
        with pytest.raises(ValidationError):
            ExecutionEnvironmentBody(
                kind="data-recipe",  # type: ignore[arg-type]
            )

    def test_rejects_extras(self) -> None:
        with pytest.raises(ValidationError):
            ExecutionEnvironmentBody(  # type: ignore[call-arg]
                surprise=True,
            )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestExecutionEnvironmentRegistration:
    def test_registered_in_default_registry(self) -> None:
        assert ("execution_environment", 1) in default_registry

    def test_get_body_model_returns_class(self) -> None:
        assert get_body_model(
            "execution_environment", 1,
        ) is ExecutionEnvironmentBody
