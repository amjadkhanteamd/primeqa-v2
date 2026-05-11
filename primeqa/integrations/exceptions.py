"""Exceptions raised by primeqa.integrations clients."""
from __future__ import annotations


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
