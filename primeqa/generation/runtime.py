"""Substrate-3 orchestration spine (D-095; the spine proposal).

The substrate starts running here — the constrained semantic orchestration
runtime (D-085). Per requirement it drives a tool-use conversation
(propose -> select -> emit), enforces the operational half of the Guardrail
boundary (all of Layer A; D-095.1), and drives toward exactly one
:class:`GenerationOutcome` (D-072 no-silent-drops). Governance (admissibility,
Layer B, refusal routing) lives behind :class:`GovernanceProvider`; the LLM
behind an injected ``tool_turn`` callable. This module contains NO governance
logic and makes NO real LLM/DB calls.

Key D-095 properties realized here:

  - **All of Layer A is spine-orchestrated and operational** (D-095.1). The
    grounding-free checks (``tools.validate_layer_a`` + forced-tool match +
    Guardrail-3 excerpt presence) run in the spine; the grounding-dependent
    S1-ref-existence check runs via ``seam.check_refs_exist`` (operational).
    Any Layer-A failure -> ``rejected_for_correction`` (a corrective
    ``tool_result``, an ``llm_calls`` row, attempt_index++); persistent ->
    ``structural-validation-failure``. None of this touches
    ``attempted_interpretation`` (D-088a) — the operational/semantic line.
  - **Phase choreography is orchestration policy** (D-095.3): a configurable
    :class:`PhasePolicy` forces the expected tool per phase. Not ontology.
  - **One conversation per requirement** (D-095.4); the batch's bounded shared
    interpretation context is computed once and injected — no hidden shared
    conversational state.

Out of scope this slice (deferred): real LLM (injected ``tool_turn``); DB
persistence (produces persistence-ready Slice-0 types); Layer B / admissibility
/ real S1 grounding / refusal-router internals (mocked seam); replay hashes;
real prompt management (minimal presenter only).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, Protocol
from uuid import UUID, uuid4

from primeqa.generation.enums import LlmCallOutcome, RefusalKind
from primeqa.generation.governance import (
    ConversationContext,
    GovernanceProvider,
    NextAction,
    RefusalDirective,
)
from primeqa.generation.protocol import BudgetSpec, GenerationOutcome, GenerationRequest
from primeqa.generation.prompts import registry as prompts_registry
from primeqa.generation import coverage as _coverage
from primeqa.generation import tools as _tools
from primeqa.generation.tools import TOOLS, TOOL_EMIT, TOOL_PROPOSE, TOOL_SELECT


# Default cap on Layer-A corrections within a single logical tool emission
# (one phase). Exceeding it -> structural-validation-failure (D-087/D-088a).
DEFAULT_MAX_CORRECTIONS = 3
# Defensive global cap on turns per requirement (prevents an unbounded loop if
# a misbehaving seam never terminates a phase).
DEFAULT_MAX_TURNS = 24


# ---------------------------------------------------------------------------
# Operational telemetry — persistence-ready llm_calls record (D-087)
# ---------------------------------------------------------------------------

@dataclass
class LlmCallRecord:
    """One ``llm_calls`` row, in memory (D-087 b). Operational telemetry, NOT
    semantic provenance. Persisted (with the FK to the outcome) by the
    governance-core slice; produced here as a persistence-ready structure."""

    call_id: UUID
    tool_name: str
    operational_outcome: LlmCallOutcome
    attempt_index: int
    model_identifier: str
    token_count_input: Optional[int] = None
    token_count_output: Optional[int] = None
    timing_duration_ms: Optional[int] = None
    raw_parameters: Optional[dict] = None
    raw_response: Optional[dict] = None
    prompt_version: Optional[str] = None   # resolved frozen prompt version (D-103.5)


# ---------------------------------------------------------------------------
# Tool-use block extraction (normalizes SDK objects or dicts)
# ---------------------------------------------------------------------------

@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict


def _battr(block: Any, key: str) -> Any:
    if isinstance(block, dict):
        return block.get(key)
    return getattr(block, key, None)


def extract_tool_uses(content_blocks: list) -> list[ToolUseBlock]:
    """Pull tool_use blocks from a turn's content (duck-typed: SDK objects or
    dicts). The spine reads ``id`` to key the next ``tool_result``."""
    out: list[ToolUseBlock] = []
    for b in content_blocks or []:
        if _battr(b, "type") == "tool_use":
            out.append(ToolUseBlock(
                id=_battr(b, "id"), name=_battr(b, "name"),
                input=_battr(b, "input") or {},
            ))
    return out


def _assistant_tool_use_msg(tu: ToolUseBlock) -> dict:
    return {"role": "assistant", "content": [
        {"type": "tool_use", "id": tu.id, "name": tu.name, "input": tu.input},
    ]}


def _user_tool_result_msg(tu: ToolUseBlock, content: str, is_error: bool = False) -> dict:
    block = {"type": "tool_result", "tool_use_id": tu.id, "content": content}
    if is_error:
        block["is_error"] = True
    return {"role": "user", "content": [block]}


# ---------------------------------------------------------------------------
# System prompt comes from the frozen prompt registry (D-089 / D-103). The
# version is resolved per-request in _run_requirement; the deterministic eval
# core + scripted-turn tests ignore ``system`` entirely (D-103.3), so the prompt
# is a quality input, never a correctness dependency.
# ---------------------------------------------------------------------------


def _initial_user_message(ctx: ConversationContext) -> dict:
    text = (
        f"Requirement: {ctx.requirement_text}\n"
        f"Shared interpretation context: {ctx.shared_context}\n"
        "Call propose_semantic_intent for this requirement."
    )
    return {"role": "user", "content": text}


# ---------------------------------------------------------------------------
# Phase choreography policy (D-095.3 — orchestration policy, not ontology)
# ---------------------------------------------------------------------------

class Phase(str, Enum):
    PROPOSE = "propose"
    SELECT = "select"
    EMIT = "emit"


_PHASE_TOOL = {
    Phase.PROPOSE: TOOL_PROPOSE,
    Phase.SELECT: TOOL_SELECT,
    Phase.EMIT: TOOL_EMIT,
}


@dataclass
class PhasePolicy:
    """Configurable per-phase tool forcing. Default: force the expected tool
    via ``tool_choice`` (substrate in control, D-085). Swap for a different
    control model without touching the spine."""

    force_per_phase: bool = True

    def expected_tool(self, phase: Phase) -> str:
        return _PHASE_TOOL[phase]

    def tool_choice(self, phase: Phase) -> Optional[dict]:
        if self.force_per_phase:
            return {"type": "tool", "name": _PHASE_TOOL[phase]}
        return None


# ---------------------------------------------------------------------------
# Budget (operational; per-request/batch — D-088c)
# ---------------------------------------------------------------------------

class BatchBudget:
    """Tracks token / time / tool-call consumption against the request's
    :class:`BudgetSpec`. Operational (spine-owned); exhaustion routes through
    the seam's refusal router."""

    def __init__(self, spec: Optional[BudgetSpec], clock: Callable[[], float] = time.monotonic):
        self._spec = spec or BudgetSpec()
        self._clock = clock
        self._start = clock()
        self.tokens = 0
        self.tool_calls = 0

    def consume(self, *, input_tokens: Optional[int], output_tokens: Optional[int]) -> None:
        self.tokens += (input_tokens or 0) + (output_tokens or 0)
        self.tool_calls += 1

    @property
    def elapsed_ms(self) -> int:
        return int((self._clock() - self._start) * 1000)

    def exceeded_dimension(self) -> Optional[str]:
        s = self._spec
        if s.token is not None and self.tokens >= s.token:
            return "token"
        if s.tool_call_count is not None and self.tool_calls >= s.tool_call_count:
            return "tool_call_count"
        if s.time_ms is not None and self.elapsed_ms >= s.time_ms:
            return "time"
        return None

    def limit_for(self, dimension: str) -> Optional[int]:
        return {"token": self._spec.token, "time": self._spec.time_ms,
                "tool_call_count": self._spec.tool_call_count}.get(dimension)

    def consumed_for(self, dimension: str) -> int:
        return {"token": self.tokens, "time": self.elapsed_ms,
                "tool_call_count": self.tool_calls}.get(dimension, 0)


