"""Live integration tests against the user's Salesforce sandbox.

Gated on @pytest.mark.sandbox marker. Skips automatically when the
4 SF_* env vars aren't set.

Run with:
    pytest tests/integration/test_sf_client_live.py -v -m sandbox

Per Phase 2 plan §4.2.
"""
from __future__ import annotations

import os
import pytest
from dotenv import load_dotenv

# Load .env at module level so the gate sees the values.
load_dotenv()


REQUIRED_ENV = ("SF_INSTANCE_URL", "SF_CLIENT_ID", "SF_CLIENT_SECRET", "SF_REFRESH_TOKEN")
HAS_SANDBOX_CREDS = all(os.environ.get(k) for k in REQUIRED_ENV)


pytestmark = pytest.mark.sandbox


@pytest.fixture
def live_client():
    """Real SalesforceClient bound to the sandbox via .env credentials."""
    if not HAS_SANDBOX_CREDS:
        missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
        pytest.skip(f"Sandbox credentials not configured (missing: {missing})")
    from primeqa.integrations.sf_client import SalesforceClient

    with SalesforceClient(
        instance_url=os.environ["SF_INSTANCE_URL"],
        client_id=os.environ["SF_CLIENT_ID"],
        client_secret=os.environ["SF_CLIENT_SECRET"],
        refresh_token=os.environ["SF_REFRESH_TOKEN"],
    ) as c:
        yield c


# ----------------------------------------------------------------------
# Live tests (each one calls Salesforce; ~1-3 seconds per test)
# ----------------------------------------------------------------------

def test_live_token_refresh(live_client):
    """Round-trip the refresh-token exchange against the real OAuth endpoint."""
    live_client._refresh_access_token()
    assert live_client._access_token is not None
    assert len(live_client._access_token) > 50, (
        "Salesforce access_token should be a long opaque string"
    )


def test_live_fetch_objects(live_client):
    """Global describe should return the standard catalog including Account."""
    objects = live_client.fetch_objects()
    assert isinstance(objects, list)
    assert len(objects) > 100, "Any sandbox should expose 100+ sobjects"
    account = next((o for o in objects if o.get("name") == "Account"), None)
    assert account is not None, "Account standard object should exist in sandbox"
    assert account.get("label"), "Account should have a label"


def test_live_fetch_fields_for_account(live_client):
    """Per-object describe of Account should include the standard Industry field."""
    fields = live_client.fetch_fields_for_object("Account")
    assert isinstance(fields, list)
    assert len(fields) > 10, "Account always has many fields"
    industry = next((f for f in fields if f.get("name") == "Industry"), None)
    assert industry is not None, "Industry standard field should exist on Account"
    assert industry.get("type") == "picklist"


def test_live_fetch_validation_rules(live_client):
    """Tooling SOQL for ValidationRule should return a list (possibly empty)."""
    rules = live_client.fetch_validation_rules()
    assert isinstance(rules, list)
    # Sandbox may have zero validation rules; just verify the call works.
    if rules:
        first = rules[0]
        # Tooling API field naming is PascalCase
        assert "ValidationName" in first or "validationName" in first
        assert "Metadata" in first or "metadata" in first


def test_live_fetch_record_types(live_client):
    """Tooling SOQL for RecordType should return a list (possibly empty)
    with FullName and Metadata merged in via the two-phase fetch.

    Sandbox at the time of writing has 5 RecordTypes (all from the
    License Management App managed package). Don't assert on count
    or specific values — just verify shape and that both phases
    completed for each record.
    """
    rts = live_client.fetch_record_types()
    assert isinstance(rts, list)
    assert len(rts) > 0, "Sandbox should have at least one RecordType"

    for rt in rts:
        # Phase 1 fields
        assert "Id" in rt
        assert "Name" in rt
        assert "IsActive" in rt
        assert "SobjectType" in rt
        assert "EntityDefinitionId" in rt
        assert "ManageableState" in rt
        # Phase 2 fields (merged in from per-Id query)
        assert "FullName" in rt
        assert "Metadata" in rt
        if rt["Metadata"]:
            # Metadata should have these keys per Salesforce schema,
            # though values may be None (e.g., businessProcess often NULL).
            metadata = rt["Metadata"]
            assert "active" in metadata
            assert "label" in metadata
            assert "picklistValues" in metadata  # list, possibly empty


