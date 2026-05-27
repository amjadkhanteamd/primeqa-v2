"""Substrate 4 — Execution Engine.

The runtime that executes an S2 recipe against a Salesforce org and captures
the observed truth, including the grounded run outcome (the SPEC lives at
``docs/architecture/substrate_4_execution/SPEC.md``). Distinct from v1's
``primeqa.execution`` (the legacy per-step REST runner) — S4 owns the recipe-
execution orchestration, the grounded outcome, and an evidence-first result
model; it reuses v1's mechanical primitives only beneath that seam (F1).

Slice 1 (D-108): the recipe -> executable-plan bridge for the
metadata-inspection vertical. Given substrate-2's typed ``RecipeRead`` it
produces a :class:`MetadataInspectionPlan` — the semantic, S1-edge-vocabulary
contract that later slices consume (executor + assertion evaluation; evidence
capture; the S2 posture callback).
"""
from primeqa.execution_engine.bridge import build_metadata_inspection_plan
from primeqa.execution_engine.errors import (
    ExecutionEngineError,
    PlanTranslationError,
)
from primeqa.execution_engine.plan import (
    MetadataInspectionPlan,
    PlannedAssertion,
    PlannedRead,
    PlanStep,
)

__all__ = [
    "build_metadata_inspection_plan",
    "ExecutionEngineError",
    "PlanTranslationError",
    "MetadataInspectionPlan",
    "PlannedAssertion",
    "PlannedRead",
    "PlanStep",
]
