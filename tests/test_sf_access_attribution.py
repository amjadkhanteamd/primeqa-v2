"""Item 2 — structured SF access-error attribution (pure unit tests).

Two pure surfaces:
  - SFRequestError.error_code / .fields — fail-safe parser of the SF error body.
  - classify_sf_exception — typed SF exception -> (failure_class, message);
    a non-SF exception -> (None, None) so the executor keeps its generic
    str(e) path unchanged.

The executor wiring (except -> classify_sf_exception -> failure_class persisted
on the RunStepResult, with expect_fail precedence preserved) is exercised end to
end by the existing tests/test_executor.py.
"""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from primeqa.execution.executor import StepExecutor, classify_sf_exception
from primeqa.integrations.exceptions import (
    SFAuthError, SFRateLimitError, SFRequestError,
)

INSUFFICIENT_BODY = (
    '[{"message":"insufficient access rights on cross-reference id",'
    '"errorCode":"INSUFFICIENT_ACCESS_OR_READONLY","fields":["Contract_Value__c"]}]'
)
INVALID_FIELD_BODY = (
    '[{"message":"No such column Bogus__c on entity Opportunity",'
    '"errorCode":"INVALID_FIELD","fields":["Bogus__c"]}]'
)


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


def run_tests():
    results = []
    print("\n=== Item 2 — SF access-error attribution (pure) ===\n")

    # ---- parser ----------------------------------------------------------
    def p1():
        e = SFRequestError("x", status_code=403, response_body=INSUFFICIENT_BODY)
        assert e.error_code == "INSUFFICIENT_ACCESS_OR_READONLY"
        assert e.fields == ["Contract_Value__c"]
    results.append(test("P1. well-formed array -> errorCode + fields", p1))

    def p2():
        e = SFRequestError("x", status_code=400,
                           response_body='{"errorCode":"INVALID_FIELD","fields":["A__c"]}')
        assert e.error_code == "INVALID_FIELD" and e.fields == ["A__c"]
    results.append(test("P2. single object (not array) parses", p2))

    def p3():
        e = SFRequestError("x", response_body='[{"errorCode":"INSUFFICIENT_ACCESS"}]')
        assert e.error_code == "INSUFFICIENT_ACCESS" and e.fields == []
    results.append(test("P3. missing fields -> [] (errorCode still parsed)", p3))

    def p4():
        # response_body is captured truncated (resp.text[:500]); a cut-off JSON
        # must degrade gracefully, never raise.
        e = SFRequestError("x", status_code=403,
                           response_body='[{"errorCode":"INSUFFICIENT_ACCESS_OR_RE')
        assert e.error_code is None and e.fields == []
    results.append(test("P4. truncated body -> None / [] (no raise)", p4))

    def p5():
        assert SFRequestError("x", response_body="not json").error_code is None
        assert SFRequestError("x", response_body="").fields == []
        assert SFRequestError("x", response_body=None).error_code is None
        assert SFRequestError("x", response_body="[]").error_code is None
    results.append(test("P5. malformed / empty / None -> None / [] (no raise)", p5))

    # ---- classify_sf_exception ------------------------------------------
    def c1():
        fc, msg = classify_sf_exception(SFRequestError(
            "HTTP 403", status_code=403, response_body=INSUFFICIENT_BODY))
        assert fc == "insufficient_access", fc
        assert "Contract_Value__c" in msg, msg
    results.append(test("C1. 403/INSUFFICIENT_ACCESS -> insufficient_access + field", c1))

    def c2():
        fc, msg = classify_sf_exception(SFRequestError(
            "HTTP 400", status_code=400, response_body=INVALID_FIELD_BODY))
        assert fc == "invalid_field", fc
        assert "Bogus__c" in msg, msg
    results.append(test("C2. 400/INVALID_FIELD -> invalid_field + field", c2))

    def c3():
        fc, msg = classify_sf_exception(SFAuthError("token refresh failed"))
        assert fc == "auth_error" and "auth" in msg.lower(), (fc, msg)
    results.append(test("C3. SFAuthError -> auth_error", c3))

    def c4():
        fc, msg = classify_sf_exception(SFRateLimitError("429 after retries"))
        assert fc == "transient_error", fc
    results.append(test("C4. SFRateLimitError -> transient_error", c4))

    def c5():
        fc, msg = classify_sf_exception(SFRequestError(
            "HTTP 503", status_code=503, response_body="<html>busy</html>"))
        assert fc == "transient_error", fc
    results.append(test("C5. SFRequestError 5xx -> transient_error", c5))

    def c6():
        # bare 400 with no parseable INVALID_FIELD code: classify generic, but
        # preserve status_code + raw body (NOT a bare str(e)).
        fc, msg = classify_sf_exception(SFRequestError(
            "HTTP 400", status_code=400,
            response_body='[{"errorCode":"MALFORMED_QUERY","message":"bad soql"}]'))
        assert fc == "step_error", fc
        assert "400" in msg and "MALFORMED_QUERY" in msg, msg
    results.append(test("C6. other/unparseable 4xx -> step_error, status+body preserved", c6))

    def c7():
        # non-SF exception: not classified -> executor keeps its generic path.
        assert classify_sf_exception(RuntimeError("boom")) == (None, None)
        assert classify_sf_exception(ValueError("x")) == (None, None)
    results.append(test("C7. generic non-SF Exception -> (None, None) [generic path preserved]", c7))

    # ---- executor wiring (mocked repo + SF; no DB) -----------------------
    def c8_wiring():
        # Prove the except -> classify -> failure_class persistence wiring in
        # execute_step without a live DB: mock the SF client (raises 403),
        # mock the step-result repo (captures the update payload), patch the
        # SSE emitter. The final update_step_result call must carry the
        # categorized failure_class + attributed message.
        sf = MagicMock()
        sf.update_record.side_effect = SFRequestError(
            "HTTP 403", status_code=403, response_body=INSUFFICIENT_BODY)
        step_repo = MagicMock()
        created = MagicMock()
        created.id = 999
        step_repo.create_step_result.return_value = created
        with patch("primeqa.execution.executor.run_streams", MagicMock()):
            ex = StepExecutor(sf, 1, "smart", step_repo, MagicMock(), MagicMock())
            _, status = ex.execute_step(123, {
                "step_order": 1, "action": "update", "target_object": "Opportunity",
                "record_ref": "006X", "field_values": {}})
        assert status == "error", status
        payload = step_repo.update_step_result.call_args.args[1]  # final (id, payload)
        assert payload.get("failure_class") == "insufficient_access", payload.get("failure_class")
        assert "Contract_Value__c" in (payload.get("error_message") or ""), payload.get("error_message")
    results.append(test("C8. executor wiring: SF 403 -> persisted failure_class + message", c8_wiring))

    def c9_expect_fail_precedence():
        # An expect_fail step that ALSO raises an SF access error: the SF error
        # IS the expected outcome, so expect_fail_class must win the threading
        # (`expect_fail_class or failure_class`), not the SF classification.
        sf = MagicMock()
        sf.update_record.side_effect = SFRequestError(
            "HTTP 403", status_code=403, response_body=INSUFFICIENT_BODY)
        step_repo = MagicMock()
        created = MagicMock()
        created.id = 999
        step_repo.create_step_result.return_value = created
        with patch("primeqa.execution.executor.run_streams", MagicMock()):
            ex = StepExecutor(sf, 1, "smart", step_repo, MagicMock(), MagicMock())
            _, status = ex.execute_step(123, {
                "step_order": 1, "action": "update", "target_object": "Opportunity",
                "record_ref": "006X", "field_values": {}, "expect_fail": True})
        payload = step_repo.update_step_result.call_args.args[1]
        assert payload.get("failure_class") == "expected_fail_verified", payload.get("failure_class")
        assert status == "passed", status
    results.append(test("C9. expect_fail precedence: expect_fail_class wins over SF class",
                        c9_expect_fail_precedence))

    def c10_clean_pass_writes_no_class():
        # A successful step writes no failure_class (stays None / absent).
        sf = MagicMock()
        sf.query.return_value = {
            "api_request": {"method": "GET", "url": "/query/", "body": {}},
            "api_response": {"status_code": 200, "body": {"totalSize": 0, "records": []}},
            "success": True, "record_id": None}
        step_repo = MagicMock()
        created = MagicMock()
        created.id = 999
        step_repo.create_step_result.return_value = created
        with patch("primeqa.execution.executor.run_streams", MagicMock()):
            ex = StepExecutor(sf, 1, "smart", step_repo, MagicMock(), MagicMock())
            _, status = ex.execute_step(123, {
                "step_order": 1, "action": "query", "target_object": "Opportunity",
                "soql": "SELECT Id FROM Opportunity LIMIT 1"})
        payload = step_repo.update_step_result.call_args.args[1]
        assert status == "passed", status
        assert payload.get("failure_class") is None, payload.get("failure_class")
    results.append(test("C10. clean pass writes no failure_class", c10_clean_pass_writes_no_class))

    passed, total = sum(results), len(results)
    print(f"\n{'='*60}\n  {passed}/{total} passed\n{'='*60}\n")
    return passed == total


if __name__ == "__main__":
    sys.exit(0 if run_tests() else 1)
