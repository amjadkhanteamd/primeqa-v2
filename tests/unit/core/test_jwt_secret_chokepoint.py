"""SEC-8 / SEC-P1: JWT signing (core/service) and web verification (views)
resolve the secret through the fail-closed core.secrets.get_jwt_secret()
chokepoint — never a forgeable os.getenv("JWT_SECRET", "dev-secret-change-me")
default. Pure unit test (no DB/network).
"""
import jwt
import pytest
from flask import Flask


def test_service_signer_delegates_to_chokepoint(monkeypatch):
    import primeqa.core.secrets as secrets
    import primeqa.core.service as service
    monkeypatch.setattr(secrets, "get_jwt_secret", lambda: "SENTINEL_SECRET")
    # _get_jwt_secret now does a function-local import of get_jwt_secret, so the
    # monkeypatch on the source module is picked up at call time.
    assert service._get_jwt_secret() == "SENTINEL_SECRET"


def test_views_verify_uses_chokepoint(monkeypatch):
    import primeqa.core.secrets as secrets
    monkeypatch.setattr(secrets, "get_jwt_secret", lambda: "SENTINEL_SECRET")
    from primeqa.views import get_current_user

    app = Flask(__name__)
    good = jwt.encode({"sub": "1", "tenant_id": 1, "role": "admin"},
                      "SENTINEL_SECRET", algorithm="HS256")
    forged = jwt.encode({"sub": "1", "tenant_id": 1, "role": "admin"},
                        "dev-secret-change-me", algorithm="HS256")
    with app.test_request_context("/", headers={"Cookie": f"access_token={good}"}):
        u = get_current_user()
        assert u and u["id"] == 1, "a token signed with the chokepoint secret must verify"
    with app.test_request_context("/", headers={"Cookie": f"access_token={forged}"}):
        assert get_current_user() is None, \
            "a token forged with the old dev-default must NOT verify"


def test_get_jwt_secret_fails_closed_in_production(monkeypatch):
    import primeqa.core.secrets as secrets
    monkeypatch.setattr(secrets, "is_production", lambda: True)
    # unset -> refuse
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(secrets.SecretConfigError):
        secrets.get_jwt_secret()
    # the public dev default -> also refused in production
    monkeypatch.setenv("JWT_SECRET", "dev-secret-change-me")
    with pytest.raises(secrets.SecretConfigError):
        secrets.get_jwt_secret()
    # a real value -> accepted
    monkeypatch.setenv("JWT_SECRET", "a-real-production-secret")
    assert secrets.get_jwt_secret() == "a-real-production-secret"
