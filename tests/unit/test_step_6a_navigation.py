"""Step 6a — the navigation collapse and its gates (LLD_STEP_6A §a, §e).

Pure: the registry and the capability map, no DB.
"""
from __future__ import annotations

import pytest

from primeqa.core.authz import Tier
from primeqa.core.navigation import SIDEBAR_ITEMS, build_sidebar
from primeqa.core.permissions import _CAPABILITY_MIN_TIER, _role_capabilities

pytestmark = pytest.mark.unit

RENDERED = [i for i in SIDEBAR_ITEMS if i.get("enabled", True)]


def _labels(role):
    caps = _role_capabilities(role)
    return [i["label"] for i in build_sidebar(caps, "/requirements",
                                              is_superadmin=(role == "superadmin"))]


def test_the_registry_renders_exactly_four_items():
    assert [i["id"] for i in RENDERED] == ["requirements", "results", "releases", "settings"]
    assert [i["label"] for i in RENDERED] == ["Requirements", "Results", "Releases", "Settings"]


def test_the_removed_slots_are_gone():
    ids = {i["id"] for i in SIDEBAR_ITEMS}
    for gone in ("run_tests", "my_reviews", "test_library", "dashboard", "my_tickets"):
        assert gone not in ids, f"{gone} still in the registry"


def test_the_disabled_items_stay_but_never_render():
    disabled = {i["id"] for i in SIDEBAR_ITEMS if not i.get("enabled", True)}
    assert disabled == {"coverage", "audit_log"}
    assert not ({i["id"] for i in RENDERED} & disabled)


def test_a_viewer_now_sees_the_work_surfaces():
    """The gate bug 6a fixes: before, Requirements was gated on a MEMBER
    capability, so a viewer saw no Requirements item at all."""
    assert _labels("viewer") == ["Requirements", "Results", "Releases"]
    assert "Settings" not in _labels("viewer")


def test_every_role_sees_what_its_tier_admits():
    assert _labels("admin") == ["Requirements", "Results", "Releases", "Settings"]
    assert _labels("superadmin") == ["Requirements", "Results", "Releases", "Settings"]
    # A MEMBER sees Settings too, because that slot is gated on ANY `manage_`
    # capability and MEMBER already held manage_test_suites / manage_knowledge.
    # PRE-EXISTING, and not something 6a changed — asserted below.
    assert _labels("ba") == ["Requirements", "Results", "Releases", "Settings"]
    assert _labels("tester") == ["Requirements", "Results", "Releases", "Settings"]


def test_the_new_capabilities_did_not_widen_who_sees_settings():
    """6a adds manage_requirements / manage_results / manage_releases at MEMBER,
    which match the Settings slot's `manage_` prefix. That must not be what
    opens Settings to a member: the pre-6a manage_* capabilities already did."""
    member = _role_capabilities("tester")
    new = {"manage_requirements", "manage_results", "manage_releases"}
    pre_existing = {c for c in member if c.startswith("manage_")} - new
    assert pre_existing, "a member holds no pre-6a manage_* capability"
    assert {"manage_test_suites", "manage_knowledge"} <= pre_existing
    # without the new three, Settings still renders for a member
    labels = [i["label"] for i in build_sidebar(member - new, "/requirements")]
    assert "Settings" in labels
    # and a VIEWER still never sees it
    assert "Settings" not in [i["label"] for i in build_sidebar(_role_capabilities("viewer"), "/")]


def test_the_new_capabilities_sit_at_the_stated_tiers():
    for cap in ("view_requirements", "view_results", "view_releases"):
        assert _CAPABILITY_MIN_TIER[cap] == Tier.VIEWER
    for cap in ("manage_requirements", "manage_results", "manage_releases"):
        assert _CAPABILITY_MIN_TIER[cap] == Tier.MEMBER
    caps = _role_capabilities("viewer")
    assert {"view_requirements", "view_results", "view_releases"} <= caps
    assert not ({"manage_requirements", "manage_results", "manage_releases"} & caps)


@pytest.mark.parametrize("path,expected", [
    ("/requirements", "Requirements"), ("/requirements/12", "Requirements"),
    ("/claims/9f1c2a3b-0000-0000-0000-000000000000", "Requirements"),
    ("/runs/substrate", "Results"), ("/runs/conformance/abc", "Results"),
    ("/results", "Results"),
    ("/releases", "Releases"), ("/releases/16", "Releases"), ("/releases/compare", "Releases"),
    ("/settings", "Settings"), ("/settings/quality-policy", "Settings"),
])
def test_the_active_item_follows_the_path_including_the_redirected_ones(path, expected):
    items = build_sidebar(_role_capabilities("admin"), path)
    active = [i["label"] for i in items if i["active"]]
    assert active == [expected], f"{path} lit {active}"
