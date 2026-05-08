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
from typing import Any, Iterable

import httpx

from .exceptions import (
    SFAuthError,
    SFClientError,
    SFRateLimitError,
    SFRequestError,
)
from .sf_constants import STANDARD_VALUE_SET_LABELS


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

    def _query_all(self, path: str, soql: str) -> list[dict]:
        """Issue SOQL via the given path and walk pagination.

        Salesforce SOQL endpoints (both /tooling/query/ and /query/)
        return at most 2000 rows per response by default, with
        `done=False` and a `nextRecordsUrl` cursor when more rows
        exist. This helper walks the cursor chain until `done=True`,
        returning the aggregated records list.

        Per corrections-log §6: the 2000-row cap is a Salesforce
        platform constraint applying to all SOQL responses. Direct
        use of self._request for SOQL is a correctness bug because
        rows past 2000 are silently dropped. All SOQL-issuing fetch
        methods route through this helper.

        REST methods (sobjects/, describe/, etc.) are NOT subject to
        this constraint — different pagination semantics — and use
        self._request directly.

        nextRecordsUrl values returned by Salesforce are server-
        relative paths (e.g., /services/data/v66.0/query/<cursor>);
        they're passed to self._request unchanged. The cursor is in
        the path, not the params, so subsequent calls don't pass
        a `q` parameter.
        """
        resp = self._request("GET", path, params={"q": soql})
        data = resp.json()
        records: list[dict] = list(data.get("records", []))
        while not data.get("done", True):
            next_url = data.get("nextRecordsUrl")
            if not next_url:
                # Defensive: done=False with no nextRecordsUrl is
                # malformed; break rather than infinite-loop.
                break
            resp = self._request("GET", next_url)
            data = resp.json()
            records.extend(data.get("records", []))
        return records

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
        records: list[dict] = self._query_all(path, phase1_soql)

        # Phase 2: per-Id Metadata fetch (Salesforce constraint: 1 row max
        # when selecting Metadata).
        for rec in records:
            rec_id = rec.get("Id")
            if not rec_id:
                continue
            phase2_soql = (
                f"SELECT Id, Metadata FROM ValidationRule WHERE Id = '{rec_id}'"
            )
            phase2_records = self._query_all(path, phase2_soql)
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
        records: list[dict] = self._query_all(path, phase1_soql)

        # Phase 2: per-Id FullName + Metadata fetch (Salesforce constraint:
        # 1 row max when selecting either field).
        for rec in records:
            rec_id = rec.get("Id")
            if not rec_id:
                continue
            phase2_soql = (
                f"SELECT Id, FullName, Metadata FROM RecordType WHERE Id = '{rec_id}'"
            )
            phase2_records = self._query_all(path, phase2_soql)
            if phase2_records:
                rec["FullName"] = phase2_records[0].get("FullName")
                rec["Metadata"] = phase2_records[0].get("Metadata")

        return records

    def fetch_global_value_sets(self) -> list[dict]:
        """Tooling SOQL: SELECT … FROM GlobalValueSet, with FullName + Metadata.

        Endpoint: GET /services/data/{api_version}/tooling/query/?q=<SOQL>

        Returned records carry: Id, MasterLabel, Description, ManageableState,
        NamespacePrefix, FullName, and Metadata. Metadata exposes the
        customValue list (each element: fullName, default, isActive, label).

        # Two-phase fetch driven by the Metadata-or-FullName 1-row
        # constraint (corrections log §1). Phase 1 bulk-fetches non-
        # Metadata/FullName fields; Phase 2 fetches FullName + Metadata
        # per-Id. N+1 round trips for N GVSes.
        # Sandbox at 0 GlobalValueSets in this dev org; live integration
        # test exercises the empty path. Populated path covered by unit-
        # test mocks against documented Salesforce schema:
        # Metadata.customValue: list of {fullName, default, isActive,
        # label}.
        # Sync layer (Phase 2 step 4) materializes child PicklistValue
        # entities from each customValue element per D-037 entity
        # ordering.
        """
        path = f"/services/data/{self.api_version}/tooling/query/"

        # Phase 1: bulk fetch all GVSes without Metadata or FullName.
        phase1_soql = (
            "SELECT Id, MasterLabel, Description, ManageableState, "
            "NamespacePrefix "
            "FROM GlobalValueSet"
        )
        records: list[dict] = self._query_all(path, phase1_soql)

        # Phase 2: per-Id FullName + Metadata fetch (Salesforce constraint:
        # 1 row max when selecting either field).
        for rec in records:
            rec_id = rec.get("Id")
            if not rec_id:
                continue
            phase2_soql = (
                f"SELECT Id, FullName, Metadata FROM GlobalValueSet "
                f"WHERE Id = '{rec_id}'"
            )
            phase2_records = self._query_all(path, phase2_soql)
            if phase2_records:
                rec["FullName"] = phase2_records[0].get("FullName")
                rec["Metadata"] = phase2_records[0].get("Metadata")

        return records

    def fetch_standard_value_sets(
        self,
        labels: Iterable[str] | None = None,
    ) -> list[dict]:
        """Tooling SOQL: SELECT … FROM StandardValueSet WHERE MasterLabel = …

        Endpoint: GET /services/data/{api_version}/tooling/query/?q=<SOQL>

        Returned records carry: Id, MasterLabel, FullName, Metadata.
        Metadata exposes the standardValue list (each element with
        SVS-specific shape per the Salesforce Metadata API guide).

        # No bulk enumeration available — StandardValueSet requires WHERE
        # filter on MasterLabel or DurableId per the reified-column
        # constraint (corrections log §4). Iterates either the full
        # canonical catalog (sf_constants.STANDARD_VALUE_SET_LABELS, 616
        # entries pinned to API v66.0) or a caller-supplied label subset.
        #
        # labels=None: full-iteration mode. ~6 min wall-clock against
        # this sandbox (~0.6s/call × 616 labels). Most calls return 0
        # rows on uncustomized orgs (per §5 category 3). Used for catalog
        # audits and initial seed syncs.
        #
        # labels=<iterable>: subset mode. Sync layer (Phase 2 step 4)
        # discovers SVSes referenced by field describes and passes the
        # subset, materializing only what the org actually uses.
        #
        # FullName is selectable (per Tooling describe of StandardValueSet)
        # despite filterable=False — the WHERE filter on MasterLabel
        # satisfies the 1-row constraint, freeing FullName + Metadata in
        # the same query.
        #
        # Sync layer materializes child PicklistValue entities from
        # Metadata.standardValue per D-037 entity ordering.
        """
        path = f"/services/data/{self.api_version}/tooling/query/"
        target_labels: tuple[str, ...] | tuple[str, ...]
        if labels is None:
            target_labels = STANDARD_VALUE_SET_LABELS
        else:
            target_labels = tuple(labels)

        results: list[dict] = []
        for label in target_labels:
            # Defensive escape: SOQL string literals escape apostrophes
            # with backslash. None of the canonical catalog labels contain
            # apostrophes, but caller-supplied subset mode could carry
            # discovered labels with arbitrary characters.
            escaped_label = label.replace("'", "\\'")
            soql = (
                "SELECT Id, MasterLabel, FullName, Metadata "
                "FROM StandardValueSet "
                f"WHERE MasterLabel = '{escaped_label}'"
            )
            recs = self._query_all(path, soql)
            if recs:
                results.append(recs[0])

        return results

    def fetch_profiles(self) -> list[dict]:
        """Tooling SOQL: SELECT … FROM Profile, with FullName + Metadata.

        Endpoint: GET /services/data/{api_version}/tooling/query/?q=<SOQL>

        Returned records carry: Id, Name, Description, CreatedDate,
        LastModifiedDate, FullName, Metadata. Metadata exposes the full
        Profile permission shape: objectPermissions, fieldPermissions,
        userPermissions, tabVisibilities, recordTypeVisibilities,
        classAccesses, pageAccesses, applicationVisibilities, etc.

        # Two-phase fetch (Category 2) per corrections-log §1, §5.
        # Phase 1 bulk fetches Id + 4 metadata-light fields. Phase 2
        # fetches FullName + Metadata per-Id. Sandbox at 18 standard
        # Profiles; Profile.Metadata payload averages ~278 KB
        # (System Administrator measured at 277,853 bytes), so per-sync
        # network ~5 MB for the standard sandbox profile set.
        # Customer orgs typically have 30-100 profiles → 8-30 MB; within
        # sync budget but capacity-planning-relevant.
        # Metadata.objectPermissions / fieldPermissions / userPermissions /
        # tabVisibilities / recordTypeVisibilities arrays are returned
        # verbatim; sync layer normalizes into edge entities per D-037.
        # Profile schema confirmed at 9 fields per Tooling describe;
        # UserType / UserLicenseId do NOT exist on Profile in Tooling
        # API at v66.0 (those live on the User object instead).
        """
        path = f"/services/data/{self.api_version}/tooling/query/"

        # Phase 1: bulk fetch Profiles without Metadata or FullName.
        phase1_soql = (
            "SELECT Id, Name, Description, CreatedDate, LastModifiedDate "
            "FROM Profile"
        )
        records: list[dict] = self._query_all(path, phase1_soql)

        # Phase 2: per-Id FullName + Metadata fetch (Salesforce constraint:
        # 1 row max when selecting either field).
        for rec in records:
            rec_id = rec.get("Id")
            if not rec_id:
                continue
            phase2_soql = (
                f"SELECT Id, FullName, Metadata FROM Profile "
                f"WHERE Id = '{rec_id}'"
            )
            phase2_records = self._query_all(path, phase2_soql)
            if phase2_records:
                rec["FullName"] = phase2_records[0].get("FullName")
                rec["Metadata"] = phase2_records[0].get("Metadata")

        return records

    def fetch_permission_sets(self) -> dict:
        """Tooling SOQL × 1 (parent) + Data SOQL × 2 (children) — full
        Category 4 fetch for PermissionSet.

        Returns:
            {
                "permission_sets": list[dict],     # parent rows
                "object_permissions": list[dict],  # child grants, ParentId-joined
                "field_permissions": list[dict],   # child grants, ParentId-joined
            }

        # Category 4 pattern (corrections-log §5). PermissionSet has no
        # Metadata complexvalue column; permission data is denormalized as
        # ~350 Permissions* boolean columns on the parent row plus separate
        # ObjectPermissions and FieldPermissions child entities.
        #
        # ENDPOINT ASYMMETRY: PermissionSet is queryable via both Tooling
        # API and Data API. ObjectPermissions and FieldPermissions are
        # Data-API-only — the Tooling API rejects them with INVALID_TYPE.
        # We use Tooling for the parent query (FIELDS(STANDARD) is a
        # Tooling-API SOQL feature giving us all 408 columns in one
        # statement) and Data API for the two child queries. Same OAuth
        # token, same retry plumbing; only the URL path differs.
        #
        # Three queries fetch the full picture. Sync layer joins children
        # to parents via ParentId.
        #
        # Returns dict with three keys (permission_sets, object_permissions,
        # field_permissions) rather than a flat list to make the structural
        # asymmetry from Category 2 entities explicit at the API surface.
        # Matches fetch_layouts_for_object's structured-dict precedent.
        #
        # Sandbox at 71 PermissionSet rows; 18 of those are Type='Profile'
        # auto-synthetic duplicates of the Profile rows (corrections-log
        # §5 Category 4 note). Sync layer filters Type='Profile' to avoid
        # duplication; fetch returns them per transparent-transport-
        # boundary principle.
        """
        tooling_path = f"/services/data/{self.api_version}/tooling/query/"
        data_path = f"/services/data/{self.api_version}/query/"

        # Query 1: PermissionSet parent rows via FIELDS(STANDARD) — Tooling
        parent_soql = "SELECT FIELDS(STANDARD) FROM PermissionSet"
        permission_sets = self._query_all(tooling_path, parent_soql)

        # Query 2: ObjectPermissions — Data API (Tooling rejects with
        # INVALID_TYPE)
        op_soql = (
            "SELECT ParentId, SobjectType, "
            "PermissionsRead, PermissionsCreate, PermissionsEdit, "
            "PermissionsDelete, PermissionsModifyAllRecords, "
            "PermissionsViewAllRecords "
            "FROM ObjectPermissions"
        )
        object_permissions = self._query_all(data_path, op_soql)

        # Query 3: FieldPermissions — Data API (same INVALID_TYPE rationale)
        fp_soql = (
            "SELECT ParentId, Field, "
            "PermissionsRead, PermissionsEdit "
            "FROM FieldPermissions"
        )
        field_permissions = self._query_all(data_path, fp_soql)

        return {
            "permission_sets": permission_sets,
            "object_permissions": object_permissions,
            "field_permissions": field_permissions,
        }

    def fetch_users(self) -> list[dict]:
        """Fetch all User records via Data API SOQL with a deliberately-
        scoped 12-field SELECT.

        Category 1 single-phase pattern (corrections-log §5): User has
        no Metadata complexvalue and no two-phase fetch requirement.
        Single SOQL returns all data needed.

        # ENDPOINT: Data API (/services/data/{v}/query/), not Tooling.
        # User is a standard sObject, not a Tooling-API entity. First
        # non-Tooling fetch method on this client. Uses the data_path
        # pattern established by fetch_permission_sets.
        #
        # PAGINATION: _query_all walks any paginated response. Sandbox
        # at 6 users (single page). Customer orgs commonly have hundreds
        # to low thousands of users; pagination engages on orgs with
        # >2000.
        #
        # FIELD SCOPE — 12 fields, deliberately limited:
        # - Identity: Id, Username, Email, Name, Alias
        # - Status/role: IsActive, UserType, ProfileId, UserRoleId
        # - Audit timestamps: CreatedDate, LastModifiedDate, LastLoginDate
        #
        # Not pulled: Phone, Fax, Street/City/PostalCode, MobilePhone,
        # CompanyName, Title, Department, Division, EmployeeNumber,
        # ManagerId, FederationIdentifier, profile photos, ContactId,
        # AccountId (community users), ~70 other standard fields.
        # Rationale: PrimeQA's semantic model uses User entities for
        # test attribution (which user ran a test, which user owns a
        # record referenced by a test). Personal data beyond identity
        # + role context isn't needed for that purpose. The deliberate
        # scope also reduces accidental PII surface in sync, model
        # storage, and downstream LLM context.
        #
        # UserLicenseId NOT selected: User does not carry it directly
        # (verified live; INVALID_FIELD on User.UserLicenseId at v66.0).
        # License attribution is derived at sync time via
        # User.ProfileId → Profile.UserLicenseId join. fetch_profiles
        # (Method 4) already pulls UserLicenseId via Profile.Metadata.
        #
        # NO FETCH-TIME FILTERING: returns all User rows regardless of
        # IsActive or UserType. Sync layer filters per its policy
        # (e.g., excluding platform synthetics like AutomatedProcess /
        # CloudIntegrationUser / CsnOnly, or excluding inactive
        # historical users). Per transparent-transport-boundary
        # principle.
        #
        # Sandbox composition (developer org, 2026-05-08):
        # - 6 total users (1 page)
        # - UserType: 3 Standard, 1 AutomatedProcess,
        #   1 CloudIntegrationUser, 1 CsnOnly
        # - IsActive: 5 active, 1 inactive
        # - 4 distinct ProfileIds (some shared)
        # - UserRoleId universally null (roles undefined; typical for
        #   developer sandboxes)
        """
        data_path = f"/services/data/{self.api_version}/query/"
        soql = (
            "SELECT Id, Username, Email, Name, Alias, "
            "IsActive, UserType, ProfileId, UserRoleId, "
            "CreatedDate, LastModifiedDate, LastLoginDate "
            "FROM User"
        )
        return self._query_all(data_path, soql)
