"""Substrate-3 production-integration intake (D-106.4, slice 2).

Assembles the :class:`GenerationRequest` the queue consumer (slice 3) hands to
``run_generation``, from per-tenant inputs.

S3 intake is **caller-fed**: ``GenerationRequest.requirement_refs`` is
caller-supplied ``{key, text}`` by design — the substrate *receives* requirement
text, it does not fetch it. Requirement-text resolution from the v1
``public.requirements`` table lives at the enqueue boundary (slice 4, v1-side),
NOT here — honoring the substrate contract and Fork A's anti-coupling rule (no
S3 → v1 schema dependency).

This slice is resolver + assembler ONLY — no requirement read (slice 4), no
enqueue (slice 4), no consumer (slice 3), no ``run_generation`` invocation.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import text

from primeqa.generation.jobs import GenerationJob, GenerationJobStore
from primeqa.generation.protocol import (
    GenerationRequest,
    GovernanceContext,
    OperationalContext,
    RequestId,
    SemanticContext,
)
from primeqa.semantic.connection import get_tenant_connection
from primeqa.semantic.query import SemanticOrgModel
from primeqa.sync.credentials import resolve_connected_org_or_raise


def resolve_current_s1_version(tenant_id: int, environment_id: int) -> tuple[int, str]:
    """The run's S1 snapshot to pin against: the latest ``logical_version`` **of
    the env's org** (``MAX(version_seq)`` scoped to ``connected_org_id``) and its
    name.

    D-286: org-scoped (mirrors the entity reads in ``run_generation``). On a
    two-org tenant the tenant-wide MAX belongs to whichever org synced last, so a
    tenant-wide pin would read the OTHER org's seq — pinning and reads MUST be the
    same org or the entity reads land at a version the org never synced. Resolves
    env→org fail-loud (the shared resolver raises if unprovisioned).

    Raises ``VersionNotFoundError`` (via
    :meth:`SemanticOrgModel.current_version_seq`) when the org has no S1 version —
    fail-loud, since there is no org snapshot to generate against.
    """
    with get_tenant_connection(tenant_id) as conn:
        org_id = resolve_connected_org_or_raise(conn, environment_id)
        seq = SemanticOrgModel(conn, connected_org_id=org_id).current_version_seq()
        name = conn.execute(
            text("SELECT version_name FROM logical_versions WHERE version_seq = :seq"),
            {"seq": seq},
        ).scalar()
        return seq, name


def build_generation_request(
    *,
    requirement_ref: dict[str, Any],
    s1_version_seq: int,
    s1_version_name: Optional[str],
    request_id: RequestId,
) -> GenerationRequest:
    """Pure assembly of a **fresh single-requirement** ``GenerationRequest`` in
    the shape ``run_generation`` accepts.

    The caller supplies ``request_id`` (minted by the job's ``start_attempt``,
    D-106.4 .B) — intake does NOT mint it. A fresh request carries no lineage
    (``prior_request_id`` / ``deltas`` / ``regeneration_kind`` all ``None``), so
    it is CHECK-valid against ``generation_requests``'s
    ``(prior_request_id IS NULL) = (deltas IS NULL)``. ``archetype_hint`` is left
    unset (the router defaults to Opus, D-106.2); a later slice may set it.
    """
    return GenerationRequest(
        request_id=request_id,
        semantic_context=SemanticContext(
            requirement_refs=[requirement_ref],
            s1_version_seq=s1_version_seq,
            s1_version_name=s1_version_name,
        ),
        governance_context=GovernanceContext(),
        operational_context=OperationalContext(),
    )


def enqueue_s3_generation(
    *, tenant_id: int, requirement_ref: dict[str, Any], environment_id: int,
    created_by: Optional[int] = None,
) -> GenerationJob:
    """Pin a **resolved** requirement + the tenant's current S1 snapshot into a
    queued job (D-106.4 slice 4, substrate side). **Caller-fed** (option B): takes
    a resolved ``requirement_ref`` ``{key, text}`` (the v1 read happens route-side)
    and a route-validated ``environment_id`` — no v1 read here. Resolves the
    current ``s1_version`` (``MAX(version_seq)``; fail-loud if the tenant has no
    S1 version) and get-or-creates the job. Idempotent on
    ``(requirement_key, s1_version_seq)``."""
    seq, name = resolve_current_s1_version(tenant_id, environment_id)  # D-286: org-scoped pin
    return GenerationJobStore(tenant_id).create_or_get_job(
        requirement_key=requirement_ref["key"],
        s1_version_seq=seq,
        s1_version_name=name,
        created_by=created_by,
        requirement_text=requirement_ref.get("text"),
        environment_id=environment_id,
    )
