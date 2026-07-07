"""2026-07-07 incident guard: a real production org ("Prod1") saved with the
default is_production=False let record-writing runs dispatch at production.

EnvironmentService now (a) refuses to save is_production=False when the save
looks like production (prod-ish name, env_type='production', or the linked
connection's declared org_type='production') unless the caller explicitly
passes confirm_not_production=True, and (b) writes an activity_log row for
every is_production change / explicit not-production confirmation.

Pure unit tests (fake repos), no DB/network. The activity write is captured by
shadowing the service's _log_flag_activity helper.
"""
from types import SimpleNamespace

import pytest

from primeqa.core.service import (
    EnvironmentService,
    PROD_NAME_RX,
    ProductionConfirmationRequired,
    production_signals,
)

SF_URL = "https://acme.my.salesforce.com"


def _env(**overrides):
    base = dict(
        id=1, tenant_id=1, name="Dev Sandbox", env_type="sandbox",
        sf_instance_url=SF_URL, sf_api_version="59.0",
        execution_policy="full", capture_mode="smart", max_execution_slots=2,
        cleanup_mandatory=False, is_production=False, is_active=True,
        created_at=None, updated_at=None, created_by=7,
        connection_id=None, jira_connection_id=None, llm_connection_id=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeEnvRepo:
    db = None

    def __init__(self, env=None):
        self.env = env
        self.created_kwargs = None

    def create_environment(self, tenant_id, name, env_type, sf_instance_url,
                           sf_api_version, **kwargs):
        self.created_kwargs = dict(kwargs)
        self.env = _env(
            tenant_id=tenant_id, name=name, env_type=env_type,
            sf_instance_url=sf_instance_url, sf_api_version=sf_api_version,
            is_production=bool(kwargs.get("is_production", False)),
            cleanup_mandatory=bool(kwargs.get("cleanup_mandatory", False)),
            created_by=kwargs.get("created_by"),
        )
        return self.env

    def get_environment(self, environment_id, tenant_id=None):
        return self.env

    def update_environment(self, environment_id, tenant_id, updates):
        for key, value in updates.items():
            if hasattr(self.env, key):
                setattr(self.env, key, value)
        return self.env


class FakeConnRepo:
    def __init__(self, org_type=None):
        self._org_type = org_type

    def get_connection_decrypted(self, connection_id, tenant_id=None):
        return {"connection_type": "salesforce",
                "config": {"instance_url": SF_URL, "api_version": "59.0",
                           "org_type": self._org_type}}

    def get_connection(self, connection_id, tenant_id=None):
        return SimpleNamespace(connection_type="salesforce",
                               config={"org_type": self._org_type})


def _service(env=None, org_type=None):
    svc = EnvironmentService(FakeEnvRepo(env), FakeConnRepo(org_type))
    svc._activity = []
    svc._log_flag_activity = (
        lambda tenant_id, user_id, action, environment_id, details:
        svc._activity.append((action, user_id, details)))
    return svc


# --- the name pattern -------------------------------------------------------

@pytest.mark.parametrize("name", [
    "Prod1", "PROD_EU", "Production", "production org", "Live EU", "go-live",
    "prod-2 copy",
])
def test_prod_name_rx_matches(name):
    assert PROD_NAME_RX.search(name), name


@pytest.mark.parametrize("name", [
    "product catalog", "Preprod", "Reproduction", "delivery box", "Dev Sandbox",
    "Oliver's org",
])
def test_prod_name_rx_does_not_match(name):
    assert not PROD_NAME_RX.search(name), name


def test_signals_cover_all_three_sources():
    signals = production_signals("Prod1", "production", "production")
    assert len(signals) == 3
    assert production_signals("Dev Sandbox", "sandbox", "sandbox") == []


# --- create ------------------------------------------------------------------

def test_create_prodish_name_without_confirm_refuses():
    svc = _service()
    with pytest.raises(ProductionConfirmationRequired):
        svc.create_environment(1, "Prod1", "sandbox", SF_URL)
    assert svc.env_repo.created_kwargs is None  # nothing persisted


def test_create_prodish_name_with_confirm_saves_and_logs():
    svc = _service()
    env = svc.create_environment(1, "Prod1", "sandbox", SF_URL,
                                 confirm_not_production=True, created_by=7)
    assert env["is_production"] is False
    assert [a for a, _, _ in svc._activity] == ["environment.not_production_confirmed"]
    assert svc._activity[0][1] == 7


def test_create_production_connection_marker_refuses():
    svc = _service(org_type="production")
    with pytest.raises(ProductionConfirmationRequired) as exc:
        svc.create_environment(1, "QA copy", "sandbox", SF_URL, connection_id=5)
    assert "login.salesforce.com" in str(exc.value)


def test_create_flag_on_saves_and_logs():
    svc = _service()
    env = svc.create_environment(1, "Prod1", "sandbox", SF_URL,
                                 is_production=True, created_by=7)
    assert env["is_production"] is True
    assert svc.env_repo.created_kwargs["is_production"] is True
    assert [a for a, _, _ in svc._activity] == ["environment.is_production_set"]


def test_create_env_type_production_defaults_flag_on():
    svc = _service()
    env = svc.create_environment(1, "Acme main org", "production", SF_URL)
    assert env["is_production"] is True
    assert svc.env_repo.created_kwargs["cleanup_mandatory"] is True


def test_create_env_type_production_explicit_flag_off_refuses():
    svc = _service()
    with pytest.raises(ProductionConfirmationRequired):
        svc.create_environment(1, "Acme main org", "production", SF_URL,
                               is_production=False)


def test_create_innocuous_sandbox_no_guard_no_log():
    svc = _service()
    env = svc.create_environment(1, "Dev Sandbox", "sandbox", SF_URL)
    assert env["is_production"] is False
    assert svc._activity == []


# --- update ------------------------------------------------------------------

def test_update_flag_flip_on_logs_change():
    svc = _service(env=_env())
    env = svc.update_environment(1, 1, {"is_production": True}, actor_user_id=9)
    assert env["is_production"] is True
    action, user_id, details = svc._activity[0]
    assert action == "environment.is_production_changed"
    assert user_id == 9
    assert details["from"] is False and details["to"] is True


def test_update_rename_to_prodish_without_confirm_refuses():
    svc = _service(env=_env())
    with pytest.raises(ProductionConfirmationRequired):
        svc.update_environment(1, 1, {"name": "Prod1"})
    assert svc.env_repo.env.name == "Dev Sandbox"  # unchanged


def test_update_rename_to_prodish_with_confirm_saves_and_logs():
    svc = _service(env=_env())
    env = svc.update_environment(
        1, 1, {"name": "Prod1", "confirm_not_production": True}, actor_user_id=9)
    assert env["name"] == "Prod1" and env["is_production"] is False
    assert [a for a, _, _ in svc._activity] == ["environment.not_production_confirmed"]


def test_update_untouched_guarded_fields_no_guard():
    # Partial update that touches neither the flag nor name/env_type must not
    # nag, even though the saved name already looks prod-ish.
    svc = _service(env=_env(name="Prod1"))
    env = svc.update_environment(1, 1, {"execution_policy": "read_only"})
    assert env["execution_policy"] == "read_only"
    assert svc._activity == []


def test_update_flag_off_with_signals_requires_confirm():
    svc = _service(env=_env(name="Prod1", is_production=True), org_type="production")
    with pytest.raises(ProductionConfirmationRequired):
        svc.update_environment(1, 1, {"is_production": False})
    env = svc.update_environment(
        1, 1, {"is_production": False, "confirm_not_production": True},
        actor_user_id=9)
    assert env["is_production"] is False
    action, _, details = svc._activity[0]
    assert action == "environment.is_production_changed"
    assert details["from"] is True and details["to"] is False
    assert details["confirmed_not_production"] is True


def test_update_flag_off_without_signals_allowed_and_logged():
    svc = _service(env=_env(is_production=True))
    env = svc.update_environment(1, 1, {"is_production": False})
    assert env["is_production"] is False
    assert [a for a, _, _ in svc._activity] == ["environment.is_production_changed"]


def test_update_connection_marker_reaches_guard():
    # Flag explicitly saved off + innocuous name/type, but the env's linked
    # connection is a declared production org → guard trips.
    svc = _service(env=_env(connection_id=5), org_type="production")
    with pytest.raises(ProductionConfirmationRequired):
        svc.update_environment(1, 1, {"is_production": False})
