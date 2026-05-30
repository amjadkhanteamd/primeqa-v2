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
contract later slices consume.

Slice 2 (D-108.1): the executor. The plan is translated edge->SOQL
(:func:`translate_read`), read via a thin S4-local Tooling client
(:class:`ToolingReadClient`, credentialed by :func:`resolve_tooling_client`),
its `exists` assertion evaluated, and a grounded run outcome + in-memory
:class:`RunEvidence` produced by :func:`execute_metadata_inspection`.

Slice 3 (D-108.2): the result store. :func:`persist_run_evidence` maps an
in-memory :class:`RunEvidence` to an :class:`S4ExecutionRun` row (per-tenant
``s4_execution_runs`` — typed identity/outcome columns + an ``evidence`` JSONB
captured-trace). The executor stays produce-only; the persister is the only
writer.
"""
from primeqa.execution_engine.bridge import build_metadata_inspection_plan
from primeqa.execution_engine.credentials import resolve_tooling_client
from primeqa.execution_engine.errors import (
    AssertionResolutionError,
    CredentialResolutionError,
    ExecutionEngineError,
    PlanTranslationError,
    UnsupportedEdgeError,
    UnsupportedPredicateError,
)
from primeqa.execution_engine.evidence import (
    AssertEvidence,
    ErrorSurface,
    ReadEvidence,
    RunEvidence,
    StepEvidence,
)
from primeqa.execution_engine.executor import execute_metadata_inspection
from primeqa.execution_engine.plan import (
    MetadataInspectionPlan,
    PlannedAssertion,
    PlannedRead,
    PlanStep,
)
from primeqa.execution_engine.result_store import (
    S4ExecutionRun,
    persist_run_evidence,
)
from primeqa.execution_engine.tooling_client import ToolingReadClient
from primeqa.execution_engine.translator import ToolingQuery, translate_read

__all__ = [
    # slice 1 — bridge + plan
    "build_metadata_inspection_plan",
    "MetadataInspectionPlan",
    "PlannedAssertion",
    "PlannedRead",
    "PlanStep",
    # slice 2 — executor
    "translate_read",
    "ToolingQuery",
    "ToolingReadClient",
    "resolve_tooling_client",
    "execute_metadata_inspection",
    "RunEvidence",
    "ReadEvidence",
    "AssertEvidence",
    "StepEvidence",
    "ErrorSurface",
    # slice 3 — result store
    "S4ExecutionRun",
    "persist_run_evidence",
    # errors
    "ExecutionEngineError",
    "PlanTranslationError",
    "CredentialResolutionError",
    "UnsupportedEdgeError",
    "UnsupportedPredicateError",
    "AssertionResolutionError",
]
