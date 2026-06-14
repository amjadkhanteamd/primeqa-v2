"""D-235 — run-time test-data injection (route + UI).

The web surface for per-run field overrides: the "Custom test data" control on
the claim-detail Run panel (behavioral claims only) and the claims_run route
threading the parsed overrides into trigger_claim_run. The parser is unit-tested
in tests/unit/test_field_overrides_parse.py; the executor merge in
tests/unit/execution_engine/test_data_executor_positive.py. Render uses prod
reads with the bridges mocked; the route POST mocks the env-check + the bridge so
nothing is written or run. Run standalone:

    python tests/test_data_injection.py
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from primeqa.app import app

CLAIM_ID = "44444444-4444-4444-4444-444444444444"
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
    return client.post("/login", data={"email": email, "password": password},
                       follow_redirects=False)


def _csrf():
    client.get("/health")
    ck = client.get_cookie("csrf_token")
    return ck.value if ck else ""


def _detail(depth):
    return {"available": True, "found": True, "claim": {
        "test_id": CLAIM_ID, "title": "Amount cap",
        "claim_kind": "value-claim", "archetype": "expected_state",
        "depth": depth, "status": "approved", "version_seq": 1,
        "asserted_truth": None, "semantic_conditions": None, "recipes": []}}


def _render(depth):
    with patch("primeqa.intelligence.s3_generation_console.read_claim_detail",
               return_value=_detail(depth)), \
         patch("primeqa.intelligence.s3_generation_console.read_claim_siblings",
               return_value={"available": True, "siblings": []}), \
         patch("primeqa.intelligence.s3_generation_console.read_claim_requirement",
               return_value={"available": True, "requirement_key": None}), \
         patch("primeqa.intelligence.s4_execution_console.read_claim_runs",
               return_value={"available": True, "runs": []}), \
         patch("primeqa.intelligence.quarantine.manual_states", return_value={}), \
         patch("primeqa.intelligence.quarantine.is_quarantined", return_value=False):
        r = client.get(f"/claims/{CLAIM_ID}")
    assert r.status_code == 200, r.status_code
    return r.get_data(as_text=True)


def run_tests():
    results = []
    print("\n=== Run-time Test-Data Injection (D-235) ===\n")
    login_form("admin@primeqa.io", "changeme123")

    def test_control_renders_for_behavioral():
        html = _render("behavioral")
        assert "Custom test data" in html, "injection control missing for behavioral claim"
        assert 'name="field_overrides"' in html, "overrides textarea missing"
    results.append(test("1. Run panel shows the injection control for behavioral claims",
                        test_control_renders_for_behavioral))

    def test_control_hidden_for_config_check():
        html = _render("configuration-check")
        assert "Custom test data" not in html, \
            "injection control leaked onto a config-check claim (creates nothing)"
    results.append(test("2. config-check claims hide the injection control",
                        test_control_hidden_for_config_check))

    def test_route_threads_parsed_overrides():
        csrf = _csrf()
        captured = {}

        def fake_trigger(tid, test_id, env_id, *, field_overrides=None):
            captured["fo"] = field_overrides
            return {"ok": True, "ran": True, "outcome": "passed", "verdict": None}

        with patch("primeqa.core.repository.EnvironmentRepository") as ER, \
             patch("primeqa.runs.bulk.environment_can_bulk_run",
                   return_value=(True, "ok")), \
             patch("primeqa.intelligence.s4_execution_console.trigger_claim_run",
                   side_effect=fake_trigger):
            ER.return_value.get_environment.return_value = SimpleNamespace(
                id=59, is_production=False)
            r = client.post(
                f"/claims/{CLAIM_ID}/run",
                data={"environment_id": "59",
                      "field_overrides": "Amount=2000000\nStageName=Prospecting",
                      "csrf_token": csrf},
                follow_redirects=False)
        assert r.status_code in (301, 302), r.status_code
        assert captured.get("fo") == {"Amount": "2000000",
                                      "StageName": "Prospecting"}, captured
    results.append(test("3. claims_run parses + threads field_overrides to the bridge",
                        test_route_threads_parsed_overrides))

    def test_route_empty_overrides_threads_empty():
        csrf = _csrf()
        captured = {}

        def fake_trigger(tid, test_id, env_id, *, field_overrides=None):
            captured["fo"] = field_overrides
            return {"ok": True, "ran": True, "outcome": "passed", "verdict": None}

        with patch("primeqa.core.repository.EnvironmentRepository") as ER, \
             patch("primeqa.runs.bulk.environment_can_bulk_run",
                   return_value=(True, "ok")), \
             patch("primeqa.intelligence.s4_execution_console.trigger_claim_run",
                   side_effect=fake_trigger):
            ER.return_value.get_environment.return_value = SimpleNamespace(
                id=59, is_production=False)
            client.post(f"/claims/{CLAIM_ID}/run",
                        data={"environment_id": "59", "csrf_token": csrf},
                        follow_redirects=False)
        assert captured.get("fo") == {}, "no overrides should thread an empty dict"
    results.append(test("4. an empty form threads {} (today's behavior)",
                        test_route_empty_overrides_threads_empty))

    passed = sum(results)
    total = len(results)
    print(f"\n{'='*60}\n  {passed}/{total} passed\n{'='*60}\n")
    return passed == total


if __name__ == "__main__":
    ok = run_tests()
    sys.exit(0 if ok else 1)