# ---------------------------------------------------------------------------
# Inputs / results
# ---------------------------------------------------------------------------

@dataclass
class RequirementInput:
    """One requirement to resolve. ``ref`` is the external requirement
    reference (Slice-0 ``GenerationOutcome.requirement_ref`` shape)."""

    ref: dict[str, Any]
    text: str = ""


@dataclass
class BatchProgress:
    resolved: int
    total: int


@dataclass
class RequirementResult:
    outcome: GenerationOutcome
    llm_calls: list[LlmCallRecord]
    # Substrate-authored S2 bodies for a draft (an ``emission.EmissionBundle``;
    # Any to avoid an S2 import here). The persister writes claim + recipe +
    # ledger atomically (D-097.4 / D-099). None on refusal.
    # D-207: ``emissions`` carries ALL bundles (one per grounded intent);
    # ``emission`` is the first bundle for single-bundle readers.
    emission: Optional[Any] = None
    emissions: Optional[list] = None


@dataclass
class BatchResult:
    results: list[RequirementResult]


class ToolTurnFn(Protocol):
    """Narrow injected transport. The real wiring binds gateway.tool_turn's
    task/tenant_id/api_key/max_tokens; tests inject a fake. The return value is
    duck-typed: ``.content_blocks``, ``.input_tokens``, ``.output_tokens``,
    ``.model``, ``.stop_reason``, ``.latency_ms``."""

    def __call__(self, *, messages: list, tools: list, tool_choice: Optional[dict],
                 system: Optional[str]) -> Any: ...


