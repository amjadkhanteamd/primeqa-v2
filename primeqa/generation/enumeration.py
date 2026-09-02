"""S3 deterministic conformance enumeration (LLD 3A-3 §b/§c; HLD DE-05).

The cross product — active rules from a PINNED catalogue release (D-461's
pin object) × one inventory version × persona scope — with ZERO LLM
involvement: this IS the deterministic-before-LLM principle applied per
kind (the ENUMERATED_ONLY vocabulary exception, LLD 3A-2 §d.11).

Refusals are enumerated and fail-loud (never a silent skip or an empty
success): surface outside the inventory, unpinned release, stale release
(a member rule no longer ACTIVE at its recorded version — cut a new
catalogue release), empty cross product, unknown capability.

Idempotence: identity = plimsol_rule_id × the frozen surface natural key,
so re-enumeration yields the same identity hashes; the writer resolves
equivalents via ``query_equivalent_claims`` (the D-339 persister posture
— deprecated rows excluded) and the same-hash S3 regeneration no-ops.
A re-run reports ``{created: 0, existing: N}``.

Applicability (§c) is computed HERE, per (rule × surface), from rule
capability — the worker never decides applicability (D-460 boundary);
it is recorded on the claim_set member row. AUTO_WITH_ACTION rules
enumerate as claims but mark NOT executable until Mode B — honest,
visible, never dropped.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from primeqa.knowledge import rule_registry
from primeqa.test_representation import claim_sets
from primeqa.test_representation.coordinator import (
    SemanticTransactionCoordinator,
)
from primeqa.test_representation.identity_hash import (
    IDENTITY_HASH_VERSION,
    compute_identity_hash,
)
from primeqa.test_representation.models.claims.ui.conformance_claim import (
    ConformanceClaimBody,
)
from primeqa.test_representation.models.conditions import (
    SemanticConditionsBody,
)
from primeqa.test_representation.models.environment import (
    ExecutionEnvironmentBody,
)
from primeqa.test_representation.models.recipes.ui_inspection import (
    UiInspectionBody,
)
from primeqa.test_representation.models.surface import SurfaceNaturalKey
from primeqa.test_representation.models.triggers.inspection import (
    InspectionTriggerBody,
)

APPLICABLE = "APPLICABLE"
NOT_APPLICABLE = "NOT_APPLICABLE"
HUMAN_REVIEW = "HUMAN_REVIEW"

STALE_RELEASE_MSG = (
    "stale release — rule {rule_id} is recorded at version {recorded} but "
    "its current ACTIVE version is {current}; cut a new catalogue release "
    "(enumerating history is not allowed: releases pin INTERPRETATION, "
    "new enumeration requires a current release)"
)


class EnumerationRefusal(ValueError):
    """A refused enumeration — the message names the exact cause."""


def applicability_for(capability: str) -> tuple[str, bool]:
    """(applicability, executable) per rule capability — fail-closed on
    unknown values, mirroring RECIPE_MODES (D6). NOT_APPLICABLE is
    reserved for surface-metadata contradictions once rules carry the
    markers (LLD 3A-3 §c); no capability maps to it today."""
    if capability == "AUTO":
        return (APPLICABLE, True)
    if capability == "AUTO_WITH_ACTION":
        return (APPLICABLE, False)      # NOT executable until Mode B
    if capability in ("HUMAN_WITH_CANDIDATE", "HUMAN_ONLY"):
        return (HUMAN_REVIEW, False)
    raise EnumerationRefusal(
        f"unknown automation_capability {capability!r} — undeclared "
        "capabilities are refused, never inferred")


def enumerate_claims(
    session: Session,
    *,
    catalogue_release_id: int,
    inventory_version: int,
    persona_scope: str,
    created_by: int,
    standard_profile: str = "WCAG22",
    surface_keys: Optional[list[str]] = None,
) -> dict:
    """Run the deterministic cross product and record a DRAFT claim_set.

    ``surface_keys`` optionally restricts to an explicit subset — every
    listed key must be a recorded member of the inventory version at the
    persona scope, or the whole enumeration refuses (naming the key).

    Returns ``{claim_set_id, created, existing, members,
    applicability_counts}``. Claims are idempotent across runs (identity
    -hash no-op); each RUN records its own draft claim_set — enumeration
    is an act with a recorded output, and the human approves one set.
    """
    # --- pin 1: the catalogue release -------------------------------
    release = rule_registry.release(session, catalogue_release_id)
    if release is None:
        raise EnumerationRefusal(
            f"unpinned release — catalogue release {catalogue_release_id} "
            "does not exist; enumeration requires an existing release id "
            "(D-461 pin), never 'current rules'")

    # --- stale-release check (refusal 3) ----------------------------
    rules: list = []
    for rule_id, recorded_version in release["members"]:
        current = rule_registry.rule(session, rule_id)  # ACTIVE version
        if current is None or current.version != recorded_version:
            raise EnumerationRefusal(STALE_RELEASE_MSG.format(
                rule_id=rule_id, recorded=recorded_version,
                current=(current.version if current else "none (RETIRED)")))
        rules.append(current)

    # --- the tenant union (Phase 5 §g, D-281) ------------------------
    # A tenant's release is the platform release's members PLUS the
    # tenant's custom rules RECORDED for it at cut time. No record ->
    # pure platform enumeration, unchanged. A stale recorded version
    # refuses, exactly as the platform staleness law above does.
    from collections import namedtuple

    from primeqa.knowledge.cust_authoring import tenant_release_members
    _CustRule = namedtuple("_CustRule",
                           "rule_id version automation_capability")
    for cm in tenant_release_members(session, catalogue_release_id):
        rules.append(_CustRule(cm["rule_id"], cm["version"],
                               cm["automation_capability"]))

    # --- pin 2: the inventory version -------------------------------
    members = claim_sets.inventory_members(session, inventory_version)
    surfaces = [m for m in members if m["persona_scope"] == persona_scope]
    if surface_keys is not None:
        recorded = {m["surface_key"] for m in surfaces}
        for key in surface_keys:
            if key not in recorded:
                raise EnumerationRefusal(
                    f"surface outside inventory — {key!r} is not a "
                    f"recorded member of inventory version "
                    f"{inventory_version} at persona {persona_scope!r}")
        surfaces = [m for m in surfaces if m["surface_key"] in
                    set(surface_keys)]

    # --- refusal 4: empty cross product -----------------------------
    if not rules or not surfaces:
        raise EnumerationRefusal(
            f"empty cross product — release {catalogue_release_id} has "
            f"{len(rules)} active rules and inventory version "
            f"{inventory_version} has {len(surfaces)} surfaces at persona "
            f"{persona_scope!r}; an empty enumeration is never a success")

    # --- the cross product ------------------------------------------
    coord = SemanticTransactionCoordinator()
    conditions = SemanticConditionsBody()
    created = 0
    existing = 0
    set_members: list[dict] = []
    applicability_counts: dict[str, int] = {}
    for rule in rules:
        applicability, executable = applicability_for(
            rule.automation_capability)
        for m in surfaces:
            surface = SurfaceNaturalKey(
                site=m["site"], path=m["path"],
                persona_scope=m["persona_scope"],
                record_context_ref=m["record_context_ref"],
                viewport=m["viewport"],
            )
            body = ConformanceClaimBody(
                plimsol_rule_id=rule.rule_id, surface=surface)
            new_hash = compute_identity_hash(
                "ui", "conformance-claim", body, conditions)
            equivalent = [
                c for c in coord.query_equivalent_claims(
                    session, identity_hash=new_hash,
                    identity_hash_version=IDENTITY_HASH_VERSION)
                if c.status != "deprecated"]
            existing_test_id = (
                equivalent[0].test_id if equivalent else None)
            cr = coord.write_claim(
                session, actor="s3", test_id=existing_test_id,
                archetype="ui", claim_kind="conformance-claim",
                asserted_truth=body, semantic_conditions=conditions)
            if cr.was_noop:
                existing += 1
            else:
                created += 1
                coord.write_recipe(
                    session, actor="s3", recipe_id=None,
                    claim_test_id=cr.test_id,
                    trigger_kind="inspection-trigger",
                    recipe_kind="ui-inspection",
                    causal_initiation=InspectionTriggerBody(),
                    observation_realization=UiInspectionBody(
                        surface=surface),
                    execution_environment=ExecutionEnvironmentBody(),
                    claim_version_seq=cr.version_seq)
            set_members.append({
                "test_id": cr.test_id,
                "applicability": applicability,
                "executable": executable,
            })
            applicability_counts[applicability] = (
                applicability_counts.get(applicability, 0) + 1)

    claim_set_id = claim_sets.create_claim_set(
        session,
        persona_scope=persona_scope,
        inventory_version=inventory_version,
        catalogue_release_id=catalogue_release_id,
        created_by=created_by,
        members=set_members,
        standard_profile=standard_profile,
    )
    return {
        "claim_set_id": claim_set_id,
        "created": created,
        "existing": existing,
        "members": len(set_members),
        "applicability_counts": applicability_counts,
    }
