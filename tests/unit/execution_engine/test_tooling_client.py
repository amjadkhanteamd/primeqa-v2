"""Unit tests for the thin S4-local Tooling client (D-108.1) — no network.

The client is pure transport: authenticated read + pagination + typed error
mapping. These tests monkeypatch the session's GET so no real HTTP happens, and
confirm (i) cursor pagination aggregates, (ii) non-2xx maps to the neutral
`integrations.exceptions` types.
"""
from __future__ import annotations

import pytest

from primeqa.execution_engine.tooling_client import ToolingReadClient
from primeqa.integrations.exceptions import (
    SFAuthError,
    SFRateLimitError,
    SFRequestError,
)


class _Resp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def _client():
    return ToolingReadClient("https://acme.my.salesforce.com", "60.0", "tok-abc")


def _seq_get(responses):
    """A fake session.get that returns the queued responses in order."""
    it = iter(responses)

    def _get(url, params=None, timeout=None):
        return next(it)

    return _get


def test_api_version_normalized_accepts_both_forms():
    # Bare ("66.0") and v-prefixed ("v66.0") build the identical base — the
    # client can't produce a "/vv66.0/" 404 regardless of what the caller passes.
    bare = ToolingReadClient("https://acme.my.salesforce.com", "66.0", "t")
    prefixed = ToolingReadClient("https://acme.my.salesforce.com", "v66.0", "t")
    assert bare._base == prefixed._base
    assert bare._base.endswith("/services/data/v66.0")


def test_query_walks_pagination_cursor():
    c = _client()
    c._session.get = _seq_get([
        _Resp(200, {"records": [{"Id": "a"}],
                    "nextRecordsUrl": "/services/data/v60.0/tooling/query/01gX"}),
        _Resp(200, {"records": [{"Id": "b"}]}),  # no cursor -> done
    ])
    rows = c.query("SELECT Id FROM ValidationRule")
    assert rows == [{"Id": "a"}, {"Id": "b"}]


def test_query_single_page():
    c = _client()
    c._session.get = _seq_get([_Resp(200, {"records": [{"Id": "x"}]})])
    assert c.query("SELECT Id FROM ValidationRule") == [{"Id": "x"}]


def test_401_maps_to_auth_error():
    c = _client()
    c._session.get = lambda url, params=None, timeout=None: _Resp(401, text="bad token")
    with pytest.raises(SFAuthError):
        c.query("SELECT Id FROM ValidationRule")


def test_429_maps_to_rate_limit_error():
    c = _client()
    c._session.get = lambda url, params=None, timeout=None: _Resp(429, text="slow down")
    with pytest.raises(SFRateLimitError):
        c.query("SELECT Id FROM ValidationRule")


def test_500_maps_to_request_error_with_status():
    c = _client()
    c._session.get = lambda url, params=None, timeout=None: _Resp(
        500, text="server error")
    with pytest.raises(SFRequestError) as exc:
        c.query("SELECT Id FROM ValidationRule")
    assert exc.value.status_code == 500