class LedgerPersisterProtocol(Protocol):
    """Optional ledger persistence (D-096.6). When injected, the runtime calls
    ``persist_request`` once at batch start, then ``persist_requirement_result``
    as each requirement completes (per-requirement transaction). Spine unit
    tests inject none."""

    def persist_request(self, request: "GenerationRequest") -> None: ...
    def persist_requirement_result(self, request: "GenerationRequest",
                                   result: "RequirementResult") -> None: ...


# ---------------------------------------------------------------------------
# Per-requirement state
# ---------------------------------------------------------------------------

@dataclass
class RequirementState:
    request_id: Any
    requirement_ref: dict[str, Any]
    messages: list = field(default_factory=list)
    system: str = field(default_factory=lambda: prompts_registry.get())
    prompt_version: str = field(default_factory=lambda: prompts_registry.CURRENT)
    llm_calls: list[LlmCallRecord] = field(default_factory=list)
    # Accumulating semantic provenance — populated ONLY from seam deltas
    # (spine stores, never authors). Operational rejections never touch this.
    attempted_interpretation: dict = field(default_factory=lambda: {
        "candidate_paths": [], "dismissed_alternatives_by_reason": {}, "selected_path_id": None,
    })
    # Operational counters (for budget partial_state).
    candidates_proposed: int = 0
    candidates_admissibly_grounded: int = 0
    canonicals_selected: int = 0
    presented_candidates: list = field(default_factory=list)
    # Grounding facts stashed by governance during resolve (D-097.5 / D-207):
    # an ORDERED list, one entry per admissibly-grounded intent, in propose
    # order. finalize_outcome authors one EmissionBundle per entry. Governance
    # appends via its ``_stash_grounding`` helper; empty until a grounded
    # candidate forms. (The pre-D-207 singular ``grounded_emission`` /
    # ``grounded_negative`` / ``grounded_positive`` attributes are retired —
    # finalize still honors them as a legacy one-element fallback when set
    # directly by older callers/tests.)
    groundings: list = field(default_factory=list)
    # D-247 per-AC coverage enforcer ("AI lists, computer double-checks").
    # ``coverage_acs`` is the MODEL-declared AC list (the denominator), captured
    # on the first propose turn; ``coverage_floor`` is the regex cross-check
    # count (catches under-declaration). ``coverage_tagged`` / ``coverage_refused``
    # accumulate the ac_refs addressed across the single bounded re-prompt hop.
    # ``coverage_map`` is the per-AC verdict, persisted onto
    # attempted_interpretation (rides JSONB — no migration). Empty declared list
    # ⇒ the enforcer is a no-op (freeform requirements behave as before).
    coverage_captured: bool = False
    coverage_acs: list = field(default_factory=list)
    coverage_floor: int = 0
    coverage_tagged: set = field(default_factory=set)
    coverage_refused: dict = field(default_factory=dict)
    coverage_reprompted: bool = False
    coverage_map: dict = field(default_factory=dict)

    def merge_interpretation(self, delta: Optional[dict]) -> None:
        """Store a governance-authored semantic-provenance fragment. The spine
        merges; it never invents semantic content."""
        if not delta:
            return
        ai = self.attempted_interpretation
        for cp in delta.get("candidate_paths", []) or []:
            ai["candidate_paths"].append(cp)
        for reason, ids in (delta.get("dismissed_alternatives_by_reason") or {}).items():
            ai["dismissed_alternatives_by_reason"].setdefault(reason, []).extend(ids)
        if delta.get("selected_path_id") is not None:
            ai["selected_path_id"] = delta["selected_path_id"]
        # D-302: partial refusals accumulate across propose turns (the D-247
        # re-prompt hop) — the catch-all overwrite below would drop turn 1's.
        for pr in delta.get("partial_refusals", []) or []:
            ai.setdefault("partial_refusals", []).append(pr)
        for k, v in delta.items():
            if k not in ("candidate_paths", "dismissed_alternatives_by_reason",
                         "selected_path_id", "partial_refusals"):
                ai[k] = v


