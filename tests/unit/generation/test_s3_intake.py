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
