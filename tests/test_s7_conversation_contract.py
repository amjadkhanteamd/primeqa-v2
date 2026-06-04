"""Substrate-7 Conversation — contract / drift-guard (D-163, the open).

Offline, no-DB. Pins the S7 contract the open ratifies — the public API surface, the
frozen type shapes, the ``Evidence.is_empty`` keystone gate, and the **LLM-free
package** invariant — so a future change that silently breaks them trips here. The
pipeline *behaviours* (classify / retrieve / assemble / answer) get their own suites
in later slices (D-163.1–D-163.4); this asserts the *contract* the open names.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import fields, is_dataclass

import pytest

import primeqa.conversation as s7
from primeqa.conversation import (
    Answer,
    Citation,
    Evidence,
    EvidenceItem,
    Intent,
    QuestionContext,
)

pytestmark = pytest.mark.unit


def _frozen(t) -> bool:
    return is_dataclass(t) and t.__dataclass_params__.frozen


# ---------------------------------------------------------------------------
# The public API surface (the substrate boundary the open ratifies)
# ---------------------------------------------------------------------------

def test_public_api_exports_the_opened_surface():
    expected = {"QuestionContext", "Intent", "IntentKind", "EvidenceItem",
                "Evidence", "Citation", "Answer", "AnswerStatus"}
    assert expected <= set(s7.__all__)
    for name in expected:
        assert hasattr(s7, name), f"{name} missing from the S7 package surface"


# ---------------------------------------------------------------------------
# The contract type shapes (frozen, behaviour-free)
# ---------------------------------------------------------------------------

def test_question_context_shape_is_the_bounded_scope():
    assert _frozen(QuestionContext)
    assert {f.name for f in fields(QuestionContext)} == {
        "tenant_id", "environment_id", "requirement_key",
        "recipe_id", "test_id", "object_api_name"}
    q = QuestionContext(tenant_id=1)
    assert q.environment_id is None and q.object_api_name is None


def test_intent_shape():
    assert _frozen(Intent)
    assert {f.name for f in fields(Intent)} == {"kind", "matched_keywords"}
    assert Intent(kind="failure_cause").matched_keywords == ()


def test_evidence_item_shape():
    assert _frozen(EvidenceItem)
    assert {f.name for f in fields(EvidenceItem)} == {
        "citation_id", "source", "kind", "data"}


def test_citation_shape():
    assert _frozen(Citation)
    assert {f.name for f in fields(Citation)} == {
        "citation_id", "source", "kind", "ref"}


def test_answer_shape_and_defaults():
    assert _frozen(Answer)
    assert {f.name for f in fields(Answer)} == {
        "status", "text", "citations", "refusal_reason"}
    a = Answer(status="refused", text="cannot answer from the available data")
    assert a.citations == () and a.refusal_reason is None


# ---------------------------------------------------------------------------
# Evidence.is_empty — the grounded-or-refuse keystone gate
# ---------------------------------------------------------------------------

def test_evidence_is_empty_drives_the_refusal_gate():
    assert _frozen(Evidence)
    assert Evidence(intent="failure_cause").is_empty is True
    e = Evidence(
        intent="failure_cause",
        items=(EvidenceItem(citation_id="E1", source="S6",
                            kind="interpretation", data={"run_id": "r"}),))
    assert e.is_empty is False


# ---------------------------------------------------------------------------
# The LLM-free package invariant (the S6/S8 boundary)
# ---------------------------------------------------------------------------

def test_conversation_package_imports_no_intelligence():
    # SPEC §2: the substrate package is LLM-free — the phrase step is injected, the
    # real llm_call lives in v1. Importing primeqa.conversation must pull in ZERO
    # primeqa.intelligence. Checked in a fresh interpreter so prior test imports
    # don't pollute sys.modules.
    code = (
        "import primeqa.conversation\n"
        "import sys\n"
        "bad = sorted(m for m in sys.modules if m.startswith('primeqa.intelligence'))\n"
        "assert not bad, bad\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, (r.stdout + r.stderr)
