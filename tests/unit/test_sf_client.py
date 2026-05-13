"""Unit tests for primeqa.integrations.sf_client.SalesforceClient.

All HTTP mocked via httpx.MockTransport. No external dependencies, no DB.
Per Phase 2 plan §4.2.
"""
from __future__ import annotations

import json
import pytest
import httpx

from unittest import mock

from primeqa.integrations.sf_client import (
    SalesforceClient,
    SF_API_VERSION,
    MAX_RETRIES,
    RETRY_BACKOFF_SEQ,
    extract_picklist_value_payloads_from_metadata,
)
from primeqa.integrations.exceptions import (
    SFAuthError,
    SFRateLimitError,
    SFRequestError,
)


pytestmark = pytest.mark.unit


# ----------------------------------------------------------------------
# Fixtures / helpers
# ----------------------------------------------------------------------

INSTANCE_URL = "https://test.salesforce.com"
CLIENT_ID = "fake_client_id"
CLIENT_SECRET = "fake_client_secret"
REFRESH_TOKEN = "fake_refresh_token"


def _make_client(transport: httpx.MockTransport) -> SalesforceClient:
    """Build a SalesforceClient whose underlying httpx.Client uses a mock transport."""
    c = SalesforceClient(
        instance_url=INSTANCE_URL,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        refresh_token=REFRESH_TOKEN,
    )
    c._client.close()
    c._client = httpx.Client(transport=transport, timeout=5.0)
    return c


def _token_response(token: str = "MOCK_ACCESS_TOKEN") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "access_token": token,
            "instance_url": INSTANCE_URL,
            "token_type": "Bearer",
        },
    )


def _token_failure(status: int = 401, code: str = "invalid_grant") -> httpx.Response:
    return httpx.Response(
        status,
        json={"error": code, "error_description": "expired refresh token"},
    )


# ----------------------------------------------------------------------
# Token lifecycle
# ----------------------------------------------------------------------

