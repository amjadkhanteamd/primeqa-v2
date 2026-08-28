"""D-465 fix slice §e.10 — the run-set pin is part of determinism.

Env-gated like the other browser tests (SPIKE_BROWSER=1 + a local
fixture server). Two scans of the same fixture under the same pinned
run set must produce identical run_set and identical attestation id
sets; and a rule the engine ships DISABLED must actually run when the
run set names it.
"""
from __future__ import annotations

import functools
import http.server
import os
import socketserver
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("SPIKE_BROWSER") != "1",
    reason="needs chromium (set SPIKE_BROWSER=1)")

_FIX = Path(__file__).parent / "fixtures"
_PORT = 8646
# deliberately includes target-size + audio-caption, which axe 4.13.0
# ships enabled:false — without the run-set pin they never execute.
_RUN_SET = ["image-alt", "label", "region", "target-size", "audio-caption"]


@pytest.fixture(scope="module")
def server():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(_FIX))
    httpd = socketserver.TCPServer(("127.0.0.1", _PORT), handler,
                                   bind_and_activate=False)
    httpd.allow_reuse_address = True
    httpd.server_bind()
    httpd.server_activate()
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{_PORT}"
    httpd.shutdown()


def test_run_set_and_attestation_are_deterministic(server):
    from primeqa.browser_worker.spike import scan_page

    url = f"{server}/fixture-bad.html"
    a = scan_page(url, run_set=_RUN_SET, max_wait_s=25)
    b = scan_page(url, run_set=_RUN_SET, max_wait_s=25)
    ea, eb = a["engine_observations"], b["engine_observations"]

    assert a["status"] == b["status"] == "OK"
    assert ea["run_set"] == eb["run_set"] == sorted(_RUN_SET)
    assert ea["passes_ids"] == eb["passes_ids"]
    assert ea["inapplicable_ids"] == eb["inapplicable_ids"]
    assert ({v["id"] for v in ea["violations"]}
            == {v["id"] for v in eb["violations"]})


def test_engine_disabled_rule_runs_when_the_run_set_names_it(server):
    from primeqa.browser_worker.spike import scan_page

    a = scan_page(f"{server}/fixture-bad.html", run_set=_RUN_SET,
                  max_wait_s=25)
    eo = a["engine_observations"]
    reported = (set(eo["passes_ids"]) | set(eo["inapplicable_ids"])
                | {v["id"] for v in eo["violations"]}
                | {i["id"] for i in eo.get("incomplete", [])})
    # target-size is enabled:false in the vendored axe 4.13.0
    assert "target-size" in reported


def test_without_a_run_set_the_disabled_rule_is_absent(server):
    """The pre-fix behaviour, pinned as the regression it was: with no
    run set the engine silently omits its disabled rules."""
    from primeqa.browser_worker.spike import scan_page

    a = scan_page(f"{server}/fixture-bad.html", max_wait_s=25)
    eo = a["engine_observations"]
    reported = (set(eo["passes_ids"]) | set(eo["inapplicable_ids"])
                | {v["id"] for v in eo["violations"]}
                | {i["id"] for i in eo.get("incomplete", [])})
    assert "target-size" not in reported
    assert eo["run_set"] is None
