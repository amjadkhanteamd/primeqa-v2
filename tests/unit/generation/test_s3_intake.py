"""Unit tests for S3 intake assembly (D-106.4 slice 2) — pure, no PG.

``build_generation_request`` produces a fresh single-requirement
``GenerationRequest`` in the shape ``run_generation`` accepts, with no lineage
(CHECK-valid). ``resolve_current_s1_version`` is PG-backed → tested in the
integration suite.
"""
from __future__ import annotations

from uuid import uuid4

from primeqa.generation.intake import build_generation_request
from primeqa.generation.protocol import GenerationRequest


def test_build_generation_request_fresh_single_requirement():
    rid = uuid4()
    ref = {"key": "PROJ-1", "text": "Users must not save a Lead without a reason."}
    req = build_generation_request(
        requirement_ref=ref, s1_version_seq=42, s1_version_name="v42", request_id=rid)

    assert isinstance(req, GenerationRequest)
    assert req.request_id == rid
    # the single ref + pinned snapshot ride semantic_context
    assert req.semantic_context.requirement_refs == [ref]
    assert req.semantic_context.s1_version_seq == 42
    assert req.semantic_context.s1_version_name == "v42"
    # default contexts present (the shape run_generation accepts)
    assert req.governance_context is not None
    assert req.operational_context is not None


def test_build_generation_request_is_fresh_no_lineage():
    # Fresh request -> no lineage -> CHECK-valid ((prior IS NULL)=(deltas IS NULL)).
    req = build_generation_request(
        requirement_ref={"key": "R", "text": "x"}, s1_version_seq=1,
        s1_version_name="v1", request_id=uuid4())
    assert req.prior_request_id is None
    assert req.deltas is None
    assert req.regeneration_kind is None
    # the persisted shape (LedgerPersister path) keeps both NULL together
    dumped = req.model_dump(mode="json")
    assert (dumped["prior_request_id"] is None) == (dumped["deltas"] is None)


def test_build_generation_request_does_not_mint_request_id():
    # Intake receives the id (minted by start_attempt) — it must round-trip verbatim.
    rid = uuid4()
    req = build_generation_request(
        requirement_ref={"key": "R", "text": "x"}, s1_version_seq=1,
        s1_version_name=None, request_id=rid)
    assert req.request_id == rid
    assert req.semantic_context.s1_version_name is None


# ---------------------------------------------------------------------------
# D-300 (S3): the bva-boundary flag rides the OPERATIONAL context.
# ---------------------------------------------------------------------------

def test_build_request_default_bva_off():
    from primeqa.generation.intake import build_generation_request
    from uuid import uuid4
    req = build_generation_request(
        requirement_ref={"key": "R1", "text": "x"},
        s1_version_seq=1, s1_version_name=None, request_id=uuid4())
    assert req.operational_context.enable_bva_boundaries is False


def test_build_request_threads_bva_flag():
    from primeqa.generation.intake import build_generation_request
    from uuid import uuid4
    req = build_generation_request(
        requirement_ref={"key": "R1", "text": "x"},
        s1_version_seq=1, s1_version_name=None, request_id=uuid4(),
        enable_bva_boundaries=True)
    assert req.operational_context.enable_bva_boundaries is True
    # operational, never governance (GovernanceContext is identity-bearing)
    assert not hasattr(req.governance_context, "enable_bva_boundaries")


def test_bva_flag_read_is_best_effort_false_on_any_error(monkeypatch):
    # missing column / missing row / broken DB all read False (the feature
    # stays dormant) — the migration-054 deploy-safety precedent.
    from primeqa.generation import consumer as C

    def _boom():
        raise RuntimeError("no db")
    monkeypatch.setattr("primeqa.db.get_db", _boom)
    assert C._bva_boundaries_enabled(1) is False


def test_finalize_outcome_threads_the_bva_flag_to_the_author(monkeypatch):
    # D-300 review-fix (governance seam): a typo'd getattr / dropped kwarg here
    # kills the feature for flag-ON tenants with every suite green — this test
    # pins the exact ctx -> author_emission threading (the mutant-killer).
    from types import SimpleNamespace
    import pytest as _pytest
    from primeqa.generation import governance_core as gc

    captured = {}

    class _Sentinel(Exception):
        pass

    def fake_author(g, *, enable_bva_boundaries=False):
        captured["flag"] = enable_bva_boundaries
        raise _Sentinel()

    monkeypatch.setattr(gc, "author_emission", fake_author)
    core = gc.GovernanceCore(None)
    state = SimpleNamespace(groundings=[object()],
                            attempted_interpretation={},
                            presented_candidates=None)

    ctx_on = SimpleNamespace(operational_context=SimpleNamespace(
        enable_bva_boundaries=True))
    with _pytest.raises(_Sentinel):
        core.finalize_outcome(outcome_input={}, ctx=ctx_on, state=state)
    assert captured["flag"] is True

    # a legacy/test ctx without the operational_context reads OFF, never raises
    ctx_legacy = SimpleNamespace()
    with _pytest.raises(_Sentinel):
        core.finalize_outcome(outcome_input={}, ctx=ctx_legacy, state=state)
    assert captured["flag"] is False


def test_bva_flag_read_happy_path_true(monkeypatch):
    # D-300 review-fix (consumer seam): the raw-SQL read returns the column
    # value — a `return False` mutant dies here.
    from types import SimpleNamespace
    from primeqa.generation import consumer as C

    class _FakeDb:
        def execute(self, *a, **k):
            return SimpleNamespace(scalar=lambda: True)

        def close(self):
            pass

    def fake_get_db():
        yield _FakeDb()

    monkeypatch.setattr("primeqa.db.get_db", fake_get_db)
    assert C._bva_boundaries_enabled(1) is True


def test_process_job_threads_the_bva_flag_into_the_request(monkeypatch):
    # D-300 review-fix (consumer seam): the flag reaches the request's
    # operational context — a deleted-kwarg mutant dies here.
    from types import SimpleNamespace
    from uuid import uuid4 as _u
    from primeqa.generation import consumer as C

    job = SimpleNamespace(id=7, requirement_key="R1", requirement_text="x",
                          s1_version_seq=1, s1_version_name=None,
                          environment_id=59)

    class _FakeStore:
        def __init__(self, tenant_id):
            pass

        def claim_next_queued_job(self):
            return job

        def heartbeat(self, jid):
            pass

        def start_attempt(self, jid):
            return SimpleNamespace(request_id=str(_u()))

        def complete(self, jid):
            pass

        def fail(self, jid, **k):
            pass

    captured = {}

    def fake_run_generation(request, **kwargs):
        captured["request"] = request
        return SimpleNamespace(results=[])

    monkeypatch.setattr(C, "GenerationJobStore", _FakeStore)
    monkeypatch.setattr(C, "run_generation", fake_run_generation)
    monkeypatch.setattr(C, "_bva_boundaries_enabled", lambda tid: True)

    C.process_job_for_tenant(1, api_key_resolver=lambda t, e: "k")
    assert captured["request"].operational_context.enable_bva_boundaries is True
