"""Integration: ``write_claim`` persists ``strategy_kind`` and ``ClaimRead``
exposes it (D-285, Slice 4f.0) — the full PERSIST + EXPOSE round-trip of Fork C.

DB-gated (local-PG substrate-2 convention; skips when unreachable). The
conftest's alembic chain runs the new ``20260627_0020`` migration, so a green run
here also proves the migration applies cleanly to a fresh schema.

DORMANT: every existing caller omits ``strategy_kind`` → NULL → routes single,
byte-identical to today. Generation sets ``'bva'`` later (4f.2).
"""
from __future__ import annotations

from primeqa.test_representation import SemanticTransactionCoordinator

from ._builders import empty_conditions, make_value_claim


def test_strategy_kind_defaults_null_and_reads_none(session) -> None:
    # A write that OMITS strategy_kind (every caller today) → NULL column →
    # ClaimRead exposes None. This is the dormant / byte-identical default.
    coord = SemanticTransactionCoordinator()
    r = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=make_value_claim(), semantic_conditions=empty_conditions(),
    )
    read = coord.get_latest_claim(session, r.test_id)
    assert read is not None
    assert read.strategy_kind is None


def test_strategy_kind_round_trips_bva(session) -> None:
    # An explicit strategy_kind='bva' persists and ClaimRead exposes it — the
    # forward path 4f.2 will drive. (No claim sets this today.)
    coord = SemanticTransactionCoordinator()
    r = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=make_value_claim(), semantic_conditions=empty_conditions(),
        strategy_kind="bva",
    )
    read = coord.get_latest_claim(session, r.test_id)
    assert read is not None
    assert read.strategy_kind == "bva"


def test_strategy_kind_round_trips_single(session) -> None:
    coord = SemanticTransactionCoordinator()
    r = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=make_value_claim(), semantic_conditions=empty_conditions(),
        strategy_kind="single",
    )
    read = coord.get_latest_claim(session, r.test_id)
    assert read is not None
    assert read.strategy_kind == "single"
