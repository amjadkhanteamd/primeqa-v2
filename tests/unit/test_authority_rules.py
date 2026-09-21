"""Unit: the two owner-or-admin rules of the triage batch (2026-09-19), judged
in the SERVICE (the route only says who is asking; the table refuses the
empty reason a second time).

AUD-020 — a waiver's revocation: the reviewer who accepted it, the person who
recorded it, or an admin; with a reason.
AUD-023 — a release target's removal: the person who declared it, or an
admin; with a reason.

Pure: the session is a double that records what would have been executed,
so each refusal is proven to happen BEFORE any write.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


class _Session:
    def __init__(self, scalar=None):
        self.executed = []
        self._scalar = scalar

    def execute(self, stmt, params=None):
        self.executed.append((str(stmt), params))
        s = self

        class _R:
            rowcount = 1

            def scalar(self_inner):
                return s._scalar

            def fetchone(self_inner):
                return None

            def fetchall(self_inner):
                return []
        return _R()

    def flush(self):
        pass

    def begin_nested(self):
        import contextlib
        return contextlib.nullcontext()


# --- AUD-020 ------------------------------------------------------------------

def _waiver(**over):
    w = {"id": "w1", "release_id": 1, "item_kind": "claim", "item_ref": "t", "axis": "functional",
         "reviewer_user_id": 15, "reason": "ok", "expires_at": "2099-01-01", "created_by": 16,
         "created_at": "2026-09-01", "revoked_by": None, "revoked_at": None, "revocation_reason": None,
         "state": "active"}
    w.update(over)
    return w


@pytest.fixture
def qp(monkeypatch):
    from primeqa.intelligence import quality_policy as qp
    monkeypatch.setattr(qp, "get_waiver", lambda session, wid, now=None: _waiver())
    monkeypatch.setattr(qp, "_audit", lambda *a, **k: None)
    return qp


@pytest.mark.parametrize("user", [14, 99])
def test_a_stranger_cannot_revoke_a_waiver(qp, user):
    s = _Session()
    with pytest.raises(qp.PolicyError, match="reviewer, the person who recorded it, or an admin"):
        qp.revoke_waiver(s, waiver_id="w1", user_id=user, reason="because", tenant_id=1)
    assert s.executed == []                                    # refused before any write


@pytest.mark.parametrize("why", ["", "   ", None])
def test_an_empty_reason_is_refused_even_for_the_reviewer(qp, why):
    s = _Session()
    with pytest.raises(qp.PolicyError, match="carries its reason"):
        qp.revoke_waiver(s, waiver_id="w1", user_id=15, reason=why, tenant_id=1)
    assert s.executed == []


@pytest.mark.parametrize("user,admin", [(15, False), (16, False), (14, True), (99, True)])
def test_the_reviewer_the_recorder_or_an_admin_revokes_with_a_reason(qp, user, admin):
    s = _Session()
    qp.revoke_waiver(s, waiver_id="w1", user_id=user, reason="  no longer holds ", tenant_id=1, actor_is_admin=admin)
    assert len(s.executed) == 1 and "UPDATE quality_waivers" in s.executed[0][0]
    assert s.executed[0][1]["r"] == "no longer holds"          # stored trimmed, never empty


# --- AUD-023 ------------------------------------------------------------------

@pytest.fixture
def planner(monkeypatch):
    from primeqa.execution_engine import planner
    monkeypatch.setattr(planner, "_audit", lambda *a, **k: None)
    return planner


def test_a_stranger_cannot_remove_another_users_target(planner):
    s = _Session(scalar=15)                                    # declared_by 15
    with pytest.raises(planner.TargetAuthorityError, match="declared this target, or an admin"):
        planner.remove_target(s, tenant_id=1, release_id=1, environment_id=59, actor_user_id=14, reason="wrong org")
    assert all("UPDATE" not in st for st, _ in s.executed)   # only the SELECT ran


@pytest.mark.parametrize("why", ["", "  ", None])
def test_an_empty_reason_is_refused_before_the_read(planner, why):
    s = _Session(scalar=15)
    with pytest.raises(planner.TargetAuthorityError, match="carries its reason"):
        planner.remove_target(s, tenant_id=1, release_id=1, environment_id=59, actor_user_id=15, reason=why)
    assert s.executed == []


@pytest.mark.parametrize("user,admin", [(15, False), (14, True)])
def test_the_declarer_or_an_admin_removes_with_a_reason(planner, user, admin):
    s = _Session(scalar=15)
    out = planner.remove_target(s, tenant_id=1, release_id=1, environment_id=59, actor_user_id=user,
                                reason=" wrong org ", actor_is_admin=admin)
    assert out == {"removed": True}
    upd = [p for st, p in s.executed if "UPDATE release_targets" in st]
    assert upd and upd[0]["why"] == "wrong org"


def test_no_active_target_is_not_an_authority_question(planner):
    s = _Session(scalar=None)
    assert planner.remove_target(s, tenant_id=1, release_id=1, environment_id=59, actor_user_id=14, reason="x") == {"removed": False}


# --- AUD-037 (triage round 2): a surface link's undo -----------------------------------------

@pytest.fixture
def sl(monkeypatch):
    from primeqa.test_representation import surface_links as sl

    class _Link:
        active = True; declared_by = 15; external_system = "jira"; requirement_key = "SQ-1"
        surface_key = "s.example.com|/x|s|-|-"; display_name = "x"; path = "/x"; inventory_version = 1
    monkeypatch.setattr(sl, "get_link", lambda session, link_id: _Link())
    return sl


def test_a_stranger_cannot_unlink_a_surface(sl):
    s = _Session()
    with pytest.raises(sl.SurfaceLinkError) as ex:
        sl.unlink(s, link_id="l1", actor_user_id=14, reason="not mine", tenant_id=1)
    assert ex.value.reason == "not_declarer" and s.executed == []


@pytest.mark.parametrize("why", ["", "  ", None])
def test_an_empty_unlink_reason_is_refused_even_for_the_declarer(sl, why):
    s = _Session()
    with pytest.raises(sl.SurfaceLinkError) as ex:
        sl.unlink(s, link_id="l1", actor_user_id=15, reason=why, tenant_id=1)
    assert ex.value.reason == "no_reason" and s.executed == []


@pytest.mark.parametrize("user,admin", [(15, False), (14, True)])
def test_the_declarer_or_an_admin_unlinks_with_a_reason(sl, user, admin):
    s = _Session()
    sl.unlink(s, link_id="l1", actor_user_id=user, reason=" retired ", tenant_id=1, actor_is_admin=admin)
    upd = [p for st, p in s.executed if "UPDATE requirement_surface_links" in st]
    assert upd and upd[0]["r"] == "retired"
