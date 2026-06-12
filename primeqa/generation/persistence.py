"""Substrate-3 ledger persistence (D-096.6).

Turns the runtime's in-memory ``GenerationOutcome`` + ``LlmCallRecord``s into
Slice-0 ledger rows. Runtime-invoked; **per-requirement transaction boundary**
— each outcome is durable as it completes, so a later requirement failing does
not roll back an earlier persisted one (important for replay + budget-
exhaustion partial state). FK write order: ``generation_requests`` (once per
request, at batch start) -> per requirement ``generation_outcomes`` -> its
``llm_calls``.

Writes over a per-tenant connection (``get_tenant_connection``); the Slice-0
generation tables live in the per-tenant schema (no tenant_id column —
schema-only isolation), so inserts are unqualified under the search_path.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from primeqa.semantic.connection import get_tenant_connection
from primeqa.generation.protocol import (
    ClaimRef,
    GenerationOutcome,
    GenerationRequest,
    RecipeRef,
)
from primeqa.test_representation.coordinator import SemanticTransactionCoordinator
from primeqa.test_representation.identity_hash import (
    IDENTITY_HASH_VERSION,
    compute_identity_hash,
)


def _j(obj: Any) -> Optional[str]:
    """JSON-encode for a JSONB column, or None."""
    return None if obj is None else json.dumps(obj)


class LedgerPersister:
    """Per-tenant ledger writer. Construct with the tenant id; the runtime
    calls :meth:`persist_request` once, then :meth:`persist_requirement_result`
    per requirement (each its own transaction)."""

    def __init__(self, tenant_id: int):
        self._tenant_id = tenant_id
        self._coordinator = SemanticTransactionCoordinator()

    # -- generation_requests (once per request, FK target) --------------
    def persist_request(self, request: GenerationRequest) -> None:
        with get_tenant_connection(self._tenant_id) as conn:
            conn.execute(text(
                "INSERT INTO generation_requests "
                "(request_id, semantic_context, governance_context, "
                " operational_context, prior_request_id, deltas) "
                "VALUES (CAST(:rid AS uuid), CAST(:sc AS jsonb), "
                " CAST(:gc AS jsonb), CAST(:oc AS jsonb), "
                " CAST(:prior AS uuid), CAST(:deltas AS jsonb))"
            ), {
                "rid": str(request.request_id),
                "sc": _j(request.semantic_context.model_dump(mode="json")),
                "gc": _j(request.governance_context.model_dump(mode="json")),
                "oc": _j(request.operational_context.model_dump(mode="json")),
                "prior": str(request.prior_request_id) if request.prior_request_id else None,
                "deltas": _j(request.deltas),
            })

    # -- one semantic transaction: claim + recipe + ledger (D-097.4/D-099) --
    def persist_requirement_result(self, request: GenerationRequest, result: Any) -> None:
        """A draft is one semantic transaction (D-097.4 / D-099): the S2 claim +
        recipe AND the generation ledger rows write in a single Session bound to
        the tenant connection, committed once. The Coordinator flushes but never
        commits; the tenant connection's transaction (opened by
        ``get_tenant_connection``) owns commit — so a mid-emission failure rolls
        back the claim, recipe, AND ledger together. Refusals write the ledger
        only. Each call is its own transaction -> partial-failure isolation
        across requirements.

        ``result`` is a runtime.RequirementResult (outcome + llm_calls +
        optional emission(s))."""
        outcome: GenerationOutcome = result.outcome
        # D-207: a draft may carry N bundles (one per grounded intent). All
        # write in the SAME transaction — claims, recipes, links, ledger,
        # commit-once. Older single-bundle results fall back to ``emission``.
        emissions = list(getattr(result, "emissions", None) or [])
        if not emissions:
            single = getattr(result, "emission", None)
            if single is not None:
                emissions = [single]
        with get_tenant_connection(self._tenant_id) as conn:
            session = Session(bind=conn)
            try:
                # S2 first: write the claim + recipe so their refs exist before
                # the outcome row that records them (D-099).
                for emission in emissions:
                    self._write_emission(session, outcome, emission)
                self._insert_outcome(session, outcome)
                self._insert_llm_calls(session, outcome, result.llm_calls)
                session.flush()
            finally:
                session.close()
        # get_tenant_connection commits the transaction on clean exit (atomic).
        # Any exception above propagates and triggers its trans.rollback().

    # -- S2 emission (claim + recipe) in the caller's Session ---------------
    def _write_emission(self, session: Session, outcome: GenerationOutcome,
                        emission: Any) -> None:
        """Persist the substrate-authored bodies (authored in D-097.5) via the
        Coordinator, then stamp the resulting refs onto the outcome — they exist
        only post-write (D-099).

        Identity dedup: ``identity_hash`` is not unique, so a fresh
        ``test_id=None`` would mint a duplicate test. Instead look up a
        current claim with the same identity_hash and re-version *that* test —
        a same-hash S3 regeneration is a no-op (SPEC §7.7), so no duplicate is
        created and ``equivalent_existing`` records the match. On the no-op
        path the equivalent claim already carries its verification recipe, so
        a duplicate recipe is not minted."""
        new_hash = compute_identity_hash(
            emission.archetype, emission.claim_kind,
            emission.asserted_truth, emission.semantic_conditions,
        )
        equivalent = self._coordinator.query_equivalent_claims(
            session, identity_hash=new_hash,
            identity_hash_version=IDENTITY_HASH_VERSION,
        )
        existing_test_id = equivalent[0].test_id if equivalent else None

        cr = self._coordinator.write_claim(
            session, actor="s3", test_id=existing_test_id,
            archetype=emission.archetype, claim_kind=emission.claim_kind,
            asserted_truth=emission.asserted_truth,
            semantic_conditions=emission.semantic_conditions,
        )
        # D-207: append — one outcome accumulates refs across N bundles.
        outcome.claims_written = (outcome.claims_written or []) + [
            ClaimRef(test_id=cr.test_id, version_seq=cr.version_seq)]

        # D-166: record the generated_from requirement link — the documented-
        # intended S3 behaviour (link_requirement's docstring), previously unwired —
        # so list_tests_by_requirement resolves generated tests. Same atomic tx as
        # the claim; idempotent on its PK, so it is written on both the new-claim
        # and same-hash no-op paths. The key rides outcome.requirement_ref; a
        # missing/blank key skips the link rather than raising.
        req_key = (outcome.requirement_ref or {}).get("key")
        if req_key:
            self._coordinator.link_requirement(
                session, actor="s3", test_id=cr.test_id,
                external_system="jira", external_key=req_key,
                link_kind="generated_from",
            )

        if cr.was_noop:
            # Same-hash regeneration (SPEC §7.7): the equivalent test already
            # exists with its verification recipe — record it, mint nothing new.
            # D-207: append — sibling bundles in the same outcome still write.
            outcome.equivalent_existing = (outcome.equivalent_existing or []) + [cr.test_id]
            return

        rr = self._coordinator.write_recipe(
            session, actor="s3", recipe_id=None, claim_test_id=cr.test_id,
            trigger_kind=emission.trigger_kind, recipe_kind=emission.recipe_kind,
            causal_initiation=emission.causal_initiation,
            observation_realization=emission.observation_realization,
            execution_environment=emission.execution_environment,
            claim_version_seq=cr.version_seq,
        )
        outcome.recipes_written = (outcome.recipes_written or []) + [
            RecipeRef(recipe_id=rr.recipe_id, version_seq=rr.version_seq)]

        # D-228: secondary realizations of the SAME claim — each its own recipe
        # row (fresh recipe_id) under the one claim_test_id, in the SAME atomic
        # transaction. Identity is untouched (Option-C: recipes are outside the
        # hash). Only minted on the new-claim path — the same-hash no-op above
        # returns before reaching here, so an equivalent claim's existing recipe
        # set is never duplicated.
        for sec in getattr(emission, "secondary_recipes", ()) or ():
            sr = self._coordinator.write_recipe(
                session, actor="s3", recipe_id=None, claim_test_id=cr.test_id,
                trigger_kind=sec.trigger_kind, recipe_kind=sec.recipe_kind,
                causal_initiation=sec.causal_initiation,
                observation_realization=sec.observation_realization,
                execution_environment=sec.execution_environment,
                claim_version_seq=cr.version_seq,
                priority=sec.priority,
            )
            outcome.recipes_written = (outcome.recipes_written or []) + [
                RecipeRef(recipe_id=sr.recipe_id, version_seq=sr.version_seq)]

    # -- ledger rows (same Session / same transaction) ----------------------
    def _insert_outcome(self, session: Session, outcome: GenerationOutcome) -> None:
        o = outcome.model_dump(mode="json")
        session.execute(text(
            "INSERT INTO generation_outcomes "
            "(outcome_id, request_id, requirement_ref, outcome_kind, "
            " claims_written, recipes_written, equivalent_existing, "
            " admissibility_layer, caveat_required, caveat_kind, "
            " refusal_kind, refusal_policy_version, "
            " refusal_schema_version, refusals, attempted_interpretation, "
            " explanation_hash, dismissal_taxonomy_version) "
            "VALUES (CAST(:oid AS uuid), CAST(:rid AS uuid), "
            " CAST(:rref AS jsonb), CAST(:okind AS generation_outcome_kind), "
            " CAST(:cw AS jsonb), CAST(:rw AS jsonb), CAST(:ee AS jsonb), "
            " CAST(:alayer AS admissibility_layer), :creq, "
            " CAST(:ckind AS caveat_kind), "
            " CAST(:rkind AS refusal_kind), :rpv, :rsv, "
            " CAST(:refusals AS jsonb), CAST(:ai AS jsonb), :eh, :dtv)"
        ), {
            "oid": str(outcome.outcome_id),
            "rid": str(outcome.request_id),
            "rref": _j(o["requirement_ref"]),
            "okind": o["outcome_kind"],
            "cw": _j(o["claims_written"]),
            "rw": _j(o["recipes_written"]),
            "ee": _j(o["equivalent_existing"]),
            "alayer": o["admissibility_layer"],
            "creq": o["caveat_required"],
            "ckind": o["caveat_kind"],
            "rkind": o["refusal_kind"],
            "rpv": o["refusal_policy_version"],
            "rsv": o["refusal_schema_version"],
            "refusals": _j(o["refusals"]),
            "ai": _j(o["attempted_interpretation"]),
            "eh": o["explanation_hash"],
            "dtv": o["dismissal_taxonomy_version"],
        })

    def _insert_llm_calls(self, session: Session, outcome: GenerationOutcome,
                         calls: Any) -> None:
        for rec in calls:
            session.execute(text(
                "INSERT INTO llm_calls "
                "(call_id, generation_outcome_id, tool_name, raw_parameters, "
                " raw_response, operational_outcome, attempt_index, "
                " timing_start, timing_duration_ms, token_count_input, "
                " token_count_output, model_identifier, prompt_version) "
                "VALUES (CAST(:cid AS uuid), CAST(:oid AS uuid), :tool, "
                " CAST(:rp AS jsonb), CAST(:rr AS jsonb), "
                " CAST(:oo AS llm_call_outcome), :ai, :ts, :dur, :ti, :to, :model, :pv)"
            ), {
                "cid": str(rec.call_id),
                "oid": str(outcome.outcome_id),
                "tool": rec.tool_name,
                "rp": _j(rec.raw_parameters),
                "rr": _j(rec.raw_response),
                "oo": rec.operational_outcome.value,
                "ai": rec.attempt_index,
                "ts": None,
                "dur": rec.timing_duration_ms,
                "ti": rec.token_count_input,
                "to": rec.token_count_output,
                "model": rec.model_identifier,
                "pv": getattr(rec, "prompt_version", None),
            })
