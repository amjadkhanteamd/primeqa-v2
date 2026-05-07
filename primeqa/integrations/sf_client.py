"""Salesforce REST + Tooling API client (Phase 2 step 2C, skinny scope).

Per Phase 2 plan §4.2 and decisions D-029, D-046, D-048.

Hybrid client:
  - REST describe API for structural metadata: Object, Field
    (and later Phase 2 extensions: RecordType, Layout, Picklist
    families).
  - Tooling API SOQL for behavioral metadata: ValidationRule
    (and later: Flow, Profile, PermissionSet, User).

Skinny scope per locked decision (O-10 in plan): 3 entity types here
(Object, Field, ValidationRule). Remaining 8 follow established pattern
in a "2C-extended" step.

API version pinned at v66.0 (Salesforce Spring '26). Rotate every ~3
months — bump SF_API_VERSION constant and re-run the live integration
tests against the sandbox.

Token lifecycle:
  - Lazy: first call triggers refresh-token exchange.
  - Transparent re-auth on 401: one retry per request after refresh.
  - Repeated 401 after refresh raises SFAuthError.

Retry policy:
  - Transient 5xx + 429: up to MAX_RETRIES with backoff (1s, 2s, 4s).
  - Persistent 429 raises SFRateLimitError.
  - Non-transient 4xx (400, 403, 404, ...) raises SFRequestError
    immediately, no retry.

Connection management: implements context-manager protocol
(`with SalesforceClient(...) as c:`). Close() releases httpx.Client.
"""
from __future__ import annotations

import time
import urllib.parse
from typing import Any

import httpx

from .exceptions import (
    SFAuthError,
    SFClientError,
    SFRateLimitError,
    SFRequestError,
)


SF_API_VERSION = "v66.0"  # Salesforce Spring '26; rotate ~quarterly
TRANSIENT_STATUS_CODES = {429, 502, 503, 504}
MAX_RETRIES = 3
RETRY_BACKOFF_SEQ: tuple[float, ...] = (1.0, 2.0, 4.0)  # seconds


