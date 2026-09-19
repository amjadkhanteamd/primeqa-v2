"""AUD-038 containment (D-499): a deactivated user is OUT on their next request,
and there is ONE deactivation path.

Three guards, the pyflakes-gate kind:

1. The AST guard — no module in ``primeqa/`` assigns a user row's
   ``is_active`` outside the service/repository pair, and no SQL literal
   updates ``users.is_active``. The API deactivate route once did exactly
   that (``u.is_active = False; db.commit()``), skipping the service's
   refresh-token revocation. A second such path cannot be added quietly:
   this test names the file and line.
2. The chokepoint guard — ``require_auth`` (API) and ``login_required`` (web)
   both refuse when the account is inactive, BEFORE the view runs; the
   check reads the real source (the decorators call ``session_is_active``)
   and the behaviour (a stubbed inactive answer refuses).
3. The service guard — deactivation through the service revokes refresh
   tokens on every path, refuses to deactivate the last active superadmin,
   and leaves an active user's other updates untouched.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

pytestmark = pytest.mark.unit

REPO = pathlib.Path(__file__).resolve().parents[2]
PKG = REPO / "primeqa"

# The ONLY places a user row's active flag may be written.
ALLOWED_FILES = {"primeqa/core/service.py", "primeqa/core/repository.py"}
# Attribute receivers that are NOT user rows (facts, environments) — named, not guessed.
NON_USER_RECEIVERS = {"fact"}


def _is_active_assignments():
    hits = []
    for path in sorted(PKG.rglob("*.py")):
        rel = str(path.relative_to(REPO))
        tree = ast.parse(path.read_text(), filename=rel)
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            for t in targets:
                if isinstance(t, ast.Attribute) and t.attr == "is_active":
                    recv = t.value.id if isinstance(t.value, ast.Name) else ast.dump(t.value)[:40]
                    hits.append((rel, node.lineno, recv))
    return hits


def test_no_module_writes_a_users_active_flag_outside_the_service():
    offenders = [(f, ln, r) for f, ln, r in _is_active_assignments()
                 if f not in ALLOWED_FILES and r not in NON_USER_RECEIVERS]
    assert offenders == [], ("a user's is_active is assigned outside core/service.py + core/repository.py "
                             "— route it through AuthService.update_user (AUD-038): %s" % offenders)


def test_no_sql_literal_updates_the_users_active_flag():
    rx = re.compile(r"UPDATE\s+(?:public\.)?users\s+SET[^;]*is_active", re.I | re.S)
    offenders = []
    for path in sorted(PKG.rglob("*.py")):
        rel = str(path.relative_to(REPO))
        if rel in ALLOWED_FILES:
            continue
        if rx.search(path.read_text()):
            offenders.append(rel)
    assert offenders == [], offenders


def test_the_guard_sees_a_planted_direct_write(tmp_path, monkeypatch):
    # the guard must be shown to fail: a module with `u.is_active = False` is reported
    planted = tmp_path / "primeqa" / "rogue.py"
    planted.parent.mkdir(parents=True)
    planted.write_text("def off(u):\n    u.is_active = False\n")
    monkeypatch.setattr("tests.unit.test_deactivation_paths.PKG", tmp_path / "primeqa")
    monkeypatch.setattr("tests.unit.test_deactivation_paths.REPO", tmp_path)
    hits = _is_active_assignments()
    assert hits == [("primeqa/rogue.py", 2, "u")]


# --- 2. the chokepoint ----------------------------------------------------------

def test_both_auth_decorators_call_the_one_chokepoint():
    auth = (PKG / "core" / "auth.py").read_text()
    views = (PKG / "views.py").read_text()
    assert "def session_is_active(" in auth
    # the API decorator's body calls it before the view
    body = auth[auth.index("def require_auth("):auth.index("def require_role(")]
    assert "session_is_active(" in body and "ACCOUNT_INACTIVE" in body
    # the web decorator's body calls the same function
    wbody = views[views.index("def login_required("):views.index("def role_required(")]
    assert "session_is_active(" in wbody and "reason=inactive" in wbody


def test_an_inactive_account_is_refused_by_the_api_decorator_before_the_view(monkeypatch):
    import jwt as pyjwt
    from datetime import datetime, timedelta, timezone

    from primeqa.core import auth as A
    from primeqa.app import app
    now = datetime.now(timezone.utc)
    tok = pyjwt.encode({"sub": "14", "tenant_id": 1, "role": "tester", "email": "t@x", "full_name": "t",
                        "iat": now, "exp": now + timedelta(minutes=5)}, A._get_jwt_secret(), algorithm="HS256")
    monkeypatch.setattr(A, "session_is_active", lambda uid, tid: False)
    ran = []

    @A.require_auth
    def view():
        ran.append(1); return "ok"
    with app.test_request_context("/api/x", headers={"Authorization": "Bearer " + tok}):
        resp = view()
    body, status = (resp if isinstance(resp, tuple) else (resp, resp.status_code))
    assert status == 401 and ran == []
    assert "ACCOUNT_INACTIVE" in (body.get_data(as_text=True) if hasattr(body, "get_data") else str(body))


def test_an_inactive_account_is_refused_by_the_web_decorator_and_the_cookie_cleared(monkeypatch):
    import jwt as pyjwt
    from datetime import datetime, timedelta, timezone

    from primeqa.core import auth as A
    from primeqa.app import app
    now = datetime.now(timezone.utc)
    tok = pyjwt.encode({"sub": "14", "tenant_id": 1, "role": "tester", "email": "t@x", "full_name": "t",
                        "iat": now, "exp": now + timedelta(minutes=5)}, A._get_jwt_secret(), algorithm="HS256")
    monkeypatch.setattr(A, "session_is_active", lambda uid, tid: False)
    c = app.test_client(); c.set_cookie("access_token", tok)
    r = c.get("/requirements", follow_redirects=False)
    assert r.status_code == 302 and r.headers["Location"].endswith("/login?reason=inactive")
    assert any("access_token=;" in h or "access_token=\"\"" in h for h in r.headers.getlist("Set-Cookie"))


def test_a_failed_activity_read_refuses_never_admits(monkeypatch):
    from primeqa.core import auth as A
    from primeqa import db as dbm

    class _Boom:
        def connect(self):
            raise RuntimeError("db down")
    monkeypatch.setattr(dbm, "engine", _Boom())
    from primeqa.app import app
    with app.test_request_context("/"):
        assert A.session_is_active(14, 1) is False


# --- 3. the service --------------------------------------------------------------

class _User:
    def __init__(self, id, role="tester", is_active=True, tenant_id=1):
        self.id, self.role, self.is_active, self.tenant_id = id, role, is_active, tenant_id
        self.email, self.full_name, self.created_at, self.last_login_at = "u@x", "u", None, None


class _UserRepo:
    def __init__(self, users, supers=1):
        self.users = {u.id: u for u in users}; self.supers = supers; self.updated = []

    def get_user_by_id(self, uid, tenant_id=None):
        return self.users.get(uid)

    def update_user(self, uid, updates, tenant_id=None):
        u = self.users[uid]
        for k, v in updates.items():
            setattr(u, k, v)
        self.updated.append((uid, dict(updates)))
        return u

    def count_active_superadmins(self, tenant_id, *, exclude_user_id=None):
        return self.supers


class _TokenRepo:
    def __init__(self):
        self.revoked = []

    def revoke_all_user_tokens(self, uid):
        self.revoked.append(uid)


ADMIN = {"id": 15, "tenant_id": 1, "role": "admin"}


def test_deactivation_through_the_service_revokes_refresh_tokens():
    from primeqa.core.service import AuthService
    users, tokens = _UserRepo([_User(14)]), _TokenRepo()
    AuthService(users, tokens).update_user(14, ADMIN, is_active=False)
    assert users.users[14].is_active is False and tokens.revoked == [14]


def test_an_active_users_other_updates_revoke_nothing():
    from primeqa.core.service import AuthService
    users, tokens = _UserRepo([_User(14)]), _TokenRepo()
    AuthService(users, tokens).update_user(14, ADMIN, full_name="renamed")
    assert users.users[14].full_name == "renamed" and tokens.revoked == []


def test_the_last_active_superadmin_cannot_be_deactivated_on_the_service_path():
    from primeqa.core.service import AuthService, LastSuperadminError
    users, tokens = _UserRepo([_User(1, role="superadmin")], supers=0), _TokenRepo()
    with pytest.raises(LastSuperadminError):
        AuthService(users, tokens).update_user(1, {"id": 1, "tenant_id": 1, "role": "superadmin"}, is_active=False)
    assert users.users[1].is_active is True and tokens.revoked == []


def test_a_superadmin_with_another_active_one_can_be_deactivated():
    from primeqa.core.service import AuthService
    users, tokens = _UserRepo([_User(1, role="superadmin")], supers=1), _TokenRepo()
    AuthService(users, tokens).update_user(1, {"id": 2, "tenant_id": 1, "role": "superadmin"}, is_active=False)
    assert users.users[1].is_active is False and tokens.revoked == [1]
