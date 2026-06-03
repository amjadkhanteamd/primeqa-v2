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

Slice 2 (D-111.1): deeper attribution. ``attribute_run(interpretation, evidence,
*, s1)`` enriches the failed behavioral verdicts with a structured
:class:`Cause`, read deterministically from S1's VR metadata through the
:class:`S1VrReader` port. Never re-judges the outcome.

The **read/consumer surface** (D-137) is re-exported here so the substrate has
one coherent reads-and-clusters API: :func:`read_interpretation` /
:func:`list_interpretations` (single-run + scoped reads of the persisted store)
and the cross-run clustering reads (:func:`cluster_recurring_causes` /
:func:`cluster_by_vr` / :func:`cluster_flapping`). The LLM-phrasing live-fire
stays in v1 (``intelligence.interpretation_phrasing``) — this package is
deliberately LLM-free.
"""
from primeqa.interpretation.attribution import (
    S1VrReader,
    VrMeta,
    attribute_run,
)
from primeqa.interpretation.clustering import (
    CauseCluster,
    FlappingCluster,
    VrCluster,
    cluster_by_vr,
    cluster_flapping,
    cluster_recurring_causes,
)
from primeqa.interpretation.interpreter import interpret_run
from primeqa.interpretation.s1_reader import S1ValidationRuleReader
from primeqa.interpretation.model import (
    Cause,
    CauseKind,
    EvidenceRef,
    Interpretation,
    Verdict,
)
from primeqa.interpretation.result_store import (
    InterpretationRead,
    list_interpretations,
    read_interpretation,
)

__all__ = [
    # slice 1 — the interpreter
    "interpret_run",
    "Interpretation",
    "EvidenceRef",
    "Verdict",
    # slice 2 — deeper attribution
    "attribute_run",
    "Cause",
    "CauseKind",
    "S1VrReader",
    "VrMeta",
    "S1ValidationRuleReader",
    # D-137 — the read API
    "read_interpretation",
    "list_interpretations",
    "InterpretationRead",
    # D-137 — the cross-run clustering reads
    "cluster_recurring_causes",
    "cluster_by_vr",
    "cluster_flapping",
    "CauseCluster",
    "VrCluster",
    "FlappingCluster",
]