def test_live_fetch_layouts_for_account(live_client):
    """REST describe/layouts for Account should return the full structured
    response: layouts, recordTypeMappings, recordTypeSelectorRequired.

    Sandbox at the time of writing has 1 Account layout. Don't assert on
    layout name or count beyond >=1 — varies by org. Verifies the nested
    detailLayoutSections / layoutRows structure that the sync layer will
    walk in Phase 2 step 4.
    """
    result = live_client.fetch_layouts_for_object("Account")
    assert isinstance(result, dict)
    assert "layouts" in result
    assert "recordTypeMappings" in result
    assert "recordTypeSelectorRequired" in result
    assert isinstance(result["layouts"], list)
    assert len(result["layouts"]) >= 1

    # Inspect first layout structure
    layout = result["layouts"][0]
    assert "id" in layout
    assert "detailLayoutSections" in layout
    assert isinstance(layout["detailLayoutSections"], list)
    assert len(layout["detailLayoutSections"]) >= 1

    # Section structure
    section = layout["detailLayoutSections"][0]
    assert "heading" in section
    assert "layoutRows" in section
    assert isinstance(section["layoutRows"], list)


def test_live_fetch_global_value_sets(live_client):
    """Two-phase Tooling SOQL for GlobalValueSet should return a list
    (possibly empty). This sandbox has 0 GVSes; the live test exercises
    the empty path. Populated path is covered by unit-test mocks against
    the documented Salesforce schema.
    """
    result = live_client.fetch_global_value_sets()
    assert isinstance(result, list)
    # Sandbox has 0 GVSes; this is the empty-path coverage.
    for gvs in result:  # may be empty; that's fine
        assert "Id" in gvs
        assert "MasterLabel" in gvs
        assert "FullName" in gvs
        assert "Metadata" in gvs


def test_live_fetch_standard_value_sets_subset(live_client):
    """Subset-mode fetch via 5 well-known core-platform labels.
    Full-catalog mode (labels=None) is ~6min runtime — NOT exercised
    here; unit-test mocks provide that contract coverage.

    The 5 labels are a stable subset of the 30 confirmed during the
    sanity-check phase of Method 3 catalog capture.
    """
    labels = [
        "Industry",
        "CaseStatus",
        "LeadSource",
        "OpportunityStage",
        "AccountType",
    ]
    result = live_client.fetch_standard_value_sets(labels=labels)
    assert isinstance(result, list)
    assert len(result) == 5  # all 5 confirmed during sanity-check
    for svs in result:
        assert "Id" in svs
        assert "MasterLabel" in svs
        assert "FullName" in svs
        assert "Metadata" in svs
        assert svs["MasterLabel"] in labels


def test_live_fetch_profiles(live_client):
    """Two-phase Tooling SOQL for Profile. Sandbox has 18 standard
    profiles; verify all phase-2 fields land and the documented
    Metadata sub-keys are present (objectPermissions, userPermissions,
    userLicense)."""
    profiles = live_client.fetch_profiles()
    assert isinstance(profiles, list)
    assert len(profiles) >= 15  # standard profiles always present
    for p in profiles:
        assert "Id" in p
        assert "Name" in p
        assert "FullName" in p
        assert "Metadata" in p
        if p["Metadata"]:
            md = p["Metadata"]
            assert "userPermissions" in md
            assert "objectPermissions" in md
            assert "userLicense" in md


