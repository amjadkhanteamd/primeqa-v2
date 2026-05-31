"""Substrate 6 — Observation & Interpretation (Intelligence / Attribution).

Interprets S4's captured truth (a grounded run outcome + evidence) into a
structured, QA-readable :class:`Interpretation` — the *meaning* of a run (what
was tested, what happened, the semantic attribution). The boundary is sharp: S4
captures truth and owns the outcome; S6 consumes ``evidence.outcome`` and
explains it, never re-judging it (the SPEC lives at
``docs/architecture/substrate_6_intelligence/SPEC.md``). Distinct from v1's
``primeqa.intelligence`` (the legacy LLM-gateway / explanations module).

Slice 1 (D-111): the deterministic interpreter. ``interpret_run(RunEvidence) →
Interpretation`` maps structured evidence to a verdict + attribution with **no
LLM** — attribution is derived from the evidence, never generated.
"""
from primeqa.interpretation.interpreter import interpret_run
from primeqa.interpretation.model import (
    EvidenceRef,
    Interpretation,
    Verdict,
)

__all__ = [
    "interpret_run",
    "Interpretation",
    "EvidenceRef",
    "Verdict",
]
