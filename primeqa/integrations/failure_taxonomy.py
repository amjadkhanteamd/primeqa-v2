"""Typed failure taxonomy — the single source of truth that classifies a
sync/execution failure into one queryable category (Phase 2 Slice A).

ONE mapping table: :func:`category_for` (keyed on the recorded ``error_type`` name
+ the Salesforce ``errorCode``). :func:`classify_failure` is its canonical
**exception** entry point — the S1 sync engine passes the LIVE exception, so this
reads ``SFRequestError.error_code`` (the parsed SF ``errorCode``) and
``status_code`` for the fullest fidelity. The S4 result store reuses
``category_for`` over the recorded evidence surface (it has no live exception at
persist). The mapping is NOT duplicated at call sites — both entry points route
through ``category_for``.

Categories (stored as VARCHAR; the code enum below is the source of truth):
  auth          — authentication / token failure (``SFAuthError`` / 401)
  rate_limit    — Salesforce throttle / quota (``SFRateLimitError`` / 429)
  permission    — the integration user lacks access (FLS / object / record): the
                  SF ``errorCode`` is one of the access-denial codes (the CF-1
                  signal; D-225 named these on the mutation path)
  transient     — a retryable server / transport hiccup (5xx, broken pagination)
  normalization — a representation / world-build defect on our side
  unknown       — unclassified (the default; never load-bearing)
"""
from __future__ import annotations

from typing import Optional, Tuple


class FailureCategory:
    """The failure-category vocabulary (one source of truth, importable by both
    sync and execution). Stored as a nullable VARCHAR — NULL means "no failure"."""

    AUTH = "auth"
    PERMISSION = "permission"
    TRANSIENT = "transient"
    RATE_LIMIT = "rate_limit"
    NORMALIZATION = "normalization"
    UNKNOWN = "unknown"


ALL_CATEGORIES = frozenset({
    FailureCategory.AUTH, FailureCategory.PERMISSION, FailureCategory.TRANSIENT,
    FailureCategory.RATE_LIMIT, FailureCategory.NORMALIZATION,
    FailureCategory.UNKNOWN,
})

# Salesforce ``errorCode``s meaning "the integration user lacks access" — the
# permission / FLS signal. These are exactly the codes D-225 surfaces on the
# mutation path (an FLS-hidden field denies the write); they may also ride an
# ``SFRequestError`` on a read.
_PERMISSION_CODES = frozenset({
    "INSUFFICIENT_ACCESS",
    "INSUFFICIENT_ACCESS_OR_READONLY",
    "INSUFFICIENT_FIELD_ACCESS",
    "INSUFFICIENT_ACCESS_ON_CROSS_REFERENCE_ENTITY",
    "INVALID_FIELD_FOR_INSERT_UPDATE",
})

# Salesforce ``errorCode``s meaning "the request we built was malformed / named
# something that does not exist" — a representation defect on our side.
_NORMALIZATION_CODES = frozenset({
    "MALFORMED_QUERY",
    "INVALID_FIELD",
    "INVALID_TYPE",
    "INVALID_QUERY_FILTER_OPERATOR",
    "JSON_PARSER_ERROR",
})

# Recorded ``error_type`` names (exception class names OR synthesized
# ``ErrorSurface`` types) that are representation / world-build defects on our side.
_NORMALIZATION_TYPES = frozenset({
    "UnfillableWorld",          # S4 could not construct required field(s)/parent(s)
    "StepRefResolutionError",   # a cross-step reference could not be resolved
})

# Recorded ``error_type`` names that are transient server / transport hiccups.
_TRANSIENT_TYPES = frozenset({
    "SFIncompletePaginationError",   # an SF cursor broke mid-walk (retryable)
})


def category_for(error_type: Optional[str], sf_error_code: Optional[str]) -> str:
    """The single mapping table. ``error_type`` is the recorded exception class
    name OR a synthesized ``ErrorSurface`` type; ``sf_error_code`` is the parsed
    Salesforce ``errorCode`` when one is available (else ``None``).

    Pure and total — always returns a category (defaults to ``unknown``)."""
    if error_type == "SFAuthError":
        return FailureCategory.AUTH
    if error_type == "SFRateLimitError":
        return FailureCategory.RATE_LIMIT
    # A clear access-denial code wins regardless of which exception carried it.
    if sf_error_code in _PERMISSION_CODES:
        return FailureCategory.PERMISSION
    if sf_error_code in _NORMALIZATION_CODES:
        return FailureCategory.NORMALIZATION
    if error_type in _NORMALIZATION_TYPES:
        return FailureCategory.NORMALIZATION
    if error_type in _TRANSIENT_TYPES:
        return FailureCategory.TRANSIENT
    return FailureCategory.UNKNOWN


def classify_failure(exc: BaseException) -> Tuple[str, Optional[str]]:
    """Canonical entry: classify a LIVE exception into
    ``(category, sf_error_code)``.

    The caller passes the live exception so this can read
    ``SFRequestError.error_code`` (the parsed Salesforce ``errorCode``) and
    ``status_code`` for the fullest fidelity. Pure; never raises (a faulty
    ``error_code`` property is swallowed → ``None``)."""
    error_type = type(exc).__name__
    sf_error_code = _safe_error_code(exc)
    category = category_for(error_type, sf_error_code)
    # Refine an otherwise-unknown SFRequestError by HTTP status: a 5xx (or a 429
    # that did not classify as SFRateLimitError) is a transient server hiccup.
    if category == FailureCategory.UNKNOWN and error_type == "SFRequestError":
        status = getattr(exc, "status_code", None)
        if isinstance(status, int) and (status == 429 or 500 <= status <= 599):
            category = FailureCategory.TRANSIENT
    return category, sf_error_code


def _safe_error_code(exc: BaseException) -> Optional[str]:
    """Read ``exc.error_code`` (on ``SFRequestError`` this property parses the SF
    response body) without ever raising; returns the string code or ``None``."""
    try:
        code = getattr(exc, "error_code", None)
    except Exception:
        return None
    return code if isinstance(code, str) and code else None


# --- metadata-gap surfacing (Phase 2 Slice B) --------------------------------
# A "gap" is a swallowed metadata-fetch failure that DROPPED real model data (a
# field's metadata, a permission-set edge) — the sites that previously
# log-and-continued, hiding a permission/FLS denial behind a warning. Each gap is
# classified through the SAME taxonomy; the dropped subject travels in ``context``.

def build_gap(site: str, exc: BaseException, *, context: Optional[dict] = None) -> dict:
    """Classify a swallowed metadata-fetch failure into a serializable gap record.
    ``site`` names the swallow location; ``context`` carries WHAT was dropped (e.g.
    ``{"field_id": ...}`` / ``{"query": "PermissionSetGroupComponent"}``)."""
    category, sf_error_code = classify_failure(exc)
    return {
        "site": site,
        "category": category,
        "sf_error_code": sf_error_code,
        "context": context or {},
    }


def genuine_gap_count(gaps) -> int:
    """How many surfaced gaps reflect a REAL data-loss failure (not a benign,
    expected catalog gap). A gap classified ``unknown`` — e.g. a 404 because an
    org legitimately lacks a feature (PSG not enabled) — is recorded for
    visibility but does NOT count, so it never false-positives the run to
    ``partial_success``. A typed failure (``permission`` / ``transient`` /
    ``rate_limit`` / ``normalization``) is genuine and counts."""
    return sum(1 for g in gaps
               if g.get("category") and g["category"] != FailureCategory.UNKNOWN)
