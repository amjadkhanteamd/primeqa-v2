"""D-184 unit: MetadataService drift "synced since" anchor source-switch.

Behind the per-tenant ``cutover_read_s1`` flag, ``check_drift``'s anchor (the
timestamp + label the live-SF drift probes compare against) comes from the env's
last successful S1 sync (``read_s1_freshness``, env-scoped per D-183.1) instead of
the v1 ``meta_*`` current version, with a ``meta_*`` fallback during the parallel
window. The live-SF Tooling probes are unchanged — these tests cover only the
anchor resolution + the ``has_current_meta=False`` collapse.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import pytest

from primeqa.metadata.service import MetadataService

pytestmark = pytest.mark.unit

_NOW = datetime.now(timezone.utc)
_FLAG = "primeqa.metadata_bridge.accessor.cutover_read_s1_enabled"
_S1 = "primeqa.metadata_bridge.s1_sync_console.read_s1_freshness"


def _svc(*, current=None):
    repo = mock.MagicMock()
    repo.db = mock.MagicMock()
    repo.get_current_version.return_value = current
    return MetadataService(repo, mock.MagicMock()), repo


def _meta_row():
    return SimpleNamespace(id=5, version_label="v5", completed_at=_NOW)


def _s1(**over):
    base = {"available": True, "provisioned": True, "usable": True,
            "current_version_seq": 43, "last_success_at": _NOW.isoformat()}
    base.update(over)
    return base


class TestResolveDriftAnchor:
    def test_flag_off_uses_meta(self):
        svc, repo = _svc(current=_meta_row())
        with mock.patch(_FLAG, return_value=False), \
             mock.patch(_S1, side_effect=AssertionError("S1 read while flag off")):
            a = svc._resolve_drift_anchor(7, 1)
        assert a["source"] == "meta"
        assert a["version_id"] == 5 and a["version_label"] == "v5"
        assert a["synced_dt"] == _NOW

    def test_flag_on_s1_usable(self):
        svc, repo = _svc(current=_meta_row())
        with mock.patch(_FLAG, return_value=True), \
             mock.patch(_S1, return_value=_s1()):
            a = svc._resolve_drift_anchor(7, 1)
        assert a["source"] == "s1"
        assert a["version_id"] == 43 and a["version_label"] == "Org model v43"
        assert a["synced_dt"] == datetime.fromisoformat(_NOW.isoformat())
        repo.get_current_version.assert_not_called()   # S1 path never touches meta_*

    def test_flag_on_s1_not_usable_returns_none(self):
        svc, _ = _svc(current=_meta_row())
        with mock.patch(_FLAG, return_value=True), \
             mock.patch(_S1, return_value=_s1(usable=False, current_version_seq=None,
                                              last_success_at=None)):
            a = svc._resolve_drift_anchor(7, 1)
        assert a is None

    def test_flag_on_s1_not_provisioned_falls_back_to_meta(self):
        svc, repo = _svc(current=_meta_row())
        with mock.patch(_FLAG, return_value=True), \
             mock.patch(_S1, return_value={"available": True, "provisioned": False}):
            a = svc._resolve_drift_anchor(7, 1)
        assert a["source"] == "meta" and a["version_id"] == 5

    def test_flag_on_s1_unavailable_falls_back_to_meta(self):
        svc, _ = _svc(current=_meta_row())
        with mock.patch(_FLAG, return_value=True), \
             mock.patch(_S1, return_value={"available": False, "provisioned": False}):
            a = svc._resolve_drift_anchor(7, 1)
        assert a["source"] == "meta"

    def test_meta_fallback_no_current_returns_none(self):
        svc, _ = _svc(current=None)                    # never synced (meta)
        with mock.patch(_FLAG, return_value=False):
            a = svc._resolve_drift_anchor(7, 1)
        assert a is None

    def test_meta_current_without_completed_at_returns_none(self):
        svc, _ = _svc(current=SimpleNamespace(id=9, version_label="v9",
                                              completed_at=None))
        with mock.patch(_FLAG, return_value=False):
            a = svc._resolve_drift_anchor(7, 1)
        assert a is None


class TestCheckDriftAnchorNone:
    def test_anchor_none_yields_has_current_meta_false(self):
        """When the anchor resolves to None, check_drift returns the 'Never synced'
        shape without any Salesforce work."""
        svc, _ = _svc()
        # env lookup returns a truthy MagicMock; anchor is None.
        with mock.patch.object(svc, "_resolve_drift_anchor", return_value=None):
            res = svc.check_drift(7, 1, oauth_token_fetcher=lambda *a: "tok")
        assert res["has_current_meta"] is False
        assert res["drift_detected"] is False
        assert res["current_meta_version_id"] is None
