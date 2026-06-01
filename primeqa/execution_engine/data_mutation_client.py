"""Thin S4-local data-mutation client (D-110.2, Fork D = build-thin).

Authenticated data-REST `create` + `delete` + `query` — **nothing more**. Per the SPEC §3
boundary (the `ToolingReadClient` precedent), this client must **never**
accumulate entity semantics, outcome judgment, or VR interpretation. It POSTs /
DELETEs and returns a normalized envelope; it is the **executor** that judges
the run outcome from that envelope (F1 — S4 owns the outcome).

Crucially, `create` does **not** raise on an HTTP error *response* — a VR
rejection (HTTP 400 with `FIELD_CUSTOM_VALIDATION_EXCEPTION`) IS the data the
behavioral negative captures. It raises a typed
:class:`~primeqa.integrations.exceptions.SFClientError` only on a **transport /
network** failure (no response at all — the create couldn't be attempted). The
executor maps an error *response* (401 / 429 / 5xx) and a transport raise to
`errored`, a business rejection (400) to the grounded eval, and a 2xx to
`failed` (the prohibition didn't enforce).

`query` (the positive vertical's read-back, D-115) is the mirror image: a non-2xx
read IS a failure — you couldn't observe the record — so it **raises** a typed
:class:`~primeqa.integrations.exceptions.SFClientError` like the
`ToolingReadClient` reader, and the executor maps that to `errored`.
"""
from __future__ import annotations

import requests

from primeqa.integrations.exceptions import (
    SFAuthError,
    SFRateLimitError,
    SFRequestError,
)

_TIMEOUT = 30.0


class DataMutationClient:
    """Runs data-REST create / delete against one authenticated org.

    Constructed from an already-resolved access token (see
    :func:`primeqa.execution_engine.credentials.resolve_data_mutation_client`);
    it does no credential resolution itself — pure transport.
    """

    def __init__(
        self, instance_url: str, api_version: str, access_token: str,
    ) -> None:
        # Normalize "66.0" / "v66.0" (the ToolingReadClient guard).
        version = api_version[1:] if api_version.startswith("v") else api_version
        self._base = f"{instance_url.rstrip('/')}/services/data/v{version}"
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Bearer {access_token}"
        self._session.headers["Content-Type"] = "application/json"
        self._session.headers["Accept"] = "application/json"

    def create(self, sobject: str, field_values: dict) -> dict:
        """POST `/sobjects/{sobject}/` with ``field_values``; return the
        normalized envelope. Does **not** raise on an HTTP error response (a
        rejection is captured data); raises :class:`SFRequestError` only on a
        transport/network failure."""
        url = f"{self._base}/sobjects/{sobject}/"
        try:
            resp = self._session.post(url, json=field_values, timeout=_TIMEOUT)
        except requests.RequestException as e:
            raise SFRequestError(f"Network error during create: {e}") from e
        return self._envelope(resp, "POST", url, field_values)

    def delete(self, sobject: str, record_id: str) -> dict:
        """DELETE `/sobjects/{sobject}/{record_id}`; return the envelope. Used
        only for the N-5 targeted best-effort cleanup. Raises only on
        transport/network failure."""
        url = f"{self._base}/sobjects/{sobject}/{record_id}"
        try:
            resp = self._session.delete(url, timeout=_TIMEOUT)
        except requests.RequestException as e:
            raise SFRequestError(f"Network error during delete: {e}") from e
        return self._envelope(resp, "DELETE", url, None)

    def query(self, soql: str) -> list[dict]:
        """Run a data-REST SOQL read, walking the pagination cursor to
        completion; return the aggregated records (the read-back for the positive
        create-and-verify, D-115). Unlike :meth:`create` (a rejection is captured
        data), a non-2xx read is a failure — you couldn't observe — so this raises
        a typed :class:`~primeqa.integrations.exceptions.SFClientError` subclass,
        which the executor maps to the ``errored`` outcome."""
        data = self._get(f"{self._base}/query/", params={"q": soql})
        records = list(data.get("records", []))
        while data.get("nextRecordsUrl"):
            # nextRecordsUrl is server-root-relative ("/services/data/...").
            root = self._base.rsplit("/services/", 1)[0]
            data = self._get(f"{root}{data['nextRecordsUrl']}")
            records.extend(data.get("records", []))
        return records

    # --- internals ----------------------------------------------------

    def _get(self, url: str, *, params: dict | None = None) -> dict:
        """GET + typed non-2xx mapping for the read-back (mirrors the
        `ToolingReadClient` reader). A read failure is never captured data — it
        raises, and the executor renders ``errored``."""
        try:
            resp = self._session.get(url, params=params, timeout=_TIMEOUT)
        except requests.RequestException as e:
            raise SFRequestError(f"Network error during data read: {e}") from e
        code = resp.status_code
        if code == 401:
            raise SFAuthError(f"data read unauthorized (401): {resp.text[:200]}")
        if code == 429:
            raise SFRateLimitError(
                f"data read rate-limited (429): {resp.text[:200]}")
        if code >= 400:
            raise SFRequestError(
                f"data read failed (HTTP {code})",
                status_code=code,
                response_body=resp.text[:500],
            )
        return resp.json()

    @staticmethod
    def _envelope(resp, method: str, url: str, body) -> dict:
        try:
            resp_body = resp.json() if resp.content else None
        except ValueError:
            resp_body = resp.text
        return {
            "api_request": {"method": method, "url": url, "body": body},
            "api_response": {"status_code": resp.status_code, "body": resp_body},
            "http_status": resp.status_code,
            "success": 200 <= resp.status_code < 300,
            "record_id": (resp_body.get("id")
                          if isinstance(resp_body, dict) else None),
        }

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "DataMutationClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
