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
