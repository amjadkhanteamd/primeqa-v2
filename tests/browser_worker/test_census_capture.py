"""Census capture smoke (Phase 5 §h) — a REAL browser over a local
data: page, no network, no engine dependence on remote state.

Opt-in with SPIKE_BROWSER=1 like the other spike tests."""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("SPIKE_BROWSER") != "1",
    reason="browser spike disabled (set SPIKE_BROWSER=1 to run)",
)

_PAGE = ("data:text/html,"
         "<html lang='en'><head><title>census</title></head><body>"
         "<main><h1>Title</h1>"
         "<button type='submit' aria-label='Save' "
         "style='width:48px;height:32px;background-color:rgb(0,82,204)'>"
         "S</button>"
         "<c-loan-card role='region' aria-label='Loan'>x</c-loan-card>"
         "</main></body></html>")


def test_census_is_captured_under_the_pinned_config():
    from primeqa.browser_worker.spike import scan_page
    from primeqa.knowledge.census_schema import census_pins

    result = scan_page(_PAGE, census=census_pins())
    assert result["status"] == "OK"
    census = result["census"]
    assert census["schema_version"] == 1
    assert census["traversal_mode"] in ("light_only", "native_open",
                                        "synthetic_aura")
    assert census["cap_hit"] is False and census["capture_errors"] == 0
    assert census["n"] == len(census["nodes"]) >= 3
    assert "census" in result["timings_ms"]

    by_role = {}
    for n in census["nodes"]:
        by_role.setdefault(n["role"] or n["tag"], n)
    heading = by_role["heading"]
    assert heading["heading"] == 1 and heading["name"] == "Title"
    button = by_role["button"]
    assert button["attrs"].get("type") == "submit"
    assert button["attrs"].get("aria-label") == "Save"
    assert set(button["style"]) == set(census_pins()["property_allowlist"])
    assert button["style"]["background-color"].startswith("rgb")
    x, y, w, h = button["box"]
    assert w >= 40 and h >= 20                    # numeric, plausible
    assert "main" in button["anc"]
    card = next(n for n in census["nodes"] if n["tag"] == "c-loan-card")
    assert card["role"] == "region"                # explicit role wins
    assert card["attrs"].get("role") == "region"   # ...and stays witnessed

    # no census config -> no capture, no census phase (the pin decides)
    bare = scan_page(_PAGE)
    assert bare["census"] is None
    assert "census" not in bare["timings_ms"]


def test_the_node_cap_is_recorded_when_hit():
    from primeqa.browser_worker.spike import scan_page
    from primeqa.knowledge.census_schema import census_pins

    many = "".join(f"<button>b{i}</button>" for i in range(30))
    page = f"data:text/html,<main>{many}</main>"
    cfg = dict(census_pins(), node_cap=10)
    result = scan_page(page, census=cfg)
    census = result["census"]
    assert census["cap_hit"] is True
    assert census["n"] == 10                       # bounded, recorded
