"""Smoke test for the browser-worker spike.

Opt-in only: needs a machine with playwright + chromium installed and
network egress. Enable with SPIKE_BROWSER=1. Default test runs skip it
entirely (no pytest.ini mark registration — env-flag skip instead, so no
existing file changes).
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("SPIKE_BROWSER") != "1",
    reason="browser spike disabled (set SPIKE_BROWSER=1 to run)",
)


def test_scan_example_com_smoke():
    from primeqa.browser_worker.spike import scan_page

    result = scan_page("https://example.com")

    assert result["status"] == "OK"
    assert result["timings_ms"], "phase timings missing"
    for phase, ms in result["timings_ms"].items():
        assert ms > 0, f"phase {phase} has non-positive timing: {ms}"
    obs = result["engine_observations"]
    assert obs is not None
    assert obs["passes_count"] + obs["violations_count"] + obs["incomplete_count"] > 0
