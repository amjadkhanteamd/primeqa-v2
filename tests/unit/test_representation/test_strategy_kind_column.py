"""Unit: the ClaimRead ``strategy_kind`` field (D-285, Slice 4f.0) — the EXPOSE
half of Fork C. The column is persisted by ``write_claim`` (round-trip proven in
the integration test); here we prove the read-projection dataclass carries it and
the run router reads the REAL value off a ClaimRead (was a getattr default).

DORMANT: nothing assigns ``'bva'`` until generation (4f.2). A claim with no
recorded strategy reads ``None`` and routes single — byte-identical to today.
Pure, no DB.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

pytestmark = pytest.mark.unit

from primeqa.test_representation.coordinator import ClaimRead
from primeqa.execution_engine.run import _recorded_strategy_kind, route_strategy


_NOW = datetime(2026, 6, 27, tzinfo=timezone.utc)


def _claim_read(**over):
    """A ClaimRead with placeholder bodies (the dataclass does not type-check
    them); override any field, notably ``strategy_kind``."""
    base = dict(
        test_id=uuid4(), version_seq=1, valid_from=_NOW, valid_to=None,
        archetype="data_behavior", claim_kind="property",
        asserted_truth=object(), semantic_conditions=object(),
        identity_hash="h", identity_hash_version=1, status="approved",
        created_at=_NOW, updated_at=_NOW)
    base.update(over)
    return ClaimRead(**base)


# --- the field + its trailing default ---------------------------------------

def test_claim_read_strategy_kind_defaults_none():
    # the trailing-optional default: an existing construction that omits it (every
    # claim authored before 4f.2) reads None.
    assert _claim_read().strategy_kind is None


def test_claim_read_strategy_kind_exposes_value():
    assert _claim_read(strategy_kind="bva").strategy_kind == "bva"
    assert _claim_read(strategy_kind="single").strategy_kind == "single"


def test_claim_read_explicit_none():
    assert _claim_read(strategy_kind=None).strategy_kind is None


# --- the router reads the REAL ClaimRead value (was a getattr default) -------

def test_router_reads_recorded_kind_off_a_real_claim_read():
    # the seam: _recorded_strategy_kind getattr's the attr — now a real column
    # value, not the absent-attr default. None → single (dormant today).
    assert _recorded_strategy_kind(_claim_read()) is None
    assert route_strategy(_recorded_strategy_kind(_claim_read())) == "single"


def test_router_reads_bva_off_a_real_claim_read():
    # forward-compat: WHEN 4f.2 records 'bva', a real ClaimRead carries it and the
    # router routes run-all. (No claim does this today.)
    bva = _claim_read(strategy_kind="bva")
    assert _recorded_strategy_kind(bva) == "bva"
    assert route_strategy(_recorded_strategy_kind(bva)) == "runall"