class TestTokenLifecycle:
    def test_refresh_access_token_populates_token(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/services/oauth2/token"
            return _token_response("ABC123")

        c = _make_client(httpx.MockTransport(handler))
        c._refresh_access_token()
        assert c._access_token == "ABC123"
        c.close()

    def test_refresh_access_token_raises_sfautherror_on_4xx(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _token_failure(status=400, code="invalid_grant")

        c = _make_client(httpx.MockTransport(handler))
        with pytest.raises(SFAuthError) as exc_info:
            c._refresh_access_token()
        assert "Token refresh failed" in str(exc_info.value)
        assert "400" in str(exc_info.value)
        c.close()

    def test_refresh_access_token_raises_when_response_missing_token(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"instance_url": INSTANCE_URL})

        c = _make_client(httpx.MockTransport(handler))
        with pytest.raises(SFAuthError) as exc_info:
            c._refresh_access_token()
        assert "missing access_token" in str(exc_info.value)
        c.close()

    def test_ensure_access_token_idempotent(self) -> None:
        call_count = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            return _token_response()

        c = _make_client(httpx.MockTransport(handler))
        c._ensure_access_token()
        c._ensure_access_token()
        c._ensure_access_token()
        assert call_count["n"] == 1, "refresh should only happen once"
        c.close()

    def test_401_response_triggers_refresh_and_retry_once(self) -> None:
        responses = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                responses.append("token")
                return _token_response()
            responses.append("api")
            # First api call returns 401, second returns 200
            api_calls = [r for r in responses if r == "api"]
            if len(api_calls) == 1:
                return httpx.Response(401, json={"error": "INVALID_SESSION_ID"})
            return httpx.Response(200, json={"sobjects": [{"name": "Account"}]})

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_objects()
        assert result == [{"name": "Account"}]
        assert responses == ["token", "api", "token", "api"]
        c.close()

    def test_repeated_401_after_refresh_raises_sfautherror(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            return httpx.Response(401, json={"error": "INVALID_SESSION_ID"})

        c = _make_client(httpx.MockTransport(handler))
        with pytest.raises(SFAuthError) as exc_info:
            c.fetch_objects()
        assert "Repeated 401" in str(exc_info.value)
        c.close()


# ----------------------------------------------------------------------
# Retry logic
# ----------------------------------------------------------------------

class TestRetryLogic:
    def test_429_triggers_retry_with_backoff(self) -> None:
        seen_status = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            seen_status.append("api")
            n = len(seen_status)
            if n <= 2:
                return httpx.Response(429, text="Too Many Requests")
            return httpx.Response(200, json={"sobjects": []})

        c = _make_client(httpx.MockTransport(handler))
        with mock.patch("primeqa.integrations.sf_client.time.sleep") as msleep:
            result = c.fetch_objects()
        assert result == []
        # Two 429 responses → two backoffs (RETRY_BACKOFF_SEQ[0], [1])
        assert msleep.call_count == 2
        assert msleep.call_args_list[0][0][0] == RETRY_BACKOFF_SEQ[0]
        assert msleep.call_args_list[1][0][0] == RETRY_BACKOFF_SEQ[1]
        c.close()

    @pytest.mark.parametrize("status", [502, 503, 504])
    def test_5xx_transient_triggers_retry(self, status: int) -> None:
        attempts = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            attempts.append(1)
            if len(attempts) == 1:
                return httpx.Response(status, text="Transient")
            return httpx.Response(200, json={"sobjects": []})

        c = _make_client(httpx.MockTransport(handler))
        with mock.patch("primeqa.integrations.sf_client.time.sleep"):
            c.fetch_objects()
        assert len(attempts) == 2
        c.close()

    def test_persistent_429_raises_sfratelimiterror_after_max_retries(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            return httpx.Response(429, text="Too Many Requests")

        c = _make_client(httpx.MockTransport(handler))
        with mock.patch("primeqa.integrations.sf_client.time.sleep"):
            with pytest.raises(SFRateLimitError) as exc_info:
                c.fetch_objects()
        assert f"after {MAX_RETRIES} retries" in str(exc_info.value)
        c.close()

    @pytest.mark.parametrize("status", [400, 403, 404])
    def test_non_transient_4xx_raises_sfrequesterror_immediately(
        self, status: int,
    ) -> None:
        attempts = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            attempts.append(1)
            return httpx.Response(status, text="Bad request")

        c = _make_client(httpx.MockTransport(handler))
        with mock.patch("primeqa.integrations.sf_client.time.sleep") as msleep:
            with pytest.raises(SFRequestError) as exc_info:
                c.fetch_objects()
        assert exc_info.value.status_code == status
        assert msleep.call_count == 0  # no retries
        assert len(attempts) == 1  # called exactly once
        c.close()


# ----------------------------------------------------------------------
# fetch_objects
# ----------------------------------------------------------------------

class TestFetchObjects:
    def test_fetch_objects_returns_sobjects_list(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            return httpx.Response(200, json={
                "sobjects": [
                    {"name": "Account", "label": "Account"},
                    {"name": "Contact", "label": "Contact"},
                ]
            })

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_objects()
        assert len(result) == 2
        assert result[0]["name"] == "Account"
        c.close()

    def test_fetch_objects_uses_correct_endpoint(self) -> None:
        urls_seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            urls_seen.append(str(request.url))
            return httpx.Response(200, json={"sobjects": []})

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_objects()
        assert any(f"/services/data/{SF_API_VERSION}/sobjects" in u for u in urls_seen)
        c.close()


# ----------------------------------------------------------------------
# fetch_fields_for_object
# ----------------------------------------------------------------------

class TestFetchFieldsForObject:
    def test_fetch_fields_for_object_returns_fields_list(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            return httpx.Response(200, json={
                "name": "Account",
                "fields": [
                    {"name": "Name", "type": "string"},
                    {"name": "Industry", "type": "picklist"},
                ],
            })

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_fields_for_object("Account")
        assert len(result) == 2
        assert result[1]["name"] == "Industry"
        c.close()

    def test_fetch_fields_for_object_uses_correct_endpoint(self) -> None:
        urls_seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            urls_seen.append(request.url.path)
            return httpx.Response(200, json={"fields": []})

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_fields_for_object("Account")
        expected_path = f"/services/data/{SF_API_VERSION}/sobjects/Account/describe"
        assert expected_path in urls_seen
        c.close()

    def test_fetch_fields_for_object_url_encodes_name(self) -> None:
        urls_seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            urls_seen.append(request.url.path)
            return httpx.Response(200, json={"fields": []})

        c = _make_client(httpx.MockTransport(handler))
        # Custom field with __c suffix — verify the path is encoded properly
        c.fetch_fields_for_object("My_Object__c")
        expected_path = (
            f"/services/data/{SF_API_VERSION}/sobjects/My_Object__c/describe"
        )
        assert expected_path in urls_seen
        c.close()


# ----------------------------------------------------------------------
# fetch_fields_for_objects_bulk (composite/batch variant)
# ----------------------------------------------------------------------


def _composite_batch_handler(
    describes_by_name: dict[str, dict],
    failed_names: dict[str, int] | None = None,
):
    """Build a handler that returns composite/batch responses
    matching the live Salesforce shape.

    describes_by_name: {sObject_name: describe_dict_with_fields} for
        sObjects that should return statusCode=200.
    failed_names: {sObject_name: status_code} for sObjects that
        should return non-200 (404, 403, etc.) with an error array
        as result — matches the real "this sObject doesn't exist /
        you can't access" behavior.

    Captures all POST bodies in a list keyed by request order;
    accessible via the handler's `posted_bodies` attribute (a list).
    """
    failed_names = failed_names or {}
    posted_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/services/oauth2/token":
            return _token_response()
        # composite/batch is a POST with JSON body
        body = json.loads(request.content.decode())
        posted_bodies.append(body)
        sub_responses = []
        for sub in body["batchRequests"]:
            # Sub-request URL format: 'v66.0/sobjects/{name}/describe'
            # Extract the {name}.
            url = sub["url"]
            # url like 'v66.0/sobjects/Account/describe' (or
            # URL-encoded). We'll match by suffix.
            parts = url.split("/")
            # ['v66.0', 'sobjects', 'NAME', 'describe']
            name = urllib.parse.unquote(parts[2]) if len(parts) >= 4 else ""
            if name in failed_names:
                sub_responses.append({
                    "statusCode": failed_names[name],
                    "result": [{
                        "errorCode": "NOT_FOUND",
                        "message": f"No describe for {name}",
                    }],
                })
            elif name in describes_by_name:
                sub_responses.append({
                    "statusCode": 200,
                    "result": describes_by_name[name],
                })
            else:
                # Unknown name — treat as 404 (defensive)
                sub_responses.append({
                    "statusCode": 404,
                    "result": [{"errorCode": "NOT_FOUND",
                                 "message": f"Unknown {name}"}],
                })
        return httpx.Response(200, json={
            "hasErrors": any(r["statusCode"] != 200 for r in sub_responses),
            "results": sub_responses,
        })

    handler.posted_bodies = posted_bodies  # type: ignore[attr-defined]
    return handler


import urllib.parse
import json


class TestFetchFieldsForObjectsBulk:
    def test_empty_input_makes_no_api_calls(self) -> None:
        """Empty list short-circuits to empty dict; no composite/batch
        request issued."""
        posted: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            posted.append({"path": request.url.path})
            return httpx.Response(200, json={"hasErrors": False,
                                              "results": []})

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_fields_for_objects_bulk([])
        assert result == {}
        # No composite/batch hits (token endpoint may be hit during
        # client construction; filter to composite calls only).
        assert not any("composite/batch" in p["path"] for p in posted)
        c.close()

    def test_returns_fields_keyed_by_object_name(self) -> None:
        """Two-Object batch → result dict has both keys, each value
        is the describe.fields list."""
        describes = {
            "Account": {"fields": [
                {"name": "Id", "type": "id"},
                {"name": "Industry", "type": "picklist"},
            ]},
            "Contact": {"fields": [
                {"name": "Id", "type": "id"},
                {"name": "Email", "type": "email"},
            ]},
        }
        h = _composite_batch_handler(describes)
        c = _make_client(httpx.MockTransport(h))
        result = c.fetch_fields_for_objects_bulk(["Account", "Contact"])
        assert set(result.keys()) == {"Account", "Contact"}
        assert len(result["Account"]) == 2
        assert result["Account"][1]["name"] == "Industry"
        assert result["Contact"][1]["name"] == "Email"
        c.close()

    def test_single_batch_for_input_under_limit(self) -> None:
        """≤25 sObjects → exactly 1 composite/batch request issued."""
        names = [f"Obj_{i}__c" for i in range(20)]
        describes = {n: {"fields": [{"name": "Id"}]} for n in names}
        h = _composite_batch_handler(describes)
        c = _make_client(httpx.MockTransport(h))
        result = c.fetch_fields_for_objects_bulk(names)
        # All 20 sObjects landed in the result
        assert len(result) == 20
        # Exactly 1 composite/batch POST was issued
        assert len(h.posted_bodies) == 1
        # batchRequests has 20 sub-requests
        assert len(h.posted_bodies[0]["batchRequests"]) == 20
        c.close()

    def test_chunks_input_over_limit(self) -> None:
        """146 sObjects → 6 composite batches: 25, 25, 25, 25, 25, 21."""
        names = [f"Obj_{i}__c" for i in range(146)]
        describes = {n: {"fields": [{"name": "Id"}]} for n in names}
        h = _composite_batch_handler(describes)
        c = _make_client(httpx.MockTransport(h))
        result = c.fetch_fields_for_objects_bulk(names)
        # All 146 sObjects accounted for in result
        assert len(result) == 146
        # Exactly 6 composite batches
        assert len(h.posted_bodies) == 6
        chunk_sizes = [
            len(body["batchRequests"]) for body in h.posted_bodies
        ]
        assert chunk_sizes == [25, 25, 25, 25, 25, 21]
        c.close()

    def test_omits_failed_objects_from_result(self) -> None:
        """Per-Object 404 within a batch is silently omitted from
        the result dict. Other sObjects in the same batch return
        normally."""
        describes = {
            "Account": {"fields": [{"name": "Id"}]},
            "Contact": {"fields": [{"name": "Id"}]},
        }
        # Lead sub-request will return statusCode=404
        h = _composite_batch_handler(describes, failed_names={"Lead": 404})
        c = _make_client(httpx.MockTransport(h))
        result = c.fetch_fields_for_objects_bulk(
            ["Account", "Lead", "Contact"],
        )
        # Lead omitted; Account + Contact present
        assert set(result.keys()) == {"Account", "Contact"}
        c.close()

    def test_sub_request_url_format(self) -> None:
        """Sub-request URL uses api_version-prefixed relative path
        — Salesforce composite/batch requires this exact form
        (e.g., 'v66.0/sobjects/Account/describe'). Verifies the
        survey-confirmed format isn't accidentally dropped."""
        describes = {"Account": {"fields": []}}
        h = _composite_batch_handler(describes)
        c = _make_client(httpx.MockTransport(h))
        c.fetch_fields_for_objects_bulk(["Account"])
        assert len(h.posted_bodies) == 1
        sub_url = h.posted_bodies[0]["batchRequests"][0]["url"]
        assert sub_url == f"{SF_API_VERSION}/sobjects/Account/describe"
        c.close()

    def test_url_encodes_object_names(self) -> None:
        """Custom sObject names with special chars (rare but
        possible) get URL-encoded in the sub-request URL."""
        # __c suffix is common; encoding leaves it alone, but the
        # quote-with-safe='' call ensures any future special chars
        # would be handled.
        describes = {"My_Object__c": {"fields": []}}
        h = _composite_batch_handler(describes)
        c = _make_client(httpx.MockTransport(h))
        c.fetch_fields_for_objects_bulk(["My_Object__c"])
        sub_url = h.posted_bodies[0]["batchRequests"][0]["url"]
        assert "My_Object__c" in sub_url


# ----------------------------------------------------------------------
# fetch_validation_rules
# ----------------------------------------------------------------------

class TestFetchValidationRules:
    def test_fetch_validation_rules_uses_tooling_endpoint(self) -> None:
        urls_seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            urls_seen.append(request.url.path)
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_validation_rules()
        # Both phases use the tooling/query endpoint; phase 2 is skipped
        # when phase 1 returns empty, so we just need >=1 hit.
        assert any(
            f"/services/data/{SF_API_VERSION}/tooling/query" in p for p in urls_seen
        )
        c.close()

    def test_fetch_validation_rules_returns_records_list(self) -> None:
        """Two-phase fetch: phase 1 returns N records with EntityDefinitionId
        (NOT EntityDefinition.QualifiedApiName, NOT Metadata, NOT FullName).
        Phase 2 returns 1 record per Id with FullName + Metadata.
        Result merges FullName + Metadata onto each phase-1 record
        alongside EntityDefinitionId."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            # Phase 1: bulk SELECT (no Metadata, no FullName) → return
            # 2 records with EntityDefinitionId
            if "Metadata" not in soql:
                return httpx.Response(200, json={
                    "records": [
                        {
                            "Id": "03d000000000001",
                            "ValidationName": "AmountPositive",
                            "Active": True,
                            "ErrorMessage": "Amount must be positive",
                            "EntityDefinitionId": "01IF9000001CNEB",
                        },
                        {
                            "Id": "03d000000000002",
                            "ValidationName": "StatusValid",
                            "Active": True,
                            "ErrorMessage": "Status must be set",
                            "EntityDefinitionId": "01IF9000001CNEC",
                        },
                    ]
                })
            # Phase 2: per-Id SELECT Id, FullName, Metadata WHERE Id = '...'
            if "03d000000000001" in soql:
                return httpx.Response(200, json={
                    "records": [{
                        "Id": "03d000000000001",
                        "FullName": "Account.AmountPositive",
                        "Metadata": {"errorConditionFormula": "Amount <= 0"},
                    }]
                })
            if "03d000000000002" in soql:
                return httpx.Response(200, json={
                    "records": [{
                        "Id": "03d000000000002",
                        "FullName": "Opportunity.StatusValid",
                        "Metadata": {"errorConditionFormula": "ISBLANK(Status)"},
                    }]
                })
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_validation_rules()
        assert len(result) == 2
        assert result[0]["ValidationName"] == "AmountPositive"
        # EntityDefinitionId preserved from phase 1
        assert result[0]["EntityDefinitionId"] == "01IF9000001CNEB"
        assert result[1]["EntityDefinitionId"] == "01IF9000001CNEC"
        # FullName + Metadata merged onto phase-1 records from phase 2
        assert result[0]["FullName"] == "Account.AmountPositive"
        assert result[1]["FullName"] == "Opportunity.StatusValid"
        assert result[0]["Metadata"] == {"errorConditionFormula": "Amount <= 0"}
        assert result[1]["Metadata"] == {"errorConditionFormula": "ISBLANK(Status)"}
        c.close()

    def test_fetch_validation_rules_phase2_includes_full_name(
        self,
    ) -> None:
        """Phase 2 SOQL must include FullName (post-P5 enhancement).
        FullName is the canonical '{Object}.{ValidationName}'
        identifier the sync layer uses for external_id + parent-
        Object api_name extraction. Both FullName and Metadata
        share the 1-row constraint (§1) so they can co-fetch in
        a single SOQL call."""
        soqls_seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            soqls_seen.append(soql)
            if "Metadata" not in soql:
                return httpx.Response(200, json={
                    "records": [{"Id": "03d1", "ValidationName": "X"}],
                })
            return httpx.Response(200, json={
                "records": [{
                    "Id": "03d1",
                    "FullName": "Account.X",
                    "Metadata": {},
                }],
            })

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_validation_rules()
        # Result carries FullName
        assert result[0]["FullName"] == "Account.X"
        # Phase 2 SOQL included FullName + Metadata
        phase2_soqls = [s for s in soqls_seen if "Metadata" in s]
        assert len(phase2_soqls) == 1
        assert "FullName" in phase2_soqls[0]
        c.close()

    def test_fetch_validation_rules_soql_phase_split(self) -> None:
        """Phase 1 SOQL must NOT contain Metadata or FullName (both
        1-row-constrained). Phase 2 SOQL MUST contain Metadata +
        FullName + WHERE Id = '...' filter."""
        soqls_seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            soqls_seen.append(soql)
            if "Metadata" not in soql:
                # Phase 1: return one Id so phase 2 fires
                return httpx.Response(200, json={
                    "records": [{"Id": "03d000000000001", "ValidationName": "X"}]
                })
            # Phase 2
            return httpx.Response(200, json={
                "records": [{
                    "Id": "03d000000000001",
                    "FullName": "Account.X",
                    "Metadata": {},
                }]
            })

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_validation_rules()

        # Find the phase-1 and phase-2 SOQL queries
        phase1_soqls = [s for s in soqls_seen if "Metadata" not in s]
        phase2_soqls = [s for s in soqls_seen if "Metadata" in s]

        assert len(phase1_soqls) == 1, "Exactly one phase-1 bulk query expected"
        # Phase 1: must NOT contain Metadata or FullName (both 1-row-
        # constrained), must NOT join EntityDefinition, must include
        # EntityDefinitionId, must target ValidationRule.
        assert "Metadata" not in phase1_soqls[0]
        assert "FullName" not in phase1_soqls[0]
        assert "EntityDefinition." not in phase1_soqls[0]
        assert "EntityDefinitionId" in phase1_soqls[0]
        assert "ValidationRule" in phase1_soqls[0]

        assert len(phase2_soqls) >= 1, "At least one phase-2 per-Id query expected"
        assert "Metadata" in phase2_soqls[0]
        assert "FullName" in phase2_soqls[0]
        assert "WHERE Id =" in phase2_soqls[0]
        c.close()

    def test_fetch_validation_rules_phase1_does_not_traverse_entitydefinition(
        self,
    ) -> None:
        """Regression guard: Phase 1 SOQL must NOT join EntityDefinition.

        Salesforce Tooling API throws EXTERNAL_OBJECT_UNSUPPORTED_EXCEPTION
        when a query traverses an EntityDefinition relationship and that
        relationship's underlying row count exceeds 1000. Real sandboxes
        always exceed this. Use EntityDefinitionId direct field instead;
        sync layer resolves the Id → name from the Object describe cache.
        """
        soqls_seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soqls_seen.append(request.url.params.get("q", ""))
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_validation_rules()

        assert len(soqls_seen) == 1, "Empty phase 1 → no phase 2 expected"
        phase1 = soqls_seen[0]
        # The dot in 'EntityDefinition.' is the relationship-traversal marker.
        # EntityDefinitionId (no dot) is fine; EntityDefinition.* is not.
        assert "EntityDefinition." not in phase1, (
            f"Phase 1 SOQL must not traverse EntityDefinition relationship; "
            f"got: {phase1!r}"
        )
        c.close()

    def test_fetch_validation_rules_makes_n_plus_one_calls(self) -> None:
        """Phase 1 returns 3 IDs → exactly 3 phase-2 calls = 4 total Tooling
        API calls. Locks in the N+1 contract."""
        tooling_calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            tooling_calls.append(request.url.params.get("q", ""))
            soql = request.url.params.get("q", "")
            if "Metadata" not in soql:
                # Phase 1: return 3 IDs
                return httpx.Response(200, json={
                    "records": [
                        {"Id": "03d000000000001", "ValidationName": "R1"},
                        {"Id": "03d000000000002", "ValidationName": "R2"},
                        {"Id": "03d000000000003", "ValidationName": "R3"},
                    ]
                })
            # Phase 2: return one record matching the Id
            for rid in ("03d000000000001", "03d000000000002", "03d000000000003"):
                if rid in soql:
                    return httpx.Response(200, json={
                        "records": [{"Id": rid, "Metadata": {"key": rid}}]
                    })
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_validation_rules()
        # 1 phase-1 call + 3 phase-2 calls = 4 total Tooling API calls
        assert len(tooling_calls) == 4
        # Phase 1: exactly one query without Metadata
        assert sum(1 for s in tooling_calls if "Metadata" not in s) == 1
        # Phase 2: exactly three queries with Metadata
        assert sum(1 for s in tooling_calls if "Metadata" in s) == 3
        # All 3 records have Metadata merged in
        assert len(result) == 3
        for rec in result:
            assert rec.get("Metadata") is not None
        c.close()


# ----------------------------------------------------------------------
# Context manager
# ----------------------------------------------------------------------

class TestContextManager:
    def test_client_works_as_context_manager(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _token_response()

        c = _make_client(httpx.MockTransport(handler))
        # close() is idempotent on httpx.Client; just verify it doesn't error
        with c as client_in_block:
            assert client_in_block is c
        # After exit, close() has been called. Accessing _client should still work
        # (close is graceful).


# ----------------------------------------------------------------------
# fetch_record_types (2C-extended Method 1)
# ----------------------------------------------------------------------

class TestFetchRecordTypes:
    def test_fetch_record_types_uses_tooling_endpoint(self) -> None:
        urls_seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            urls_seen.append(request.url.path)
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_record_types()
        assert any(
            f"/services/data/{SF_API_VERSION}/tooling/query" in p for p in urls_seen
        )
        c.close()

    def test_fetch_record_types_returns_records_list(self) -> None:
        """Two-phase fetch: phase 1 returns N record types without
        FullName/Metadata; phase 2 returns 1 record per Id with both.
        Result merges FullName and Metadata onto each phase-1 record."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            # Phase 1: bulk SELECT (no FullName, no Metadata) → return 2 records
            if "Metadata" not in soql and "FullName" not in soql:
                return httpx.Response(200, json={
                    "records": [
                        {
                            "Id": "012000000000001",
                            "Name": "Customer",
                            "IsActive": True,
                            "Description": "Direct customer accounts",
                            "SobjectType": "Account",
                            "EntityDefinitionId": "01IF9000001CNEB",
                            "BusinessProcessId": None,
                            "ManageableState": "unmanaged",
                            "NamespacePrefix": None,
                        },
                        {
                            "Id": "012000000000002",
                            "Name": "Partner",
                            "IsActive": True,
                            "Description": "Channel partner accounts",
                            "SobjectType": "Account",
                            "EntityDefinitionId": "01IF9000001CNEB",
                            "BusinessProcessId": None,
                            "ManageableState": "unmanaged",
                            "NamespacePrefix": None,
                        },
                    ]
                })
            # Phase 2: per-Id SELECT Id, FullName, Metadata WHERE Id = '...'
            # First record: populated picklistValues element shape per Salesforce schema
            if "012000000000001" in soql:
                return httpx.Response(200, json={
                    "records": [{
                        "Id": "012000000000001",
                        "FullName": "Account.Customer",
                        "Metadata": {
                            "active": True,
                            "label": "Customer",
                            "description": "Direct customer accounts",
                            "businessProcess": None,
                            "compactLayoutAssignment": None,
                            "picklistValues": [
                                {
                                    "picklist": "Status",
                                    "values": [
                                        {"valueName": "Open", "default": True, "isActive": True},
                                        {"valueName": "Closed", "default": False, "isActive": True},
                                    ],
                                },
                            ],
                            "urls": None,
                        },
                    }]
                })
            if "012000000000002" in soql:
                return httpx.Response(200, json={
                    "records": [{
                        "Id": "012000000000002",
                        "FullName": "Account.Partner",
                        "Metadata": {
                            "active": True,
                            "label": "Partner",
                            "description": "Channel partner accounts",
                            "businessProcess": None,
                            "compactLayoutAssignment": None,
                            "picklistValues": [],
                            "urls": None,
                        },
                    }]
                })
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_record_types()
        assert len(result) == 2

        # Phase 1 fields preserved
        assert result[0]["Name"] == "Customer"
        assert result[0]["SobjectType"] == "Account"
        assert result[0]["EntityDefinitionId"] == "01IF9000001CNEB"
        assert result[0]["ManageableState"] == "unmanaged"

        # Phase 2 fields merged in
        assert result[0]["FullName"] == "Account.Customer"
        assert result[1]["FullName"] == "Account.Partner"
        assert result[0]["Metadata"]["active"] is True
        assert result[0]["Metadata"]["label"] == "Customer"

        # Populated picklistValues element shape exercised
        pv_lists = result[0]["Metadata"]["picklistValues"]
        assert len(pv_lists) == 1
        first_pv = pv_lists[0]
        assert first_pv["picklist"] == "Status"
        assert len(first_pv["values"]) == 2
        assert first_pv["values"][0]["valueName"] == "Open"
        assert first_pv["values"][0]["default"] is True
        assert first_pv["values"][0]["isActive"] is True

        # Empty picklistValues case handled too
        assert result[1]["Metadata"]["picklistValues"] == []
        c.close()

    def test_fetch_record_types_phase_split(self) -> None:
        """Phase 1 SOQL must NOT contain Metadata OR FullName.
        Phase 2 SOQL MUST contain both Metadata and FullName + WHERE Id."""
        soqls_seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            soqls_seen.append(soql)
            if "Metadata" not in soql and "FullName" not in soql:
                return httpx.Response(200, json={
                    "records": [{"Id": "012000000000001", "Name": "X"}]
                })
            return httpx.Response(200, json={
                "records": [{"Id": "012000000000001", "FullName": "Account.X", "Metadata": {}}]
            })

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_record_types()

        phase1_soqls = [s for s in soqls_seen if "Metadata" not in s and "FullName" not in s]
        phase2_soqls = [s for s in soqls_seen if "Metadata" in s and "FullName" in s]

        assert len(phase1_soqls) == 1, "Exactly one phase-1 bulk query expected"
        assert "Metadata" not in phase1_soqls[0]
        assert "FullName" not in phase1_soqls[0]
        assert "RecordType" in phase1_soqls[0]
        assert "EntityDefinitionId" in phase1_soqls[0]

        assert len(phase2_soqls) >= 1, "At least one phase-2 per-Id query expected"
        assert "Metadata" in phase2_soqls[0]
        assert "FullName" in phase2_soqls[0]
        assert "WHERE Id =" in phase2_soqls[0]
        c.close()

    def test_fetch_record_types_phase1_does_not_traverse_entitydefinition(
        self,
    ) -> None:
        """Regression guard: Phase 1 SOQL must NOT join EntityDefinition.

        Same constraint as ValidationRule's Phase 1 — managed-package-heavy
        orgs trip the EXTERNAL_OBJECT_UNSUPPORTED_EXCEPTION subquery limit
        when traversing EntityDefinition. Use EntityDefinitionId direct
        field instead.
        """
        soqls_seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soqls_seen.append(request.url.params.get("q", ""))
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_record_types()

        assert len(soqls_seen) == 1, "Empty phase 1 → no phase 2 expected"
        phase1 = soqls_seen[0]
        assert "EntityDefinition." not in phase1, (
            f"Phase 1 SOQL must not traverse EntityDefinition relationship; "
            f"got: {phase1!r}"
        )
        c.close()

    def test_fetch_record_types_makes_n_plus_one_calls(self) -> None:
        """Phase 1 returns 3 IDs → exactly 3 phase-2 calls = 4 total Tooling
        API calls. Locks in the N+1 contract for RecordType."""
        tooling_calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            tooling_calls.append(request.url.params.get("q", ""))
            soql = request.url.params.get("q", "")
            if "Metadata" not in soql and "FullName" not in soql:
                return httpx.Response(200, json={
                    "records": [
                        {"Id": "012000000000001", "Name": "RT1"},
                        {"Id": "012000000000002", "Name": "RT2"},
                        {"Id": "012000000000003", "Name": "RT3"},
                    ]
                })
            for rid in ("012000000000001", "012000000000002", "012000000000003"):
                if rid in soql:
                    return httpx.Response(200, json={
                        "records": [{
                            "Id": rid,
                            "FullName": f"Account.{rid}",
                            "Metadata": {"label": rid, "active": True, "picklistValues": []},
                        }]
                    })
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_record_types()
        assert len(tooling_calls) == 4
        # Phase 1: exactly one query without Metadata/FullName
        assert sum(1 for s in tooling_calls if "Metadata" not in s and "FullName" not in s) == 1
        # Phase 2: exactly three queries with both Metadata and FullName
        assert sum(1 for s in tooling_calls if "Metadata" in s and "FullName" in s) == 3
        # All 3 records have FullName + Metadata merged in
        assert len(result) == 3
        for rec in result:
            assert rec.get("FullName") is not None
            assert rec.get("Metadata") is not None
        c.close()


# ----------------------------------------------------------------------
# fetch_layouts_for_object (2C-extended Method 2)
# ----------------------------------------------------------------------

# Minimal-but-realistic mock response shape mirroring the live sandbox:
# 1 layout with 1 detail section, 1 row, 1 item, 1 layoutComponent, plus
# 1 recordTypeMapping and 1 recordTypeSelectorRequired entry.
def _layouts_describe_fixture() -> dict:
    return {
        "layouts": [
            {
                "id": "00h000000000001",
                "buttonLayoutSection": {"detailButtons": []},
                "detailLayoutSections": [
                    {
                        "heading": "Account Information",
                        "columns": 2,
                        "rows": 1,
                        "collapsed": False,
                        "useCollapsibleSection": False,
                        "useHeading": True,
                        "tabOrder": "LeftRight",
                        "layoutSectionId": "0Md000000000001",
                        "parentLayoutId": "00h000000000001",
                        "layoutRows": [
                            {
                                "numItems": 1,
                                "layoutItems": [
                                    {
                                        "label": "Account Name",
                                        "editableForNew": True,
                                        "editableForUpdate": True,
                                        "placeholder": False,
                                        "required": True,
                                        "uiBehavior": "Edit",
                                        "layoutComponents": [
                                            {"type": "Field", "value": "Name"},
                                        ],
                                    },
                                ],
                            },
                        ],
                    },
                ],
                "editLayoutSections": [],
                "multirowEditLayoutSections": [],
                "feedView": None,
                "highlightsPanelLayoutSection": None,
                "offlineLinks": [],
                "quickActionList": {"quickActionListItems": []},
                "relatedContent": None,
                "relatedLists": [],
                "saveOptions": [],
            },
        ],
        "recordTypeMappings": [
            {
                "recordTypeId": "012000000000000AAA",
                "layoutId": "00h000000000001",
                "name": "Master",
                "developerName": "Master",
                "active": True,
                "available": True,
                "defaultRecordTypeMapping": True,
                "master": True,
                "picklistsForRecordType": [],
                "urls": {"layout": "/services/data/v66.0/..."},
            },
        ],
        "recordTypeSelectorRequired": [True],
    }


class TestFetchLayoutsForObject:
    def test_fetch_layouts_uses_describe_endpoint(self) -> None:
        urls_seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            urls_seen.append(request.url.path)
            return httpx.Response(200, json=_layouts_describe_fixture())

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_layouts_for_object("Account")
        expected = f"/services/data/{SF_API_VERSION}/sobjects/Account/describe/layouts"
        assert expected in urls_seen
        c.close()

    def test_fetch_layouts_returns_full_response_dict(self) -> None:
        """All 3 top-level keys preserved (layouts, recordTypeMappings,
        recordTypeSelectorRequired). Transparent transport boundary;
        sync layer extracts what it needs."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            return httpx.Response(200, json=_layouts_describe_fixture())

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_layouts_for_object("Account")
        assert isinstance(result, dict)
        # All 3 top-level keys present
        assert "layouts" in result
        assert "recordTypeMappings" in result
        assert "recordTypeSelectorRequired" in result
        # Each is the right shape
        assert isinstance(result["layouts"], list)
        assert len(result["layouts"]) == 1
        assert isinstance(result["recordTypeMappings"], list)
        assert len(result["recordTypeMappings"]) == 1
        assert isinstance(result["recordTypeSelectorRequired"], list)
        assert len(result["recordTypeSelectorRequired"]) == 1
        c.close()

    def test_fetch_layouts_returns_layouts_with_nested_structure(self) -> None:
        """Nested structure detailLayoutSections -> layoutRows -> layoutItems
        -> layoutComponents survives the call. Multi-section layout."""
        # Build a 2-section layout, each section with 2 items
        fixture = _layouts_describe_fixture()
        # First section already exists; add a second with 2 items
        fixture["layouts"][0]["detailLayoutSections"].append({
            "heading": "Address Information",
            "columns": 2,
            "rows": 2,
            "collapsed": False,
            "useCollapsibleSection": False,
            "useHeading": True,
            "tabOrder": "LeftRight",
            "layoutSectionId": "0Md000000000002",
            "parentLayoutId": "00h000000000001",
            "layoutRows": [
                {
                    "numItems": 2,
                    "layoutItems": [
                        {"label": "Billing Street", "layoutComponents": [
                            {"type": "Field", "value": "BillingStreet"},
                        ]},
                        {"label": "Shipping Street", "layoutComponents": [
                            {"type": "Field", "value": "ShippingStreet"},
                        ]},
                    ],
                },
            ],
        })

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            return httpx.Response(200, json=fixture)

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_layouts_for_object("Account")

        layouts = result["layouts"]
        assert len(layouts) == 1
        sections = layouts[0]["detailLayoutSections"]
        assert len(sections) == 2
        # Section 1: 1 row with 1 item
        assert sections[0]["heading"] == "Account Information"
        assert len(sections[0]["layoutRows"]) == 1
        assert len(sections[0]["layoutRows"][0]["layoutItems"]) == 1
        # Section 2: 1 row with 2 items
        assert sections[1]["heading"] == "Address Information"
        assert len(sections[1]["layoutRows"]) == 1
        assert len(sections[1]["layoutRows"][0]["layoutItems"]) == 2
        # layoutComponents structure preserved
        components = sections[1]["layoutRows"][0]["layoutItems"][0]["layoutComponents"]
        assert components[0]["type"] == "Field"
        assert components[0]["value"] == "BillingStreet"
        c.close()

    @pytest.mark.parametrize("object_name", ["Account", "Contact", "Custom_Object__c"])
    def test_fetch_layouts_object_name_in_url(self, object_name: str) -> None:
        """URL is correctly constructed for standard, common, and custom (__c)
        object names. Custom-suffix __c must round-trip without erroneous
        URL encoding (urllib.parse.quote with safe='' should still produce
        Account, Contact, Custom_Object__c verbatim — underscore/letters
        are URL-safe)."""
        urls_seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            urls_seen.append(request.url.path)
            return httpx.Response(200, json=_layouts_describe_fixture())

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_layouts_for_object(object_name)
        expected = (
            f"/services/data/{SF_API_VERSION}"
            f"/sobjects/{object_name}/describe/layouts"
        )
        assert expected in urls_seen
        c.close()

    def test_fetch_layouts_makes_one_call(self) -> None:
        """Single REST call — no N+1 round-trips like Tooling-API methods."""
        api_calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            api_calls.append(request.url.path)
            return httpx.Response(200, json=_layouts_describe_fixture())

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_layouts_for_object("Account")
        assert len(api_calls) == 1, f"Expected exactly 1 API call, got {len(api_calls)}"
        c.close()


# ----------------------------------------------------------------------
# fetch_global_value_sets (2C-extended Method 3, GVS half)
# ----------------------------------------------------------------------

class TestFetchGlobalValueSets:
    def test_fetch_global_value_sets_uses_tooling_endpoint(self) -> None:
        urls_seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            urls_seen.append(request.url.path)
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_global_value_sets()
        assert any(
            f"/services/data/{SF_API_VERSION}/tooling/query" in p for p in urls_seen
        )
        c.close()

    def test_fetch_global_value_sets_returns_records_list(self) -> None:
        """Two-phase fetch: phase 1 returns N records (no Metadata, no
        FullName); phase 2 returns 1 row per Id with FullName + Metadata.
        Mock uses Salesforce-documented schema: Metadata.customValue is
        a list of dicts with fullName, default, isActive, label."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            # Phase 1: bulk SELECT (no Metadata, no FullName) → 2 records
            if "Metadata" not in soql and "FullName" not in soql:
                return httpx.Response(200, json={
                    "records": [
                        {
                            "Id": "0Nt000000000001",
                            "MasterLabel": "Region",
                            "Description": "Sales region picklist",
                            "ManageableState": "unmanaged",
                            "NamespacePrefix": None,
                        },
                        {
                            "Id": "0Nt000000000002",
                            "MasterLabel": "Tier",
                            "Description": "Account tier",
                            "ManageableState": "unmanaged",
                            "NamespacePrefix": None,
                        },
                    ]
                })
            # Phase 2: per-Id Metadata + FullName fetch
            if "0Nt000000000001" in soql:
                return httpx.Response(200, json={
                    "records": [{
                        "Id": "0Nt000000000001",
                        "FullName": "Region",
                        "Metadata": {
                            "masterLabel": "Region",
                            "description": "Sales region picklist",
                            "sorted": False,
                            "customValue": [
                                {"fullName": "NA", "default": True,
                                 "isActive": True, "label": "North America"},
                                {"fullName": "EU", "default": False,
                                 "isActive": True, "label": "Europe"},
                            ],
                        },
                    }]
                })
            if "0Nt000000000002" in soql:
                return httpx.Response(200, json={
                    "records": [{
                        "Id": "0Nt000000000002",
                        "FullName": "Tier",
                        "Metadata": {
                            "masterLabel": "Tier",
                            "description": "Account tier",
                            "sorted": False,
                            "customValue": [
                                {"fullName": "Gold", "default": False,
                                 "isActive": True, "label": "Gold"},
                            ],
                        },
                    }]
                })
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_global_value_sets()
        assert len(result) == 2
        assert result[0]["MasterLabel"] == "Region"
        # Phase 1 fields
        assert result[0]["Description"] == "Sales region picklist"
        assert result[0]["ManageableState"] == "unmanaged"
        # Phase 2 fields merged onto phase-1 records
        assert result[0]["FullName"] == "Region"
        assert "customValue" in result[0]["Metadata"]
        assert len(result[0]["Metadata"]["customValue"]) == 2
        assert result[0]["Metadata"]["customValue"][0]["fullName"] == "NA"
        assert result[1]["FullName"] == "Tier"
        c.close()

    def test_fetch_global_value_sets_phase_split(self) -> None:
        """Phase 1 SOQL must NOT contain Metadata or FullName.
        Phase 2 SOQL MUST contain Metadata + FullName + WHERE Id = '...'."""
        soqls_seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            soqls_seen.append(soql)
            if "Metadata" not in soql and "FullName" not in soql:
                return httpx.Response(200, json={
                    "records": [
                        {"Id": "0Nt000000000001", "MasterLabel": "X"},
                    ],
                })
            return httpx.Response(200, json={
                "records": [{
                    "Id": "0Nt000000000001",
                    "FullName": "X",
                    "Metadata": {},
                }],
            })

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_global_value_sets()

        # 2 SOQLs total: 1 phase-1 bulk + 1 phase-2 per-Id
        assert len(soqls_seen) == 2

        # Phase 1: no Metadata, no FullName
        phase1 = soqls_seen[0]
        assert "FROM GlobalValueSet" in phase1
        assert "Metadata" not in phase1
        assert "FullName" not in phase1
        assert "WHERE" not in phase1  # bulk, no filter

        # Phase 2: BOTH Metadata and FullName, with WHERE Id = '...'
        phase2 = soqls_seen[1]
        assert "Metadata" in phase2
        assert "FullName" in phase2
        assert "WHERE Id = '0Nt000000000001'" in phase2
        c.close()

    def test_fetch_global_value_sets_phase1_bulk_no_filter(self) -> None:
        """Phase 1 is unfiltered bulk SELECT — distinguishes from the
        SVS pattern where every query is filtered (reified-column
        constraint). Regression guard for category-1 vs category-3
        treatment."""
        soqls_seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            soqls_seen.append(soql)
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_global_value_sets()

        # Exactly 1 SOQL when phase 1 returns empty (no phase-2 iteration)
        assert len(soqls_seen) == 1
        phase1 = soqls_seen[0]
        assert "FROM GlobalValueSet" in phase1
        # No filter clause — confirms unfiltered enumeration
        assert "WHERE" not in phase1
        c.close()

    def test_fetch_global_value_sets_makes_n_plus_one_calls(self) -> None:
        """N records → N+1 API calls (1 bulk phase 1 + N per-Id phase 2)."""
        api_calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            api_calls.append(soql)
            if "Metadata" not in soql and "FullName" not in soql:
                # Phase 1: 3 records
                return httpx.Response(200, json={
                    "records": [
                        {"Id": f"0Nt00000000000{i}", "MasterLabel": f"L{i}"}
                        for i in (1, 2, 3)
                    ]
                })
            # Phase 2: each Id resolves to one record
            return httpx.Response(200, json={
                "records": [{
                    "Id": "0Nt000000000001",
                    "FullName": "X",
                    "Metadata": {},
                }],
            })

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_global_value_sets()
        assert len(result) == 3
        # 1 phase-1 call + 3 phase-2 calls
        assert len(api_calls) == 4
        c.close()


# ----------------------------------------------------------------------
# fetch_standard_value_sets (2C-extended Method 3, SVS half)
# ----------------------------------------------------------------------

class TestFetchStandardValueSets:
    def test_fetch_standard_value_sets_uses_tooling_endpoint(self) -> None:
        urls_seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            urls_seen.append(request.url.path)
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_standard_value_sets(labels=["Industry"])
        assert any(
            f"/services/data/{SF_API_VERSION}/tooling/query" in p for p in urls_seen
        )
        c.close()

    def test_fetch_standard_value_sets_default_iterates_full_catalog(self) -> None:
        """labels=None → iterate sf_constants.STANDARD_VALUE_SET_LABELS
        in order. One SOQL per label, each WHERE-filtered by MasterLabel.
        Verifies the 616-entry catalog drives default behaviour."""
        from primeqa.integrations.sf_constants import (
            STANDARD_VALUE_SET_LABELS,
        )

        soqls_seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soqls_seen.append(request.url.params.get("q", ""))
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_standard_value_sets()  # labels=None default

        # One SOQL per catalog label
        assert len(soqls_seen) == len(STANDARD_VALUE_SET_LABELS)
        assert len(soqls_seen) == 616  # locks the catalog size

        # Every query filters by MasterLabel and the labels appear in
        # the same order as STANDARD_VALUE_SET_LABELS
        for label, soql in zip(STANDARD_VALUE_SET_LABELS, soqls_seen):
            assert f"WHERE MasterLabel = '{label}'" in soql
        c.close()

    def test_fetch_standard_value_sets_subset_iterates_provided_labels(self) -> None:
        """labels=<iterable> → exactly the provided labels, in the
        provided order. No reference to the canonical catalog."""
        soqls_seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soqls_seen.append(request.url.params.get("q", ""))
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_standard_value_sets(
            labels=["Industry", "CaseStatus", "LeadSource"]
        )

        assert len(soqls_seen) == 3
        assert "WHERE MasterLabel = 'Industry'" in soqls_seen[0]
        assert "WHERE MasterLabel = 'CaseStatus'" in soqls_seen[1]
        assert "WHERE MasterLabel = 'LeadSource'" in soqls_seen[2]
        c.close()

    def test_fetch_standard_value_sets_soql_shape(self) -> None:
        """Each query SELECTs Id, MasterLabel, FullName, Metadata
        FROM StandardValueSet WHERE MasterLabel = '<label>'."""
        soqls_seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soqls_seen.append(request.url.params.get("q", ""))
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_standard_value_sets(labels=["Industry"])

        soql = soqls_seen[0]
        # SELECT clause: all 4 expected fields
        assert "Id" in soql
        assert "MasterLabel" in soql
        assert "FullName" in soql
        assert "Metadata" in soql
        # FROM clause
        assert "FROM StandardValueSet" in soql
        # WHERE clause with MasterLabel filter (the reified-column
        # constraint pattern from corrections-log §4)
        assert "WHERE MasterLabel = 'Industry'" in soql
        c.close()

    def test_fetch_standard_value_sets_skips_empty_results(self) -> None:
        """Some labels return 0 rows on uncustomized orgs (per §5).
        Those are silently skipped — only non-empty results in output."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            if "MasterLabel = 'Industry'" in soql:
                return httpx.Response(200, json={
                    "records": [{
                        "Id": "0VS000000000001",
                        "MasterLabel": "Industry",
                        "FullName": "Industry",
                        "Metadata": {"standardValue": []},
                    }],
                })
            if "MasterLabel = 'AccountType'" in soql:
                return httpx.Response(200, json={
                    "records": [{
                        "Id": "0VS000000000002",
                        "MasterLabel": "AccountType",
                        "FullName": "AccountType",
                        "Metadata": {"standardValue": []},
                    }],
                })
            # All other labels return empty (e.g., uncustomized built-in)
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_standard_value_sets(
            labels=["Industry", "ZZZ_Empty1", "AccountType", "ZZZ_Empty2"]
        )

        # Only the 2 populated labels appear in the result
        assert len(result) == 2
        assert {r["MasterLabel"] for r in result} == {"Industry", "AccountType"}
        c.close()

    def test_fetch_standard_value_sets_makes_n_calls_not_n_plus_one(self) -> None:
        """No bulk phase exists for SVS (reified-column constraint).
        Every label is one call. Distinct from category-2 N+1 pattern."""
        api_calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            api_calls.append(request.url.params.get("q", ""))
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        labels = ["Industry", "CaseStatus", "OpportunityStage",
                  "LeadStatus", "OrderStatus"]
        c.fetch_standard_value_sets(labels=labels)
        # Exactly N calls (not N+1; no phase-1 bulk)
        assert len(api_calls) == len(labels) == 5
        c.close()

    def test_fetch_standard_value_sets_tolerates_per_label_500(self) -> None:
        """Industry-cloud SVSes return HTTP 500 in orgs where the
        corresponding cloud is not enabled (~32% of catalog in a
        no-cloud sandbox). A single label's failure must not abort
        the iteration; the consumer should still receive the
        subset the org supports. Per corrections-log §6 category 3
        + §8 addendum."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            # Healthy labels return populated records
            if "MasterLabel = 'AccountType'" in soql:
                return httpx.Response(200, json={
                    "records": [{
                        "Id": "0VS000000000001",
                        "MasterLabel": "AccountType",
                        "FullName": "AccountType",
                        "Metadata": {"standardValue": []},
                    }],
                })
            if "MasterLabel = 'Industry'" in soql:
                return httpx.Response(200, json={
                    "records": [{
                        "Id": "0VS000000000002",
                        "MasterLabel": "Industry",
                        "FullName": "Industry",
                        "Metadata": {"standardValue": []},
                    }],
                })
            # Industry-cloud-style label returns persistent 500
            # (HTTP 500 is not in TRANSIENT_STATUS_CODES, so it
            # raises SFRequestError immediately).
            return httpx.Response(500, json={"errorCode": "UNKNOWN",
                                              "message": "cloud not enabled"})

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_standard_value_sets(
            labels=["AccountType", "HealthCloudFoo", "Industry",
                    "PublicSectorBar"],
        )
        # Only the 2 healthy labels surface; the 2 failed labels
        # are skipped (no exception bubbles).
        assert len(result) == 2
        assert {r["MasterLabel"] for r in result} == {
            "AccountType", "Industry",
        }
        c.close()

    def test_fetch_standard_value_sets_auth_error_still_aborts(
        self,
    ) -> None:
        """SFAuthError signals an infrastructure/credential issue
        that should still abort the iteration — the per-label
        try/except deliberately does NOT swallow it."""
        from primeqa.integrations.exceptions import SFAuthError
        call_count = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                # First token call OK; subsequent refresh attempt
                # (after 401) also returns 401 → SFAuthError.
                if call_count["n"] == 0:
                    call_count["n"] += 1
                    return _token_response()
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(401, json={"error": "expired"})

        c = _make_client(httpx.MockTransport(handler))
        with pytest.raises(SFAuthError):
            c.fetch_standard_value_sets(labels=["AccountType", "Industry"])
        c.close()


# ----------------------------------------------------------------------
# fetch_profiles (2C-extended Method 4, Profile half — Category 2)
# ----------------------------------------------------------------------

class TestFetchProfiles:
    def test_fetch_profiles_uses_tooling_endpoint(self) -> None:
        urls_seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            urls_seen.append(request.url.path)
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_profiles()
        assert any(
            f"/services/data/{SF_API_VERSION}/tooling/query" in p for p in urls_seen
        )
        c.close()

    def test_fetch_profiles_returns_records_list(self) -> None:
        """Two-phase fetch: phase 1 returns N records (no Metadata, no
        FullName); phase 2 returns 1 row per Id with FullName + Metadata.
        Mock uses Salesforce-documented Profile.Metadata schema with
        objectPermissions, fieldPermissions, userPermissions arrays."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            # Phase 1: no Metadata, no FullName → return 2 profiles
            if "Metadata" not in soql and "FullName" not in soql:
                return httpx.Response(200, json={
                    "records": [
                        {
                            "Id": "00e000000000001",
                            "Name": "System Administrator",
                            "Description": None,
                            "CreatedDate": "2024-01-01T00:00:00.000+0000",
                            "LastModifiedDate": "2024-06-01T00:00:00.000+0000",
                        },
                        {
                            "Id": "00e000000000002",
                            "Name": "Standard User",
                            "Description": None,
                            "CreatedDate": "2024-01-01T00:00:00.000+0000",
                            "LastModifiedDate": "2024-06-01T00:00:00.000+0000",
                        },
                    ],
                })
            if "00e000000000001" in soql:
                return httpx.Response(200, json={
                    "records": [{
                        "Id": "00e000000000001",
                        "FullName": "Admin",
                        "Metadata": {
                            "userLicense": "Salesforce",
                            "objectPermissions": [
                                {"object": "Account", "allowRead": True,
                                 "allowCreate": True, "allowEdit": True,
                                 "allowDelete": True,
                                 "modifyAllRecords": True,
                                 "viewAllRecords": True,
                                 "viewAllFields": False,
                                 "customizeSetup": False,
                                 "deleteSetup": False,
                                 "viewSetup": True},
                                {"object": "Contact", "allowRead": True,
                                 "allowCreate": True, "allowEdit": True,
                                 "allowDelete": False,
                                 "modifyAllRecords": False,
                                 "viewAllRecords": False,
                                 "viewAllFields": False,
                                 "customizeSetup": False,
                                 "deleteSetup": False,
                                 "viewSetup": False},
                            ],
                            "fieldPermissions": [
                                {"field": "Account.Name",
                                 "readable": True, "editable": True},
                                {"field": "Account.Industry",
                                 "readable": True, "editable": True},
                                {"field": "Contact.Email",
                                 "readable": True, "editable": True},
                            ],
                            "userPermissions": [
                                {"name": "ApiEnabled", "enabled": True},
                                {"name": "ViewSetup", "enabled": True},
                            ],
                        },
                    }],
                })
            if "00e000000000002" in soql:
                return httpx.Response(200, json={
                    "records": [{
                        "Id": "00e000000000002",
                        "FullName": "Standard",
                        "Metadata": {
                            "userLicense": "Salesforce",
                            "objectPermissions": [],
                            "fieldPermissions": [],
                            "userPermissions": [
                                {"name": "ApiEnabled", "enabled": False},
                            ],
                        },
                    }],
                })
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_profiles()
        assert len(result) == 2
        # Phase 1 fields preserved
        assert result[0]["Name"] == "System Administrator"
        assert result[1]["Name"] == "Standard User"
        # Phase 2 merged
        assert result[0]["FullName"] == "Admin"
        assert result[1]["FullName"] == "Standard"
        # Metadata structure
        md = result[0]["Metadata"]
        assert md["userLicense"] == "Salesforce"
        assert len(md["objectPermissions"]) == 2
        assert len(md["fieldPermissions"]) == 3
        assert len(md["userPermissions"]) == 2
        c.close()

    def test_fetch_profiles_phase_split(self) -> None:
        """Phase 1 SOQL must NOT contain Metadata or FullName.
        Phase 2 SOQL MUST contain BOTH + WHERE Id = '...'."""
        soqls_seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            soqls_seen.append(soql)
            if "Metadata" not in soql and "FullName" not in soql:
                return httpx.Response(200, json={
                    "records": [{"Id": "00e000000000001", "Name": "X"}],
                })
            return httpx.Response(200, json={
                "records": [{
                    "Id": "00e000000000001",
                    "FullName": "X",
                    "Metadata": {},
                }],
            })

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_profiles()

        # 2 SOQLs: 1 phase-1 bulk + 1 phase-2 per-Id
        assert len(soqls_seen) == 2

        # Phase 1: no Metadata, no FullName, no WHERE
        phase1 = soqls_seen[0]
        assert "FROM Profile" in phase1
        assert "Metadata" not in phase1
        assert "FullName" not in phase1
        assert "WHERE" not in phase1

        # Phase 2: BOTH Metadata and FullName, WHERE Id = '...'
        phase2 = soqls_seen[1]
        assert "Metadata" in phase2
        assert "FullName" in phase2
        assert "WHERE Id = '00e000000000001'" in phase2
        c.close()

    def test_fetch_profiles_phase1_does_not_traverse_entitydefinition(self) -> None:
        """Regression guard: Phase 1 must NOT traverse any relationship
        (no '.' joins, no EntityDefinition.X). Profile schema is flat
        (Tooling describe shows 9 fields, none are reference traversals
        we need)."""
        soqls_seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soqls_seen.append(request.url.params.get("q", ""))
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_profiles()

        # Phase 1 query — no relationship traversals
        phase1 = soqls_seen[0]
        # No EntityDefinition reference at all
        assert "EntityDefinition" not in phase1
        # No relationship dots between FROM and end of statement (only
        # the simple field-list)
        assert "." not in phase1.split("FROM")[0]
        c.close()

    def test_fetch_profiles_makes_n_plus_one_calls(self) -> None:
        """N profiles → N+1 API calls (1 bulk phase 1 + N per-Id phase 2)."""
        api_calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            api_calls.append(soql)
            if "Metadata" not in soql and "FullName" not in soql:
                # Phase 1: 4 profiles
                return httpx.Response(200, json={
                    "records": [
                        {"Id": f"00e00000000000{i}", "Name": f"P{i}"}
                        for i in (1, 2, 3, 4)
                    ],
                })
            return httpx.Response(200, json={
                "records": [{
                    "Id": "00e000000000001",
                    "FullName": "X",
                    "Metadata": {},
                }],
            })

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_profiles()
        assert len(result) == 4
        # 1 phase-1 + 4 phase-2 = 5 calls
        assert len(api_calls) == 5
        c.close()


# ----------------------------------------------------------------------
# fetch_permission_sets (2C-extended Method 4, PermissionSet half —
# Category 4: wide-flat-row + child entities)
# ----------------------------------------------------------------------

class TestFetchPermissionSets:
    def test_fetch_permission_sets_uses_correct_endpoints(self) -> None:
        """Endpoint asymmetry: parent query goes through Tooling API
        (FIELDS(STANDARD) is Tooling-only); child queries
        (ObjectPermissions, FieldPermissions) go through the Data API
        because the Tooling API rejects them with INVALID_TYPE."""
        urls_seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            urls_seen.append(request.url.path)
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_permission_sets()
        # ≥1 call goes to Tooling (parent FIELDS(STANDARD))
        tooling_hits = [p for p in urls_seen
                        if p == f"/services/data/{SF_API_VERSION}/tooling/query/"]
        assert len(tooling_hits) >= 1
        # ≥1 call goes to Data API (children ObjectPermissions /
        # FieldPermissions)
        data_hits = [p for p in urls_seen
                     if p == f"/services/data/{SF_API_VERSION}/query/"]
        assert len(data_hits) >= 1
        c.close()

    def test_fetch_permission_sets_returns_five_key_dict(self) -> None:
        """Result must be a dict with exactly five keys —
        permission_sets / object_permissions / field_permissions /
        psg_components / license_id_to_label — matching the
        Category 4 structured-dict precedent. Cycle 9 (PermissionSet
        phase) extended the original three-key shape to support
        INHERITS_PERMISSION_SET edges and license_type resolution."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            if "FROM PermissionSetGroupComponent" in soql:
                return httpx.Response(200, json={
                    "records": [
                        {"Id": "0PCxxxx",
                         "PermissionSetGroupId": "0PSyyyy",
                         "PermissionSetId": "0PSzzzz"},
                    ],
                })
            if "FROM PermissionSetLicense" in soql:
                return httpx.Response(200, json={
                    "records": [
                        {"Id": "0PLxxxx",
                         "MasterLabel": "Salesforce Platform"},
                    ],
                })
            if "FROM PermissionSet" in soql:
                return httpx.Response(200, json={
                    "records": [
                        {"Id": "0PS000000000001", "Name": "Custom",
                         "Type": "Regular",
                         "PermissionsApiEnabled": True},
                    ],
                })
            if "FROM ObjectPermissions" in soql:
                return httpx.Response(200, json={
                    "records": [
                        {"ParentId": "0PS000000000001",
                         "SobjectType": "Account",
                         "PermissionsRead": True,
                         "PermissionsCreate": True,
                         "PermissionsEdit": True,
                         "PermissionsDelete": False,
                         "PermissionsModifyAllRecords": False,
                         "PermissionsViewAllRecords": False},
                    ],
                })
            if "FROM FieldPermissions" in soql:
                return httpx.Response(200, json={
                    "records": [
                        {"ParentId": "0PS000000000001",
                         "Field": "Account.Industry",
                         "PermissionsRead": True,
                         "PermissionsEdit": True},
                    ],
                })
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_permission_sets()
        assert isinstance(result, dict)
        assert set(result.keys()) == {
            "permission_sets",
            "object_permissions",
            "field_permissions",
            "psg_components",
            "license_id_to_label",
        }
        assert isinstance(result["permission_sets"], list)
        assert isinstance(result["object_permissions"], list)
        assert isinstance(result["field_permissions"], list)
        assert isinstance(result["psg_components"], list)
        assert isinstance(result["license_id_to_label"], dict)
        assert len(result["permission_sets"]) == 1
        assert len(result["object_permissions"]) == 1
        assert len(result["field_permissions"]) == 1
        assert len(result["psg_components"]) == 1
        assert result["license_id_to_label"] == {
            "0PLxxxx": "Salesforce Platform",
        }
        c.close()

    def test_fetch_permission_sets_makes_five_calls(self) -> None:
        """Exactly five SOQL queries: parent + ObjectPermissions
        + FieldPermissions + PermissionSetGroupComponent +
        PermissionSetLicense. No N+1, no per-PS iteration."""
        api_calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            api_calls.append(request.url.params.get("q", ""))
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_permission_sets()
        assert len(api_calls) == 5
        c.close()

    def test_fetch_permission_sets_parent_query_uses_fields_standard(self) -> None:
        """Parent SOQL uses FIELDS(STANDARD) per FIELDS-syntax confirmed
        live on Tooling API v66.0 (returns 408 columns: 390 Permissions*
        booleans + 18 descriptive)."""
        soqls_seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soqls_seen.append(request.url.params.get("q", ""))
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_permission_sets()
        # Find the parent query (the FROM PermissionSet one — NOT the
        # child entities PermissionSetGroupComponent / PermissionSetLicense
        # which also match "FROM PermissionSet" as a prefix).
        parent_soqls = [
            s for s in soqls_seen
            if "FROM PermissionSet " in s + " "
            and "FROM PermissionSetGroupComponent" not in s
            and "FROM PermissionSetLicense" not in s
        ]
        assert len(parent_soqls) == 1
        parent = parent_soqls[0]
        # FIELDS(STANDARD) is the canonical wide-SELECT shorthand
        assert "FIELDS(STANDARD)" in parent.upper()
        assert "FROM PermissionSet" in parent
        c.close()

    def test_fetch_permission_sets_object_permissions_query_shape(self) -> None:
        """ObjectPermissions query SELECTs ParentId + SobjectType +
        Permissions* fields. Entity-wide — no WHERE clause. Goes through
        the Data API endpoint, NOT Tooling (Tooling rejects this entity
        with INVALID_TYPE)."""
        seen: list[tuple[str, str]] = []  # (path, soql)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            seen.append((request.url.path, request.url.params.get("q", "")))
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_permission_sets()
        op_calls = [(path, q) for (path, q) in seen
                    if "FROM ObjectPermissions" in q]
        assert len(op_calls) == 1
        op_path, op = op_calls[0]
        # Endpoint check: Data API, NOT Tooling
        assert op_path == f"/services/data/{SF_API_VERSION}/query/"
        assert "tooling" not in op_path
        # Required SELECT fields
        assert "ParentId" in op
        assert "SobjectType" in op
        # At least one permissions column
        assert "Permissions" in op
        # No filter — entity-wide query
        assert "WHERE" not in op
        c.close()

    def test_fetch_permission_sets_field_permissions_query_shape(self) -> None:
        """FieldPermissions query SELECTs ParentId + Field + Permissions*.
        Entity-wide — no WHERE clause. Goes through the Data API
        endpoint, NOT Tooling (same INVALID_TYPE rationale as
        ObjectPermissions)."""
        seen: list[tuple[str, str]] = []  # (path, soql)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            seen.append((request.url.path, request.url.params.get("q", "")))
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_permission_sets()
        fp_calls = [(path, q) for (path, q) in seen
                    if "FROM FieldPermissions" in q]
        assert len(fp_calls) == 1
        fp_path, fp = fp_calls[0]
        # Endpoint check: Data API, NOT Tooling
        assert fp_path == f"/services/data/{SF_API_VERSION}/query/"
        assert "tooling" not in fp_path
        # Required SELECT fields
        assert "ParentId" in fp
        assert "Field" in fp
        # At least one permissions column
        assert "Permissions" in fp
        # No filter — entity-wide query
        assert "WHERE" not in fp
        c.close()

    def test_fetch_permission_sets_includes_synthetic_profile_rows(self) -> None:
        """Type='Profile' synthetic auto-gen rows appear in results.
        Filtering them is a sync-layer concern (per corrections-log §5),
        not fetch's responsibility — transparent transport boundary."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            if "FROM PermissionSet" in soql and "Component" not in soql \
               and "License" not in soql:
                return httpx.Response(200, json={
                    "records": [
                        {"Id": "0PS000000000001", "Name": "Custom",
                         "Type": "Regular"},
                        {"Id": "0PS000000000002", "Name": "X00e00001",
                         "Type": "Profile"},  # synthetic auto-gen
                        {"Id": "0PS000000000003", "Name": "Standard",
                         "Type": "Standard"},
                    ],
                })
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_permission_sets()
        # All 3 rows present — Type='Profile' NOT filtered at fetch layer
        assert len(result["permission_sets"]) == 3
        types = {r["Type"] for r in result["permission_sets"]}
        assert types == {"Regular", "Profile", "Standard"}
        c.close()

    def test_fetch_permission_sets_psg_components_query_shape(
        self,
    ) -> None:
        """PermissionSetGroupComponent query SELECTs Id +
        PermissionSetGroupId + PermissionSetId. Entity-wide (no WHERE).
        Goes through Data API (not Tooling)."""
        seen: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            seen.append((request.url.path,
                         request.url.params.get("q", "")))
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_permission_sets()
        psg_calls = [
            (path, q) for (path, q) in seen
            if "FROM PermissionSetGroupComponent" in q
        ]
        assert len(psg_calls) == 1
        psg_path, psg_q = psg_calls[0]
        assert psg_path == f"/services/data/{SF_API_VERSION}/query/"
        assert "PermissionSetGroupId" in psg_q
        assert "PermissionSetId" in psg_q
        assert "WHERE" not in psg_q
        c.close()

    def test_fetch_permission_sets_license_query_shape_and_map(
        self,
    ) -> None:
        """PermissionSetLicense query SELECTs Id + MasterLabel. The
        fetcher transforms the rows into an Id → MasterLabel dict
        (not a list) so downstream callers can do O(1) lookups."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            if "FROM PermissionSetLicense" in soql:
                return httpx.Response(200, json={
                    "records": [
                        {"Id": "0PL_AAA",
                         "MasterLabel": "Salesforce"},
                        {"Id": "0PL_BBB",
                         "MasterLabel": "Salesforce Platform"},
                    ],
                })
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_permission_sets()
        assert result["license_id_to_label"] == {
            "0PL_AAA": "Salesforce",
            "0PL_BBB": "Salesforce Platform",
        }
        c.close()

    def test_fetch_permission_sets_license_query_failure_returns_empty_map(
        self,
    ) -> None:
        """Fault tolerance: if PermissionSetLicense is inaccessible
        (returns 400/403), the fetcher logs a warning and returns an
        empty license_id_to_label map. Downstream mapper falls back
        to the '(no license)' sentinel — sync continues."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            if "FROM PermissionSetLicense" in soql:
                # Simulate org-level access denial
                return httpx.Response(403, json={
                    "errorCode": "FORBIDDEN",
                    "message": "no access to PermissionSetLicense",
                })
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_permission_sets()
        # Empty map on failure; other 4 keys present and well-formed
        assert result["license_id_to_label"] == {}
        assert "permission_sets" in result
        assert "psg_components" in result
        c.close()

    def test_fetch_permission_sets_psg_components_failure_returns_empty(
        self,
    ) -> None:
        """Fault tolerance for PSG: if PermissionSetGroupComponent
        is inaccessible, the fetcher logs and returns an empty
        psg_components list. INHERITS_PERMISSION_SET edges become
        empty for this sync; everything else continues."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            if "FROM PermissionSetGroupComponent" in soql:
                return httpx.Response(400, json={
                    "errorCode": "INVALID_TYPE",
                    "message": "PSG feature not enabled",
                })
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_permission_sets()
        assert result["psg_components"] == []
        c.close()


# ----------------------------------------------------------------------
# _query_all — SOQL pagination helper (corrections-log §6)
# ----------------------------------------------------------------------

class TestQueryAll:
    def test_query_all_single_page(self) -> None:
        """done=true on first response → one HTTP call, all records
        returned."""
        api_calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            api_calls.append((request.url.path, dict(request.url.params)))
            return httpx.Response(200, json={
                "totalSize": 5,
                "done": True,
                "records": [{"Id": f"00100000000000{i}"} for i in range(5)],
            })

        c = _make_client(httpx.MockTransport(handler))
        path = f"/services/data/{SF_API_VERSION}/tooling/query/"
        result = c._query_all(path, "SELECT Id FROM SomeEntity")
        assert len(result) == 5
        # Exactly 1 SOQL call (token call doesn't count)
        assert len(api_calls) == 1
        assert api_calls[0][0] == path
        assert api_calls[0][1].get("q") == "SELECT Id FROM SomeEntity"
        c.close()

    def test_query_all_walks_pagination(self) -> None:
        """done=false + nextRecordsUrl → walk cursor until done.
        Aggregates records across pages. Cursor calls don't pass q
        param (cursor encodes the query state in the path)."""
        api_calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            api_calls.append((request.url.path, dict(request.url.params)))
            # Initial query: done=false, 2000 records, give cursor
            if request.url.params.get("q"):
                return httpx.Response(200, json={
                    "totalSize": 2600,
                    "done": False,
                    "nextRecordsUrl":
                        f"/services/data/{SF_API_VERSION}/query/cursor1-2000",
                    "records": [{"Id": f"page1_{i}"} for i in range(2000)],
                })
            # Cursor call: done=true, 600 records
            return httpx.Response(200, json={
                "totalSize": 2600,
                "done": True,
                "records": [{"Id": f"page2_{i}"} for i in range(600)],
            })

        c = _make_client(httpx.MockTransport(handler))
        path = f"/services/data/{SF_API_VERSION}/query/"
        result = c._query_all(path, "SELECT Id FROM Big")
        # Aggregated total
        assert len(result) == 2600
        # First 2000 from page 1, last 600 from page 2
        assert result[0]["Id"] == "page1_0"
        assert result[1999]["Id"] == "page1_1999"
        assert result[2000]["Id"] == "page2_0"
        assert result[2599]["Id"] == "page2_599"
        # Exactly 2 SOQL calls
        assert len(api_calls) == 2
        # Second call uses the cursor URL — and importantly, NO 'q' param
        cursor_path = f"/services/data/{SF_API_VERSION}/query/cursor1-2000"
        assert api_calls[1][0] == cursor_path
        assert "q" not in api_calls[1][1]
        c.close()

    def test_query_all_handles_done_false_without_next_url(self) -> None:
        """Defensive: done=false but no nextRecordsUrl is a malformed
        Salesforce response. _query_all must break the loop and return
        what it has, not infinite-loop."""
        call_count = [0]

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            call_count[0] += 1
            return httpx.Response(200, json={
                "totalSize": 99,
                "done": False,
                # nextRecordsUrl deliberately absent
                "records": [{"Id": "0010000000001"}],
            })

        c = _make_client(httpx.MockTransport(handler))
        path = f"/services/data/{SF_API_VERSION}/tooling/query/"
        result = c._query_all(path, "SELECT Id FROM Weird")
        # Returns what it has; no infinite loop
        assert len(result) == 1
        # Exactly 1 SOQL call (no follow-up because no cursor)
        assert call_count[0] == 1
        c.close()


# ----------------------------------------------------------------------
# fetch_users (2C-extended Method 6 — Category 1 single-phase, Data API)
# ----------------------------------------------------------------------

class TestFetchUsers:
    def test_fetch_users_uses_data_api_endpoint(self) -> None:
        """First non-Tooling fetch method. URL is /services/data/v66.0/
        query/, NOT /tooling/query/. User is a standard sObject."""
        urls_seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            urls_seen.append(request.url.path)
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_users()
        # ≥1 call to Data API
        data_hits = [p for p in urls_seen
                     if p == f"/services/data/{SF_API_VERSION}/query/"]
        assert len(data_hits) >= 1
        # No call to Tooling
        tooling_hits = [p for p in urls_seen
                        if p == f"/services/data/{SF_API_VERSION}"
                                f"/tooling/query/"]
        assert len(tooling_hits) == 0
        c.close()

    def test_fetch_users_soql_field_set(self) -> None:
        """Regression guard against scope creep on the User SOQL.
        User query (the first of two SOQLs) must SELECT exactly
        the 12 spec'd fields and nothing else (no FIELDS(STANDARD),
        no Phone/Address/etc)."""
        soqls_seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soqls_seen.append(request.url.params.get("q", ""))
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_users()
        # 2 SOQLs: User + PSA. Profile + PermissionSet Id→external_id
        # maps are built by phase_user from the entities table.
        assert len(soqls_seen) == 2
        user_soqls = [
            s for s in soqls_seen
            if "FROM User" in s
            and "FROM UserRole" not in s
            and "FROM PermissionSetAssignment" not in s
        ]
        assert len(user_soqls) == 1
        soql = user_soqls[0]

        # All 12 expected fields present
        expected_fields = (
            "Id", "Username", "Email", "Name", "Alias",
            "IsActive", "UserType", "ProfileId", "UserRoleId",
            "CreatedDate", "LastModifiedDate", "LastLoginDate",
        )
        for field in expected_fields:
            assert field in soql, f"missing expected field {field!r}"

        assert "FROM User" in soql

        # Scope-creep guards: NOT FIELDS(STANDARD), NOT pulling
        # PII-heavy or out-of-scope fields
        assert "FIELDS(STANDARD)" not in soql.upper()
        for field in ("Phone", "MobilePhone", "Street", "PostalCode",
                      "EmployeeNumber", "ManagerId",
                      "FederationIdentifier", "Department", "Title"):
            assert field not in soql, (
                f"unexpected field in scope: {field!r} — "
                f"deliberate-scope contract violated"
            )

        # UserLicenseId regression guard (User.UserLicenseId is
        # INVALID_FIELD per live probe)
        assert "UserLicenseId" not in soql
        c.close()

    def test_fetch_users_returns_two_key_dict(self) -> None:
        """Category 4 result is a 2-key dict. Profile +
        PermissionSet Id→external_id maps are built by phase_user
        from the entities table — sidesteps the Salesforce SOQL
        Tooling-vs-Data API name asymmetry on Profile (live test
        caught that Tooling Profile.FullName like 'Admin'
        differs from Data API Profile.Name like 'System
        Administrator')."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            if "FROM PermissionSetAssignment" in soql:
                return httpx.Response(200, json={
                    "records": [
                        {"Id": "0Pa001", "AssigneeId": "005U001",
                         "PermissionSetId": "0PS001",
                         "PermissionSetGroupId": None,
                         "ExpirationDate": None,
                         "SystemModstamp": (
                             "2026-01-01T00:00:00.000+0000"
                         )},
                    ],
                })
            if "FROM User" in soql:
                return httpx.Response(200, json={
                    "records": [
                        {"Id": "005U001",
                         "Username": "alice@example.com",
                         "UserType": "Standard",
                         "ProfileId": "00eA",
                         "IsActive": True},
                    ],
                })
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_users()
        assert isinstance(result, dict)
        assert set(result.keys()) == {
            "users",
            "permission_set_assignments",
        }
        assert isinstance(result["users"], list)
        assert isinstance(result["permission_set_assignments"], list)
        # Users
        assert len(result["users"]) == 1
        # PSAs (no PermissionSet.Name nested — that came from a
        # related-query the v66.0 SOQL declined)
        assert len(result["permission_set_assignments"]) == 1
        psa = result["permission_set_assignments"][0]
        assert psa["AssigneeId"] == "005U001"
        assert psa["PermissionSetId"] == "0PS001"
        c.close()

    def test_fetch_users_makes_two_calls(self) -> None:
        """User cycle scopes fetch_users to exactly 2 SOQLs:
        User + PermissionSetAssignment. Profile + PermissionSet
        Id→external_id resolution happens in phase_user against
        the entities table."""
        api_calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            api_calls.append(request.url.params.get("q", ""))
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_users()
        assert len(api_calls) == 2
        c.close()

    def test_fetch_users_psa_soql_shape(self) -> None:
        """PSA SOQL includes the 6 spec'd fields + uses SystemModstamp
        as the assigned_at proxy (CreatedById/CreatedDate restricted
        on PSA in v66.0). NO WHERE filter — PSG filtering happens
        in phase_user's Python code."""
        soqls_seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soqls_seen.append(request.url.params.get("q", ""))
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_users()
        psa_soqls = [
            s for s in soqls_seen
            if "FROM PermissionSetAssignment" in s
        ]
        assert len(psa_soqls) == 1
        psa = psa_soqls[0]
        # Required field set
        for f in ("AssigneeId", "PermissionSetId",
                  "PermissionSetGroupId", "ExpirationDate",
                  "SystemModstamp"):
            assert f in psa, f"missing PSA field {f!r}"
        # PermissionSet.Name related-query NOT used (HTTP 400 in
        # v66.0 — see fetch_users docstring)
        assert "PermissionSet.Name" not in psa
        # CreatedById/CreatedDate NOT used (HTTP 400 in v66.0)
        assert "CreatedById" not in psa
        assert "CreatedDate" not in psa
        # No WHERE filter — PSG filtering moved to Python
        assert "WHERE" not in psa
        c.close()

    def test_fetch_users_psa_failure_returns_empty_list(self) -> None:
        """Fault tolerance: if PermissionSetAssignment query fails
        (rare; integration user without PSA read access), the
        fetcher logs and returns empty list; User entities still
        materialize via the User query. HAS_PERMISSION_SET edges
        become 0 for this sync."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            if "FROM PermissionSetAssignment" in soql:
                return httpx.Response(403, json={
                    "errorCode": "FORBIDDEN",
                    "message": "no access",
                })
            if "FROM User" in soql:
                return httpx.Response(200, json={
                    "records": [
                        {"Id": "005U001", "Username": "alice",
                         "ProfileId": "00eA", "UserType": "Standard"},
                    ],
                })
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_users()
        # Empty list on PSA failure; users still populated
        assert result["permission_set_assignments"] == []
        assert len(result["users"]) == 1
        c.close()

    def test_fetch_users_returns_inactive_users(self) -> None:
        """No fetch-time IsActive filter — sync-layer concern.
        Both active and inactive users appear in users list."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            if "FROM User" in soql and "Permission" not in soql:
                return httpx.Response(200, json={
                    "records": [
                        {"Id": "005U001", "Username": "active",
                         "IsActive": True, "UserType": "Standard",
                         "ProfileId": "00eA"},
                        {"Id": "005U002", "Username": "inactive",
                         "IsActive": False, "UserType": "Standard",
                         "ProfileId": "00eA"},
                    ],
                })
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_users()
        users = result["users"]
        assert len(users) == 2
        active_states = {r["IsActive"] for r in users}
        assert active_states == {True, False}  # both kept
        c.close()

    def test_fetch_users_returns_synthetic_user_types(self) -> None:
        """Platform-synthetic UserTypes (AutomatedProcess,
        CloudIntegrationUser, etc.) appear in fetch result's
        users list. Filtering them is a sync-layer policy
        decision per transparent-transport-boundary principle."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            if "FROM User" in soql and "Permission" not in soql:
                return httpx.Response(200, json={
                    "records": [
                        {"Id": f"005U00{i}",
                         "Username": utype,
                         "IsActive": True, "UserType": utype,
                         "ProfileId": "00eA"}
                        for i, utype in enumerate(
                            ["Standard", "AutomatedProcess",
                             "CloudIntegrationUser"], start=1,
                        )
                    ],
                })
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_users()
        utypes = {r["UserType"] for r in result["users"]}
        assert utypes == {
            "Standard",
            "AutomatedProcess",
            "CloudIntegrationUser",
        }
        c.close()


# ----------------------------------------------------------------------
# fetch_flow_definitions (2C-extended Method 5, FlowDefinition half —
# Category 2)
# ----------------------------------------------------------------------

class TestFetchFlowDefinitions:
    def test_fetch_flow_definitions_uses_tooling_endpoint(self) -> None:
        urls_seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            urls_seen.append(request.url.path)
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_flow_definitions()
        assert any(
            f"/services/data/{SF_API_VERSION}/tooling/query" in p
            for p in urls_seen
        )
        c.close()

    def test_fetch_flow_definitions_returns_records_list(self) -> None:
        """Two-phase fetch: phase 1 returns N records (no Metadata,
        no FullName); phase 2 returns 1 row per Id with summary
        Metadata (activeVersionNumber, masterLabel, description, urls)
        per Salesforce-documented schema."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            if "Metadata" not in soql and "FullName" not in soql:
                return httpx.Response(200, json={
                    "records": [
                        {
                            "Id": "300F90000000001",
                            "DeveloperName": "Org_Expiration_Notification",
                            "MasterLabel": None,
                            "ActiveVersionId": "301F90000000001",
                            "LatestVersionId": "301F90000000001",
                            "ManageableState": "installed",
                            "NamespacePrefix": "MHolt",
                        },
                        {
                            "Id": "300F90000000002",
                            "DeveloperName": "Custom_Flow",
                            "MasterLabel": "Custom Flow",
                            "ActiveVersionId": "301F90000000010",
                            "LatestVersionId": "301F90000000010",
                            "ManageableState": "unmanaged",
                            "NamespacePrefix": None,
                        },
                    ],
                })
            if "300F90000000001" in soql:
                return httpx.Response(200, json={
                    "records": [{
                        "Id": "300F90000000001",
                        "FullName": "MHolt__Org_Expiration_Notification",
                        "Metadata": {
                            "activeVersionNumber": 2,
                            "description": None,
                            "masterLabel": None,
                            "urls": {
                                "metadata": "/services/data/v66.0/...",
                            },
                        },
                    }],
                })
            if "300F90000000002" in soql:
                return httpx.Response(200, json={
                    "records": [{
                        "Id": "300F90000000002",
                        "FullName": "Custom_Flow",
                        "Metadata": {
                            "activeVersionNumber": 1,
                            "description": "Custom flow description",
                            "masterLabel": "Custom Flow",
                            "urls": {
                                "metadata": "/services/data/v66.0/...",
                            },
                        },
                    }],
                })
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_flow_definitions()
        assert len(result) == 2
        # Phase 1 fields preserved
        assert result[0]["DeveloperName"] == "Org_Expiration_Notification"
        assert result[0]["NamespacePrefix"] == "MHolt"
        assert result[0]["ActiveVersionId"] == "301F90000000001"
        assert result[1]["DeveloperName"] == "Custom_Flow"
        # Phase 2 merged
        assert result[0]["FullName"] == "MHolt__Org_Expiration_Notification"
        assert result[0]["Metadata"]["activeVersionNumber"] == 2
        assert result[1]["FullName"] == "Custom_Flow"
        assert result[1]["Metadata"]["description"] == \
            "Custom flow description"
        c.close()

    def test_fetch_flow_definitions_phase_split(self) -> None:
        """Phase 1 SOQL must NOT contain Metadata or FullName.
        Phase 2 SOQL MUST contain BOTH + WHERE Id = '...'."""
        soqls_seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            soqls_seen.append(soql)
            if "Metadata" not in soql and "FullName" not in soql:
                return httpx.Response(200, json={
                    "records": [{
                        "Id": "300F90000000001",
                        "DeveloperName": "X",
                    }],
                })
            return httpx.Response(200, json={
                "records": [{
                    "Id": "300F90000000001",
                    "FullName": "X",
                    "Metadata": {},
                }],
            })

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_flow_definitions()
        assert len(soqls_seen) == 2

        phase1 = soqls_seen[0]
        assert "FROM FlowDefinition" in phase1
        assert "Metadata" not in phase1
        assert "FullName" not in phase1
        assert "WHERE" not in phase1

        phase2 = soqls_seen[1]
        assert "Metadata" in phase2
        assert "FullName" in phase2
        assert "WHERE Id = '300F90000000001'" in phase2
        c.close()

    def test_fetch_flow_definitions_phase1_bulk_no_filter(self) -> None:
        """Phase 1 is unfiltered bulk SELECT — Category 2 standard
        (no reified-column constraint applies to FlowDefinition)."""
        soqls_seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soqls_seen.append(request.url.params.get("q", ""))
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_flow_definitions()
        # Exactly 1 SOQL when phase 1 returns empty
        assert len(soqls_seen) == 1
        phase1 = soqls_seen[0]
        assert "FROM FlowDefinition" in phase1
        assert "WHERE" not in phase1
        c.close()

    def test_fetch_flow_definitions_makes_n_plus_one_calls(self) -> None:
        """N FlowDefinitions → N+1 API calls (1 bulk + N per-Id)."""
        api_calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            api_calls.append(soql)
            if "Metadata" not in soql and "FullName" not in soql:
                return httpx.Response(200, json={
                    "records": [
                        {"Id": f"300F9000000000{i}",
                         "DeveloperName": f"Flow_{i}"}
                        for i in (1, 2, 3, 4, 5)
                    ],
                })
            return httpx.Response(200, json={
                "records": [{
                    "Id": "300F90000000001",
                    "FullName": "X",
                    "Metadata": {},
                }],
            })

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_flow_definitions()
        assert len(result) == 5
        # 1 phase-1 + 5 phase-2 = 6 calls
        assert len(api_calls) == 6
        c.close()


# ----------------------------------------------------------------------
# fetch_flow_versions (2C-extended Method 5, Flow half — Category 2)
# ----------------------------------------------------------------------

class TestFetchFlowVersions:
    def test_fetch_flow_versions_uses_tooling_endpoint(self) -> None:
        urls_seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            urls_seen.append(request.url.path)
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_flow_versions()
        assert any(
            f"/services/data/{SF_API_VERSION}/tooling/query" in p
            for p in urls_seen
        )
        c.close()

    def test_fetch_flow_versions_returns_records_list(self) -> None:
        """Two-phase fetch: phase 1 returns version-level fields;
        phase 2 returns full graph Metadata (actionCalls, decisions,
        recordLookups, etc. per Salesforce v66.0 schema)."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            if "Metadata" not in soql and "FullName" not in soql:
                return httpx.Response(200, json={
                    "records": [
                        {
                            "Id": "301F90000000001",
                            "DefinitionId": "300F90000000001",
                            "MasterLabel": "Org Expiration Notification",
                            "VersionNumber": 2,
                            "Status": "Active",
                            "ProcessType": "AutoLaunchedFlow",
                            "ManageableState": "unmanaged",
                            "ApiVersion": 60.0,
                            "IsTemplate": False,
                            "IsOverridable": False,
                            "RunInMode": None,
                            "TriggerOrder": None,
                            "Environments": "Default",
                            "IsPaused": False,
                        },
                    ],
                })
            return httpx.Response(200, json={
                "records": [{
                    "Id": "301F90000000001",
                    "FullName": "MHolt__Org_Expiration_Notification",
                    "Metadata": {
                        "actionCalls": [
                            {"name": "Send_Email",
                             "actionType": "emailAlert",
                             "actionName": "MHolt__OrgExpiration"},
                        ],
                        "decisions": [
                            {"name": "Days_Until",
                             "rules": []},
                        ],
                        "recordLookups": [
                            {"name": "Get_Org",
                             "object": "Organization"},
                        ],
                        "screens": [],
                        "variables": [
                            {"name": "daysUntilExpiry",
                             "dataType": "Number",
                             "isCollection": False},
                        ],
                        "start": {
                            "connector": {"targetReference": "Get_Org"},
                            "locationX": 50,
                            "locationY": 0,
                        },
                        "processType": "AutoLaunchedFlow",
                        "label": "Org Expiration Notification",
                    },
                }],
            })

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_flow_versions()
        assert len(result) == 1
        # Phase 1 fields preserved
        assert result[0]["DefinitionId"] == "300F90000000001"
        assert result[0]["Status"] == "Active"
        assert result[0]["ProcessType"] == "AutoLaunchedFlow"
        # Phase 2 merged
        md = result[0]["Metadata"]
        assert md["processType"] == "AutoLaunchedFlow"
        assert len(md["actionCalls"]) == 1
        assert len(md["decisions"]) == 1
        assert len(md["recordLookups"]) == 1
        assert len(md["variables"]) == 1
        assert "start" in md
        c.close()

    def test_fetch_flow_versions_phase_split(self) -> None:
        """Phase 1 has no Metadata/FullName; Phase 2 has both
        + WHERE Id = '...'."""
        soqls_seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            soqls_seen.append(soql)
            if "Metadata" not in soql and "FullName" not in soql:
                return httpx.Response(200, json={
                    "records": [{
                        "Id": "301F90000000001",
                        "DefinitionId": "300F90000000001",
                        "Status": "Active",
                    }],
                })
            return httpx.Response(200, json={
                "records": [{
                    "Id": "301F90000000001",
                    "FullName": "X",
                    "Metadata": {},
                }],
            })

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_flow_versions()
        assert len(soqls_seen) == 2

        phase1 = soqls_seen[0]
        assert "FROM Flow" in phase1
        assert "Metadata" not in phase1
        assert "FullName" not in phase1
        # Phase 1 has NO WHERE — returns all versions, including
        # Obsolete/Draft (no active-only filter)
        assert "WHERE" not in phase1

        phase2 = soqls_seen[1]
        assert "Metadata" in phase2
        assert "FullName" in phase2
        assert "WHERE Id = '301F90000000001'" in phase2
        c.close()

    def test_fetch_flow_versions_returns_inactive_versions(self) -> None:
        """No Status filter at fetch — Active, Obsolete, Draft, and
        InvalidDraft all flow through. Codifies the no-active-filter
        principle so a future PR cannot silently add WHERE Status =
        'Active'."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            if "Metadata" not in soql and "FullName" not in soql:
                return httpx.Response(200, json={
                    "records": [
                        {"Id": "301F90000000001", "Status": "Active",
                         "DefinitionId": "300F90000000001"},
                        {"Id": "301F90000000002", "Status": "Obsolete",
                         "DefinitionId": "300F90000000001"},
                        {"Id": "301F90000000003", "Status": "Draft",
                         "DefinitionId": "300F90000000002"},
                    ],
                })
            return httpx.Response(200, json={
                "records": [{
                    "Id": "301F90000000001",
                    "FullName": "X",
                    "Metadata": {},
                }],
            })

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_flow_versions()
        assert len(result) == 3
        statuses = {r["Status"] for r in result}
        assert statuses == {"Active", "Obsolete", "Draft"}
        c.close()

    def test_fetch_flow_versions_returns_mixed_process_types(self) -> None:
        """No ProcessType filter at fetch — all flow types (Flow,
        AutoLaunchedFlow, RecordTriggered, etc.) flow through. Sync
        layer filters per its policy if needed."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            if "Metadata" not in soql and "FullName" not in soql:
                return httpx.Response(200, json={
                    "records": [
                        {"Id": "301F90000000001",
                         "DefinitionId": "300F90000000001",
                         "ProcessType": "Flow", "Status": "Active"},
                        {"Id": "301F90000000002",
                         "DefinitionId": "300F90000000002",
                         "ProcessType": "AutoLaunchedFlow",
                         "Status": "Active"},
                        {"Id": "301F90000000003",
                         "DefinitionId": "300F90000000003",
                         "ProcessType": "RecordTriggered",
                         "Status": "Active"},
                    ],
                })
            return httpx.Response(200, json={
                "records": [{
                    "Id": "301F90000000001",
                    "FullName": "X",
                    "Metadata": {},
                }],
            })

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_flow_versions()
        assert len(result) == 3
        ptypes = {r["ProcessType"] for r in result}
        assert ptypes == {"Flow", "AutoLaunchedFlow", "RecordTriggered"}
        c.close()

    def test_fetch_flow_versions_makes_n_plus_one_calls(self) -> None:
        """N Flow versions → N+1 API calls (1 bulk + N per-Id)."""
        api_calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soql = request.url.params.get("q", "")
            api_calls.append(soql)
            if "Metadata" not in soql and "FullName" not in soql:
                return httpx.Response(200, json={
                    "records": [
                        {"Id": f"301F9000000000{i}",
                         "DefinitionId": f"300F9000000000{i}",
                         "Status": "Active"}
                        for i in (1, 2, 3, 4)
                    ],
                })
            return httpx.Response(200, json={
                "records": [{
                    "Id": "301F90000000001",
                    "FullName": "X",
                    "Metadata": {},
                }],
            })

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_flow_versions()
        assert len(result) == 4
        # 1 phase-1 + 4 phase-2 = 5 calls
        assert len(api_calls) == 5
        c.close()


# ----------------------------------------------------------------------
# fetch_layout_names (2C-extended Method 7 — single-phase Tooling SOQL,
# closes the layout-name-resolution second-pass deferred from Method 2)
# ----------------------------------------------------------------------

class TestFetchLayoutNames:
    def test_fetch_layout_names_uses_tooling_endpoint(self) -> None:
        urls_seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            urls_seen.append(request.url.path)
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_layout_names()
        assert any(
            f"/services/data/{SF_API_VERSION}/tooling/query" in p
            for p in urls_seen
        )
        c.close()

    def test_fetch_layout_names_returns_records_list(self) -> None:
        """Mock 3 records with the verified-against-sandbox LayoutType
        enumeration {Standard, GlobalQuickActionList}. Shape preservation
        verified across both LayoutType values."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            return httpx.Response(200, json={
                "totalSize": 3,
                "done": True,
                "records": [
                    {
                        "Id": "00hF900000CBOs9IAH",
                        "Name": "Account Layout",
                        "EntityDefinitionId": "Account",
                        "NamespacePrefix": None,
                        "ManageableState": "unmanaged",
                        "LayoutType": "Standard",
                    },
                    {
                        "Id": "00hF900000CBOsAIAX",
                        "Name": "Contact Layout",
                        "EntityDefinitionId": "Contact",
                        "NamespacePrefix": None,
                        "ManageableState": "unmanaged",
                        "LayoutType": "Standard",
                    },
                    {
                        "Id": "00hF900000CBOsBIAX",
                        "Name": "Global Quick Action List",
                        "EntityDefinitionId": "Global",
                        "NamespacePrefix": None,
                        "ManageableState": "unmanaged",
                        "LayoutType": "GlobalQuickActionList",
                    },
                ],
            })

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_layout_names()
        assert len(result) == 3
        # Field shape preserved across both LayoutType values
        assert result[0]["Name"] == "Account Layout"
        assert result[0]["LayoutType"] == "Standard"
        # EntityDefinitionId is QualifiedApiName, not Id
        assert result[0]["EntityDefinitionId"] == "Account"
        assert result[2]["LayoutType"] == "GlobalQuickActionList"
        # All 3 distinct LayoutType values present (Standard ×2 +
        # GlobalQuickActionList ×1)
        ltypes = [r["LayoutType"] for r in result]
        assert ltypes.count("Standard") == 2
        assert ltypes.count("GlobalQuickActionList") == 1
        c.close()

    def test_fetch_layout_names_soql_field_set(self) -> None:
        """Regression guard against scope creep. SOQL must SELECT
        exactly the 6 spec'd fields; no Metadata, no FullName, no
        FIELDS(STANDARD)."""
        soqls_seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            soqls_seen.append(request.url.params.get("q", ""))
            return httpx.Response(200, json={"records": []})

        c = _make_client(httpx.MockTransport(handler))
        c.fetch_layout_names()
        assert len(soqls_seen) == 1
        soql = soqls_seen[0]

        # All 6 expected fields present
        for field in ("Id", "Name", "EntityDefinitionId",
                      "NamespacePrefix", "ManageableState",
                      "LayoutType"):
            assert field in soql, f"missing expected field {field!r}"

        # FROM Layout
        assert "FROM Layout" in soql

        # Scope-creep guards: NOT Metadata, NOT FullName, NOT
        # FIELDS(STANDARD), NOT TableEnumOrId (in describe but
        # out of spec), NOT audit timestamps
        for field in ("Metadata", "FullName", "TableEnumOrId",
                      "ShowSubmitAndAttachButton"):
            assert field not in soql, (
                f"unexpected field in scope: {field!r}"
            )
        assert "FIELDS(STANDARD)" not in soql.upper()

        # Single-phase: no WHERE clause (Layout has no reified-column
        # constraint; bulk enumeration works directly)
        assert "WHERE" not in soql
        c.close()

    def test_fetch_layout_names_makes_one_call(self) -> None:
        """Single-phase: exactly one SOQL. _query_all short-circuits
        on done=true. No per-Id loop (no Metadata to fetch)."""
        api_calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/services/oauth2/token":
                return _token_response()
            api_calls.append(request.url.params.get("q", ""))
            return httpx.Response(200, json={
                "totalSize": 5,
                "done": True,
                "records": [
                    {"Id": f"00hF90000000{i:03d}",
                     "Name": f"Layout {i}",
                     "EntityDefinitionId": "Account",
                     "LayoutType": "Standard"}
                    for i in range(5)
                ],
            })

        c = _make_client(httpx.MockTransport(handler))
        result = c.fetch_layout_names()
        assert len(result) == 5
        # Single SOQL call (no phase-2 iteration, no pagination
        # follow-up because done=true)
        assert len(api_calls) == 1
        c.close()


# ----------------------------------------------------------------------
# extract_picklist_value_payloads_from_metadata helper
# (module-level; supports the §9 SVS-cache PV-phase optimization)
# ----------------------------------------------------------------------


class TestExtractPicklistValuePayloadsFromMetadata:
    """Helper transforms a value-set Metadata sub-tree into the
    raw_payloads shape the PicklistValue phase passes to
    batched_materialize. Used by phase_picklist_value for both the
    cached SVS path and the GVS / fallback paths (single source of
    truth for the transform)."""

    def test_extracts_standard_values_with_markers(self) -> None:
        """SVS path: standardValue list → payloads with SVS-
        prefixed parent_external_id (caller supplies it pre-
        prefixed)."""
        metadata = {
            "standardValue": [
                {"valueName": "Web", "label": "Web", "isActive": True},
                {"valueName": "Phone", "label": "Phone Inquiry",
                 "isActive": True, "default": True},
            ],
        }
        result = extract_picklist_value_payloads_from_metadata(
            parent_external_id="SVS:AccountSource",
            metadata=metadata,
            value_list_key="standardValue",
        )
        assert len(result) == 2
        assert result[0]["_parent_external_id"] == "SVS:AccountSource"
        assert result[0]["_sort_order"] == 0
        assert result[0]["valueName"] == "Web"
        assert result[0]["label"] == "Web"
        assert result[1]["_parent_external_id"] == "SVS:AccountSource"
        assert result[1]["_sort_order"] == 1
        assert result[1]["valueName"] == "Phone"
        assert result[1]["default"] is True

    def test_extracts_custom_values_with_markers(self) -> None:
        """GVS path: customValue list → payloads with unprefixed
        parent_external_id."""
        metadata = {
            "customValue": [
                {"valueName": "Banking", "label": "Banking"},
                {"valueName": "Tech", "label": "Technology"},
            ],
        }
        result = extract_picklist_value_payloads_from_metadata(
            parent_external_id="MyGVS",
            metadata=metadata,
            value_list_key="customValue",
        )
        assert len(result) == 2
        assert result[0]["_parent_external_id"] == "MyGVS"
        assert result[0]["valueName"] == "Banking"

    def test_empty_metadata_returns_empty(self) -> None:
        result = extract_picklist_value_payloads_from_metadata(
            parent_external_id="EmptyGVS",
            metadata={},
            value_list_key="customValue",
        )
        assert result == []

    def test_missing_value_list_key_returns_empty(self) -> None:
        """Metadata without the requested list key (e.g., SVS
        Metadata that lacks standardValue) returns []. Common in
        sandbox SVSes that resolve empty."""
        result = extract_picklist_value_payloads_from_metadata(
            parent_external_id="SVS:Empty",
            metadata={"someOtherKey": "value"},
            value_list_key="standardValue",
        )
        assert result == []

    def test_null_value_list_returns_empty(self) -> None:
        """If the list-key resolves to None (rather than missing
        entirely), the `or []` guard yields an empty list."""
        result = extract_picklist_value_payloads_from_metadata(
            parent_external_id="SVS:Nullish",
            metadata={"standardValue": None},
            value_list_key="standardValue",
        )
        assert result == []

    def test_skips_entries_without_value_name(self) -> None:
        """Defensive: Metadata API occasionally returns placeholder
        entries with empty/missing valueName. These are dropped to
        prevent malformed external_id construction downstream."""
        metadata = {
            "standardValue": [
                {"valueName": "Good", "label": "Good"},
                {"valueName": "", "label": "Blank"},      # filtered
                {"label": "NoValueName"},                  # filtered
            ],
        }
        result = extract_picklist_value_payloads_from_metadata(
            parent_external_id="SVS:PartialVS",
            metadata=metadata,
            value_list_key="standardValue",
        )
        assert len(result) == 1
        assert result[0]["valueName"] == "Good"

    def test_skips_non_dict_entries(self) -> None:
        """Defensive: Metadata API occasionally emits string or
        null entries in the value list. These are dropped."""
        metadata = {
            "standardValue": [
                {"valueName": "Good"},
                "NotADict",
                None,
                42,
            ],
        }
        result = extract_picklist_value_payloads_from_metadata(
            parent_external_id="SVS:Junk",
            metadata=metadata,
            value_list_key="standardValue",
        )
        assert len(result) == 1
        assert result[0]["valueName"] == "Good"

    def test_preserves_extra_fields_via_spread(self) -> None:
        """Every key on the value dict survives the **v spread —
        substrate-1's normalize layer needs the full shape, not a
        whitelisted subset."""
        metadata = {
            "standardValue": [
                {
                    "valueName": "Hot",
                    "label": "Hot",
                    "isActive": True,
                    "default": False,
                    "description": "Hot lead",
                    "color": "#ff0000",
                    "translations": [{"locale": "fr", "value": "Chaud"}],
                },
            ],
        }
        result = extract_picklist_value_payloads_from_metadata(
            parent_external_id="SVS:LeadSource",
            metadata=metadata,
            value_list_key="standardValue",
        )
        assert len(result) == 1
        v = result[0]
        # Original keys preserved
        assert v["description"] == "Hot lead"
        assert v["color"] == "#ff0000"
        assert v["translations"] == [{"locale": "fr", "value": "Chaud"}]
        # Markers injected
        assert v["_parent_external_id"] == "SVS:LeadSource"
        assert v["_sort_order"] == 0

    def test_sort_order_reflects_input_order(self) -> None:
        """Salesforce returns values in display order; _sort_order
        captures that index. The value's position in the list IS
        the display order."""
        metadata = {
            "standardValue": [
                {"valueName": "A"},
                {"valueName": "B"},
                {"valueName": "C"},
            ],
        }
        result = extract_picklist_value_payloads_from_metadata(
            parent_external_id="SVS:ABCs",
            metadata=metadata,
            value_list_key="standardValue",
        )
        assert [v["_sort_order"] for v in result] == [0, 1, 2]
        assert [v["valueName"] for v in result] == ["A", "B", "C"]
