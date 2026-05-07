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
