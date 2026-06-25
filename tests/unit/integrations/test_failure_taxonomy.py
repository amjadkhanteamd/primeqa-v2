"""Phase 2 Slice A — the typed failure taxonomy (pure, no DB).

``classify_failure(exc)`` is the canonical exception entry; ``category_for`` is the
single mapping table both sync and execution route through. This matrix is the
red-proof: every SF exception type + representative SF errorCodes → the right
category; the permission-shaped codes (the CF-1 signal) map to ``permission``;
non-SF / unclassified → ``unknown``.
"""
import json

import pytest

pytestmark = pytest.mark.unit

from primeqa.execution_engine.errors import StepRefResolutionError
from primeqa.integrations.exceptions import (
    SFAuthError, SFIncompletePaginationError, SFRateLimitError, SFRequestError,
)
from primeqa.integrations.failure_taxonomy import (
    ALL_CATEGORIES, INDETERMINATE_CATEGORIES, PERMANENT_CATEGORIES,
    FailureCategory, category_for, classify_failure, is_indeterminate,
)


def _req(status=None, code=None, message="boom"):
    body = json.dumps([{"errorCode": code, "message": message}]) if code else None
    return SFRequestError("boom", status_code=status, response_body=body)


def test_auth():
    assert classify_failure(SFAuthError("repeated 401")) == \
        (FailureCategory.AUTH, None)


def test_rate_limit():
    assert classify_failure(SFRateLimitError("429")) == \
        (FailureCategory.RATE_LIMIT, None)


@pytest.mark.parametrize("code", [
    "INSUFFICIENT_FIELD_ACCESS", "INSUFFICIENT_ACCESS_OR_READONLY",
    "INSUFFICIENT_ACCESS", "INVALID_FIELD_FOR_INSERT_UPDATE",
    "INSUFFICIENT_ACCESS_ON_CROSS_REFERENCE_ENTITY",
])
def test_permission_codes_map_to_permission(code):
    cat, got = classify_failure(_req(status=403, code=code))
    assert cat == FailureCategory.PERMISSION
    assert got == code


@pytest.mark.parametrize("code", [
    "MALFORMED_QUERY", "INVALID_FIELD", "INVALID_TYPE",
    "INVALID_QUERY_FILTER_OPERATOR", "JSON_PARSER_ERROR",
])
def test_normalization_codes(code):
    cat, got = classify_failure(_req(status=400, code=code))
    assert cat == FailureCategory.NORMALIZATION
    assert got == code


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_5xx_request_error_is_transient(status):
    cat, code = classify_failure(_req(status=status, code=None))
    assert cat == FailureCategory.TRANSIENT
    assert code is None


def test_request_error_4xx_no_code_is_unknown():
    # a 4xx with no parseable errorCode and not access-shaped → unknown
    assert classify_failure(_req(status=400, code=None)) == \
        (FailureCategory.UNKNOWN, None)


def test_step_ref_resolution_is_normalization():
    assert classify_failure(StepRefResolutionError("ref")) == \
        (FailureCategory.NORMALIZATION, None)


def test_incomplete_pagination_is_transient():
    cat, code = classify_failure(SFIncompletePaginationError("cursor"))
    assert cat == FailureCategory.TRANSIENT
    assert code is None


def test_non_sf_exception_is_unknown():
    assert classify_failure(ValueError("not an SF error")) == \
        (FailureCategory.UNKNOWN, None)


def test_category_for_synthesized_surface_types():
    # UnfillableWorld is a synthesized ErrorSurface type (not an exception)
    assert category_for("UnfillableWorld", None) == FailureCategory.NORMALIZATION
    # a synthesized HTTP rejection with a permission code → permission
    assert category_for("SetupRejected", "INSUFFICIENT_FIELD_ACCESS") == \
        FailureCategory.PERMISSION
    # the same without a code → unknown (coarse, as documented)
    assert category_for("SetupRejected", None) == FailureCategory.UNKNOWN


def test_category_for_is_total():
    assert category_for("AnythingElse", None) in ALL_CATEGORIES
    assert category_for(None, None) == FailureCategory.UNKNOWN


# --- D-272 Slice 1: indeterminate-vs-permanent partition ---------------------

def test_partition_is_exhaustive_and_disjoint():
    # every failure_category value is classified, exactly once.
    assert PERMANENT_CATEGORIES <= ALL_CATEGORIES
    assert INDETERMINATE_CATEGORIES | PERMANENT_CATEGORIES == ALL_CATEGORIES
    assert not (INDETERMINATE_CATEGORIES & PERMANENT_CATEGORIES)


def test_only_normalization_is_permanent():
    # the one our-side malformed/un-buildable defect is permanent; the rest are
    # could-not-evaluate → re-runnable.
    assert PERMANENT_CATEGORIES == {FailureCategory.NORMALIZATION}


@pytest.mark.parametrize("category", [
    FailureCategory.AUTH, FailureCategory.PERMISSION, FailureCategory.TRANSIENT,
    FailureCategory.RATE_LIMIT, FailureCategory.UNKNOWN,
])
def test_indeterminate_categories_are_re_runnable(category):
    # credential / throttle / transport / environment-not-satisfiable / unknown
    # are evidence-incomplete → re-runnable (D-272 §2.4), incl. auth + permission.
    assert is_indeterminate(category) is True


def test_normalization_is_permanent_not_re_runnable():
    assert is_indeterminate(FailureCategory.NORMALIZATION) is False


def test_none_is_indeterminate_conservative():
    # an errored run with no captured/derivable category never settles as a
    # permanent failure — re-runnable by default.
    assert is_indeterminate(None) is True
