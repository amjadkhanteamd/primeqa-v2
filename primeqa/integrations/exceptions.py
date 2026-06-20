"""Exceptions raised by primeqa.integrations clients."""
from __future__ import annotations

import json


class SFClientError(Exception):
    """Base for all Salesforce client errors."""


class SFAuthError(SFClientError):
    """Authentication or token refresh failed."""


class SFRateLimitError(SFClientError):
    """Salesforce returned 429 or a quota-exceeded response."""


class SFRequestError(SFClientError):
    """Salesforce returned a 4xx or 5xx not classified above."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body

    def _parsed(self) -> dict | None:
        """First Salesforce error object parsed from ``response_body``.

        SF REST errors come back as a JSON list of ``{message, errorCode,
        fields}`` objects (occasionally a single object). Fail-safe: the body
        is captured truncated (``resp.text[:500]``), so a cut-off / malformed /
        empty body returns ``None`` rather than raising — callers then fall back
        to ``status_code`` classification.
        """
        if not self.response_body:
            return None
        try:
            data = json.loads(self.response_body)
        except (ValueError, TypeError):
            return None
        if isinstance(data, list):
            data = data[0] if data and isinstance(data[0], dict) else None
        return data if isinstance(data, dict) else None

    @property
    def error_code(self) -> str | None:
        """The SF ``errorCode`` (e.g. ``INSUFFICIENT_ACCESS_OR_READONLY``,
        ``INVALID_FIELD``) or ``None`` when absent / unparseable."""
        obj = self._parsed()
        code = obj.get("errorCode") if obj else None
        return code if isinstance(code, str) else None

    @property
    def fields(self) -> list[str]:
        """The SF ``fields`` implicated in the error, or ``[]`` when absent /
        unparseable."""
        obj = self._parsed()
        raw = obj.get("fields") if obj else None
        return [f for f in raw if isinstance(f, str)] if isinstance(raw, list) else []


class SFIncompletePaginationError(SFClientError):
    """A SOQL pagination walk ended on a malformed cursor (``done=False`` with no
    ``nextRecordsUrl``) while the caller required a COMPLETE result.

    Raised only by ``_query_all(require_complete=True)`` (D-253). The default
    ``require_complete=False`` keeps the historical behavior — silently return the
    rows-so-far. Callers that feed a present-set into a deletion reconcile pass
    ``require_complete=True`` so a truncated id-list becomes a hard error they
    fail-closed on (skip the reconcile) rather than a silent subset that would
    mass-close live entities."""
