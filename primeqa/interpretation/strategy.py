"""S6 Evaluation Strategy applier — the pure ``Verified`` predicate (D-274, Slice 2a).

ADR-001 §3b: given the set of ``ProbeResult``s for one claim, decide whether the
claim is **Verified** under a named strategy. The PRIMARY OUTPUT is a single
per-claim boolean — coverage's sole input (D-271). evaluation-semantics-v1.md
(D-272) governs the semantics.

**Pure and STANDALONE — wired into nothing in Slice 2a.** This module imports
only the standard library; it must never import ``execution_engine``, ``db``,
``sqlalchemy``, ``semantic`` / Salesforce, or ``intelligence.substrate_decision``,
so the ``Verified`` semantics never couple to execution or persistence. (Slice 2b
wires it into the decision engine — alongside ``bva``/Slice 4; Slice 3's S4
run-all produces the multi-probe ``ProbeResult``s; the schema lands with the
build.)

The ``single`` arm is the **N=1 identity of today's release rule**: a claim is
"good" iff its latest counted run's ``outcome == 'passed'``
(``substrate_decision._assemble_claim_evidence`` + ``compute_substrate_decision``'s
pass_rate). One passing probe → Verified; a ``fail`` or an ``indeterminate``
(evidence-incomplete, D-272 §2.4) → NOT Verified. So when Slice 2b later wires
this in, behavior is provably byte-identical to today.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Sequence

ProbeOutcome = Literal["pass", "fail", "indeterminate"]
Polarity = Literal["accept", "reject"]
StrategyKind = Literal["single", "bva"]


@dataclass(frozen=True)
class ProbeResult:
    """One probe's evidence-input to the strategy (ADR-001 §3a), abstracted away
    from any S4 type. The ``single`` arm reads ONLY ``outcome``; the rest is
    carried for the contract's completeness and ``bva``'s future use.

    - ``outcome``     — the three-way result (D-272): ``pass`` / ``fail`` /
      ``indeterminate``.
    - ``polarity``    — the probe's expectation (``accept`` vs ``reject``).
      CARRIED, **not** read by ``single`` (S4 already folds polarity into
      ``outcome``); ``bva``'s strict-AND uses it for diagnostics.
    - ``boundary``    — which-boundary marker; ``"single"`` for the one-probe
      case; ``bva`` will set ``min`` / ``min-1`` / ``nominal`` / ``max`` /
      ``max+1``. Carried.
    - ``diagnostics`` — optional carried detail (error / refs) for
      explainability; not read by ``single``.
    """

    outcome: ProbeOutcome
    polarity: Polarity = "accept"
    boundary: Optional[str] = None
    diagnostics: Optional[object] = None


# The ONLY place that knows S4's ``RUN_OUTCOME_ENUM`` strings (passed / failed /
# errored / skipped). Keyed on the outcome STRING — never a RunEvidence — so this
# module never imports execution_engine.
_OUTCOME_TO_PROBE: dict[str, ProbeOutcome] = {
    "passed": "pass",
    "failed": "fail",
    "errored": "indeterminate",   # could-not-evaluate → evidence-incomplete (D-272)
    "skipped": "indeterminate",   # evidence-absent → same family; only a real pass is "good"
}


def probe_result_from_outcome(outcome: str, *, expect_rejection: bool) -> ProbeResult:
    """Build the ONE ``ProbeResult`` for the ``single`` case from an S4 run's
    outcome string + the recipe's ``expect_rejection`` polarity.

    Exhaustive over ``RUN_OUTCOME_ENUM`` (passed / failed / errored / skipped);
    ANY other string RAISES ``ValueError`` — a new outcome value must be mapped
    deliberately, never silently defaulted. ``expect_rejection`` sets the carried
    polarity (``reject`` vs ``accept``); ``boundary`` is ``"single"``.
    """
    try:
        mapped = _OUTCOME_TO_PROBE[outcome]
    except KeyError:
        raise ValueError(
            f"unmodeled S4 outcome {outcome!r}; expected one of "
            f"{sorted(_OUTCOME_TO_PROBE)}") from None
    return ProbeResult(
        outcome=mapped,
        polarity="reject" if expect_rejection else "accept",
        boundary="single",
    )


def apply_strategy(claim, kind: str, probe_results: Sequence[ProbeResult]) -> bool:
    """The ``Verified`` predicate (ADR-001 §3b). Pure & deterministic over
    ``(claim, kind, probe_results)``: no execution, no S1 / SF / DB, no clock,
    no random.

    ``claim`` is accepted but UNUSED by the ``single`` arm — it is the seam
    ``bva`` (Slice 4) needs to know which boundary probes are *required*; carrying
    it here keeps the signature stable across strategies.
    """
    if kind == "single":
        return _apply_single(probe_results)
    if kind == "bva":
        raise NotImplementedError("bva arm — Slice 4")
    raise ValueError(f"unknown strategy kind {kind!r}")


def _apply_single(probe_results: Sequence[ProbeResult]) -> bool:
    """N=1 identity of today's latest-run rule: Verified iff the one probe
    ``pass``ed. A wrong arity (N != 1) is a wiring bug — RAISE, never a silent
    ``Verified`` (N=0 / never-run is the decision engine's separate ``has_runs``
    path, not ours; N>1 is ``bva``, not ``single``)."""
    n = len(probe_results)
    if n != 1:
        raise ValueError(
            f"single strategy requires exactly 1 probe result, got {n}")
    outcome = probe_results[0].outcome
    if outcome == "pass":
        return True
    if outcome in ("fail", "indeterminate"):
        return False
    raise ValueError(f"unrecognized ProbeResult.outcome {outcome!r}")
