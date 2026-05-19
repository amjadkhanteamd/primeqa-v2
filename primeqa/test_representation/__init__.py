"""Substrate 2 — Test Representation.

See ``docs/architecture/substrate_2_test_representation/SPEC.md``
and ``docs/architecture/DECISIONS_LOG.md`` (D-051 through D-065)
for the design and the lock terms.

Package layout mirrors substrate-1's domain-named convention
(``primeqa/sync``, ``primeqa/semantic``). The substrate-2 domain
name is "test representation"; the SPEC dir is
``substrate_2_test_representation``.

Importing this package is the **registry-populating import** per
SPEC §4.4 + §10.2. After ``import primeqa.test_representation``,
:data:`default_registry` holds an entry for every body kind
shipped to date — currently 16 ``(kind, body_schema_version)``
pairs spanning conditions, the four data-behavior claim bodies,
the five trigger bodies, the execution-environment body, and the
five recipe bodies.

Consumers (S3 / S4 / S6 / S8 + the Coordinator) import what they
need from this package's top-level namespace. The deeper module
paths remain available for code that needs only a single body or
sub-archetype.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

from primeqa.test_representation.errors import (
    AuthorityViolationError,
    BodyCorruptionError,
    FloatInIdentityBearingContentError,
    OntologyViolationError,
    SchemaIncompatibilityError,
    SubstrateError,
    UnmarkedArraySemanticsError,
    UnresolvedSemanticProjectionError,
)

# ---------------------------------------------------------------------------
# Common types — base classes, sentinel markers, type aliases
# ---------------------------------------------------------------------------

from primeqa.test_representation.models.common import (
    ArraySemantics,
    BodyBase,
    EntityId,
    IdentityHash,
    ProjectionField,
    RecipeId,
    SemanticField,
    TestId,
)

# ---------------------------------------------------------------------------
# References + the OperationalRef union + helper
# ---------------------------------------------------------------------------

from primeqa.test_representation.models.references import (
    IdentityBearingRef,
    LogicalRef,
    OperationalRef,
    PinnedRef,
    is_identity_bearing,
)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

from primeqa.test_representation.models.registry import (
    BodyRegistry,
    default_registry,
    get_body_model,
    get_latest_body_version,
    register_body,
)

# ---------------------------------------------------------------------------
# Primitives shared across body shapes
# ---------------------------------------------------------------------------

from primeqa.test_representation.models.primitives import (
    AssertionPredicate,
    BlockedOperationEffect,
    EffectDescriptor,
    EventDescriptor,
    ExpectedValue,
    FieldChangeEffect,
    LiteralValue,
    NullValue,
    RejectionSignal,
    SideEffect,
    StateDescriptor,
)

# ---------------------------------------------------------------------------
# Conditions body (B-β) — identity-bearing, archetype-independent
# ---------------------------------------------------------------------------

from primeqa.test_representation.models.conditions import (
    Condition,
    SemanticConditionsBody,
)

# ---------------------------------------------------------------------------
# Claim bodies (B-β; future archetypes extend) + cross-archetype union
# ---------------------------------------------------------------------------

from primeqa.test_representation.models.claims import (
    AutomationEffectClaimBody,
    ClaimBody,
    DataBehaviorClaimBody,
    ProhibitionClaimBody,
    StateTransitionClaimBody,
    ValueClaimBody,
)

# ---------------------------------------------------------------------------
# Trigger bodies (B-γ) + per-track and cross-track unions
# ---------------------------------------------------------------------------

from primeqa.test_representation.models.triggers import (
    ConfigurationTriggerBody,
    DataMutationTriggerBody,
    InboundTriggerBody,
    TimeTriggerBody,
    TriggerBody,
    UITriggerBody,
)

# Step-level descriptors that appear inside trigger bodies and are
# part of the substrate's public surface.
from primeqa.test_representation.models.triggers.configuration import (
    ConfigurationChange,
)
from primeqa.test_representation.models.triggers.inbound import (
    PayloadDescriptor,
)
from primeqa.test_representation.models.triggers.time import (
    TimePredicate,
)
from primeqa.test_representation.models.triggers.ui import (
    PageContext,
)

# ---------------------------------------------------------------------------
# Recipe bodies (B-δ) + observation-realization union
# ---------------------------------------------------------------------------

from primeqa.test_representation.models.recipes import (
    CalloutInterceptRecipeBody,
    DataRecipeBody,
    EventSubscriptionRecipeBody,
    MetadataRecipeBody,
    ObservationRealizationBody,
    UIRecipeBody,
)

from primeqa.test_representation.models.recipes.callout_intercept import (
    MockResponse,
)

# ---------------------------------------------------------------------------
# Execution-environment body (B-δ) + assumption shapes
# ---------------------------------------------------------------------------

from primeqa.test_representation.models.environment import (
    AuthAssumption,
    ExecutionEnvironmentBody,
    FeatureAssumption,
    InfrastructureAssumption,
)

# ---------------------------------------------------------------------------
# Canonicalization + identity_hash (Track C)
#
# Imports are ordered so the canonicalization core loads first; the
# identity_hash module depends on it. The ``canonicalizers`` package
# import then triggers registration of the state-transition and
# automation-effect custom canonicalizers as a side effect.
# ---------------------------------------------------------------------------

from primeqa.test_representation.canonicalization import (
    canonical_serialize,
    canonicalize,
)
from primeqa.test_representation.identity_hash import (
    IDENTITY_HASH_VERSION,
    compute_identity_hash,
)
from primeqa.test_representation.canonicalizers import (
    CanonicalizerRegistry,
    default_canonicalizer_registry,
    register_canonicalizer,
)

# ---------------------------------------------------------------------------
# SQLAlchemy table models (Track D-α)
#
# These are the Python-side projection of the substrate-2 schema. They
# share the project-wide ``Base`` (from ``primeqa.db``) per substrate-1
# convention. Importing this package adds the six tables to
# ``Base.metadata`` as a side effect — consumers that build queries
# against the substrate-2 tables can import the classes directly from
# the top level.
# ---------------------------------------------------------------------------

from primeqa.test_representation.models_db import (
    TestClaim,
    TestClaimCoverage,
    TestProvenance,
    TestRecipe,
    TestRecipeRuntimeState,
    TestRequirementLink,
)

# ---------------------------------------------------------------------------
# Coordinator + write-flow foundational helpers (Track D-β.1)
#
# Coverage extraction, authority model, and the Semantic Transaction
# Coordinator class skeleton. The write methods on the Coordinator are
# stubs in D-β.1; D-β.2 and D-β.3 implement write_claim and
# write_recipe respectively.
# ---------------------------------------------------------------------------

from primeqa.test_representation.authority import (
    ActorKind,
    AuthorityDecision,
    check_claim_write_authority,
    check_recipe_write_authority,
    enforce_authority,
)
from primeqa.test_representation.coverage import (
    CoverageEntry,
    extract_coverage,
)
from primeqa.test_representation.coordinator import (
    ClaimRead,
    CoverageMatch,
    RecipeRead,
    RequirementMatch,
    SemanticTransactionCoordinator,
    WriteClaimResult,
    WriteRecipeResult,
)


__all__ = [
    # ----- Errors -----
    "AuthorityViolationError",
    "BodyCorruptionError",
    "FloatInIdentityBearingContentError",
    "OntologyViolationError",
    "SchemaIncompatibilityError",
    "SubstrateError",
    "UnmarkedArraySemanticsError",
    "UnresolvedSemanticProjectionError",
    # ----- Common types -----
    "ArraySemantics",
    "BodyBase",
    "EntityId",
    "IdentityHash",
    "ProjectionField",
    "RecipeId",
    "SemanticField",
    "TestId",
    # ----- References -----
    "IdentityBearingRef",
    "LogicalRef",
    "OperationalRef",
    "PinnedRef",
    "is_identity_bearing",
    # ----- Registry -----
    "BodyRegistry",
    "default_registry",
    "get_body_model",
    "get_latest_body_version",
    "register_body",
    # ----- Primitives -----
    "AssertionPredicate",
    "BlockedOperationEffect",
    "EffectDescriptor",
    "EventDescriptor",
    "ExpectedValue",
    "FieldChangeEffect",
    "LiteralValue",
    "NullValue",
    "RejectionSignal",
    "SideEffect",
    "StateDescriptor",
    # ----- Conditions -----
    "Condition",
    "SemanticConditionsBody",
    # ----- Claim bodies + unions -----
    "AutomationEffectClaimBody",
    "ClaimBody",
    "DataBehaviorClaimBody",
    "ProhibitionClaimBody",
    "StateTransitionClaimBody",
    "ValueClaimBody",
    # ----- Trigger bodies + union + step descriptors -----
    "ConfigurationChange",
    "ConfigurationTriggerBody",
    "DataMutationTriggerBody",
    "InboundTriggerBody",
    "PageContext",
    "PayloadDescriptor",
    "TimePredicate",
    "TimeTriggerBody",
    "TriggerBody",
    "UITriggerBody",
    # ----- Recipe bodies + union + descriptors -----
    "CalloutInterceptRecipeBody",
    "DataRecipeBody",
    "EventSubscriptionRecipeBody",
    "MetadataRecipeBody",
    "MockResponse",
    "ObservationRealizationBody",
    "UIRecipeBody",
    # ----- Execution environment + assumption shapes -----
    "AuthAssumption",
    "ExecutionEnvironmentBody",
    "FeatureAssumption",
    "InfrastructureAssumption",
    # ----- Canonicalization + identity_hash (Track C) -----
    "CanonicalizerRegistry",
    "IDENTITY_HASH_VERSION",
    "canonical_serialize",
    "canonicalize",
    "compute_identity_hash",
    "default_canonicalizer_registry",
    "register_canonicalizer",
    # ----- SQLAlchemy table models (Track D-α) -----
    "TestClaim",
    "TestClaimCoverage",
    "TestProvenance",
    "TestRecipe",
    "TestRecipeRuntimeState",
    "TestRequirementLink",
    # ----- Coordinator + write-flow helpers (Track D-β.1) -----
    "ActorKind",
    "AuthorityDecision",
    "ClaimRead",
    "CoverageEntry",
    "CoverageMatch",
    "RecipeRead",
    "RequirementMatch",
    "SemanticTransactionCoordinator",
    "WriteClaimResult",
    "WriteRecipeResult",
    "check_claim_write_authority",
    "check_recipe_write_authority",
    "enforce_authority",
    "extract_coverage",
]
