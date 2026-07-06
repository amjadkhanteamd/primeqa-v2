"""SEC-9: ConnectionService.test_connection must not return raw upstream error
bodies (or raw exception strings) in `detail` — that value is echoed into a
redirect URL that lands in browser history, the Referer header, and proxy logs.
The raw body is logged server-side; the returned detail is a generic message.

Pure unit test (stubbed transport + repo), no DB/network.
"""
from primeqa.core.service import ConnectionService


class _FakeConnRepo:
    def __init__(self, cfg):
        self._cfg = cfg

    def get_connection_decrypted(self, cid, tid):
        return {"connection_type": "salesforce", "config": self._cfg}

    def update_status(self, *a, **k):
        pass


def _sf_cfg():
    # a valid Salesforce host so the SEC-3 URL guard passes and we reach the POST
    return {"instance_url": "https://acme.my.salesforce.com", "client_id": "c",
            "client_secret": "s", "auth_flow": "client_credentials"}


def test_oauth_error_body_not_leaked_into_detail(monkeypatch):
    import requests
    marker = "SECRET_UPSTREAM_BODY_abc123"

    class FakeResp:
        status_code = 401
        text = marker + " error=invalid_client"

        def json(self):
            return {}

    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResp())
    result = ConnectionService(_FakeConnRepo(_sf_cfg())).test_connection(1, 1)
    assert result["status"] == "failed"
    assert marker not in str(result), f"SEC-9: raw OAuth body leaked into detail: {result}"
    assert "HTTP 401" in result["detail"], "expected a generic category message"


def test_exception_string_not_leaked_into_detail(monkeypatch):
    import requests
    marker = "INTERNAL_EXCEPTION_xyz789"

    def boom(*a, **k):
        raise RuntimeError(marker)

    monkeypatch.setattr(requests, "post", boom)
    result = ConnectionService(_FakeConnRepo(_sf_cfg())).test_connection(1, 1)
    assert result["status"] == "failed"
    assert marker not in str(result), f"SEC-9: exception internals leaked into detail: {result}"
