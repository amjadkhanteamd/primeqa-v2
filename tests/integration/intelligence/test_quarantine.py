"""Integration: D-232 — the persisted quarantine ledger (`claim_quarantine`). The
QuarantineStore's pin / unpin / read against the migrated governance schema. The
`manual_states` override map (manual wins; auto is not an override) is the seam the
release decision consumes."""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from primeqa.intelligence import quarantine
from primeqa.semantic.connection import get_tenant_connection

from tests.integration.generation.conftest import TEST_TENANT_ID

pytestmark = pytest.mark.usefixtures("db_setup")


@pytest.fixture(autouse=True)
def _clean(db_setup):
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        conn.execute(text("DELETE FROM claim_quarantine"))
    yield


def test_pin_then_read():
    tid = uuid4()
    assert quarantine.pin(TEST_TENANT_ID, tid, reason="flaky in CI", actor=3) is True
    assert quarantine.is_quarantined(TEST_TENANT_ID, tid) is True
    assert quarantine.manual_states(TEST_TENANT_ID) == {str(tid): "pinned"}
    listed = quarantine.list_quarantined(TEST_TENANT_ID)
    assert len(listed) == 1 and listed[0]["test_id"] == str(tid)
    assert listed[0]["reason"] == "flaky in CI" and listed[0]["source"] == "manual"


def test_unpin_lifts_and_persists_the_override():
    tid = uuid4()
    quarantine.pin(TEST_TENANT_ID, tid, reason="x", actor=3)
    assert quarantine.unpin(TEST_TENANT_ID, tid, actor=4) is True
    assert quarantine.is_quarantined(TEST_TENANT_ID, tid) is False
    # a manual lift PERSISTS as an override so the live auto-signal stays suppressed.
    assert quarantine.manual_states(TEST_TENANT_ID) == {str(tid): "lifted"}
    assert quarantine.list_quarantined(TEST_TENANT_ID) == []


def test_unpin_never_pinned_records_a_manual_lift_override():
    # Un-quarantining a claim with no row inserts an inactive manual lift — the operator
    # explicitly overriding a claim the auto-signal would otherwise flag.
    tid = uuid4()
    assert quarantine.unpin(TEST_TENANT_ID, tid, actor=4) is True
    assert quarantine.manual_states(TEST_TENANT_ID) == {str(tid): "lifted"}


def test_repin_reactivates():
    tid = uuid4()
    quarantine.pin(TEST_TENANT_ID, tid, actor=3)
    quarantine.unpin(TEST_TENANT_ID, tid, actor=4)
    quarantine.pin(TEST_TENANT_ID, tid, reason="again", actor=5)
    assert quarantine.is_quarantined(TEST_TENANT_ID, tid) is True
    assert quarantine.manual_states(TEST_TENANT_ID) == {str(tid): "pinned"}


def test_auto_source_is_not_a_manual_override():
    # An auto-pinned row (the future loop) shows the badge but is NOT an override —
    # manual_states ignores it, so the live signal still governs the decision.
    tid = uuid4()
    quarantine.pin(TEST_TENANT_ID, tid, source="auto", actor=None)
    assert quarantine.is_quarantined(TEST_TENANT_ID, tid) is True
    assert quarantine.manual_states(TEST_TENANT_ID) == {}
