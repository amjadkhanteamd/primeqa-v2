"""Unit tests for the S4 worker wiring (D-132) — mock-patched, no DB.

Mirrors the S3 worker tests (test_worker_enrichment.py): the session is a
MagicMock and ``_discover_tenant_schemas`` / the consumer tick are patched. The
tests verify the orchestration — discovery -> resolver -> delegate — not the SQL.
"""
from __future__ import annotations

from contextlib import ExitStack
from unittest import mock

import pytest

from primeqa import worker

pytestmark = pytest.mark.unit


def _patch(name):
    return mock.patch.object(worker, name)


def test_s4_execution_tick_noops_when_no_tenants():
    factory = mock.MagicMock(side_effect=[mock.MagicMock()])
    with _patch("_discover_tenant_schemas") as disc:
        disc.return_value = []
        assert worker.s4_execution_tick(db_factory=factory) == {}


def test_s4_execution_tick_delegates_to_run_tick_with_resolver():
    factory = mock.MagicMock(side_effect=[mock.MagicMock()])
    def stub_resolver(tenant_id, environment_id):
        return object()

    with ExitStack() as s:
        s.enter_context(_patch("_discover_tenant_schemas")).return_value = [
            ("tenant_1", 1),
        ]
        run = s.enter_context(mock.patch(
            "primeqa.execution_engine.consumer.run_s4_execution_tick"))
        run.return_value = {1: "empty"}
        out = worker.s4_execution_tick(db_factory=factory, client_resolver=stub_resolver)

    assert out == {1: "empty"}
    run.assert_called_once()
    args, kwargs = run.call_args
    assert args[0] == [1]                            # the discovered tenant_ids
    assert kwargs["client_resolver"] is stub_resolver


def test_default_client_resolver_requires_environment_id():
    factory = mock.MagicMock()
    resolve = worker._default_s4_client_resolver(factory)
    with pytest.raises(ValueError, match="no environment_id"):
        resolve(1, None)
    factory.assert_not_called()   # guarded before any session is opened
