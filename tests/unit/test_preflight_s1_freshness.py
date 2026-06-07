"""D-192 (GAP-2) unit: Preflight reads freshness/health from S1, unconditionally.

Preflight reads the org-model freshness + (all-or-nothing) health from the S1
substrate (``read_s1_freshness``); the v1 ``meta_*`` fallback was removed at GAP-2
(the ``meta_*`` drop cannot proceed while preflight still reads ``meta_*``). These
tests drive ``Preflight.check`` with mocked repos, patching the S1 reader, and
assert the freshness blockers/warnings + the ``metadata_source`` summary marker
(now always ``'s1'``). S1 unprovisioned/unavailable -> ``NO_METADATA`` (degraded),
never a ``meta_*`` read.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import pytest

from primeqa.runs.preflight import Preflight

pytestmark = pytest.mark.unit

_NOW = datetime.now(timezone.utc)
_S1_PATCH = "primeqa.metadata_bridge.s1_sync_console.read_s1_freshness"


def _resolved():
    return SimpleNamespace(test_count=1, test_case_ids=[],
                           resolution_warnings=[], missing_jira_keys=[])


def _mk():
    """A Preflight whose env passes the connection/credential/LLM sections, so the
    only freshness signal under test comes from the S1 freshness branch. No
    ``meta_repo`` — preflight reads freshness/health from S1 (GAP-2)."""
    env = SimpleNamespace(
        id=1, name="Dev", env_type="sandbox", sf_instance_url="https://x",
        is_active=True, connection_id=10, llm_connection_id=20,
        current_meta_version_id=None)
    env_repo = mock.MagicMock()
    env_repo.get_environment.return_value = env
    conn_repo = mock.MagicMock()
    conn_repo.get_connection_decrypted.return_value = {"status": "ok"}
    pf = Preflight(mock.MagicMock(), env_repo=env_repo, conn_repo=conn_repo,
                   tc_repo=mock.MagicMock())
    pf._eta_range = lambda *a, **k: {"min": 0, "max": 0}
    return pf


def _codes(items):
    return {i["code"] for i in items}


def _s1(**over):
    base = {"available": True, "provisioned": True, "usable": True,
            "current_version_seq": 43, "age_hours": 2.0,
            "last_success_at": _NOW.isoformat()}
    base.update(over)
    return base


class TestS1FreshnessBranch:
    def test_s1_fresh_no_blocker(self):
        pf = _mk()
        with mock.patch(_S1_PATCH, return_value=_s1()):
            rpt = pf.check(1, {"role": "tester"}, 5, _resolved())
        assert rpt.summary["metadata_source"] == "s1"
        assert "METADATA_VERY_STALE" not in _codes(rpt.blockers)
        assert "METADATA_STALE" not in _codes(rpt.warnings)
        assert "NO_METADATA" not in _codes(rpt.blockers)
        assert rpt.summary["meta_version"]["id"] == 43
        assert rpt.summary["meta_version"]["version_label"] == "Org model v43"

    def test_s1_very_stale_blocks(self):
        pf = _mk()
        with mock.patch(_S1_PATCH, return_value=_s1(age_hours=800.0)):
            rpt = pf.check(1, {"role": "tester"}, 5, _resolved())
        assert rpt.summary["metadata_source"] == "s1"
        assert "METADATA_VERY_STALE" in _codes(rpt.blockers)

    def test_s1_stale_warns(self):
        pf = _mk()
        with mock.patch(_S1_PATCH, return_value=_s1(age_hours=200.0)):
            rpt = pf.check(1, {"role": "tester"}, 5, _resolved())
        assert "METADATA_STALE" in _codes(rpt.warnings)
        assert "METADATA_VERY_STALE" not in _codes(rpt.blockers)

    def test_s1_not_usable_blocks(self):
        pf = _mk()
        with mock.patch(_S1_PATCH, return_value=_s1(
                usable=False, current_version_seq=None, age_hours=None,
                last_success_at=None)):
            rpt = pf.check(1, {"role": "tester"}, 5, _resolved())
        assert rpt.summary["metadata_source"] == "s1"
        assert "NO_METADATA" in _codes(rpt.blockers)

    def test_s1_usable_age_none_not_stale(self):
        """First sync still running: usable (a version exists) but no measured
        age -> not stale, not blocked."""
        pf = _mk()
        with mock.patch(_S1_PATCH, return_value=_s1(
                current_version_seq=1, age_hours=None, last_success_at=None)):
            rpt = pf.check(1, {"role": "tester"}, 5, _resolved())
        assert "METADATA_STALE" not in _codes(rpt.warnings)
        assert "METADATA_VERY_STALE" not in _codes(rpt.blockers)
        assert "NO_METADATA" not in _codes(rpt.blockers)


class TestGap2NoMetaFallback:
    """GAP-2 (D-192): there is NO ``meta_*`` fallback. S1 unprovisioned / unavailable
    -> ``NO_METADATA`` blocker (degraded), never a ``meta_*`` read."""

    def test_s1_not_provisioned_blocks(self):
        pf = _mk()
        with mock.patch(_S1_PATCH, return_value=_s1(
                provisioned=False, usable=False, current_version_seq=None)):
            rpt = pf.check(1, {"role": "tester"}, 5, _resolved())
        assert rpt.summary["metadata_source"] == "s1"
        assert "NO_METADATA" in _codes(rpt.blockers)

    def test_s1_unavailable_blocks(self):
        pf = _mk()
        with mock.patch(_S1_PATCH, return_value={"available": False,
                                                 "provisioned": False}):
            rpt = pf.check(1, {"role": "tester"}, 5, _resolved())
        assert rpt.summary["metadata_source"] == "s1"
        assert "NO_METADATA" in _codes(rpt.blockers)

    def test_reads_s1_unconditionally(self):
        """Preflight always consults S1 — no flag gate, no ``meta_*`` path."""
        pf = _mk()
        with mock.patch(_S1_PATCH, return_value=_s1()) as s1:
            rpt = pf.check(1, {"role": "tester"}, 5, _resolved())
        s1.assert_called_once()
        assert rpt.summary["metadata_source"] == "s1"
