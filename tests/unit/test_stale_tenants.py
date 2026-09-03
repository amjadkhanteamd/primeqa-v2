"""Unit: the loudly-once stale-tenant guard (``primeqa/shared/stale_tenants``)
— the 0862c5e posture generalised for every per-tenant scheduler tick."""
from __future__ import annotations

from unittest import mock

from primeqa.shared import stale_tenants as ST


def _conn(regclass):
    conn = mock.MagicMock()
    conn.execute.return_value.scalar.return_value = regclass
    return conn


def test_provisioned_tenant_passes_and_never_warns():
    log = mock.MagicMock()
    assert ST.skip_unprovisioned(_conn("tenant_1.t"), 1, "t", log) is False
    log.warning.assert_not_called()


def test_unprovisioned_tenant_skips_and_warns_once():
    ST._WARNED_UNPROVISIONED.discard((7, "t"))
    log = mock.MagicMock()
    assert ST.skip_unprovisioned(_conn(None), 7, "t", log) is True
    assert ST.skip_unprovisioned(_conn(None), 7, "t", log) is True
    assert log.warning.call_count == 1                   # loudly-ONCE


def test_warn_key_is_per_tenant_and_table():
    for key in ((8, "a"), (8, "b"), (9, "a")):
        ST._WARNED_UNPROVISIONED.discard(key)
    log = mock.MagicMock()
    ST.skip_unprovisioned(_conn(None), 8, "a", log)
    ST.skip_unprovisioned(_conn(None), 8, "b", log)
    ST.skip_unprovisioned(_conn(None), 9, "a", log)
    assert log.warning.call_count == 3
