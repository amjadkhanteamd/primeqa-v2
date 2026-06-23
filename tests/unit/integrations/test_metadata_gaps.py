"""Phase 2 Slice B — surfacing swallowed metadata-fetch gaps (pure, no DB).

A "gap" is a swallowed metadata-fetch failure that dropped real model data. The
sites record it via ``SalesforceClient.record_metadata_gap`` (classified through
the Slice-A taxonomy); the engine flushes ``permission_gaps`` (genuine count) +
``gap_details`` and ``maybe_finalize_run`` turns ``permission_gaps > 0`` into
``partial_success``. These prove the classification + the genuine-vs-benign count
(the partial_success driver) + the per-site record mechanism.
"""
import json

import pytest

pytestmark = pytest.mark.unit

from primeqa.integrations.exceptions import SFRequestError
from primeqa.integrations.failure_taxonomy import (
    FailureCategory, build_gap, genuine_gap_count,
)
from primeqa.integrations.sf_client import SalesforceClient


def _req(status=None, code=None):
    body = json.dumps([{"errorCode": code, "message": "x"}]) if code else None
    return SFRequestError("boom", status_code=status, response_body=body)


# --- build_gap ---------------------------------------------------------------

def test_build_gap_permission_names_the_dropped_subject():
    g = build_gap("fetch_custom_field_metadata",
                  _req(status=403, code="INSUFFICIENT_FIELD_ACCESS"),
                  context={"field_id": "00N..."})
    assert g == {
        "site": "fetch_custom_field_metadata",
        "category": FailureCategory.PERMISSION,
        "sf_error_code": "INSUFFICIENT_FIELD_ACCESS",
        "context": {"field_id": "00N..."},
    }


def test_build_gap_feature_absent_404_is_unknown():
    g = build_gap("fetch_permission_sets.PermissionSetGroupComponent",
                  _req(status=404, code=None), context={"dropped": "edges"})
    assert g["category"] == FailureCategory.UNKNOWN
    assert g["sf_error_code"] is None


# --- genuine_gap_count (the partial_success driver) --------------------------

def test_genuine_count_excludes_benign_unknown():
    gaps = [
        {"category": FailureCategory.PERMISSION},
        {"category": FailureCategory.UNKNOWN},      # benign feature-absent
        {"category": FailureCategory.TRANSIENT},
    ]
    assert genuine_gap_count(gaps) == 2          # → run finalizes partial_success


def test_genuine_count_all_unknown_is_zero():
    # an org that legitimately lacks features (all 404→unknown) stays 'success'
    assert genuine_gap_count([{"category": FailureCategory.UNKNOWN}] * 3) == 0


def test_genuine_count_empty():
    assert genuine_gap_count([]) == 0


# --- record_metadata_gap on a real client (the per-site mechanism) -----------

def _client():
    return SalesforceClient(
        instance_url="https://x.my.salesforce.com", client_id="c",
        client_secret="s", refresh_token="", access_token="t")


def test_record_gap_accumulates_classified_and_keeps_resilience():
    c = _client()
    assert c.metadata_gaps == []
    c.record_metadata_gap("fetch_users.PermissionSetAssignment",
                          _req(status=403, code="INSUFFICIENT_ACCESS"),
                          context={"dropped": "HAS_PERMISSION_SET edges"})
    assert len(c.metadata_gaps) == 1
    g = c.metadata_gaps[0]
    assert g["site"] == "fetch_users.PermissionSetAssignment"
    assert g["category"] == FailureCategory.PERMISSION
    assert g["sf_error_code"] == "INSUFFICIENT_ACCESS"
    assert g["context"] == {"dropped": "HAS_PERMISSION_SET edges"}
    # the genuine count drives partial_success
    assert genuine_gap_count(c.metadata_gaps) == 1


def test_record_gap_benign_404_recorded_but_not_genuine():
    c = _client()
    c.record_metadata_gap("fetch_standard_value_sets", _req(status=404),
                          context={"value_set": "CaseStatus"})
    assert len(c.metadata_gaps) == 1            # visible in gap_details
    assert genuine_gap_count(c.metadata_gaps) == 0   # but does NOT inflate
