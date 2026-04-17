"""Centralised list-query builder used by every repository.

Goal: one place for filter + search + sort + pagination logic so that no repo
duplicates it, per-entity quirks are confined to a ListSpec, and nothing in
a request body can reach SQL without passing through whitelist validation.

Public surface:
    PageResult  — the shape all list endpoints return
    ListQuery   — the builder chained off a base SQLAlchemy query
    build_page_result(...) — convenience for callers that already have items+total

Example:
    q = db.query(TestCase).filter(TestCase.tenant_id == tid)
    page = (ListQuery(q, TestCase,
                      search_fields=["title"],
                      sort_whitelist=["updated_at", "title", "status"],
                      filter_spec={"status": TestCase.status,
                                   "section_id": TestCase.section_id})
            .with_soft_delete(TestCase)
            .search(request_q)
            .filter_by({"status": "approved"})
            .sort("updated_at", "desc")
            .paginate(page=2, per_page=20))
"""

from dataclasses import dataclass
from math import ceil
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import or_, asc, desc
from sqlalchemy.orm import Query


DEFAULT_PER_PAGE = 20
MAX_PER_PAGE = 50


class QueryBuilderError(ValueError):
    """Raised for any invalid client-supplied list parameter."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class PageResult:
    items: List[Any]
    total: int
    page: int
    per_page: int

    @property
    def total_pages(self) -> int:
        if self.per_page <= 0:
            return 0
        return max(1, ceil(self.total / self.per_page)) if self.total else 0


def _clamp_per_page(value) -> int:
    try:
        n = int(value) if value is not None else DEFAULT_PER_PAGE
    except (TypeError, ValueError):
        raise QueryBuilderError("INVALID_PER_PAGE", "per_page must be an integer")
    if n < 1:
        raise QueryBuilderError("INVALID_PER_PAGE", "per_page must be >= 1")
    return min(n, MAX_PER_PAGE)


def _clamp_page(value) -> int:
    try:
        n = int(value) if value is not None else 1
    except (TypeError, ValueError):
        raise QueryBuilderError("INVALID_PAGE", "page must be an integer")
    return max(1, n)


class ListQuery:
    """Chain search/filter/sort/paginate off a prebuilt SQLAlchemy query.

    Call order does not matter; the terminal `.paginate()` is what executes SQL.
    """

    def __init__(self, base_query: Query, model, *,
                 search_fields: Optional[Iterable] = None,
                 sort_whitelist: Optional[Iterable[str]] = None,
                 filter_spec: Optional[Dict[str, Any]] = None,
                 default_sort: str = "updated_at",
                 default_order: str = "desc"):
        self._q = base_query
        self._model = model
        self._search_fields = [self._resolve_col(f) for f in (search_fields or [])]
        self._sort_whitelist = set(sort_whitelist or [])
        self._filter_spec = filter_spec or {}
        self._default_sort = default_sort
        self._default_order = default_order
        self._sort_field: Optional[str] = None
        self._sort_order: Optional[str] = None

    # ---- chainable builders --------------------------------------------------

    def with_soft_delete(self, model=None, include_deleted: bool = False) -> "ListQuery":
        m = model or self._model
        if hasattr(m, "deleted_at") and not include_deleted:
            self._q = self._q.filter(m.deleted_at.is_(None))
        elif hasattr(m, "deleted_at") and include_deleted:
            self._q = self._q.filter(m.deleted_at.isnot(None))
        return self

    def search(self, term: Optional[str]) -> "ListQuery":
        term = (term or "").strip()
        if not term or not self._search_fields:
            return self
        # Strip SQL wildcards to avoid giving callers a way to force full scans;
        # pg_trgm still handles fuzzy match.
        safe = term.replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{safe}%"
        clauses = [col.ilike(pattern, escape="\\") for col in self._search_fields]
        self._q = self._q.filter(or_(*clauses))
        return self

    def filter_by(self, filters: Dict[str, Any]) -> "ListQuery":
        """Apply only filters whose keys are in `filter_spec`. Unknown keys ignored
        (never error) — but None/empty values are skipped so clients can safely
        pass through every query-string key."""
        for key, value in (filters or {}).items():
            if key not in self._filter_spec:
                continue
            if value is None or value == "":
                continue
            col = self._filter_spec[key]
            if isinstance(value, (list, tuple)):
                self._q = self._q.filter(col.in_(list(value)))
            else:
                self._q = self._q.filter(col == value)
        return self

    def sort(self, field: Optional[str], order: Optional[str]) -> "ListQuery":
        field = field or self._default_sort
        order = (order or self._default_order).lower()
        if order not in ("asc", "desc"):
            raise QueryBuilderError("INVALID_SORT_ORDER", "order must be 'asc' or 'desc'")
        if self._sort_whitelist and field not in self._sort_whitelist:
            raise QueryBuilderError(
                "INVALID_SORT_FIELD",
                f"sort field '{field}' not allowed. Allowed: {sorted(self._sort_whitelist)}",
            )
        self._sort_field = field
        self._sort_order = order
        return self

    # ---- terminal ------------------------------------------------------------

    def paginate(self, page: int = 1, per_page: int = DEFAULT_PER_PAGE) -> PageResult:
        page = _clamp_page(page)
        per_page = _clamp_per_page(per_page)

        # count before sort/limit — sort/limit don't affect total
        total = self._q.order_by(None).count()

        sort_field = self._sort_field or self._default_sort
        sort_order = self._sort_order or self._default_order
        col = self._resolve_col(sort_field)
        direction = desc if sort_order == "desc" else asc
        items = (self._q.order_by(direction(col))
                 .limit(per_page)
                 .offset((page - 1) * per_page)
                 .all())

        return PageResult(items=items, total=total, page=page, per_page=per_page)

    # ---- internals -----------------------------------------------------------

    def _resolve_col(self, name_or_col):
        """Accept either a column name on self._model or a Column object."""
        if isinstance(name_or_col, str):
            col = getattr(self._model, name_or_col, None)
            if col is None:
                raise QueryBuilderError(
                    "INVALID_COLUMN", f"Column '{name_or_col}' not on {self._model.__name__}",
                )
            return col
        return name_or_col


def build_page_result(items, total, page, per_page) -> PageResult:
    return PageResult(items=list(items), total=int(total),
                      page=_clamp_page(page), per_page=_clamp_per_page(per_page))
