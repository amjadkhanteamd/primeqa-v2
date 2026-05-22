"""Seam-level test for ``GovernanceCore.accept_selection`` (D-096.3 Layer-B
selection floor).

The engine cannot yet produce a >=2-grounded ``AWAIT_SELECTION`` state — the
``DecompositionController`` is single-candidate by design — so the multi-
candidate flow (decomposition >=2 -> AWAIT_SELECTION -> accept_selection) is a
deferred Phase-3 decomposition increment, not a new outcome type. The seam
method is implemented now, so it is exercised directly against its designed
selection-shaped input (the D-086 ``select_canonical`` schema:
``selection_rationale.selected_path_id``) over a hand-built presented-candidate
state. No PG, no engine — accept_selection touches neither the S1 model nor the
DB."""
from __future__ import annotations

from types import SimpleNamespace

from primeqa.generation.enums import AdmissibilityLayer, RefusalKind
from primeqa.generation.governance import PresentedCandidate, SelectionVerdict
from primeqa.generation.governance_core import GovernanceCore


def _gov() -> GovernanceCore:
    # accept_selection reads only selection_input + state; the S1 model is
    # never touched, so None is a safe seam stand-in.
    return GovernanceCore(None)


def _state(*path_ids):
    return SimpleNamespace(presented_candidates=[
        PresentedCandidate(path_id=p, admissibility_layer=AdmissibilityLayer.LAYER_1)
        for p in path_ids
    ])


def _selection(selected_path_id):
    return {
        "candidate_refs": ["c0", "c1"],
        "selection_rationale": {
            "selected_path_id": selected_path_id,
            "rationale_kind": "highest_specificity",
        },
    }


def test_accepts_presented_canonical():
    sv = _gov().accept_selection(
        selection_input=_selection("c1"), ctx=None, state=_state("c0", "c1"))
    assert isinstance(sv, SelectionVerdict)
    assert sv.accepted is True
    assert sv.finding is None
    assert sv.interpretation_delta["selected_path_id"] == "c1"


def test_rejects_unpresented_path():
    sv = _gov().accept_selection(
        selection_input=_selection("c9"), ctx=None, state=_state("c0", "c1"))
    assert sv.accepted is False
    assert sv.finding is not None
    assert sv.finding.refusal_kind == RefusalKind.STRUCTURAL_VALIDATION_FAILURE


def test_rejects_missing_selected_path():
    # A selection with no selected_path_id is structurally invalid (Layer B
    # rejects; it never invents a selection).
    sv = _gov().accept_selection(
        selection_input={"candidate_refs": ["c0"], "selection_rationale": {}},
        ctx=None, state=_state("c0", "c1"))
    assert sv.accepted is False
    assert sv.finding.refusal_kind == RefusalKind.STRUCTURAL_VALIDATION_FAILURE
