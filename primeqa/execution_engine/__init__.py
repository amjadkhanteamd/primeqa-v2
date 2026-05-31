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

Slice 4 (D-108.3): the finalize step. :func:`finalize_run` persists the
evidence then reports the grounded outcome as posture to S2
(`report_run_outcome`, actor='s4') on the same session — atomically. The
write-side of the S4↔S2 read-through boundary; closes the first vertical's spine.

Run path (D-108.4): :func:`run_recipe_execution` wires all four components into
a synchronous end-to-end "execute this recipe" path (select → bridge → resolve
client → execute → finalize); :func:`run_recipe_execution_for_tenant` is the
thin production entry owning the tenant connection + the single commit.
"""
from primeqa.execution_engine.bridge import (
    build_data_recipe_plan,
    build_metadata_inspection_plan,
)
from primeqa.execution_engine.credentials import (
    resolve_data_mutation_client,
    resolve_tooling_client,
)
from primeqa.execution_engine.data_executor import execute_data_recipe
from primeqa.execution_engine.data_mutation_client import DataMutationClient
from primeqa.execution_engine.finalize import finalize_run
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
    CleanupRecord,
    CreateAttemptEvidence,
    ErrorSurface,
    ReadEvidence,
    RunEvidence,
    StepEvidence,
)
from primeqa.execution_engine.executor import execute_metadata_inspection
from primeqa.execution_engine.plan import (
    DataRecipePlan,
    MetadataInspectionPlan,
    PlannedAssertion,
    PlannedCreate,
    PlannedRead,
    PlanStep,
)
from primeqa.execution_engine.result_store import (
    S4ExecutionRun,
    persist_run_evidence,
)
from primeqa.execution_engine.run import (
    RunPathResult,
    run_recipe_execution,
    run_recipe_execution_for_tenant,
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
    # data-recipe behavioral-negative — bridge + plan + executor (D-110.2)
    "build_data_recipe_plan",
    "DataRecipePlan",
    "PlannedCreate",
    "execute_data_recipe",
    "DataMutationClient",
    "resolve_data_mutation_client",
    "CreateAttemptEvidence",
    "CleanupRecord",
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
    # slice 4 — finalize (persist + posture callback)
    "finalize_run",
    # run path — end-to-end recipe execution
    "run_recipe_execution",
    "run_recipe_execution_for_tenant",
    "RunPathResult",
    # errors
    "ExecutionEngineError",
    "PlanTranslationError",
    "CredentialResolutionError",
    "UnsupportedEdgeError",
    "UnsupportedPredicateError",
    "AssertionResolutionError",
]