class SalesforceClient:
    """REST + Tooling API client for a single connected Salesforce org."""

    def __init__(
        self,
        instance_url: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        api_version: str = SF_API_VERSION,
        timeout: float = 30.0,
    ) -> None:
        self.instance_url = instance_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.api_version = api_version
        self.timeout = timeout
        self._access_token: str | None = None
        self._client = httpx.Client(timeout=timeout)

    # --------------------------------------------------------------
    # Lifecycle
    # --------------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SalesforceClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # --------------------------------------------------------------
    # Token lifecycle
    # --------------------------------------------------------------

    def _refresh_access_token(self) -> None:
        """POST /services/oauth2/token with grant_type=refresh_token.

        Updates self._access_token. Raises SFAuthError on any non-2xx
        response (no retry — auth failures are not transient).
        """
        url = f"{self.instance_url}/services/oauth2/token"
        data = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
        }
        try:
            resp = self._client.post(url, data=data)
        except httpx.HTTPError as e:
            raise SFAuthError(f"Network error during token refresh: {e}") from e

        if resp.status_code != 200:
            body = resp.text[:500]
            raise SFAuthError(
                f"Token refresh failed: HTTP {resp.status_code}. Body: {body}"
            )

        payload = resp.json()
        token = payload.get("access_token")
        if not token:
            raise SFAuthError(
                f"Token refresh response missing access_token. Body: {payload}"
            )
        self._access_token = token

    def _ensure_access_token(self) -> None:
        if self._access_token is None:
            self._refresh_access_token()

    def _authed_headers(self) -> dict[str, str]:
        self._ensure_access_token()
        return {"Authorization": f"Bearer {self._access_token}"}

    # --------------------------------------------------------------
    # Request helper
    # --------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        _refresh_attempted: bool = False,
        _retry_count: int = 0,
    ) -> httpx.Response:
        """Authenticated request with retry + transparent re-auth.

        - 401 once → refresh token + retry once. Repeat 401 → SFAuthError.
        - Transient (429, 5xx) → retry with backoff up to MAX_RETRIES.
        - Persistent 429 → SFRateLimitError.
        - Other 4xx/5xx → SFRequestError.
        """
        url = f"{self.instance_url}{path}"
        headers = self._authed_headers()

        try:
            resp = self._client.request(method, url, headers=headers, params=params)
        except httpx.HTTPError as e:
            raise SFRequestError(f"Network error: {e}") from e

        # 401: try one transparent token refresh
        if resp.status_code == 401:
            if _refresh_attempted:
                raise SFAuthError(
                    "Repeated 401 after token refresh; refresh_token may be revoked"
                )
            # Force a refresh and retry once
            self._access_token = None
            self._refresh_access_token()
            return self._request(
                method, path, params=params,
                _refresh_attempted=True, _retry_count=_retry_count,
            )

        # Transient error: retry with backoff
        if resp.status_code in TRANSIENT_STATUS_CODES:
            if _retry_count < MAX_RETRIES:
                backoff = RETRY_BACKOFF_SEQ[min(_retry_count, len(RETRY_BACKOFF_SEQ) - 1)]
                time.sleep(backoff)
                return self._request(
                    method, path, params=params,
                    _refresh_attempted=_refresh_attempted,
                    _retry_count=_retry_count + 1,
                )
            # Retries exhausted
            if resp.status_code == 429:
                raise SFRateLimitError(
                    f"Salesforce rate limit (429) after {MAX_RETRIES} retries. "
                    f"Body: {resp.text[:200]}"
                )
            raise SFRequestError(
                f"Salesforce returned HTTP {resp.status_code} after "
                f"{MAX_RETRIES} retries",
                status_code=resp.status_code,
                response_body=resp.text[:500],
            )

        # Non-transient client/server error
        if resp.status_code >= 400:
            raise SFRequestError(
                f"Salesforce returned HTTP {resp.status_code}",
                status_code=resp.status_code,
                response_body=resp.text[:500],
            )

        return resp

    # --------------------------------------------------------------
    # Entity fetches (skinny scope: 3 types)
    # --------------------------------------------------------------

    def fetch_objects(self) -> list[dict]:
        """GET /services/data/{api_version}/sobjects.

        Returns the .sobjects list from the global describe response.
        Each entry has at least: name, label, custom, queryable, etc.
        """
        path = f"/services/data/{self.api_version}/sobjects"
        resp = self._request("GET", path)
        return resp.json().get("sobjects", [])

    def fetch_fields_for_object(self, sobject_api_name: str) -> list[dict]:
        """GET /services/data/{api_version}/sobjects/{name}/describe.

        Returns the .fields list from the per-object describe.
        Each field has: name, label, type, custom, picklistValues, etc.
        """
        encoded = urllib.parse.quote(sobject_api_name, safe="")
        path = f"/services/data/{self.api_version}/sobjects/{encoded}/describe"
        resp = self._request("GET", path)
        return resp.json().get("fields", [])

    def fetch_layouts_for_object(self, object_name: str) -> dict:
        """GET /services/data/{api_version}/sobjects/{name}/describe/layouts.

        Returns the full describe/layouts response as a dict with three
        top-level keys: layouts, recordTypeMappings, recordTypeSelectorRequired.

        # REST per-object describe/layouts endpoint. Returns full structured
        # response (layouts, recordTypeMappings, recordTypeSelectorRequired)
        # as-is; sync layer (Phase 2 step 4) extracts and normalizes.
        #
        # Layout-name resolution NOT fetched here. The REST describe response
        # does NOT include layout names — only Ids. Sync layer must run a
        # second-pass Tooling SOQL to resolve names:
        #   SELECT Id, Name, EntityDefinitionId FROM Layout
        # Plain Name (no FullName, no Metadata) is safe in bulk SOQL — no
        # 1-row constraint applies. This is a Phase 2 step 4 concern.
        #
        # Payload weight: ~121 KB per object on a typical Account layout.
        # For a ~600-object sync, ~75 MB of layout JSON. Within budget but
        # significant; capacity-planning reference for sync runtime estimates.
        """
        encoded = urllib.parse.quote(object_name, safe="")
        path = (
            f"/services/data/{self.api_version}"
            f"/sobjects/{encoded}/describe/layouts"
        )
        resp = self._request("GET", path)
        return resp.json()

    def fetch_validation_rules(self) -> list[dict]:
        """Tooling SOQL: SELECT … FROM ValidationRule, with full Metadata.

        Endpoint: GET /services/data/{api_version}/tooling/query/?q=<SOQL>

        Returned records carry: Id, ValidationName, Active, ErrorMessage,
        ErrorDisplayField, Description, EntityDefinitionId, and Metadata
        (with errorConditionFormula, etc.).

        # Two-phase fetch driven by Salesforce Tooling API constraints:
        # 1. Cannot select Metadata field on a query returning >1 row
        #    (Salesforce-documented limit).
        # 2. Cannot use EntityDefinition relationship traversal on a
        #    query returning >1000 underlying EntityDefinition rows
        #    (the EXTERNAL_OBJECT_UNSUPPORTED_EXCEPTION subquery limit).
        # Phase 1 fetches IDs + non-Metadata fields without joining
        # EntityDefinition (uses EntityDefinitionId direct field instead).
        # Phase 2 fetches Metadata per-Id one row at a time.
        # Sync layer (Phase 2 step 4) is responsible for resolving
        # EntityDefinitionId → Object entity_id via the Object describe
        # cache built earlier in the same sync run.
        # TODO Phase 2 sync layer: consider throttling or batching if a
        # tenant's rule count is large enough to hit API rate limits.
        """
        path = f"/services/data/{self.api_version}/tooling/query/"

        # Phase 1: bulk fetch all rules without Metadata, no EntityDefinition join.
        phase1_soql = (
            "SELECT Id, ValidationName, Active, ErrorMessage, "
            "ErrorDisplayField, Description, EntityDefinitionId "
            "FROM ValidationRule"
        )
        phase1_resp = self._request("GET", path, params={"q": phase1_soql})
        records: list[dict] = phase1_resp.json().get("records", [])

        # Phase 2: per-Id Metadata fetch (Salesforce constraint: 1 row max
        # when selecting Metadata).
        for rec in records:
            rec_id = rec.get("Id")
            if not rec_id:
                continue
            phase2_soql = (
                f"SELECT Id, Metadata FROM ValidationRule WHERE Id = '{rec_id}'"
            )
            phase2_resp = self._request("GET", path, params={"q": phase2_soql})
            phase2_records = phase2_resp.json().get("records", [])
            if phase2_records:
                rec["Metadata"] = phase2_records[0].get("Metadata")

        return records

    def fetch_record_types(self) -> list[dict]:
        """Tooling SOQL: SELECT … FROM RecordType, with FullName + Metadata.

        Endpoint: GET /services/data/{api_version}/tooling/query/?q=<SOQL>

        Returned records carry: Id, Name, IsActive, Description, SobjectType,
        EntityDefinitionId, BusinessProcessId, ManageableState,
        NamespacePrefix, FullName, and Metadata. Metadata exposes RecordType
        configuration: active, label, description, businessProcess (id),
        compactLayoutAssignment, picklistValues (per-field allowed values),
        and urls.

        # Two-phase fetch driven by Salesforce Tooling API constraints:
        # 1. Cannot select Metadata or FullName fields on a query returning
        #    >1 row (Salesforce-documented limit; both fields share this
        #    constraint).
        # 2. EntityDefinition relationship traversal not used (subquery
        #    limit risk on managed-package-heavy orgs).
        # Phase 1 fetches IDs + non-Metadata/FullName fields without joining
        # EntityDefinition. Phase 2 fetches FullName + Metadata per-Id one
        # row at a time. Sync layer (Phase 2 step 4) resolves
        # EntityDefinitionId → Object entity_id via Object describe cache.
        # FullName format example: 'sfLma__License__c.sfLma__Trial' for
        # managed-package; '<ObjectName>.<Name>' for org-native.
        """
        path = f"/services/data/{self.api_version}/tooling/query/"

        # Phase 1: bulk fetch all record types without Metadata or FullName,
        # no EntityDefinition join.
        phase1_soql = (
            "SELECT Id, Name, IsActive, Description, SobjectType, "
            "EntityDefinitionId, BusinessProcessId, ManageableState, "
            "NamespacePrefix "
            "FROM RecordType"
        )
        phase1_resp = self._request("GET", path, params={"q": phase1_soql})
        records: list[dict] = phase1_resp.json().get("records", [])

        # Phase 2: per-Id FullName + Metadata fetch (Salesforce constraint:
        # 1 row max when selecting either field).
        for rec in records:
            rec_id = rec.get("Id")
            if not rec_id:
                continue
            phase2_soql = (
                f"SELECT Id, FullName, Metadata FROM RecordType WHERE Id = '{rec_id}'"
            )
            phase2_resp = self._request("GET", path, params={"q": phase2_soql})
            phase2_records = phase2_resp.json().get("records", [])
            if phase2_records:
                rec["FullName"] = phase2_records[0].get("FullName")
                rec["Metadata"] = phase2_records[0].get("Metadata")

        return records
