"""Thin S4-local Tooling-read client (D-108.1, decision 1 = (a)).

Authenticated Tooling-API read + pagination + typed error mapping — **nothing
more**. Per the SPEC §3 boundary, this client must **never** accumulate entity
semantics, edge logic, metadata interpretation, or traversal policy; that would
recreate shadow metadata infrastructure inside S4. It runs a SOQL string against
`/tooling/query/` and walks the cursor — that is all it knows.

Typed errors reuse :mod:`primeqa.integrations.exceptions` (the neutral SF error
hierarchy — F1-sanctioned reuse): :class:`SFAuthError` (401),
:class:`SFRateLimitError` (429), :class:`SFRequestError` (other non-2xx). The
executor maps any of these to the `errored` run outcome.
"""
from __future__ import annotations

import requests

from primeqa.integrations.exceptions import (
    SFAuthError,
    SFRateLimitError,
    SFRequestError,
)

_TIMEOUT = 30.0


class ToolingReadClient:
    """Runs Tooling SOQL reads against one authenticated Salesforce org.

    Constructed from an already-resolved access token (see
    :func:`primeqa.execution_engine.credentials.resolve_tooling_client`); this
    object does no credential resolution itself — it is pure transport.
    """

    def __init__(
        self, instance_url: str, api_version: str, access_token: str,
    ) -> None:
        # Accept either "66.0" or "v66.0". Production passes the bare
        # `env.sf_api_version`, but normalize defensively so a v-prefixed value
        # can't silently build a "/services/data/vv66.0/" path (a 404).
        version = api_version[1:] if api_version.startswith("v") else api_version
        self._base = f"{instance_url.rstrip('/')}/services/data/v{version}"
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Bearer {access_token}"
        self._session.headers["Accept"] = "application/json"

    def query(self, soql: str) -> list[dict]:
        """Run a Tooling SOQL read, walking the pagination cursor to
        completion. Returns the aggregated records. Raises a typed
        :class:`~primeqa.integrations.exceptions.SFClientError` subclass on any
        non-2xx response."""
        data = self._get(f"{self._base}/tooling/query/", params={"q": soql})
        records = list(data.get("records", []))
        while data.get("nextRecordsUrl"):
            # nextRecordsUrl is server-root-relative ("/services/data/...").
            root = self._base.rsplit("/services/", 1)[0]
            data = self._get(f"{root}{data['nextRecordsUrl']}")
            records.extend(data.get("records", []))
        return records

    # --- internals ----------------------------------------------------

    def _get(self, url: str, *, params: dict | None = None) -> dict:
        try:
            resp = self._session.get(url, params=params, timeout=_TIMEOUT)
        except requests.RequestException as e:
            raise SFRequestError(f"Network error during Tooling read: {e}") from e
        self._raise_for_status(resp)
        return resp.json()

    @staticmethod
    def _raise_for_status(resp) -> None:
        code = resp.status_code
        if code == 401:
            raise SFAuthError(f"Tooling read unauthorized (401): {resp.text[:200]}")
        if code == 429:
            raise SFRateLimitError(
                f"Tooling read rate-limited (429): {resp.text[:200]}")
        if code >= 400:
            raise SFRequestError(
                f"Tooling read failed (HTTP {code})",
                status_code=code,
                response_body=resp.text[:500],
            )

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "ToolingReadClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
