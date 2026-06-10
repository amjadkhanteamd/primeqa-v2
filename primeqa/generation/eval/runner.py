"""Deterministic eval runner (D-102.1) — corpus-driven generalization of the
per-vertical integration machinery (scripted ``FakeToolTurn`` + the seeded S1
fixture + governed-outcome assertions).

For each golden case the harness seeds the case's S1 fixture into a fresh
logical_version, runs the generation engine with the case's scripted tool-turns
(a fixed intent ⇒ a deterministic governed outcome), and scores the outcome
against the case's expected governed properties — never LLM phrasing (D-090(f)).
``replay_stable`` runs a case twice as the two-invariant replay net (D-090(d)):
``explanation_hash`` stable across runs (transparency continuity); the emitted
claim's identity stable (a same-version re-run dedups — semantic continuity).

Operates over ``get_tenant_connection`` against whatever ``DATABASE_URL`` points
at — an eval/test DB. It SEEDS S1 fixtures; it does not clean (the caller's test
harness owns ledger/S2 reset). Not for production tenants.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import text

from primeqa.generation.governance_core import GovernanceCore
from primeqa.generation.persistence import LedgerPersister
from primeqa.generation.protocol import (
    GenerationRequest,
    GovernanceContext,
    OperationalContext,
    SemanticContext,
)
from primeqa.generation.runtime import GenerationRuntime
from primeqa.semantic.connection import get_tenant_connection
from primeqa.semantic.query import SemanticOrgModel


# ---------------------------------------------------------------------------
# Scripted tool-turn (the fixed intent) — generalizes conftest FakeToolTurn
# ---------------------------------------------------------------------------

class _Turn:
    def __init__(self, tool: str, inp: dict):
        self.content_blocks = [{
            "type": "tool_use", "id": f"tu_{uuid4().hex[:6]}",
            "name": tool, "input": inp,
        }]
        self.input_tokens = 10
        self.output_tokens = 5
        self.model = "eval-deterministic"
        self.stop_reason = "tool_use"
        self.latency_ms = 1


class _ScriptedToolTurnFn:
    """Returns the case's scripted turns in order. ``repeat_last`` re-serves the
    final turn after exhaustion — used to drive a Layer-A correction loop to
    exhaustion (structural-validation-failure) with a single bad turn."""

    def __init__(self, turns: list[dict], *, repeat_last: bool = False):
        self._turns = [_Turn(t["tool"], t["input"]) for t in turns]
        self._repeat_last = repeat_last
        self.calls = 0

    def __call__(self, *, messages, tools, tool_choice, system):
        i = self.calls
        self.calls += 1
        if i < len(self._turns):
            return self._turns[i]
        if self._repeat_last and self._turns:
            return self._turns[-1]
        raise AssertionError(f"eval scripted turns exhausted at call {i}")


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass
class CaseResult:
    case_id: str
    category: str
    passed: bool
    mismatches: list[str] = field(default_factory=list)
    # governed surface (for the aggregator report)
    outcome_kind: Optional[str] = None
    archetype: Optional[str] = None
    claim_kind: Optional[str] = None
    admissibility_layer: Optional[str] = None
    caveat_kind: Optional[str] = None
    refusal_kind: Optional[str] = None


@dataclass
class StabilityResult:
    case_id: str
    explanation_stable: bool
    identity_stable: bool   # N/A for refusals -> reported True


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

class EvalHarness:
    def __init__(self, tenant_id: int):
        self._tenant_id = tenant_id

    # -- S1 fixture seeding (fresh logical_version per case) ------------
    def _seed(self, conn, fixture: dict) -> int:
        vseq = conn.execute(text(
            "INSERT INTO logical_versions (version_name, version_type) "
            "VALUES (:n,'manual_checkpoint') RETURNING version_seq"
        ), {"n": f"eval_{uuid4().hex[:8]}"}).scalar()
        # Per-case S1 isolation, bitemporally + non-destructively: close every
        # currently-open entity/edge AT this version, so exact-match grounding
        # at `vseq` sees ONLY this case's fixture. Rows stay valid at EARLIER
        # versions (a co-resident shared `seeded` org at its low v1 is
        # unaffected), so this never disturbs other tests' fixtures.
        conn.execute(text(
            "UPDATE edges SET valid_to_seq = :v WHERE valid_to_seq IS NULL"), {"v": vseq})
        conn.execute(text(
            "UPDATE entities SET valid_to_seq = :v WHERE valid_to_seq IS NULL"), {"v": vseq})
        name_to_id: dict[str, Any] = {}
        for ent in fixture.get("entities", []):
            # attributes default to {} but a fixture may carry e.g. a VR's
            # formula_text (D-107 verified-negative probe).
            eid = conn.execute(text(
                "INSERT INTO entities (entity_type, sf_id, sf_api_name, display_name, "
                "attributes, valid_from_seq, valid_to_seq, last_synced_at) "
                "VALUES (:et,NULL,:api,:api,CAST(:attrs AS jsonb),:vf,NULL,NOW()) RETURNING id"
            ), {"et": ent["entity_type"], "api": ent["sf_api_name"], "vf": vseq,
                "attrs": json.dumps(ent.get("attributes") or {})}).scalar()
            name_to_id[ent["sf_api_name"]] = eid
        for edge in fixture.get("edges", []):
            # properties default to {} but a fixture may carry edge properties
            # (e.g. a GRANTS_FIELD_ACCESS edge's can_edit bit — D-125 capability
            # probe), mirroring the entity-attributes read above.
            conn.execute(text(
                "INSERT INTO edges (source_entity_id, target_entity_id, edge_type, "
                "edge_category, properties, valid_from_seq, valid_to_seq) "
                "VALUES (CAST(:s AS uuid),CAST(:t AS uuid),:et,:ec,CAST(:props AS jsonb),:vf,NULL)"
            ), {"s": str(name_to_id[edge["source"]]), "t": str(name_to_id[edge["target"]]),
                "et": edge["edge_type"], "ec": edge.get("edge_category", "BEHAVIOR"),
                "props": json.dumps(edge.get("properties") or {}), "vf": vseq})
        return int(vseq)

    def _make_request(self, vseq: int, *, requirement_text: str = "the requirement",
                      prompt_version: Optional[str] = None) -> GenerationRequest:
        return GenerationRequest(
            request_id=uuid4(),
            semantic_context=SemanticContext(
                requirement_refs=[{"key": "R0", "text": requirement_text}],
                s1_version_seq=vseq, s1_version_name="eval",
            ),
            governance_context=GovernanceContext(),
            operational_context=OperationalContext(prompt_template_version=prompt_version),
        )

    def _run_once(self, case, vseq: int):
        """Run the case's scripted turns once against an already-seeded version.
        Returns the RequirementResult (outcome + emission(s))."""
        req = self._make_request(vseq)
        fn = _ScriptedToolTurnFn(case.turns, repeat_last=case.repeat_last_on_exhaust)
        with get_tenant_connection(self._tenant_id) as conn:
            gov = GovernanceCore(SemanticOrgModel(conn))
            result = GenerationRuntime().run(
                request=req, seam=gov, tool_turn_fn=fn,
                persister=LedgerPersister(self._tenant_id),
            )
        return result.results[0]

    # -- public: score one case ----------------------------------------
    def run_case(self, case) -> CaseResult:
        with get_tenant_connection(self._tenant_id) as conn:
            vseq = self._seed(conn, case.fixture)
        r = self._run_once(case, vseq)
        if case.emit_twice:
            # dedup: re-run on the SAME version -> same identity_hash -> was_noop.
            r = self._run_once(case, vseq)
        return self._score(case, r.outcome, r.emission, r.emissions)

    # -- public: two-invariant replay stability ------------------------
    def replay_stable(self, case) -> StabilityResult:
        with get_tenant_connection(self._tenant_id) as conn:
            vseq = self._seed(conn, case.fixture)
        r1 = self._run_once(case, vseq)
        r2 = self._run_once(case, vseq)
        o1, o2 = r1.outcome, r2.outcome
        explanation_stable = (o1.explanation_hash == o2.explanation_hash
                              and len(o1.explanation_hash) == 64)
        # identity continuity (drafts): the same input re-emitted on the same
        # version dedups iff its identity_hash matched -> was_noop. D-207: a
        # multi-bundle draft must dedup EVERY bundle on the re-run.
        identity_stable = True
        if r1.emission is not None:
            expected = len(r1.emissions or [r1.emission])
            identity_stable = (o2.equivalent_existing is not None
                               and len(o2.equivalent_existing) == expected)
        return StabilityResult(case.id, explanation_stable, identity_stable)

    # -- scoring: governed properties only (never phrasing, D-090(f)) ---
    def _score(self, case, outcome, emission, emissions=None) -> CaseResult:
        e = case.expect
        m: list[str] = []

        def _eq(key, actual):
            if key in e and e[key] != actual:
                m.append(f"{key}: expected {e[key]!r}, got {actual!r}")

        _eq("outcome_kind", outcome.outcome_kind.value)
        _eq("admissibility_layer",
            outcome.admissibility_layer.value if outcome.admissibility_layer else None)
        _eq("caveat_required", outcome.caveat_required)
        _eq("caveat_kind", outcome.caveat_kind.value if outcome.caveat_kind else None)
        _eq("refusal_kind", outcome.refusal_kind.value if outcome.refusal_kind else None)
        if "cause" in e:
            cause = outcome.refusals[0].payload.get("cause") if outcome.refusals else None
            if cause != e["cause"]:
                m.append(f"cause: expected {e['cause']!r}, got {cause!r}")
        if "equivalent_existing" in e:
            got = outcome.equivalent_existing is not None
            if got != e["equivalent_existing"]:
                m.append(f"equivalent_existing: expected {e['equivalent_existing']}, got {got}")
        if "claim" in e:
            if emission is None:
                m.append("claim: expected an emitted claim, got none")
            else:
                for k, v in e["claim"].items():
                    got = getattr(emission, k, None)
                    if got != v:
                        m.append(f"claim.{k}: expected {v!r}, got {got!r}")
        if "recipe" in e:
            if emission is None:
                m.append("recipe: expected an emitted recipe, got none")
            else:
                for k, v in e["recipe"].items():
                    got = getattr(emission, k, None)
                    if got != v:
                        m.append(f"recipe.{k}: expected {v!r}, got {got!r}")
        if "claims" in e:
            # D-207: ordered multi-claim expectation — one entry per emitted
            # bundle, matched positionally on governed fields.
            got_emissions = emissions or ([] if emission is None else [emission])
            if len(got_emissions) != len(e["claims"]):
                m.append(f"claims: expected {len(e['claims'])} emitted claims, "
                         f"got {len(got_emissions)}")
            else:
                for i, (exp_claim, em) in enumerate(zip(e["claims"], got_emissions)):
                    for k, v in exp_claim.items():
                        got = getattr(em, k, None)
                        if got != v:
                            m.append(f"claims[{i}].{k}: expected {v!r}, got {got!r}")
        if "trigger_operation" in e:
            # The trigger body's operation — discriminates the D-203 2-step
            # update-rejected shape from the create-rejected fallback (both are
            # data-recipe / data-mutation-trigger, so kinds alone can't).
            got = getattr(getattr(emission, "causal_initiation", None),
                          "operation", None)
            if got != e["trigger_operation"]:
                m.append(f"trigger_operation: expected "
                         f"{e['trigger_operation']!r}, got {got!r}")

        return CaseResult(
            case_id=case.id, category=case.category, passed=not m, mismatches=m,
            outcome_kind=outcome.outcome_kind.value,
            archetype=getattr(emission, "archetype", None),
            claim_kind=getattr(emission, "claim_kind", None),
            admissibility_layer=(outcome.admissibility_layer.value
                                 if outcome.admissibility_layer else None),
            caveat_kind=outcome.caveat_kind.value if outcome.caveat_kind else None,
            refusal_kind=outcome.refusal_kind.value if outcome.refusal_kind else None,
        )

    def run_corpus(self, cases) -> list[CaseResult]:
        return [self.run_case(c) for c in cases]

    # -- live mode (D-104): real LLM proposes from requirement_text ----
    def run_case_live(self, case, *, tool_turn_fn, prompt_version=None,
                      model: str = "(live)"):
        """Run a live-eligible probe: the real LLM (via ``tool_turn_fn``)
        proposes from the case's ``requirement_text`` — no scripted turns — and
        the result is adjudicated against the case's semantic envelope (D-104).
        Returns a ``scorer.LiveResult``. Not persisted (the live run tests
        adjudication, not the ledger)."""
        from primeqa.generation.eval.scorer import observe, score_live
        from primeqa.generation.prompts import registry

        rt = case.live["requirement_text"]
        with get_tenant_connection(self._tenant_id) as conn:
            vseq = self._seed(conn, case.fixture)
        req = self._make_request(vseq, requirement_text=rt, prompt_version=prompt_version)
        with get_tenant_connection(self._tenant_id) as conn:
            gov = GovernanceCore(SemanticOrgModel(conn))
            result = GenerationRuntime().run(
                request=req, seam=gov, tool_turn_fn=tool_turn_fn, persister=None)
        r = result.results[0]
        observed = observe(r.outcome, r.emission, emissions=r.emissions)
        return score_live(case, observed,
                          prompt_version=prompt_version or registry.CURRENT, model=model)
