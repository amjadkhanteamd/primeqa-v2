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

from primeqa.semantic.connection import get_tenant_connection
from primeqa.generation.protocol import GenerationOutcome, GenerationRequest


def _j(obj: Any) -> Optional[str]:
    """JSON-encode for a JSONB column, or None."""
    return None if obj is None else json.dumps(obj)


class LedgerPersister:
    """Per-tenant ledger writer. Construct with the tenant id; the runtime
    calls :meth:`persist_request` once, then :meth:`persist_requirement_result`
    per requirement (each its own transaction)."""

    def __init__(self, tenant_id: int):
        self._tenant_id = tenant_id

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

    # -- generation_outcomes + llm_calls (per requirement, own txn) -----
    def persist_requirement_result(self, request: GenerationRequest, result: Any) -> None:
        """`result` is a runtime.RequirementResult (outcome + llm_calls)."""
        outcome: GenerationOutcome = result.outcome
        o = outcome.model_dump(mode="json")
        with get_tenant_connection(self._tenant_id) as conn:
            # 1. outcome row (FK -> generation_requests, must exist)
            conn.execute(text(
                "INSERT INTO generation_outcomes "
                "(outcome_id, request_id, requirement_ref, outcome_kind, "
                " claims_written, recipes_written, equivalent_existing, "
                " admissibility_layer, refusal_kind, refusal_policy_version, "
                " refusal_schema_version, refusals, attempted_interpretation, "
                " explanation_hash, dismissal_taxonomy_version) "
                "VALUES (CAST(:oid AS uuid), CAST(:rid AS uuid), "
                " CAST(:rref AS jsonb), CAST(:okind AS generation_outcome_kind), "
                " CAST(:cw AS jsonb), CAST(:rw AS jsonb), CAST(:ee AS jsonb), "
                " CAST(:alayer AS admissibility_layer), "
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
                "rkind": o["refusal_kind"],
                "rpv": o["refusal_policy_version"],
                "rsv": o["refusal_schema_version"],
                "refusals": _j(o["refusals"]),
                "ai": _j(o["attempted_interpretation"]),
                "eh": o["explanation_hash"],
                "dtv": o["dismissal_taxonomy_version"],
            })
            # 2. llm_calls (FK -> generation_outcomes.outcome_id, now present)
            for rec in result.llm_calls:
                conn.execute(text(
                    "INSERT INTO llm_calls "
                    "(call_id, generation_outcome_id, tool_name, raw_parameters, "
                    " raw_response, operational_outcome, attempt_index, "
                    " timing_start, timing_duration_ms, token_count_input, "
                    " token_count_output, model_identifier) "
                    "VALUES (CAST(:cid AS uuid), CAST(:oid AS uuid), :tool, "
                    " CAST(:rp AS jsonb), CAST(:rr AS jsonb), "
                    " CAST(:oo AS llm_call_outcome), :ai, :ts, :dur, :ti, :to, :model)"
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
                })
