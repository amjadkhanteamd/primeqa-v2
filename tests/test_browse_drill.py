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

def _run_detail(steps, **over):
    run = {
        "run_id": RUN_ID, "claim_test_id": CLAIM_ID, "recipe_id": None,
        "recipe_version_seq": 1, "environment_id": 59, "outcome": "failed",
        "started_at": None, "finished_at": "2026-06-14T00:00:00+00:00",
        "duration_ms": 1200, "api_choice": "rest", "steps": steps,
        "error": None, "interpretation": None,
        "failure_category": None, "sf_error_code": None, "source": None,
        "claim_kind": None, "asserted_truth": None, "requirement_key": None,
        "asserted_truth_pinned": None, "claim_version_drift": False,
        "recipe_semantic_fields": None}
    run.update(over)
    return {"available": True, "found": True, "run": run}


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

    def test_error_card_leads_with_org_message():
        # an errored run's card leads with the org's OWN words ("Salesforce
        # said: …") + the typed chips; the raw envelope stays collapsed.
        vr_text = ("KYC must be complete and Credit Score must be populated "
                   "before moving to Credit Assessment.")
        steps = [{"kind": "create", "ordinal": 0, "sobject": "Opportunity",
                  "success": False, "matched": None, "message": vr_text,
                  "error": {"phase": "create", "error_type": "AmbiguousRejection",
                            "message": "create rejected with no field attribution"},
                  "rejection_body": [{"message": vr_text, "fields": [],
                                      "errorCode": "FIELD_CUSTOM_VALIDATION_EXCEPTION"}]}]
        detail = _run_detail(
            steps, outcome="errored",
            error={"phase": "create", "error_type": "AmbiguousRejection",
                   "message": "create rejected with no field attribution; "
                              "cannot ascribe it to the value under test"},
            failure_category="setup_rejection",
            sf_error_code="FIELD_CUSTOM_VALIDATION_EXCEPTION")
        with patch("primeqa.intelligence.s4_execution_console.read_run_detail",
                   return_value=detail):
            html = client.get(f"/runs/{RUN_ID}").get_data(as_text=True)
        assert "Salesforce said:" in html, "org-message lead missing"
        assert vr_text in html, "the VR's own text missing"
        assert "AmbiguousRejection" in html, "error-type chip missing"
        assert "setup_rejection" in html, "failure-category chip missing"
        assert "FIELD_CUSTOM_VALIDATION_EXCEPTION" in html, "sf code chip missing"
        assert "Show technical details" in html, "raw envelope not collapsed"
    results.append(test("A3. errored run leads with the org's own error text",
                        test_error_card_leads_with_org_message))

    def test_header_renders_claim_title_and_requirement():
        detail = _run_detail(
            [], claim_kind="prohibition-claim",
            asserted_truth={"target": {"external_id": "Opportunity"},
                            "operation": "modify_field"},
            requirement_key="SQ-1")
        with patch("primeqa.intelligence.s4_execution_console.read_run_detail",
                   return_value=detail):
            html = client.get(f"/runs/{RUN_ID}").get_data(as_text=True)
        assert "Rejects editing fields on Opportunity" in html, \
            "claim title missing from header"
        assert f"Run {RUN_ID[:8]}" not in html.split("<h1")[1][:200], \
            "header still leads with the raw run id"
        assert "From requirement SQ-1" in html, "requirement line missing"
    results.append(test("A4. header renders the claim title + requirement key",
                        test_header_renders_claim_title_and_requirement))

    def test_readable_run_card_on_passed_run():
        # The QA-readable result: TEST DATA (as staged), value-bearing steps,
        # expected-vs-actual — composed from the evidence + the assertion.
        steps = [
            {"kind": "create", "sobject": "Opportunity", "success": True,
             "matched": None,
             "field_values": {"Loan_Amount__c": 5000000,
                              "Property_Value__c": 10000000, "Name": "PQA"}},
            {"kind": "read", "sobject": "Opportunity", "soql": "S",
             "row_count": 1, "fields_captured": ["Id", "Loan_to_Value__c"],
             "rows": [{"Id": "006x", "Loan_to_Value__c": 50.0}]},
            {"kind": "assert", "predicate": "equals", "held": True},
        ]
        detail = _run_detail(
            steps, outcome="passed", claim_kind="automation-effect-claim",
            asserted_truth={"expected_effect": {"kind": "field_change",
                "changes": {"field_values": {"Opportunity.Loan_to_Value__c": {
                    "kind": "literal", "value": "50"}}}}},
            recipe_semantic_fields=["Loan_Amount__c", "Property_Value__c"])
        with patch("primeqa.intelligence.s4_execution_console.read_run_detail",
                   return_value=detail):
            html = client.get(f"/runs/{RUN_ID}").get_data(as_text=True)
        assert "Test data (as staged)" in html, "TEST DATA section missing"
        assert "5,000,000" in html, "formatted staged value missing"
        assert "Read the record back" in html, "value-bearing read step missing"
        assert "expected 50 — matched" in html.replace("&#34;", '"'), \
            "expected-vs-actual sentence missing"
        assert "supporting field" in html, "padding drawer missing"
    results.append(test("A5. passed run renders the QA-readable result card",
                        test_readable_run_card_on_passed_run))

    def test_readable_run_card_suppressed_on_errored():
        vr_text = "KYC must be complete."
        steps = [{"kind": "create", "sobject": "Opportunity", "success": False,
                  "matched": None, "message": vr_text,
                  "field_values": {"Loan_Amount__c": 5000000},
                  "rejection_body": [{"message": vr_text, "fields": []}]}]
        detail = _run_detail(
            steps, outcome="errored",
            error={"phase": "create", "error_type": "AmbiguousRejection",
                   "message": "create rejected with no field attribution"})
        with patch("primeqa.intelligence.s4_execution_console.read_run_detail",
                   return_value=detail):
            html = client.get(f"/runs/{RUN_ID}").get_data(as_text=True)
        assert "Test data (as staged)" not in html, \
            "errored run must not narrate staged data as what-the-test-did"
        assert "Salesforce said:" in html, "the error card must still lead"
    results.append(test("A6. errored run suppresses the narrative, error leads",
                        test_readable_run_card_suppressed_on_errored))

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

    # ---- C — claim → requirement back-link --------------------------------

    def _detail():
        return {"available": True, "found": True, "claim": {
            "test_id": CLAIM_ID, "title": "Amount cap",
            "claim_kind": "prohibition-claim", "archetype": "prohibited_state",
            "depth": "behavioral", "status": "approved", "version_seq": 1,
            "asserted_truth": None, "semantic_conditions": None, "recipes": []}}

    def _detail_html_with_requirement(req_key):
        # claims_detail resolves the key→id via _requirement_rows on the v1 db;
        # patch the bridge (the key) and the resolver (the id) so the render is
        # deterministic and prod-write-free.
        with patch("primeqa.intelligence.s3_generation_console.read_claim_detail",
                   return_value=_detail()), \
             patch("primeqa.intelligence.s3_generation_console.read_claim_siblings",
                   return_value={"available": True, "siblings": []}), \
             patch("primeqa.intelligence.s4_execution_console.read_claim_runs",
                   return_value={"available": True, "runs": []}), \
             patch("primeqa.intelligence.s3_generation_console.read_claim_requirement",
                   return_value={"available": True, "requirement_key": req_key}), \
             patch("primeqa.intelligence.substrate_dashboard._requirement_rows",
                   return_value=({req_key: 42} if req_key else {})), \
             patch("primeqa.intelligence.quarantine.manual_states", return_value={}), \
             patch("primeqa.intelligence.quarantine.is_quarantined",
                   return_value=False):
            return client.get(f"/claims/{CLAIM_ID}").get_data(as_text=True)

    def test_requirement_backlink_resolved():
        html = _detail_html_with_requirement("SQ-205")
        assert "From requirement SQ-205" in html, "back-link text missing"
        assert 'href="/requirements/42"' in html, "back-link does not target the req"
    results.append(test("C1. claim detail links back to its resolved requirement",
                        test_requirement_backlink_resolved))

    def test_requirement_backlink_unresolved_is_plain_text():
        # the key exists but resolves to no id (manual req gone) → plain text, no link
        with patch("primeqa.intelligence.s3_generation_console.read_claim_detail",
                   return_value=_detail()), \
             patch("primeqa.intelligence.s3_generation_console.read_claim_siblings",
                   return_value={"available": True, "siblings": []}), \
             patch("primeqa.intelligence.s4_execution_console.read_claim_runs",
                   return_value={"available": True, "runs": []}), \
             patch("primeqa.intelligence.s3_generation_console.read_claim_requirement",
                   return_value={"available": True, "requirement_key": "req-999"}), \
             patch("primeqa.intelligence.substrate_dashboard._requirement_rows",
                   return_value={}), \
             patch("primeqa.intelligence.quarantine.manual_states", return_value={}), \
             patch("primeqa.intelligence.quarantine.is_quarantined",
                   return_value=False):
            html = client.get(f"/claims/{CLAIM_ID}").get_data(as_text=True)
        assert "From requirement req-999" in html, "unresolved key text missing"
        assert 'href="/requirements/' not in html, "should not link when unresolved"
    results.append(test("C2. an unresolvable requirement key shows as plain text",
                        test_requirement_backlink_unresolved_is_plain_text))

    passed = sum(results)
    total = len(results)
    print(f"\n{'='*60}\n  {passed}/{total} passed\n{'='*60}\n")
    return passed == total


if __name__ == "__main__":
    ok = run_tests()
    sys.exit(0 if ok else 1)
