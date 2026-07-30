"""D-416/D-418/D-421 — run-as identity resolution + two-client teardown.

The load-bearing assertions:
  * the NON-identity path is byte-identical to pre-run-as (same _oauth_token
    call, same client construction);
  * fallback-to-admin is IMPOSSIBLE on the identity path, not merely avoided
    — _oauth_token is never invoked, and every failure RAISES;
  * every JWT failure mode is distinguishable by reason code;
  * teardown uses the admin client while the run used the identity client.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from primeqa.execution_engine import run_as
from primeqa.execution_engine.run_as import (
    APP_REJECTED,
    EXCHANGE_FAILED,
    IDENTITY_INACTIVE,
    IDENTITY_NOT_FOUND,
    NO_SIGNING_KEY,
    NOT_PREAUTHORIZED,
    SIGNATURE_REJECTED,
    RunAsResolutionError,
    mint_run_as_token,
)

# A syntactically-valid throwaway RSA key for signing tests (generated for
# this test file; never used anywhere real).
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption()).decode()

CFG = {"client_id": "3MVG9.test", "jwt_signing_key": _PEM,
       "instance_url": "https://example--primeqa.sandbox.my.salesforce.com",
       "org_type": "sandbox"}


def _sf_response(status, body):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body
    resp.text = str(body)
    return resp


# ---------------------------------------------------------------- failures --

def test_no_signing_key_fails_loud_before_any_network():
    with patch.object(run_as.requests, "post") as post:
        with pytest.raises(RunAsResolutionError) as e:
            mint_run_as_token({**CFG, "jwt_signing_key": ""}, username="u@x")
    assert e.value.reason == NO_SIGNING_KEY
    post.assert_not_called()


@pytest.mark.parametrize("body,expected_reason", [
    ({"error": "invalid_grant",
      "error_description": "user hasn't approved this consumer"},
     NOT_PREAUTHORIZED),
    ({"error": "invalid_grant", "error_description": "invalid assertion"},
     SIGNATURE_REJECTED),
    ({"error": "invalid_grant", "error_description": "audience is invalid"},
     SIGNATURE_REJECTED),
    ({"error": "invalid_client_id", "error_description": "client identifier invalid"},
     APP_REJECTED),
    ({"error": "invalid_app_access", "error_description": "app denied"},
     APP_REJECTED),
    ({"error": "server_error", "error_description": "boom"},
     EXCHANGE_FAILED),
])
def test_org_error_vocabulary_maps_to_distinguishable_reasons(body, expected_reason):
    with patch.object(run_as.requests, "post",
                      return_value=_sf_response(400, body)):
        with pytest.raises(RunAsResolutionError) as e:
            mint_run_as_token(CFG, username="u@x")
    assert e.value.reason == expected_reason


def test_success_returns_the_token():
    with patch.object(run_as.requests, "post",
                      return_value=_sf_response(200, {"access_token": "T!"})):
        assert mint_run_as_token(CFG, username="u@x") == "T!"


def test_s1_precheck_identity_not_found_and_inactive_are_distinct():
    conn = MagicMock()
    conn.execute.return_value.first.return_value = None
    with pytest.raises(RunAsResolutionError) as e1:
        run_as.assert_identity_known_and_active(conn, "org-1", "ghost@x")
    assert e1.value.reason == IDENTITY_NOT_FOUND

    conn.execute.return_value.first.return_value = (False,)
    with pytest.raises(RunAsResolutionError) as e2:
        run_as.assert_identity_known_and_active(conn, "org-1", "sleepy@x")
    assert e2.value.reason == IDENTITY_INACTIVE


# ------------------------------------------------- no-silent-fallback ------

def test_identity_branch_cannot_reach_the_admin_token_path():
    """Structural assertion: resolve_data_mutation_client with an identity
    NEVER invokes _oauth_token (the admin grant). A failing exchange raises;
    nothing is returned. This is the D-416 worst-possible-failure guard."""
    from primeqa.execution_engine import credentials as C

    env = SimpleNamespace(id=59, connection_id=7, tenant_id=1,
                          sf_api_version="59.0", sf_instance_url=None)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = env

    with patch("primeqa.core.repository.ConnectionRepository") as CR, \
         patch("primeqa.metadata.worker_runner._oauth_token") as admin_grant, \
         patch("primeqa.semantic.connection.get_tenant_connection"), \
         patch("primeqa.sync.credentials.resolve_connected_org_or_raise",
               return_value="org-1"), \
         patch("primeqa.execution_engine.run_as."
               "assert_identity_known_and_active"), \
         patch("primeqa.execution_engine.run_as.mint_run_as_token",
               side_effect=RunAsResolutionError(NOT_PREAUTHORIZED, "no")):
        CR.return_value.get_connection_decrypted.return_value = {"config": CFG}
        with pytest.raises(RunAsResolutionError):
            C.resolve_data_mutation_client(db, 59, run_as_username="u@x")
    admin_grant.assert_not_called()


def test_absent_identity_is_the_pre_run_as_path_byte_identical():
    """No identity → the exact same _resolve_org_token call and client
    construction as before the feature existed."""
    from primeqa.execution_engine import credentials as C

    env = SimpleNamespace(sf_api_version="59.0")
    with patch.object(C, "_resolve_org_token",
                      return_value=(env, "ADMIN_TOKEN", "https://i.example")) as rt, \
         patch.object(C, "DataMutationClient") as DMC:
        C.resolve_data_mutation_client(MagicMock(), 59)
    rt.assert_called_once()
    DMC.assert_called_once_with("https://i.example", "59.0", "ADMIN_TOKEN")


# ------------------------------------------------- two-client teardown -----

def test_teardown_uses_admin_client_while_run_used_identity_client():
    """D-418: the 1-step rejected-create path — the run's create goes to the
    IDENTITY client; the unexpected-success cleanup delete goes to the ADMIN
    client."""
    from primeqa.execution_engine.data_executor import _run_create

    run_client = MagicMock(name="identity_client")
    admin_client = MagicMock(name="admin_client")
    # The org unexpectedly ACCEPTS the prohibited create -> failed + delete.
    run_client.create.return_value = {
        "http_status": 201, "success": True, "record_id": "001XX",
        "api_response": {"body": {}},
    }
    admin_client.delete.return_value = {"success": True}

    create = SimpleNamespace(
        step_id="create-x", field_values={}, expect_rejection=SimpleNamespace(
            error_code="INSUFFICIENT_ACCESS_OR_READONLY",
            error_message_pattern=None))
    _ev, outcome, _err = _run_create(create, "Case", run_client,
                                     teardown_client=admin_client)
    assert outcome == "failed"
    run_client.create.assert_called_once()
    admin_client.delete.assert_called_once_with("Case", "001XX")
    run_client.delete.assert_not_called()


# ------------------------------------------------- Phase D fixture ---------

def test_identity_scoped_fixture_validates_through_s2():
    """The hand-authored env-59 recipe round-trips the REAL S2 models: both
    coupling validators accept it, the rejection expectation is grounded, and
    the run path's identity extractor reads the designated username from it."""
    import json
    from pathlib import Path

    from primeqa.test_representation.models.recipes.data_recipe import (
        DataRecipeBody)
    from primeqa.test_representation.models.triggers.data_mutation import (
        DataMutationTriggerBody)
    from primeqa.execution_engine.run import _run_as_username_of

    raw = json.loads(Path(
        "primeqa/execution_engine/fixtures/identity_scoped_recipe_env59.json"
    ).read_text())
    trigger = DataMutationTriggerBody.model_validate(raw["causal_initiation"])
    body = DataRecipeBody.model_validate(raw["observation_realization"])

    assert trigger.identity_context == "run_as_user"
    assert trigger.run_as_user.external_id == raw["designated_user"]
    assert body.steps[0].expect_rejection.error_code == \
        "INSUFFICIENT_ACCESS_OR_READONLY"

    recipe = SimpleNamespace(recipe_id="r-1", causal_initiation=trigger,
                             observation_realization=body)
    assert _run_as_username_of(recipe) == "tarik.habibullah@primeqa.com"


def test_identity_extractor_fails_loud_on_body_disagreement():
    from primeqa.execution_engine.bridge import PlanTranslationError
    from primeqa.execution_engine.run import _run_as_username_of

    trigger = SimpleNamespace(identity_context="run_as_user",
                              run_as_user=SimpleNamespace(external_id="a@x"))
    body = SimpleNamespace(identity_context="system", run_as_user=None)
    recipe = SimpleNamespace(recipe_id="r-2", causal_initiation=trigger,
                             observation_realization=body)
    with pytest.raises(PlanTranslationError):
        _run_as_username_of(recipe)


def test_identity_extractor_none_for_system_recipes():
    from primeqa.execution_engine.run import _run_as_username_of
    trigger = SimpleNamespace(identity_context="system", run_as_user=None)
    body = SimpleNamespace(identity_context="system", run_as_user=None)
    assert _run_as_username_of(SimpleNamespace(
        recipe_id="r-3", causal_initiation=trigger,
        observation_realization=body)) is None
