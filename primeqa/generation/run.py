"""Production generation runner (D-106.1).

In-process orchestration that generalizes the live-eval binding to the
production path: route the model once per batch (``route_model``, D-106.2), bind
the routed ``gateway.tool_turn`` closure (or a test seam), open a tenant
connection for S1 reads, and drive the substrate-3 runtime with ledger
persistence ON.

This is the pure in-process core. The HTTP / worker layer that *wraps* it —
trigger / intake (Jira -> request building), auth, the async job queue, and
retry / idempotency (re-running an aborted batch conflicts on the ``request_id``
PK; fresh id per attempt or upsert is an API-layer strategy) — is the
production-integration phase (D-106.4), not this module. Holding the S1
connection across LLM latency is pilot-acceptable (keepalives + small batches);
flagged for scale (D-106.4).
"""
from __future__ import annotations

import logging
from typing import Optional

from primeqa.generation.gateway_binding import build_tool_turn_fn
from primeqa.generation.governance_core import GovernanceCore
from primeqa.generation.persistence import LedgerPersister
from primeqa.generation.protocol import GenerationRequest
from primeqa.generation.routing import route_model
from primeqa.generation.runtime import BatchResult, GenerationRuntime, ToolTurnFn
from primeqa.intelligence.llm.router import TenantPolicy
from primeqa.semantic.connection import get_tenant_connection
from primeqa.semantic.query import SemanticOrgModel
from primeqa.sync.credentials import resolve_connected_org_or_raise

log = logging.getLogger(__name__)

# The substrate-3 production generation task — the usage-accounting tag the
# gateway logs under (distinct from the eval's ``generation_live_eval``). Model
# selection is ``route_model``'s job (bound as ``model_override``), NOT this
# task's gateway ``_CHAINS`` entry (D-106.2 fork).
GENERATION_TASK = "generation"


def _requirement_key(request: GenerationRequest) -> Optional[str]:
    """The batch's requirement key for usage attribution (D-095.4: one
    conversation per requirement, and the production intake builds
    single-requirement requests). None when the refs are absent or unkeyed —
    attribution is best-effort telemetry and must never fail a run."""
    refs = request.semantic_context.requirement_refs or []
    first = refs[0] if refs else None
    return first.get("key") if isinstance(first, dict) else None


def run_generation(
    request: GenerationRequest, *, tenant_id: int, api_key: str,
    environment_id: int,
    tenant_policy: Optional[TenantPolicy] = None,
    tool_turn_fn: Optional[ToolTurnFn] = None,
    user_id: Optional[int] = None,
) -> BatchResult:
    """Run one generation batch end to end (D-106.1).

    Routes the model once (D-106.2), binds the routed gateway closure unless a
    ``tool_turn_fn`` seam is injected (the test path), and runs
    :class:`GenerationRuntime` over a :class:`GovernanceCore` with the
    :class:`LedgerPersister` wired (persistence ON).

    ``environment_id`` (D-286): the run's env, resolved to its ``connected_org``
    so S1 entity reads are **org-scoped** (mirrors execution's per-org D-260). A
    two-org tenant otherwise resolves every standard entity to >1 org →
    ambiguous-reference refusal for all generation. Fail-loud if env→org does not
    resolve (the shared resolver raises) — never an org-blind read.

    Error policy (D-106.3): abort-on-error with per-requirement-committed
    isolation (D-096.6). With D-105's refuse-not-crash, the remaining
    uncaught-error source is a provider ``LLMError`` — it aborts the batch, but
    requirements committed before it stay durable. Best-effort-continue is
    deferred (needs a runtime error hook).
    """
    model = route_model(request, tenant_policy)
    # No writeback to request.operational_context: route_model is pure-input and
    # the request's llm_model_identifier stays caller-intent. Model provenance is
    # llm_calls.model_identifier — the actual per-turn model (turn.model).
    fn = tool_turn_fn or build_tool_turn_fn(
        tenant_id=tenant_id, api_key=api_key, model=model, task=GENERATION_TASK,
        # Cross-reference attribution: environment_id reached this function (for
        # the D-286 org scoping below) but stopped here and never reached a
        # usage row; the request id and requirement key had no path out at all.
        user_id=user_id, environment_id=environment_id,
        request_id=request.request_id,
        requirement_key=_requirement_key(request))

    # The S1-read connection is held across the batch (D-106.4 pilot-acceptable);
    # LedgerPersister opens its own per-requirement transaction connections.
    with get_tenant_connection(tenant_id) as conn:
        # D-286: scope the batch's S1 reads to the run's org (fail-loud).
        org_id = resolve_connected_org_or_raise(conn, environment_id)
        s1_model = SemanticOrgModel(conn, connected_org_id=org_id)
        seam = GovernanceCore(s1_model)
        # D-378: hydrate the symbol table once per batch for the ORG FIELD
        # VOCABULARY block. Fail-soft — a failed hydration omits the block
        # (the message keeps its pre-v30 shape); it never fails the run.
        vocabulary_fn = None
        try:
            from primeqa.generation.vocabulary import build_field_vocabulary
            from primeqa.resolution.knowledge import S1KnowledgeSource
            _table = S1KnowledgeSource(s1_model).symbol_table(
                request.semantic_context.s1_version_seq)
            vocabulary_fn = (lambda req_text:
                             build_field_vocabulary(_table, req_text))
        except Exception:   # noqa: BLE001 — vocabulary must never break a run
            log.debug("D-378 vocabulary hydration failed (block omitted)",
                      exc_info=True)
        return GenerationRuntime().run(
            request=request, seam=seam, tool_turn_fn=fn,
            persister=LedgerPersister(tenant_id),
            vocabulary_fn=vocabulary_fn,
        )