def test_live_fetch_permission_sets(live_client):
    """Category 4 three-query fetch. Verify the three-key dict shape,
    parent rows include FIELDS(STANDARD)'s wide-flat-row Permissions*
    columns, Type='Profile' synthetic rows are included (not filtered
    at fetch layer), and child entities ObjectPermissions /
    FieldPermissions are populated.
    """
    result = live_client.fetch_permission_sets()
    assert isinstance(result, dict)
    assert set(result.keys()) == {
        "permission_sets",
        "object_permissions",
        "field_permissions",
    }
    assert len(result["permission_sets"]) > 0
    # Sandbox has 71 PSes; expect at least 50 to allow for platform changes
    assert len(result["permission_sets"]) >= 50

    # Spot-check parent row shape
    ps = result["permission_sets"][0]
    assert "Id" in ps
    assert "Name" in ps
    assert "Type" in ps
    # Verify some Permissions* boolean field is present
    permissions_keys = [k for k in ps.keys() if k.startswith("Permissions")]
    assert len(permissions_keys) > 100  # ~350 expected from FIELDS(STANDARD)

    # Spot-check that Type='Profile' synthetic rows are included
    # (transparent-transport-boundary; sync-layer filters)
    types = {p["Type"] for p in result["permission_sets"]}
    assert "Profile" in types

    # Spot-check children query results are populated, and verify
    # SOQL pagination kicked in (both child queries return well over
    # the 2000-row Salesforce default cap on this sandbox per
    # corrections-log §6).
    assert len(result["object_permissions"]) > 2000  # confirms pagination
    assert len(result["field_permissions"]) > 2000   # confirms pagination

    op = result["object_permissions"][0]
    assert "ParentId" in op
    assert "SobjectType" in op

    fp = result["field_permissions"][0]
    assert "ParentId" in fp
    assert "Field" in fp


def test_live_fetch_users(live_client):
    """Single-phase Data-API SOQL for User. Sandbox at ~6 users
    (well under the 2000-row pagination cap). Verifies the
    deliberately-scoped 12-field shape is populated; doesn't assert
    specific synthetic UserTypes (composition can shift across
    sandbox refreshes).
    """
    result = live_client.fetch_users()
    assert isinstance(result, list)

    # Sandbox at ~6 users; allow margin for org-state shifts
    assert 1 <= len(result) <= 50

    # Required field shape on every record
    for u in result:
        assert "Id" in u
        assert "Username" in u
        assert "IsActive" in u
        assert "UserType" in u
        assert "ProfileId" in u
        assert "CreatedDate" in u
        # UserRoleId is permitted to be null on this sandbox;
        # don't assert non-null
        assert "UserRoleId" in u

    # Sandbox should have at least one Standard user (the
    # developer logged in)
    user_types = {u["UserType"] for u in result}
    assert "Standard" in user_types

    # Don't assert specific synthetic UserTypes are present;
    # composition can shift across sandbox refreshes


def test_live_fetch_flow_definitions(live_client):
    """Two-phase Tooling SOQL for FlowDefinition. Sandbox at 13
    FlowDefinitions; allow margin for org-state shifts and
    managed-package updates. Verifies summary Metadata payload
    structure."""
    fds = live_client.fetch_flow_definitions()
    assert isinstance(fds, list)

    # Sandbox at 13 FlowDefinitions; allow margin for org-state
    # shifts and managed-package updates
    assert 5 <= len(fds) <= 100

    for fd in fds:
        assert "Id" in fd
        assert "DeveloperName" in fd
        assert "FullName" in fd
        assert "Metadata" in fd
        # Some FlowDefinitions may have no active version
        # (draft-only flows); ActiveVersionId can be null
        assert "ActiveVersionId" in fd


def test_live_fetch_flow_versions(live_client):
    """Two-phase Tooling SOQL for Flow versions. Sandbox at 14
    versions; verifies the full graph Metadata structure
    (processType + start element). Asserts at least one Active
    version is present."""
    versions = live_client.fetch_flow_versions()
    assert isinstance(versions, list)

    # Sandbox at 14 versions; same margin
    assert 5 <= len(versions) <= 200

    for v in versions:
        assert "Id" in v
        assert "DefinitionId" in v
        assert "VersionNumber" in v
        assert "Status" in v
        assert "ProcessType" in v
        assert "FullName" in v
        assert "Metadata" in v
        if v["Metadata"]:
            md = v["Metadata"]
            # Verify Metadata has the expected top-level
            # structure — the array keys are present even
            # when empty, so this asserts shape not content
            assert "processType" in md
            # 'start' is the entry-point reference; present
            # on every well-formed flow
            assert "start" in md or "startElementReference" in md

    # Should have at least one Active version (the sandbox's
    # ManageableState='installed' managed-package flows are
    # all Active)
    statuses = {v["Status"] for v in versions}
    assert "Active" in statuses
