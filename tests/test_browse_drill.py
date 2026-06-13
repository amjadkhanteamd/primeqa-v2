"""D-233 — browse/drill 2nd increment (route + render).

Three independent v2-runtime sub-pieces that make the substrate test pages
readable for non-engineers:
  A — plain-English headline per evidence step on the run-detail page;
  B — a "Last run" health badge column on /claims;
  C — a claim → requirement back-link on the claim-detail page.

The pure helpers (step_plain) are unit-tested in tests/unit/test_claim_presentation.py.
THIS suite exercises the web surface: render with the bridges mocked (prod reads
only — login + an env list; no writes). Run standalone:

    python tests/test_browse_drill.py
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from primeqa.app import app

RUN_ID = "11111111-1111-1111-1111-111111111111"
CLAIM_ID = "22222222-2222-2222-2222-222222222222"
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


# ---- A — run-detail evidence headlines ------------------------------------

def _run_detail(steps):
    return {"available": True, "found": True, "run": {
        "run_id": RUN_ID, "claim_test_id": CLAIM_ID, "recipe_id": None,
        "recipe_version_seq": 1, "environment_id": 59, "outcome": "failed",
        "started_at": None, "finished_at": "2026-06-14T00:00:00+00:00",
        "duration_ms": 1200, "api_choice": "rest", "steps": steps,
        "error": None, "interpretation": None}}


def run_tests():
    results = []
    print("\n=== Browse/Drill Tests (D-233) ===\n")
    login_form("admin@primeqa.io", "changeme123")

    def test_evidence_headlines():
        steps = [
            {"kind": "create", "ordinal": 0, "sobject": "Opportunity",
             "success": True, "matched": None},
            {"kind": "update", "ordinal": 1, "sobject": "Opportunity",
             "success": False, "matched": True,
             "error_code": "FIELD_CUSTOM_VALIDATION_EXCEPTION"},
        ]
        with patch("primeqa.intelligence.s4_execution_console.read_run_detail",
                   return_value=_run_detail(steps)):
            html = client.get(f"/runs/{RUN_ID}").get_data(as_text=True)
        assert "Created a Opportunity" in html, "create headline missing"
        assert "Tried the forbidden edit on Opportunity" in html, \
            "update-blocked headline missing"
        # the raw step tree is still present, collapsed beneath the headline
        assert "Show raw step data" in html, "raw-tree toggle missing"
    results.append(test("A1. run detail renders plain-English step headlines",
                        test_evidence_headlines))

    def test_evidence_headline_degrades():
        # an unknown step kind must still render (the kind word), never 500
        steps = [{"kind": "mystery", "ordinal": 0}]
        with patch("primeqa.intelligence.s4_execution_console.read_run_detail",
                   return_value=_run_detail(steps)):
            r = client.get(f"/runs/{RUN_ID}")
        assert r.status_code == 200, r.status_code
        assert "Show raw step data" in r.get_data(as_text=True)
    results.append(test("A2. an unknown step kind still renders (no 500)",
                        test_evidence_headline_degrades))

    # ---- B — last-run health column on /claims ----------------------------

    def _claims_list(last_run):
        return {"available": True, "total": 1, "page": 1, "per_page": 20,
                "total_pages": 1, "claims": [{
                    "test_id": CLAIM_ID, "title": "Amount cap",
                    "claim_kind": "prohibition-claim",
                    "archetype": "prohibited_state", "status": "approved",
                    "version_seq": 1, "depth": "behavioral",
                    "requirement_key": "SQ-1", "updated_at": "2026-06-14",
                    "last_run": last_run}]}

    def test_last_run_badge():
        lr = {"run_id": RUN_ID, "outcome": "failed",
              "finished_at": "2026-06-13T06:03:01+00:00"}
        with patch("primeqa.intelligence.s3_generation_console.list_claims",
                   return_value=_claims_list(lr)), \
             patch("primeqa.intelligence.quarantine.list_quarantined",
                   return_value=[]):
            html = client.get("/claims").get_data(as_text=True)
        assert f"/runs/{RUN_ID}" in html, "last-run badge does not link to the run"
        assert "Last run" in html, "Last run column header missing"
        # the failed outcome drives the red badge text
        assert ">failed</a>" in html, "failed outcome badge missing"
    results.append(test("B1. /claims shows a last-run badge linking to the run",
                        test_last_run_badge))

    def test_never_run():
        with patch("primeqa.intelligence.s3_generation_console.list_claims",
                   return_value=_claims_list(None)), \
             patch("primeqa.intelligence.quarantine.list_quarantined",
                   return_value=[]):
            html = client.get("/claims").get_data(as_text=True)
        assert "never run" in html, "never-run state missing"
    results.append(test("B2. a claim with no runs shows 'never run'",
                        test_never_run))

    passed = sum(results)
    total = len(results)
    print(f"\n{'='*60}\n  {passed}/{total} passed\n{'='*60}\n")
    return passed == total


if __name__ == "__main__":
    ok = run_tests()
    sys.exit(0 if ok else 1)
