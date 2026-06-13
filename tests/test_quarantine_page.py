"""D-232.3 — the flaky-test quarantine UI (route + badge + control).

Slice 3 of the persisted quarantine ledger: the operator pin/lift route
(`POST /claims/<id>/quarantine`), the at-a-glance badge + control panel on the
claim detail page, and the row badge on the claims list.

The store itself (pin/unpin/read against the migrated `claim_quarantine` table)
is covered by `tests/integration/intelligence/test_quarantine.py` on the LOCAL
test DB. The harmonization with the release decision is covered by
`tests/unit/test_quarantine_harmonization.py`. THIS suite exercises only the web
surface — and deliberately so without mutating prod: render tests mock the
bridges + ledger reads (prod reads only); POST happy-paths mock the ledger write
AND the activity-log session, so nothing is written to the Railway DB.
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from primeqa.app import app

TID = "33333333-3333-3333-3333-333333333333"
client = app.test_client()


def test(name, fn):
    try:
        fn()
        print(f"  PASS  {name}")
        return True
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        return False
    except Exception as e:
        import traceback
        print(f"  ERROR {name}: {type(e).__name__}: {e}")
        traceback.print_exc()
        return False


def login_form(email, password):
    return client.post("/login",
                       data={"email": email, "password": password},
                       follow_redirects=False)


def _csrf():
    """Mint + read the double-submit CSRF token from the test-client cookie jar
    (the after_request sets it on the first response that lacks the cookie)."""
    client.get("/health")
    ck = client.get_cookie("csrf_token")
    return ck.value if ck else ""


class _FakeSession:
    """A no-op stand-in for the activity-log Session so the route's audit write
    never touches the real DB. Records what was added so the test can assert it."""
    def __init__(self):
        self.added = []
        self.committed = False
        self.closed = False

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def _fake_detail():
    """A minimal approved-claim detail dict in the shape the template reads."""
    return {"available": True, "found": True, "claim": {
        "test_id": TID,
        "title": "Opportunity Amount must not exceed 1,000,000",
        "claim_kind": "behavioral-prohibition",
        "archetype": "prohibited_state",
        "depth": "behavioral",
        "status": "approved",
        "version_seq": 3,
        "asserted_truth": None,
        "semantic_conditions": None,
        "recipes": [],
    }}


def _detail_html(*, active, manual):
    """Render /claims/<TID> with the bridges stubbed and the ledger state forced."""
    with patch("primeqa.intelligence.s3_generation_console.read_claim_detail",
               return_value=_fake_detail()), \
         patch("primeqa.intelligence.s3_generation_console.read_claim_siblings",
               return_value={"available": True, "siblings": []}), \
         patch("primeqa.intelligence.s4_execution_console.read_claim_runs",
               return_value={"available": True, "runs": []}), \
         patch("primeqa.intelligence.quarantine.is_quarantined",
               return_value=active), \
         patch("primeqa.intelligence.quarantine.manual_states",
               return_value=({TID: manual} if manual else {})):
        r = client.get(f"/claims/{TID}")
    assert r.status_code == 200, r.status_code
    return r.get_data(as_text=True)


def run_tests():
    results = []
    print("\n=== Quarantine UI Tests (D-232.3) ===\n")
    login_form("admin@primeqa.io", "changeme123")

    # ------------------------------------------------------------------
    # Render: detail badge + control across the four ledger states
    # ------------------------------------------------------------------
    def test_active_manual():
        html = _detail_html(active=True, manual="pinned")
        # at-a-glance header badge (the &#9888; entity is the active-badge marker)
        assert "&#9888; quarantined" in html, "no manual-quarantine header badge"
        assert "Lift quarantine" in html, "no Lift control when active"
        # the pin modal must NOT render for an already-quarantined claim
        assert "quarantine-claim-modal" not in html, "pin modal leaked when active"
    results.append(test("1. detail: manual pin -> badge + Lift, no pin modal",
                        test_active_manual))

    def test_active_auto():
        html = _detail_html(active=True, manual=None)
        assert "&#9888; auto-quarantined" in html, "no auto-quarantine header badge"
        assert "auto-quarantined (flaky)" in html, "no auto panel badge"
        assert "automatically, because it has been flipping" in html, \
            "auto explanation missing"
        assert "Lift quarantine" in html, "no Lift control when auto-active"
    results.append(test("2. detail: auto-active -> auto badge + flaky explanation",
                        test_active_auto))

    def test_inactive():
        html = _detail_html(active=False, manual=None)
        assert "&#9888;" not in html, "active badge rendered when not quarantined"
        assert "Quarantine this claim" in html, "no Quarantine control when inactive"
        assert "quarantine-claim-modal" in html, "pin modal missing when inactive"
        assert "Lift quarantine" not in html, "Lift control shown when not active"
    results.append(test("3. detail: inactive -> Quarantine button + modal, no badge",
                        test_inactive))

    def test_lifted_note():
        html = _detail_html(active=False, manual="lifted")
        assert "&#9888;" not in html, "no badge expected for a lifted claim"
        assert "currently manually lifted" in html, "lifted-override note missing"
        assert "Quarantine this claim" in html, "re-pin control missing after lift"
    results.append(test("4. detail: manual lift -> override note + re-pin control",
                        test_lifted_note))

    # ------------------------------------------------------------------
    # Route: POST /claims/<id>/quarantine dispatch + audit
    # ------------------------------------------------------------------
    def test_unknown_action_rejected():
        csrf = _csrf()
        with patch("primeqa.intelligence.quarantine.pin") as mpin, \
             patch("primeqa.intelligence.quarantine.unpin") as munpin:
            r = client.post(f"/claims/{TID}/quarantine",
                            data={"action": "garbage", "csrf_token": csrf},
                            follow_redirects=False)
        assert r.status_code in (301, 302), r.status_code
        assert r.headers["Location"].endswith(f"/claims/{TID}"), r.headers["Location"]
        mpin.assert_not_called()
        munpin.assert_not_called()
    results.append(test("5. POST: unknown action -> redirect, no ledger write",
                        test_unknown_action_rejected))

    def test_pin_best_effort_failure():
        csrf = _csrf()
        with patch("primeqa.intelligence.quarantine.pin",
                   return_value=False) as mpin, \
             patch("primeqa.views.get_db") as mdb:
            r = client.post(f"/claims/{TID}/quarantine",
                            data={"action": "pin", "reason": "flaky",
                                  "csrf_token": csrf},
                            follow_redirects=False)
        assert r.status_code in (301, 302), r.status_code
        mpin.assert_called_once()
        _, kwargs = mpin.call_args
        assert kwargs.get("source") == "manual", kwargs
        assert kwargs.get("reason") == "flaky", kwargs
        assert kwargs.get("actor") is not None, "actor (user id) not threaded"
        # a best-effort failure returns BEFORE the activity-log write
        mdb.assert_not_called()
    results.append(test("6. POST: pin best-effort failure -> no audit write",
                        test_pin_best_effort_failure))

    def test_pin_happy_path_writes_audit():
        csrf = _csrf()
        fake = _FakeSession()
        with patch("primeqa.intelligence.quarantine.pin", return_value=True), \
             patch("primeqa.views.get_db", return_value=iter([fake])):
            r = client.post(f"/claims/{TID}/quarantine",
                            data={"action": "pin", "reason": "flaky in CI",
                                  "csrf_token": csrf},
                            follow_redirects=False)
        assert r.status_code in (301, 302), r.status_code
        assert len(fake.added) == 1, f"expected one ActivityLog, got {fake.added}"
        al = fake.added[0]
        assert al.action == "quarantine_pin", al.action
        assert al.entity_type == "claim_quarantine", al.entity_type
        assert al.entity_id is None, "UUID cannot live in the int entity_id column"
        assert al.details.get("test_id") == TID, al.details
        assert al.details.get("reason") == "flaky in CI", al.details
        assert fake.committed and fake.closed, "audit not committed/closed"
    results.append(test("7. POST: pin happy-path writes quarantine_pin audit",
                        test_pin_happy_path_writes_audit))

    def test_unpin_happy_path_writes_audit():
        csrf = _csrf()
        fake = _FakeSession()
        with patch("primeqa.intelligence.quarantine.unpin", return_value=True), \
             patch("primeqa.views.get_db", return_value=iter([fake])):
            r = client.post(f"/claims/{TID}/quarantine",
                            data={"action": "unpin", "csrf_token": csrf},
                            follow_redirects=False)
        assert r.status_code in (301, 302), r.status_code
        assert len(fake.added) == 1, fake.added
        al = fake.added[0]
        assert al.action == "quarantine_lift", al.action
        assert al.entity_type == "claim_quarantine", al.entity_type
        assert al.details == {"test_id": TID}, "a lift carries no reason"
    results.append(test("8. POST: unpin happy-path writes quarantine_lift audit",
                        test_unpin_happy_path_writes_audit))

    # ------------------------------------------------------------------
    # List: row badge
    # ------------------------------------------------------------------
    def test_list_badge():
        fake_list = {"available": True, "total": 1, "page": 1, "per_page": 20,
                     "total_pages": 1, "claims": [{
                         "test_id": TID, "title": "Amount cap",
                         "claim_kind": "behavioral-prohibition",
                         "archetype": "prohibited_state", "status": "approved",
                         "version_seq": 1, "depth": "behavioral",
                         "requirement_key": "SQ-1", "updated_at": "2026-06-14"}]}
        with patch("primeqa.intelligence.s3_generation_console.list_claims",
                   return_value=fake_list), \
             patch("primeqa.intelligence.quarantine.list_quarantined",
                   return_value=[{"test_id": TID, "reason": "x",
                                  "source": "manual", "pinned_at": None,
                                  "pinned_by": 3}]):
            html = client.get("/claims").get_data(as_text=True)
        assert "&#9888; quarantined" in html, "no row badge for a quarantined claim"
    results.append(test("9. list: quarantined row carries the badge",
                        test_list_badge))

    passed = sum(results)
    total = len(results)
    print(f"\n{'='*60}")
    print(f"  {passed}/{total} passed")
    print(f"{'='*60}\n")
    return passed == total


if __name__ == "__main__":
    ok = run_tests()
    sys.exit(0 if ok else 1)
