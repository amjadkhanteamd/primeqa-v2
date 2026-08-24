"""Arm I (ui-s2.6): tenant-boundary guard + per-surface refusal tests.

Pure — the guard fires BEFORE any S3/DB call, so refusal tests need no
bucket and no database (s3=None proves the denial happens first). The
audit event TENANT_BOUNDARY_REFUSED is asserted via caplog.
"""

import logging

import pytest

from primeqa.browser_worker import evidence as ev


class FakeSession:
    def __init__(self, schema="tenant_1"):
        self.info = {"tenant_schema": schema, "tenant_id": 1}


T1 = FakeSession("tenant_1")


# ---------- guard matrix ----------

def test_guard_passes_own_tenant_key_and_prefix():
    ev.assert_tenant_scoped(T1, "tenant_1/m/j/s/1/screenshot.png", "t")
    ev.assert_tenant_scoped(T1, "tenant_1/", "t")
    ev.assert_tenant_scoped(T1, "/tenant_1/x", "t")   # leading slash tolerated


@pytest.mark.parametrize("bad", [
    "tenant_2/m/j/s/1/screenshot.png",   # foreign tenant
    "tenant_10/x",                        # prefix-collision cousin
    "",                                   # empty
    "/",                                  # no head
    "tenant_1/../tenant_2/x",             # traversal
    "spike-test/x",                       # no tenant head at all
])
def test_guard_refuses_and_audits(bad, caplog):
    caplog.set_level(logging.WARNING, logger="primeqa.browser_worker.evidence")
    with pytest.raises(ev.TenantBoundaryError):
        ev.assert_tenant_scoped(T1, bad, "op-x")
    assert "TENANT_BOUNDARY_REFUSED" in caplog.text
    assert "op=op-x" in caplog.text and "tenant=tenant_1" in caplog.text


# ---------- per-surface refusal (guard fires before any S3 use) ----------

def test_sign_url_refuses_foreign_key():
    with pytest.raises(ev.TenantBoundaryError):
        ev.sign_url(T1, None, "bucket", "tenant_2/m/j/s/1/screenshot.png")


def test_sweep_orphans_refuses_foreign_prefix_the_live_hole(caplog):
    # THE live hole (LLD table row 7): a tenant_1 session sweeping tenant_2/.
    caplog.set_level(logging.WARNING, logger="primeqa.browser_worker.evidence")
    with pytest.raises(ev.TenantBoundaryError):
        ev.sweep_orphans(T1, None, "bucket", "tenant_2/")
    assert "TENANT_BOUNDARY_REFUSED" in caplog.text
    assert "op=sweep_orphans" in caplog.text
    assert "offending=tenant_2" in caplog.text


def test_put_evidence_refuses_foreign_key_in_dict():
    keys = {"screenshot": "tenant_1/m/j/s/1/screenshot.png",
            "observation": "tenant_2/m/j/s/1/observation.json"}
    with pytest.raises(ev.TenantBoundaryError):
        ev.put_evidence(T1, None, "bucket", keys, b"png", {"status": "OK"})


def test_build_keys_derives_prefix_from_session_only():
    keys = ev.build_keys(FakeSession("tenant_7"), "m1", "j1", "home", 2)
    assert keys == {"screenshot": "tenant_7/m1/j1/home/2/screenshot.png",
                    "observation": "tenant_7/m1/j1/home/2/observation.json"}


def test_list_keys_is_module_private():
    assert not hasattr(ev, "list_keys")
    assert hasattr(ev, "_list_keys")


def test_sweep_orphans_defaults_to_session_prefix():
    # prefix=None derives tenant_1/ — proven by the S3 stub receiving it.
    seen = {}

    class StubS3:
        def get_paginator(self, name):
            class P:
                def paginate(_self, Bucket, Prefix):
                    seen["prefix"] = Prefix
                    return []
            return P()

    class SessionWithDB(FakeSession):
        def execute(self, *a, **k):
            class R:
                def fetchall(self):
                    return []
            return R()

    out = ev.sweep_orphans(SessionWithDB(), StubS3(), "bucket")
    assert out == [] and seen["prefix"] == "tenant_1"
