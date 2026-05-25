"""Integration: S3 intake version resolution (D-106.4 slice 2) — per-tenant PG.

``resolve_current_s1_version`` returns the latest ``logical_version``
(``MAX(version_seq)``) + its name. (``build_generation_request`` is pure → unit
suite.)
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from primeqa.generation.intake import resolve_current_s1_version
from primeqa.semantic.connection import get_tenant_connection

from .conftest import TEST_TENANT_ID


def _insert_version(name: str) -> int:
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        return conn.execute(text(
            "INSERT INTO logical_versions (version_name, version_type) "
            "VALUES (:n, 'manual_checkpoint') RETURNING version_seq"
        ), {"n": name}).scalar()


def test_resolve_current_s1_version_returns_latest(seeded):
    # A freshly-inserted version is the new MAX(version_seq) (BIGSERIAL is
    # monotonic), so resolve returns exactly it — deterministic regardless of
    # versions other tests left behind.
    name = f"intake_cur_{uuid4().hex[:8]}"
    seq = _insert_version(name)
    got_seq, got_name = resolve_current_s1_version(TEST_TENANT_ID)
    assert got_seq == seq
    assert got_name == name


def test_resolve_current_s1_version_advances_with_new_version(seeded):
    # Pinning reflects the newest snapshot: a later version supersedes an earlier.
    _insert_version(f"intake_old_{uuid4().hex[:8]}")
    newer = f"intake_new_{uuid4().hex[:8]}"
    newer_seq = _insert_version(newer)
    got_seq, got_name = resolve_current_s1_version(TEST_TENANT_ID)
    assert got_seq == newer_seq and got_name == newer
