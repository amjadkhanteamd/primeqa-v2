"""D-183 (GAP-2) unit: Preflight's freshness/health source-switch.

Behind the per-tenant ``cutover_read_s1`` flag, Preflight reads freshness +
per-category health from the S1 substrate (``read_s1_freshness``) instead of the
v1 ``meta_*`` tables, with a ``meta_*`` fallback during the parallel window. These
tests drive ``Preflight.check`` with mocked repos, patching the flag
(``_use_s1_freshness``) + the S1 reader, and assert the freshness blockers/warnings
+ the ``metadata_source`` summary marker. Issue CODES are kept stable across
sources; only the message text differs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import pytest

from primeqa.runs.preflight import Preflight

pytestmark = pytest.mark.unit

_NOW = datetime.now(timezone.utc)
_S1_PATCH = "primeqa.metadata.s1_sync_console.read_s1_freshness"


def _resolved():
    return SimpleNamespace(test_count=1, test_case_ids=[],
                           resolution_warnings=[], missing_jira_keys=[])


def _mk(*, use_s1, meta_version_id=None):
    """A Preflight whose env passes the connection/credential/LLM sections, so the
    only freshness signal under test comes from the freshness branch."""
    env = SimpleNamespace(
        id=1, name="Dev", env_type="sandbox", sf_instance_url="https://x",
        is_active=True, connection_id=10, llm_connection_id=20,
        current_meta_version_id=meta_version_id)
    env_repo = mock.MagicMock()
    env_repo.get_environment.return_value = env
    conn_repo = mock.MagicMock()
    conn_repo.get_connection_decrypted.return_value = {"status": "ok"}
    pf = Preflight(mock.MagicMock(), env_repo=env_repo, conn_repo=conn_repo,
                   tc_repo=mock.MagicMock(), meta_repo=mock.MagicMock())
    pf._eta_range = lambda *a, **k: {"min": 0, "max": 0}
    pf._use_s1_freshness = lambda tid: use_s1
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
    def test_flag_on_s1_fresh_no_blocker(self):
        pf = _mk(use_s1=True)
        with mock.patch(_S1_PATCH, return_value=_s1()):
            rpt = pf.check(1, {"role": "tester"}, 5, _resolved())
        assert rpt.summary["metadata_source"] == "s1"
        assert "METADATA_VERY_STALE" not in _codes(rpt.blockers)
        assert "METADATA_STALE" not in _codes(rpt.warnings)
        assert "NO_METADATA" not in _codes(rpt.blockers)
        assert rpt.summary["meta_version"]["id"] == 43
        assert rpt.summary["meta_version"]["version_label"] == "Org model v43"

    def test_flag_on_s1_very_stale_blocks(self):
        pf = _mk(use_s1=True)
        with mock.patch(_S1_PATCH, return_value=_s1(age_hours=800.0)):
            rpt = pf.check(1, {"role": "tester"}, 5, _resolved())
        assert rpt.summary["metadata_source"] == "s1"
        assert "METADATA_VERY_STALE" in _codes(rpt.blockers)

    def test_flag_on_s1_stale_warns(self):
        pf = _mk(use_s1=True)
        with mock.patch(_S1_PATCH, return_value=_s1(age_hours=200.0)):
            rpt = pf.check(1, {"role": "tester"}, 5, _resolved())
        assert "METADATA_STALE" in _codes(rpt.warnings)
        assert "METADATA_VERY_STALE" not in _codes(rpt.blockers)

    def test_flag_on_s1_not_usable_blocks(self):
        pf = _mk(use_s1=True)
        with mock.patch(_S1_PATCH, return_value=_s1(
                usable=False, current_version_seq=None, age_hours=None,
                last_success_at=None)):
            rpt = pf.check(1, {"role": "tester"}, 5, _resolved())
        assert rpt.summary["metadata_source"] == "s1"
        assert "NO_METADATA" in _codes(rpt.blockers)

    def test_flag_on_s1_usable_age_none_not_stale(self):
        """First sync still running: usable (a version exists) but no measured
        age -> not stale, not blocked."""
        pf = _mk(use_s1=True)
        with mock.patch(_S1_PATCH, return_value=_s1(
                current_version_seq=1, age_hours=None, last_success_at=None)):
            rpt = pf.check(1, {"role": "tester"}, 5, _resolved())
        assert "METADATA_STALE" not in _codes(rpt.warnings)
        assert "METADATA_VERY_STALE" not in _codes(rpt.blockers)
        assert "NO_METADATA" not in _codes(rpt.blockers)

    def test_flag_on_but_s1_not_provisioned_falls_back_to_meta(self):
        pf = _mk(use_s1=True, meta_version_id=5)
        pf.meta_repo.get_version.return_value = SimpleNamespace(
            id=5, version_label="v5", completed_at=_NOW, status="complete")
        with mock.patch(_S1_PATCH, return_value=_s1(
                provisioned=False, usable=False, current_version_seq=None)):
            rpt = pf.check(1, {"role": "tester"}, 5, _resolved())
        assert rpt.summary["metadata_source"] == "meta"
        assert rpt.summary["meta_version"]["id"] == 5

    def test_flag_on_but_s1_unavailable_falls_back_to_meta(self):
        pf = _mk(use_s1=True, meta_version_id=5)
        pf.meta_repo.get_version.return_value = SimpleNamespace(
            id=5, version_label="v5", completed_at=_NOW, status="complete")
        with mock.patch(_S1_PATCH, return_value={"available": False,
                                                 "provisioned": False}):
            rpt = pf.check(1, {"role": "tester"}, 5, _resolved())
        assert rpt.summary["metadata_source"] == "meta"

    def test_flag_off_uses_meta_and_never_reads_s1(self):
        pf = _mk(use_s1=False, meta_version_id=5)
        pf.meta_repo.get_version.return_value = SimpleNamespace(
            id=5, version_label="v5", completed_at=_NOW, status="complete")
        with mock.patch(_S1_PATCH,
                        side_effect=AssertionError("S1 read while flag off")):
            rpt = pf.check(1, {"role": "tester"}, 5, _resolved())
        assert rpt.summary["metadata_source"] == "meta"
        assert rpt.summary["meta_version"]["id"] == 5
        assert "NO_METADATA" not in _codes(rpt.blockers)
