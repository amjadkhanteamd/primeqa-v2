"""ui-s2.4 tests: manifest immutability + enqueue-from-manifest +
fingerprint hashing + the compare_jobs label matrix.

Pure tests run anywhere (no browser, no DB). DB tests are gated on
SPIKE_DATABASE_URL like the 2.3 queue tests. pytest.ini testpaths exclude
this directory from default runs regardless.
"""

import inspect
import os
import re
from pathlib import Path

import pytest

SPIKE_DB = os.environ.get("SPIKE_DATABASE_URL")

_BW_DIR = Path(__file__).parents[2] / "primeqa" / "browser_worker"


# ---------- manifest immutability (pure) ----------

def test_manifest_module_exposes_no_update_path():
    from primeqa.browser_worker import manifest

    public = {name for name, obj in vars(manifest).items()
              if not name.startswith("_") and inspect.isfunction(obj)
              and obj.__module__ == manifest.__name__}
    assert public == {"create_manifest", "get_manifest",
                      "enqueue_for_manifest"}


def test_no_sql_mutates_manifests_anywhere_in_package():
    pattern = re.compile(
        r"(UPDATE|DELETE\s+FROM)\s+s4_ui_run_manifests", re.IGNORECASE)
    offenders = [p.name for p in _BW_DIR.glob("*.py")
                 if pattern.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


# ---------- fingerprint hashing (pure) ----------

def _node(role, name="", tag="", children=()):
    return {"role": role, "name": name, "tag": tag,
            "children": list(children)}


def test_fingerprint_sibling_order_invariant_but_name_sensitive():
    from primeqa.browser_worker.spike import _fingerprint_from_tree

    a = {"children": [_node("link", "Home"), _node("button", "Save")]}
    b = {"children": [_node("button", "Save"), _node("link", "Home")]}
    c = {"children": [_node("link", "Home"), _node("button", "Delete")]}
    assert _fingerprint_from_tree(a)["sha256"] == \
        _fingerprint_from_tree(b)["sha256"]
    assert _fingerprint_from_tree(a)["sha256"] != \
        _fingerprint_from_tree(c)["sha256"]


def test_fingerprint_reparenting_changes_hash():
    from primeqa.browser_worker.spike import _fingerprint_from_tree

    nested = {"children": [_node("form", children=[_node("button", "Go")])]}
    flat = {"children": [_node("form"), _node("button", "Go")]}
    assert _fingerprint_from_tree(nested)["sha256"] != \
        _fingerprint_from_tree(flat)["sha256"]


# ---------- compare_jobs matrix (pure, canned rows) ----------

_PINS = {"axe_version": "4.13.0", "axe_sha256": "x", "playwright_version":
         "1.62.0", "worker_image_digest": None}
_MANIFEST = {"surfaces": [{"key": "s1", "url": "http://x/1"},
                          {"key": "s2", "url": "http://x/2"}],
             "pins": _PINS}


def _obs(fp_sha="f" * 64, violations=None, named=None, axe="4.13.0",
         browser="151.0.7922.34", status="OK"):
    violations = violations if violations is not None else []
    return {
        "status": status,
        "axe_version": axe,
        "browser_version": browser,
        "fingerprint": {"sha256": fp_sha,
                        "summary": {"element_count": 3,
                                    "roles": {"link": 1},
                                    "named": named or [["link", "Home"]]}},
        "engine_observations": {
            "violations_count": len(violations),
            "passes_count": 5,
            "incomplete_count": 0,
            "violations": violations,
        },
    }


def _jobs(ma="m-1", mb="m-1"):
    return ({"job_id": "j-a", "manifest_id": ma},
            {"job_id": "j-b", "manifest_id": mb})


def test_compare_not_same_manifest():
    from primeqa.browser_worker.compare import NOT_SAME_MANIFEST, compare_jobs

    ja, jb = _jobs("m-1", "m-2")
    out = compare_jobs(ja, jb, manifest_payload=_MANIFEST,
                       results_a={}, results_b={})
    assert out["comparable"] is False
    assert out["reason"] == NOT_SAME_MANIFEST


def test_compare_tool_drift_on_pin_mismatch():
    from primeqa.browser_worker.compare import TOOL_DRIFT, compare_jobs

    ja, jb = _jobs()
    out = compare_jobs(
        ja, jb, manifest_payload=_MANIFEST,
        results_a={"s1": _obs(axe="4.12.0"), "s2": _obs()},
        results_b={"s1": _obs(), "s2": _obs()})
    assert out["comparable"] is False
    assert out["reason"] == TOOL_DRIFT
    assert any("4.12.0" in d for d in out["detail"])


def test_compare_same_and_differs_and_not_comparable():
    from primeqa.browser_worker.compare import (
        DIFFERS, NOT_COMPARABLE, SAME, compare_jobs)

    ja, jb = _jobs()
    viol = [{"id": "image-alt", "nodes": [{}]}]
    out = compare_jobs(
        ja, jb, manifest_payload=_MANIFEST,
        results_a={"s1": _obs(violations=viol), "s2": _obs(fp_sha="a" * 64)},
        results_b={"s1": _obs(violations=viol),
                   "s2": _obs(fp_sha="b" * 64,
                              named=[["link", "Different"]])})
    assert out["comparable"] is True
    assert out["surfaces"]["s1"]["label"] == SAME
    assert out["surfaces"]["s2"]["label"] == NOT_COMPARABLE
    delta = out["surfaces"]["s2"]["detail"]
    assert delta["named_removed"] == [["link", "Home"]]
    assert delta["named_added"] == [["link", "Different"]]

    out2 = compare_jobs(
        ja, jb, manifest_payload=_MANIFEST,
        results_a={"s1": _obs(violations=viol), "s2": _obs()},
        results_b={"s1": _obs(violations=[]), "s2": _obs()})
    assert out2["surfaces"]["s1"]["label"] == DIFFERS
    assert out2["surfaces"]["s2"]["label"] == SAME


def test_compare_error_side_is_not_comparable():
    from primeqa.browser_worker.compare import NOT_COMPARABLE, compare_jobs

    ja, jb = _jobs()
    out = compare_jobs(
        ja, jb, manifest_payload=_MANIFEST,
        results_a={"s1": _obs(), "s2": {"status": "ERROR", "error": "x"}},
        results_b={"s1": _obs(), "s2": _obs()})
    assert out["surfaces"]["s2"]["label"] == NOT_COMPARABLE
    assert "a: status=ERROR" in out["surfaces"]["s2"]["detail"]


# ---------- DB-gated: enqueue builds payload FROM the manifest ----------

@pytest.mark.skipif(not SPIKE_DB, reason="set SPIKE_DATABASE_URL")
def test_enqueue_for_manifest_builds_payload_from_manifest():
    from sqlalchemy import text

    from primeqa.browser_worker import manifest as m
    from primeqa.browser_worker.queue import open_tenant_session

    s = open_tenant_session(1, SPIKE_DB)
    try:
        s.execute(text("DELETE FROM s4_ui_inspection_jobs"))
        s.commit()
        mid = m.create_manifest(s, {
            "surfaces": [{"key": "k1", "url": "http://127.0.0.1:1/x",
                          "viewport": {"width": 800, "height": 600},
                          "locale": "en-US"}],
            "pins": _PINS,
            "stabilisation": {"quiet_ms": 500, "max_wait_s": 30},
            "execution": {"mode": "manual-spike"},
        })
        job_id = m.enqueue_for_manifest(s, mid)
        row = s.execute(text("""
            SELECT manifest_id, payload FROM s4_ui_inspection_jobs
            WHERE id = :id
        """), {"id": job_id}).fetchone()
        assert str(row[0]) == mid
        assert row[1]["surfaces"][0]["viewport"] == {"width": 800,
                                                     "height": 600}
        assert row[1]["stabilisation"]["max_wait_s"] == 30
    finally:
        s.close()
