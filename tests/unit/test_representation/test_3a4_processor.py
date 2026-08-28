"""3A-4 pure merge-gate tests — the verdict decision function (exact
§c semantics incl. arm H), the DE-11 ownership classifier, and the
manifest collapse rule."""
from __future__ import annotations

import pytest

from primeqa.execution_engine.ui_manifest import (
    ManifestBuildError,
    _viewport_dict,
    surface_included,
)
from primeqa.interpretation.ui_conformance import (
    FAIL,
    NEEDS_HUMAN,
    NOT_DETERMINED,
    PASS,
    classify_ownership,
    decide_verdict,
)

pytestmark = pytest.mark.unit

_IDS = frozenset({"image-alt"})


def _obs(violations=None, incomplete=None, fingerprint=True,
         incomplete_count=None):
    o = {"status": "OK",
         "engine_observations": {"violations": violations or []}}
    if incomplete is not None:
        o["engine_observations"]["incomplete"] = incomplete
    if incomplete_count is not None:
        o["engine_observations"]["incomplete_count"] = incomplete_count
    if fingerprint:
        o["fingerprint"] = {"sha256": "f" * 64}
    return o


def _auto(**kw):
    base = dict(applicability="APPLICABLE", executable=True,
                capability="AUTO", rule_engine_ids=_IDS)
    base.update(kw)
    return base


def test_no_mapped_violation_without_attestation_is_not_a_pass():
    """The D-465 correction: absence of a violation is NOT evidence of
    conformance. The 3A-4-era observation (no retained pass ids) can
    attest nothing, so it re-decides to NOT_DETERMINED — never PASS."""
    v, basis = decide_verdict(**_auto(), observation=_obs(
        violations=[{"id": "other-rule", "nodes": [{"html": "<img>"}]}]))
    assert v == NOT_DETERMINED
    assert basis["reason"] == "legacy_unattested"


def test_pass_requires_attestation_that_the_rule_ran():
    obs = _obs(violations=[{"id": "other-rule", "nodes": [{"html": "<img>"}]}])
    obs["engine_observations"]["run_set"] = ["image-alt", "other-rule"]
    obs["engine_observations"]["passes_ids"] = ["image-alt"]
    v, basis = decide_verdict(**_auto(), observation=obs)
    assert v == PASS
    assert basis["attested_by"] == ["image-alt"]
    assert basis["engine_ids_checked"] == ["image-alt"]


def test_fail_on_mapped_violation_with_resolvable_node():
    v, basis = decide_verdict(**_auto(), observation=_obs(
        violations=[{"id": "image-alt",
                     "nodes": [{"html": "<img src=x>", "target": ["img"]}]}]))
    assert v == FAIL
    assert basis["engine_ids"] == ["image-alt"]


def test_arm_h_unresolvable_element_is_not_determined_never_fail():
    v, basis = decide_verdict(**_auto(), observation=_obs(
        violations=[{"id": "image-alt", "nodes": [{}, {"html": ""}]}]))
    assert v == NOT_DETERMINED
    assert basis["reason"] == "unresolvable_element"


def test_arm_h_unmapped_dependency_is_not_determined_never_fail():
    v, basis = decide_verdict(**_auto(rule_engine_ids=frozenset()),
                              observation=_obs(
        violations=[{"id": "image-alt", "nodes": [{"html": "<img>"}]}]))
    assert v == NOT_DETERMINED
    assert basis["reason"] == "unmapped_dependency"


def test_missing_fingerprint_is_not_determined():
    v, basis = decide_verdict(**_auto(), observation=_obs(fingerprint=False))
    assert v == NOT_DETERMINED
    assert basis["reason"] == "missing_fingerprint"


def test_needs_human_attaches_candidates_when_present():
    v, basis = decide_verdict(
        **_auto(capability="HUMAN_WITH_CANDIDATE"),
        observation=_obs(incomplete=[
            {"id": "image-alt", "nodes": [{"html": "<img>"}]},
            {"id": "other", "nodes": []}]))
    assert v == NEEDS_HUMAN
    assert [c["id"] for c in basis["candidates"]] == ["image-alt"]


def test_needs_human_honest_when_observation_lacks_the_list():
    v, basis = decide_verdict(
        **_auto(capability="HUMAN_WITH_CANDIDATE"),
        observation=_obs(incomplete_count=3))
    assert v == NEEDS_HUMAN
    assert basis == {"candidates_unavailable": True, "incomplete_count": 3}


def test_unjudged_members_get_reasons_not_verdicts():
    cases = [
        (dict(applicability="NOT_APPLICABLE", executable=False,
              capability="AUTO"), "not_applicable"),
        (dict(applicability="APPLICABLE", executable=False,
              capability="AUTO_WITH_ACTION"), "not_executable_mode_b"),
        (dict(applicability="HUMAN_REVIEW", executable=False,
              capability="HUMAN_ONLY"), "human_only_no_engine_input"),
    ]
    for kw, reason in cases:
        v, basis = decide_verdict(rule_engine_ids=_IDS,
                                  observation=_obs(), **kw)
        assert v is None
        assert basis["no_verdict_reason"] == reason


def test_ownership_markers():
    # 3A-5 ruling: CONFIRMED requires bundle resolution; an unresolved
    # c-* tag is PROBABLE unconditionally (the spike-grade
    # CONFIRMED-on-marker behavior was corrected as a signed-design
    # conformance fix).
    resolver = lambda name: "E1" if name == "loanWidget" else None
    assert classify_ownership(
        {"html": '<c-loan-widget class="x">', "target": []},
        resolver) == ("CONFIRMED", "E1")
    assert classify_ownership(
        {"html": '<c-loan-widget class="x">', "target": []}) == (
        "PROBABLE", None)
    assert classify_ownership(
        {"html": '<c-other-widget>'}, resolver) == ("PROBABLE", None)
    assert classify_ownership(
        {"html": '<lightning-input class="slds-input">'}) == (
        "PROBABLE", None)
    assert classify_ownership(
        {"html": '<div class="slds-grid">'}) == ("PROBABLE", None)
    assert classify_ownership(
        {"html": "<img src=x>", "target": ["img"]}) == ("UNKNOWN", None)


def test_collapse_rule():
    assert surface_included("APPLICABLE", True, "AUTO") is True
    assert surface_included("APPLICABLE", False, "AUTO_WITH_ACTION") is False
    assert surface_included("HUMAN_REVIEW", False,
                            "HUMAN_WITH_CANDIDATE") is True
    assert surface_included("HUMAN_REVIEW", False, "HUMAN_ONLY") is False
    assert surface_included("NOT_APPLICABLE", False, "AUTO") is False


def test_viewport_parse():
    assert _viewport_dict("320x256") == {"width": 320, "height": 256}
    assert _viewport_dict(None) is None
    with pytest.raises(ManifestBuildError):
        _viewport_dict("wide")