# ---------------------------------------------------------------------------
# The runtime
# ---------------------------------------------------------------------------

class GenerationRuntime:
    """Drives a batch: one conversation per requirement, each to exactly one
    outcome (D-072 / D-095.4)."""

    def __init__(self, *, max_corrections: int = DEFAULT_MAX_CORRECTIONS,
                 max_turns: int = DEFAULT_MAX_TURNS):
        self._max_corrections = max_corrections
        self._max_turns = max_turns

    # -- public entry ----------------------------------------------------
    def run(
        self, *, request: GenerationRequest, seam: GovernanceProvider,
        tool_turn_fn: ToolTurnFn, requirements: Optional[list[RequirementInput]] = None,
        policy: Optional[PhasePolicy] = None,
        persister: Optional[LedgerPersisterProtocol] = None,
    ) -> BatchResult:
        policy = policy or PhasePolicy()
        reqs = requirements if requirements is not None else self._derive_requirements(request)
        shared = self._compute_shared_context(request, reqs)   # once, bounded (D-095.4)
        budget = BatchBudget(request.operational_context.budgets)

        # FK target first: the request row must exist before any outcome row.
        if persister is not None:
            persister.persist_request(request)

        results: list[RequirementResult] = []
        for i, req in enumerate(reqs):
            ctx = ConversationContext(
                request_id=request.request_id,
                requirement_ref=req.ref,
                requirement_text=req.text,
                semantic_context=request.semantic_context,
                governance_context=request.governance_context,
                operational_context=request.operational_context,
                shared_context=shared,
            )
            progress = BatchProgress(resolved=i, total=len(reqs))
            result = self._run_requirement(ctx, seam, tool_turn_fn, policy, budget, progress)
            results.append(result)
            # Per-requirement transaction (D-096.6): each outcome durable as it
            # completes — partial-failure isolation for later requirements.
            if persister is not None:
                persister.persist_requirement_result(request, result)

        # No-silent-drops (D-072): exactly one outcome per requirement.
        assert len(results) == len(reqs), "no-silent-drops violated"
        return BatchResult(results=results)

    # -- per-requirement loop -------------------------------------------
    def _run_requirement(
        self, ctx: ConversationContext, seam: GovernanceProvider,
        tool_turn_fn: ToolTurnFn, policy: PhasePolicy, budget: BatchBudget,
        progress: BatchProgress,
    ) -> RequirementResult:
        state = RequirementState(request_id=ctx.request_id, requirement_ref=ctx.requirement_ref)
        # Resolve the frozen prompt version for this request (D-089/D-103): the
        # request pins it (operational_context.prompt_template_version) for
        # replay; otherwise the registry CURRENT. The substrate reads a frozen
        # version, never recomposes the working source.
        _pv = ctx.operational_context.prompt_template_version or prompts_registry.CURRENT
        state.system = prompts_registry.get(_pv)
        state.prompt_version = _pv
        state.messages = [_initial_user_message(ctx)]
        phase = Phase.PROPOSE
        phase_attempt = 1   # == llm_calls.attempt_index within this logical emission (D-087)
        turns = 0

        while True:
            # Budget (operational) — checked before each turn (D-088c).
            dim = budget.exceeded_dimension()
            if dim is not None:
                return self._operational_refusal(
                    RefusalKind.OPERATIONAL_BUDGET_EXHAUSTED, ctx, state, seam,
                    payload=self._budget_payload(dim, budget, state, progress),
                )
            if turns >= self._max_turns:
                return self._structural_failure(ctx, state, seam)
            turns += 1

            expected = policy.expected_tool(phase)
            turn = tool_turn_fn(
                messages=state.messages, tools=TOOLS,
                tool_choice=policy.tool_choice(phase), system=state.system,
            )
            budget.consume(input_tokens=getattr(turn, "input_tokens", 0),
                           output_tokens=getattr(turn, "output_tokens", 0))
            model_id = getattr(turn, "model", "(unknown)")
            tool_uses = extract_tool_uses(getattr(turn, "content_blocks", []))

            # --- Layer A: a tool call must be present and well-formed ---
            if not tool_uses:
                fb = f"You must call {expected}; no tool call was made."
                self._reject(state, _placeholder_block(expected), fb, phase_attempt, model_id, turn)
                phase_attempt += 1
                if phase_attempt > self._max_corrections:
                    return self._structural_failure(ctx, state, seam)
                continue

            tu = tool_uses[0]   # forced-tool flow: exactly one call per turn
            la = _tools.validate_layer_a(tu.name, tu.input)
            if tu.name != expected:
                la = _tools.LayerAResult(False, [f"expected tool {expected}, got {tu.name}"])
            if not la.ok:
                self._reject(state, tu, la.feedback, phase_attempt, model_id, turn)
                phase_attempt += 1
                if phase_attempt > self._max_corrections:
                    return self._structural_failure(ctx, state, seam)
                continue

            # --- Layer A: operational S1-ref existence (propose only) ---
            if tu.name == TOOL_PROPOSE:
                rc = seam.check_refs_exist(intent_input=tu.input, ctx=ctx)
                if not rc.ok:
                    fb = rc.feedback or f"S1 entity refs not found: {rc.missing_refs}"
                    self._reject(state, tu, fb, phase_attempt, model_id, turn)
                    phase_attempt += 1
                    if phase_attempt > self._max_corrections:
                        return self._structural_failure(ctx, state, seam)
                    continue

            # --- Layer A passed: record the success llm_call ---
            self._record_success(state, tu, phase_attempt, model_id, turn)

            # --- Dispatch to the semantic seam ---
            if tu.name == TOOL_PROPOSE:
                # D-247: capture the model-declared AC list + regex floor on the
                # first propose turn; record which ACs this turn addresses.
                self._coverage_capture(state, ctx, tu.input)
                self._coverage_tag(state, tu.input)
                res = seam.resolve_intent(intent_input=tu.input, ctx=ctx, state=state)
                state.merge_interpretation(res.interpretation_delta)
                state.candidates_proposed += 1
                state.candidates_admissibly_grounded += len(res.grounded_candidates)
                # Accumulate grounded candidates across the (single) re-prompt hop.
                state.presented_candidates = (
                    list(state.presented_candidates) + list(res.grounded_candidates))
                # D-247: re-prompt ONCE for unaddressed ACs (or to re-enumerate if
                # the floor suspects under-declaration). Skip the SELECT path
                # (highest-specificity single-intent) — orthogonal to coverage.
                if (res.next_action != NextAction.AWAIT_SELECTION
                        and self._coverage_should_reprompt(state)):
                    state.coverage_reprompted = True
                    # D-302: the re-prompt bypasses the route below — a wholly-
                    # refused turn's directive must still leave a record.
                    self._stash_partial_refusal(state, res, tu.input)
                    self._respond(state, tu, self._coverage_reprompt_text(state))
                    phase = Phase.PROPOSE
                    phase_attempt = 1
                    continue
                self._coverage_finalize(state)
                # Refuse only when NOTHING grounded across all turns (cumulative).
                if not state.presented_candidates:
                    return self._route(res.refusal, ctx, state, seam)
                # D-302: prior turns grounded, so this turn's refusal (if any)
                # will never route — record it instead of swallowing it.
                self._stash_partial_refusal(state, res, tu.input)
                self._respond(state, tu, _present_candidates(state.presented_candidates))
                phase = Phase.SELECT if res.next_action == NextAction.AWAIT_SELECTION else Phase.EMIT
                phase_attempt = 1
                continue

            if tu.name == TOOL_SELECT:
                sv = seam.accept_selection(selection_input=tu.input, ctx=ctx, state=state)
                state.merge_interpretation(sv.interpretation_delta)
                if not sv.accepted:
                    # The select call itself was an operational success (Layer A
                    # passed; recorded above). A non-accept is a semantic Layer-B
                    # finding -> substrate-routed refusal.
                    directive = sv.finding or RefusalDirective(
                        RefusalKind.STRUCTURAL_VALIDATION_FAILURE,
                        {"reason": "selection not accepted"},
                    )
                    return self._route(directive, ctx, state, seam)
                state.canonicals_selected += 1
                self._respond(state, tu, "selection accepted; emit the outcome.")
                phase = Phase.EMIT
                phase_attempt = 1
                continue

            # TOOL_EMIT
            ov = seam.finalize_outcome(outcome_input=tu.input, ctx=ctx, state=state)
            state.merge_interpretation(ov.interpretation_delta)
            if ov.override is not None:
                return self._route(ov.override, ctx, state, seam)
            return RequirementResult(outcome=ov.outcome, llm_calls=state.llm_calls,
                                     emission=ov.emission,
                                     emissions=getattr(ov, "emissions", None))

    # -- helpers ---------------------------------------------------------
    def _reject(self, state: RequirementState, tu: ToolUseBlock, feedback: str,
                attempt_index: int, model_id: str, turn: Any) -> None:
        """Operational rejection (D-088a): append a corrective tool_result +
        an llm_calls(rejected_for_correction) row. Never touches
        attempted_interpretation."""
        state.llm_calls.append(LlmCallRecord(
            call_id=uuid4(), tool_name=tu.name,
            operational_outcome=LlmCallOutcome.REJECTED_FOR_CORRECTION,
            attempt_index=attempt_index, model_identifier=model_id,
            token_count_input=getattr(turn, "input_tokens", None),
            token_count_output=getattr(turn, "output_tokens", None),
            timing_duration_ms=getattr(turn, "latency_ms", None),
            raw_parameters=tu.input, raw_response={"feedback": feedback},
            prompt_version=state.prompt_version,
        ))
        state.messages.append(_assistant_tool_use_msg(tu))
        state.messages.append(_user_tool_result_msg(tu, feedback, is_error=True))

    def _record_success(self, state: RequirementState, tu: ToolUseBlock,
                        attempt_index: int, model_id: str, turn: Any) -> None:
        state.llm_calls.append(LlmCallRecord(
            call_id=uuid4(), tool_name=tu.name,
            operational_outcome=LlmCallOutcome.SUCCESS,
            attempt_index=attempt_index, model_identifier=model_id,
            token_count_input=getattr(turn, "input_tokens", None),
            token_count_output=getattr(turn, "output_tokens", None),
            timing_duration_ms=getattr(turn, "latency_ms", None),
            raw_parameters=tu.input,
            prompt_version=state.prompt_version,
        ))

    def _respond(self, state: RequirementState, tu: ToolUseBlock, content: str) -> None:
        """Append the assistant tool_use + the substrate's tool_result so the
        next (forced) phase has the substrate response in context."""
        state.messages.append(_assistant_tool_use_msg(tu))
        state.messages.append(_user_tool_result_msg(tu, content, is_error=False))

    # -- D-247 per-AC coverage enforcer ---------------------------------
    @staticmethod
    def _descriptors(propose_input: dict) -> list:
        """The per-intent descriptor objects of a propose call (array items, or
        the legacy single intent_descriptor) — for reading ac_ref tags."""
        if not isinstance(propose_input, dict):
            return []
        arr = propose_input.get("intent_descriptors")
        if isinstance(arr, list):
            return [d for d in arr if isinstance(d, dict)]
        d = propose_input.get("intent_descriptor")
        return [d] if isinstance(d, dict) else []

    def _coverage_capture(self, state: RequirementState, ctx: ConversationContext,
                          propose_input: dict) -> None:
        """First propose turn only: capture the model-declared AC list (the
        coverage denominator) + the regex floor (the under-declaration cross-
        check). No declared ACs ⇒ the enforcer stays a no-op."""
        if state.coverage_captured:
            return
        state.coverage_captured = True
        declared = (propose_input.get("acceptance_criteria")
                    if isinstance(propose_input, dict) else None) or []
        state.coverage_acs = [
            a for a in declared
            if isinstance(a, dict) and isinstance(a.get("index"), int)
            and not isinstance(a.get("index"), bool)]
        state.coverage_floor = len(
            _coverage.parse_acceptance_criteria(ctx.requirement_text or ""))

    def _coverage_tag(self, state: RequirementState, propose_input: dict) -> None:
        """Record the ac_refs this turn addresses — a real intent (covered) or an
        explicit no_admissible_test (refused-with-reason)."""
        for d in self._descriptors(propose_input):
            ref = d.get("ac_ref")
            if not (isinstance(ref, int) and not isinstance(ref, bool) and ref > 0):
                continue
            state.coverage_tagged.add(ref)
            if d.get("no_admissible_test"):
                state.coverage_refused[ref] = (
                    d.get("no_admissible_test_reason") or "no admissible test")

    def _coverage_should_reprompt(self, state: RequirementState) -> bool:
        if not state.coverage_acs or state.coverage_reprompted:
            return False
        declared = {a["index"] for a in state.coverage_acs}
        uncovered = _coverage.compute_uncovered(declared, state.coverage_tagged)
        shortfall = _coverage.floor_shortfall(len(declared), state.coverage_floor)
        return bool(uncovered) or shortfall > 0

    def _coverage_reprompt_text(self, state: RequirementState) -> str:
        labels = {a["index"]: a.get("label", "") for a in state.coverage_acs}
        uncovered = _coverage.compute_uncovered(set(labels), state.coverage_tagged)
        parts: list[str] = []
        if uncovered:
            listed = "\n".join(f"  - AC{i}: {labels.get(i, '')}" for i in uncovered)
            parts.append(
                "These acceptance criteria have no intent yet. Address EACH in a "
                "new propose call: either an intent tagged with its ac_ref, or an "
                "intent with no_admissible_test=true + no_admissible_test_reason "
                "if the org genuinely cannot ground a test for it. Do not invent a "
                "claim the requirement does not state.\n" + listed)
        shortfall = _coverage.floor_shortfall(len(labels), state.coverage_floor)
        if shortfall > 0:
            parts.append(
                f"Also: the requirement text appears to contain at least "
                f"{state.coverage_floor} acceptance criteria but you declared "
                f"{len(labels)}. Re-read and add any you missed (tagging each with "
                f"its ac_ref), or confirm your list is complete.")
        return "\n\n".join(parts) or "Re-check acceptance-criteria coverage."

    def _coverage_finalize(self, state: RequirementState) -> None:
        """Build the per-AC coverage_map onto attempted_interpretation (rides the
        existing JSONB — no migration). Every declared AC gets one verdict:
        covered (an intent addressed it), refused (model declared no admissible
        test), or untagged_after_reprompt (the silent-omission backstop)."""
        if not state.coverage_acs:
            return
        cmap: dict = {}
        for a in state.coverage_acs:
            idx, label = a["index"], a.get("label", "")
            if idx in state.coverage_refused:
                cmap[str(idx)] = {"status": "refused", "label": label,
                                  "reason": state.coverage_refused[idx]}
            elif idx in state.coverage_tagged:
                cmap[str(idx)] = {"status": "covered", "label": label}
            else:
                cmap[str(idx)] = {"status": "refused", "label": label,
                                  "reason": "untagged_after_reprompt"}
        state.coverage_map = cmap
        state.attempted_interpretation["coverage_map"] = cmap
        shortfall = _coverage.floor_shortfall(len(state.coverage_acs), state.coverage_floor)
        if shortfall > 0:
            state.attempted_interpretation["coverage_floor_shortfall"] = shortfall

    def _stash_partial_refusal(self, state: RequirementState, res: Any,
                               propose_input: dict) -> None:
        """D-302: store a governance-authored refusal directive the loop
        declines to route (a wholly-refused turn after a grounded one, or one
        bypassed by the re-prompt hop) as a ``partial_refusals`` entry on
        attempted_interpretation. The spine stores verbatim, never authors.
        Multi-intent turns are already recorded per-intent by the governance
        merge (their delta carries the key) — skip to avoid a double record."""
        if res.refusal is None:
            return
        if (res.interpretation_delta or {}).get("partial_refusals"):
            return
        descs = self._descriptors(propose_input)
        desc = descs[0] if len(descs) == 1 else {}
        paths = (res.interpretation_delta or {}).get("candidate_paths") or []
        state.attempted_interpretation.setdefault("partial_refusals", []).append({
            "path_id": paths[0].get("path_id") if paths else None,
            "ac_ref": desc.get("ac_ref"),
            "archetype": desc.get("archetype_hint"),
            "claim_kind": desc.get("claim_kind_hint"),
            "refusal_kind": res.refusal.refusal_kind.value,
            "payload": res.refusal.payload,
        })

    def _route(self, directive: RefusalDirective, ctx: ConversationContext,
               state: RequirementState, seam: GovernanceProvider) -> RequirementResult:
        outcome = seam.route_refusal(directive=directive, ctx=ctx, state=state)
        return RequirementResult(outcome=outcome, llm_calls=state.llm_calls)

    def _operational_refusal(self, kind: RefusalKind, ctx: ConversationContext,
                             state: RequirementState, seam: GovernanceProvider,
                             payload: dict) -> RequirementResult:
        return self._route(RefusalDirective(refusal_kind=kind, payload=payload), ctx, state, seam)

    def _structural_failure(self, ctx: ConversationContext, state: RequirementState,
                            seam: GovernanceProvider) -> RequirementResult:
        # Persistent Layer-A failure -> structural-validation-failure (D-088a):
        # operational origin, invalidity(output) refusal kind.
        return self._operational_refusal(
            RefusalKind.STRUCTURAL_VALIDATION_FAILURE, ctx, state, seam,
            payload={"reason": "persistent Layer-A correction failure"},
        )

    @staticmethod
    def _budget_payload(dim: str, budget: BatchBudget, state: RequirementState,
                        progress: BatchProgress) -> dict:
        return {
            "budget_dimension": dim,
            "budget_limit": budget.limit_for(dim),
            "budget_consumed": budget.consumed_for(dim),
            "partial_state_at_exhaustion": {
                "candidates_proposed": state.candidates_proposed,
                "candidates_admissibly_grounded": state.candidates_admissibly_grounded,
                "canonicals_selected": state.canonicals_selected,
                "requirements_resolved": progress.resolved,
                "requirements_unresolved": progress.total - progress.resolved,
            },
        }

    @staticmethod
    def _derive_requirements(request: GenerationRequest) -> list[RequirementInput]:
        out: list[RequirementInput] = []
        for ref in request.semantic_context.requirement_refs or []:
            text = ref.get("text") or ref.get("summary") or ref.get("requirement_text") or ""
            out.append(RequirementInput(ref=ref, text=text))
        return out

    @staticmethod
    def _compute_shared_context(request: GenerationRequest,
                                reqs: list[RequirementInput]) -> dict:
        """Bounded shared interpretation context (D-077 / D-095.4), computed
        once and injected per requirement. Minimal this slice; real shared
        interpretation is governance-core."""
        return {
            "s1_version_seq": request.semantic_context.s1_version_seq,
            "s1_version_name": request.semantic_context.s1_version_name,
            "requirement_count": len(reqs),
        }


def _present_candidates(candidates: list) -> str:
    items = [
        {"path_id": c.path_id, "admissibility_layer": c.admissibility_layer.value,
         "summary": c.summary}
        for c in candidates
    ]
    return f"admissibly_grounded_candidates={items}"


def _placeholder_block(expected_tool: str) -> ToolUseBlock:
    """A synthetic block for the no-tool-call rejection (so the correction
    tool_result still threads through the message history)."""
    return ToolUseBlock(id=f"no-tool-{uuid4().hex[:8]}", name=expected_tool, input={})
