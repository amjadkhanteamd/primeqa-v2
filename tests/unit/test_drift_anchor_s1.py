"""D-195.3 unit: MetadataService drift "synced since" anchor is S1-only.

Step 5a.3 retired the ``cutover_read_s1`` flag; ``_resolve_drift_anchor`` now reads
the env's last successful S1 sync (``read_s1_freshness``, env-scoped per D-183)
with **no** ``meta_*`` fallback. Returns the anchor dict when S1 is
available+provisioned+usable, else ``None`` (caller renders
``has_current_meta=False``). The live-SF Tooling drift probes are unchanged —
these tests cover only the anchor resolution + the no-fallback guarantee.

Replaces the deleted ``test_check_drift_anchor.py`` (flag + meta_* fallback gone).
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import pytest

from primeqa.metadata.service import MetadataService

pytestmark = pytest.mark.unit

_NOW = datetime.now(timezone.utc)
_S1 = "primeqa.metadata_bridge.s1_sync_console.read_s1_freshness"


def _svc():
    """A MetadataService whose meta_* repo *would* return a current version — so
    the tests prove the S1-only path never consults it (no fallback)."""
    repo = mock.MagicMock()
    repo.db = mock.MagicMock()
    repo.get_current_version.return_value = SimpleNamespace(
        id=5, version_label="v5", completed_at=_NOW)
    return MetadataService(repo, mock.MagicMock()), repo


def _s1(**over):
    base = {"available": True, "provisioned": True, "usable": True,
            "current_version_seq": 43, "last_success_at": _NOW.isoformat()}
    base.update(over)
    return base


class TestResolveDriftAnchorS1Only:
    def test_usable_s1_returns_s1_anchor(self):
        svc, repo = _svc()
        with mock.patch(_S1, return_value=_s1()):
            a = svc._resolve_drift_anchor(7, 1)
        assert a == {
            "version_id": 43,
            "version_label": "Org model v43",
            "synced_dt": datetime.fromisoformat(_NOW.isoformat()),
            "source": "s1",
        }
        repo.get_current_version.assert_not_called()   # no meta_* fallback

    def test_unusable_s1_returns_none(self):
        svc, repo = _svc()
        with mock.patch(_S1, return_value=_s1(usable=False)):
            assert svc._resolve_drift_anchor(7, 1) is None
        repo.get_current_version.assert_not_called()

    def test_unprovisioned_s1_returns_none(self):
        svc, repo = _svc()
        with mock.patch(_S1, return_value=_s1(provisioned=False)):
            assert svc._resolve_drift_anchor(7, 1) is None
        repo.get_current_version.assert_not_called()

    def test_unavailable_s1_returns_none(self):
        svc, repo = _svc()
        with mock.patch(_S1, return_value=_s1(available=False)):
            assert svc._resolve_drift_anchor(7, 1) is None
        repo.get_current_version.assert_not_called()
