"""Substrate 3 — Generation Engine.

See ``docs/architecture/substrate_3_generation/SPEC.md`` and
``docs/architecture/DECISIONS_LOG.md`` (D-070 through D-094) for the
Phase 1 design, and ``substrate_3_generation/PHASE_2_FORENSIC_REPORT.md``
for the implementation grounding.

Package layout mirrors the substrate domain-named convention
(``primeqa/semantic`` = S1, ``primeqa/test_representation`` = S2).
Substrate-3's domain name is "generation"; the SPEC dir is
``substrate_3_generation``. (Distinct from the v1 generator at
``primeqa/intelligence/generation.py``, which this substrate supersedes.)

Slice 0 (data-model foundation) ships the three-context request/outcome
protocol types and the two generation-ledger tables — contracts and
schema only, no behavior. Importing this package adds the two ledger
tables to ``Base.metadata`` as a side effect (the substrate-2 idiom).
"""
from __future__ import annotations

from primeqa.generation.enums import (
    AdmissibilityLayer,
    DismissalCategory,
    DismissalReason,
    LlmCallOutcome,
    OutcomeKind,
    RefusalKind,
    RegenerationKind,
)
from primeqa.generation.protocol import (
    AttemptedInterpretation,
    BudgetSpec,
    ClaimRef,
    ExplanationHash,
    GenerationOutcome,
    GenerationRequest,
    GovernanceContext,
    OperationalContext,
    OutcomeId,
    RecipeRef,
    RefusalEntry,
    RequestId,
    SemanticContext,
)
from primeqa.generation.models_db import (
    GenerationOutcomeRow,
    GenerationRequestRow,
    LlmCall,
)

__all__ = [
    # ----- Enums (substrate vocabulary) -----
    "AdmissibilityLayer",
    "DismissalCategory",
    "DismissalReason",
    "LlmCallOutcome",
    "OutcomeKind",
    "RefusalKind",
    "RegenerationKind",
    # ----- Protocol types (Pydantic) -----
    "AttemptedInterpretation",
    "BudgetSpec",
    "ClaimRef",
    "ExplanationHash",
    "GenerationOutcome",
    "GenerationRequest",
    "GovernanceContext",
    "OperationalContext",
    "OutcomeId",
    "RecipeRef",
    "RefusalEntry",
    "RequestId",
    "SemanticContext",
    # ----- SQLAlchemy ledger tables -----
    "GenerationOutcomeRow",
    "GenerationRequestRow",
    "LlmCall",
]
