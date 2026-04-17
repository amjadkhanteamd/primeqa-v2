"""Uniform API response envelope and error types.

Every JSON endpoint in the test-management domain uses `json_page` (for
list/search responses) and `json_error` (for 4xx/5xx). Frontend code can rely
on a single shape:

    { "data": [...], "meta": { total, page, per_page, total_pages } }
    { "error": { "code": "...", "message": "...", "details": {...} } }

Serialisation for individual items is the caller's concern (the legacy _x_dict
helpers in service.py); this module just wraps the already-dict'd payload.
"""

from typing import Any, Callable, Dict, Iterable, Optional

from flask import jsonify

from primeqa.shared.query_builder import PageResult


# ---- custom error types -------------------------------------------------------

class ServiceError(Exception):
    code = "INTERNAL_ERROR"
    http = 500

    def __init__(self, message: str, code: Optional[str] = None,
                 http: Optional[int] = None, details: Any = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if http:
            self.http = http
        self.details = details


class NotFoundError(ServiceError):
    code = "NOT_FOUND"
    http = 404


class ValidationError(ServiceError):
    code = "VALIDATION_ERROR"
    http = 400


class ConflictError(ServiceError):
    code = "CONFLICT"
    http = 409


class ForbiddenError(ServiceError):
    code = "FORBIDDEN"
    http = 403


class BulkLimitError(ValidationError):
    code = "BULK_LIMIT_EXCEEDED"


class BulkConfirmError(ValidationError):
    code = "BULK_CONFIRM_REQUIRED"


BULK_MAX_ITEMS = 100


# ---- response helpers ---------------------------------------------------------

def json_page(page: PageResult, serialize: Callable[[Any], Dict] = None, http: int = 200):
    serialize = serialize or (lambda x: x)
    body = {
        "data": [serialize(item) for item in page.items],
        "meta": {
            "total": page.total,
            "page": page.page,
            "per_page": page.per_page,
            "total_pages": page.total_pages,
        },
    }
    return jsonify(body), http


def json_list(items: Iterable, *, total: Optional[int] = None,
              page: int = 1, per_page: int = 20,
              serialize: Callable[[Any], Dict] = None, http: int = 200):
    """For callers that already have items + total separately (no ListQuery)."""
    items = list(items)
    serialize = serialize or (lambda x: x)
    t = total if total is not None else len(items)
    from math import ceil
    body = {
        "data": [serialize(i) for i in items],
        "meta": {
            "total": t,
            "page": page,
            "per_page": per_page,
            "total_pages": max(1, ceil(t / per_page)) if t else 0,
        },
    }
    return jsonify(body), http


def json_error(code: str, message: str, http: int = 400, details: Any = None):
    body = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return jsonify(body), http


def json_error_from(exc: Exception):
    """Map a ServiceError (or plain ValueError) to the uniform envelope."""
    if isinstance(exc, ServiceError):
        return json_error(exc.code, exc.message, http=exc.http, details=exc.details)
    # Plain ValueError -> 400 VALIDATION_ERROR
    return json_error("VALIDATION_ERROR", str(exc), http=400)


# ---- request param helpers ---------------------------------------------------

def parse_list_params(request, *, allowed_filters: Iterable[str] = (),
                      default_sort: str = "updated_at",
                      default_order: str = "desc") -> Dict[str, Any]:
    """Pull pagination/search/sort/filter params off a Flask request.

    Returns a plain dict (not a class) so it can flow directly into ListQuery.
    """
    args = request.args
    show_deleted = args.get("deleted", "").lower() in ("1", "true", "yes")
    filters = {k: args.get(k) for k in allowed_filters}
    return {
        "page": args.get("page", 1, type=int),
        "per_page": args.get("per_page", 20, type=int),
        "q": args.get("q") or args.get("search") or "",
        "sort": args.get("sort", default_sort),
        "order": args.get("order", default_order),
        "filters": {k: v for k, v in filters.items() if v is not None and v != ""},
        "show_deleted": show_deleted,
    }


def require_bulk_confirm(data: Dict[str, Any], ids: Iterable):
    """Reject if the bulk payload exceeds the cap or is missing confirmation."""
    ids = list(ids or [])
    if len(ids) > BULK_MAX_ITEMS:
        raise BulkLimitError(
            f"Bulk action exceeds the {BULK_MAX_ITEMS}-item limit (received {len(ids)}).",
            details={"limit": BULK_MAX_ITEMS, "received": len(ids)},
        )
    if (data or {}).get("confirm") != "DELETE":
        raise BulkConfirmError(
            "Destructive bulk action requires confirm='DELETE'.",
            details={"required_confirm_token": "DELETE"},
        )
