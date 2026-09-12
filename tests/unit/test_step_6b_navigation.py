"""Step 6b — the nav is FOUR items, testably (LLD_STEP_6B §c ruling 4), and the
Settings sidebar groups as the mock draws (§b). Pure: registry + markup."""
from __future__ import annotations

import pathlib
import re

import pytest

from primeqa.core.navigation import SIDEBAR_ITEMS

pytestmark = pytest.mark.unit

BASE = pathlib.Path(__file__).resolve().parents[2] / "primeqa" / "templates" / "base.html"
SETTINGS = pathlib.Path(__file__).resolve().parents[2] / "primeqa" / "templates" / "settings" / "base.html"


def test_the_gear_carries_the_nav_hook_so_four_items_is_testable():
    """Ruling 4: the gear IS the fourth item. Before 6b a test counting nav
    items found three and had to know about the exception."""
    html = BASE.read_text()
    strip = html[html.index("plimsol-nav"):html.index("</nav>")]
    hooks = re.findall(r'data-nav-item="([^"]+)"', strip)
    assert "Settings" in hooks, "the gear has no nav hook — 'four items' stays an assertion"
    assert hooks.count("Settings") == 1
    # the registry still holds four rendered items
    assert [i["id"] for i in SIDEBAR_ITEMS if i.get("enabled", True)] == [
        "requirements", "results", "releases", "settings"]


def test_the_gear_stays_gated_on_can_see_settings():
    html = BASE.read_text()
    i = html.index('data-nav-item="Settings"')
    assert "can_see_settings" in html[max(0, i - 600):i], "the gear lost its gate"


def test_the_settings_sidebar_groups_as_the_mock_draws():
    html = SETTINGS.read_text()
    groups = re.findall(r'data-settings-group="([^"]+)"', html)
    assert groups[:4] == ["General", "Release quality", "Conformance setup", "Org reference"]


@pytest.mark.parametrize("label,group_after", [
    ("Policy", "Release quality"), ("Waivers", "Release quality"),
    ("Sites &amp; personas", "Conformance setup"), ("Surface inventory", "Conformance setup"),
    ("Claim sets", "Conformance setup"), ("Standards &amp; catalogues", "Conformance setup"),
    ("Custom rules", "Conformance setup"), ("Schedules", "Conformance setup"),
    ("Org Model", "Org reference"), ("Knowledge", "Org reference"),
    ("Substrate Insights", "Org reference"), ("Ask", "Org reference"),
    ("General", "General"), ("Users", "General"),
])
def test_every_item_sits_in_its_drawn_group(label, group_after):
    html = SETTINGS.read_text()
    item_at = html.index(f'data-settings-item="{label}"')
    groups = [(m.start(), m.group(1)) for m in re.finditer(r'data-settings-group="([^"]+)"', html)]
    owning = [name for pos, name in groups if pos < item_at][-1]
    assert owning == group_after, f"{label} sits under {owning}, not {group_after}"


def test_schedules_is_a_LINK_to_results_not_a_copy():
    """Ruling 2: the D-214 panel is linked, never duplicated."""
    html = SETTINGS.read_text()
    i = html.index('data-settings-item="Schedules"')
    anchor = html[html.rindex("<a ", 0, i):html.index("</a>", i)]
    assert 'href="/runs/substrate"' in anchor
    assert "on Results" in anchor
