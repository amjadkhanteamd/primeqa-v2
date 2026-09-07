"""Step B — the ONE decision-facing current-sequence resolver
(``primeqa.sync.readiness.resolve_current_sequence``), the pure parts.

* org-REQUIRED: ``None`` → the recorded refusal ``CANNOT_DETERMINE /
  org_unbound`` without touching the session (never a tenant-wide number);
* bound, no version row → ``CANNOT_DETERMINE / org_never_synced``;
* bound with a row → ``CURRENT`` with the seq, its timestamp, source
  ``org_current``, axis ``org_sequence``;
* the SQL binds the org uuid — an org-less row can never match;
* ``run_stamp`` is reserved, not produced.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest import mock

from primeqa.sync import readiness as R


def test_none_org_is_the_refusal_and_never_reads():
    session = mock.MagicMock()
    r = R.resolve_current_sequence(session, connected_org_id=None)
    assert r.state == R.SEQ_CANNOT_DETERMINE and r.reason == R.REASON_ORG_UNBOUND
    assert r.current_seq is None and r.as_of is None
    assert r.source == R.SEQ_SOURCE_ORG_CURRENT and r.axis == R.SEQ_AXIS
    session.execute.assert_not_called()


def test_bound_but_never_synced():
    session = mock.MagicMock()
    session.execute.return_value.first.return_value = None
    r = R.resolve_current_sequence(session, connected_org_id="11111111-1111-1111-1111-111111111111")
    assert r.state == R.SEQ_CANNOT_DETERMINE and r.reason == R.REASON_ORG_NEVER_SYNCED
    assert r.connected_org_id == "11111111-1111-1111-1111-111111111111"


def test_bound_and_synced_is_current_with_the_orgs_own_row():
    session = mock.MagicMock()
    at = datetime(2026, 9, 6, 5, 30, tzinfo=timezone.utc)
    session.execute.return_value.first.return_value = (249, at)
    r = R.resolve_current_sequence(session, connected_org_id="org-a")
    assert r.state == R.SEQ_CURRENT and r.current_seq == 249 and r.as_of == at
    assert r.source == R.SEQ_SOURCE_ORG_CURRENT and r.reason is None
    sql = str(session.execute.call_args.args[0])
    assert "connected_org_id = CAST(:org AS uuid)" in sql       # org-less rows excluded
    assert "MAX(" not in sql or "WHERE" in sql
    assert session.execute.call_args.args[1] == {"org": "org-a"}


def test_as_dict_shape_keeps_the_axes_explicit():
    r = R.resolve_current_sequence(mock.MagicMock(), connected_org_id=None)
    d = r.as_dict()
    assert set(d) == {"state", "current_seq", "as_of", "source", "axis",
                      "connected_org_id", "reason"}
    assert d["axis"] == "org_sequence"
    assert R.SEQ_SOURCE_RUN_STAMP == "run_stamp"                 # reserved for Step 2
