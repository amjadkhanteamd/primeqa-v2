"""Eval aggregator (D-090(a)) — rolls case results into a per-category /
per-archetype / per-refusal-kind report. Correctness framing: pass/fail against
governed-outcome expectations (D-090(f) architectural-compliance), not absolute
semantic correctness."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from primeqa.generation.eval.runner import CaseResult


@dataclass
class Report:
    total: int
    passed: int
    failed: int
    by_category: dict[str, tuple[int, int]]        # category -> (passed, total)
    by_archetype: dict[str, tuple[int, int]]       # emitted-draft archetype
    by_refusal_kind: dict[str, tuple[int, int]]    # refusal kind
    failures: list[tuple[str, list[str]]] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            "generation eval — deterministic core (D-102)",
            f"  total={self.total}  passed={self.passed}  failed={self.failed}",
            "  by category:",
        ]
        for cat, (p, t) in sorted(self.by_category.items()):
            lines.append(f"    {cat:<16} {p}/{t}")
        if self.by_archetype:
            lines.append("  by archetype (drafts):")
            for a, (p, t) in sorted(self.by_archetype.items()):
                lines.append(f"    {a:<16} {p}/{t}")
        if self.by_refusal_kind:
            lines.append("  by refusal_kind:")
            for rk, (p, t) in sorted(self.by_refusal_kind.items()):
                lines.append(f"    {rk:<36} {p}/{t}")
        if self.failures:
            lines.append("  FAILURES:")
            for cid, ms in self.failures:
                lines.append(f"    {cid}: {'; '.join(ms)}")
        return "\n".join(lines)


def _bump(d: dict, key, passed: bool) -> None:
    p, t = d.get(key, (0, 0))
    d[key] = (p + (1 if passed else 0), t + 1)


def aggregate(results: list[CaseResult]) -> Report:
    by_category: dict[str, tuple[int, int]] = {}
    by_archetype: dict[str, tuple[int, int]] = {}
    by_refusal_kind: dict[str, tuple[int, int]] = {}
    failures: list[tuple[str, list[str]]] = []
    passed = 0
    for r in results:
        passed += 1 if r.passed else 0
        _bump(by_category, r.category, r.passed)
        if r.archetype is not None:
            _bump(by_archetype, r.archetype, r.passed)
        if r.refusal_kind is not None:
            _bump(by_refusal_kind, r.refusal_kind, r.passed)
        if not r.passed:
            failures.append((r.case_id, r.mismatches))
    return Report(
        total=len(results), passed=passed, failed=len(results) - passed,
        by_category=by_category, by_archetype=by_archetype,
        by_refusal_kind=by_refusal_kind, failures=failures,
    )


# ---------------------------------------------------------------------------
# Live-mode scoring (D-104): ontology-coherence envelopes, not output equality
# ---------------------------------------------------------------------------

# The governed fields a live observation exposes (never phrasing/subject/excerpt).
_GRADED = ("outcome_kind", "archetype", "claim_kind", "caveat_required", "refusal_kind")


@dataclass
class ObservedOutcome:
    """The governed surface of one live run — extracted from the outcome +
    emission; never phrasing/subject/excerpt (D-090(f))."""

    outcome_kind: str
    archetype: Optional[str] = None
    claim_kind: Optional[str] = None
    caveat_required: bool = False
    refusal_kind: Optional[str] = None


def observe(outcome, emission) -> ObservedOutcome:
    return ObservedOutcome(
        outcome_kind=outcome.outcome_kind.value,
        archetype=getattr(emission, "archetype", None),
        claim_kind=getattr(emission, "claim_kind", None),
        caveat_required=bool(outcome.caveat_required),
        refusal_kind=outcome.refusal_kind.value if outcome.refusal_kind else None,
    )


@dataclass
class LiveResult:
    """One live probe's adjudication (D-104). ``invariant_ok=False`` is the hard
    gate (auto-fail — ontology collapse). When invariants hold, divergence from
    the deterministic primary is *drift*: recorded for human judgment
    (regression|evolution|neutral), never auto-failed (D-090(c))."""

    case_id: str
    prompt_version: str
    model: str
    invariant_ok: bool
    violated: list[str] = field(default_factory=list)
    drift: bool = False
    divergences: list[str] = field(default_factory=list)
    verdict: str = "neutral"   # neutral | drift-unadjudicated | violation


def _eval_predicate(observed: ObservedOutcome, p: dict[str, Any]) -> bool:
    val = getattr(observed, p["field"], None)
    op = p["op"]
    if op == "eq":
        return val == p["value"]
    if op == "not_eq":
        return val != p["value"]
    if op == "in":
        return val in p["value"]
    if op == "not_in":
        return val not in p["value"]
    if op == "is_false":
        return not val
    if op == "is_true":
        return bool(val)
    raise ValueError(f"unknown envelope op: {op!r}")


def _primary(expect: dict[str, Any]) -> dict[str, Any]:
    """The deterministic primary's governed fields, for drift comparison."""
    claim = expect.get("claim", {})
    return {
        "outcome_kind": expect.get("outcome_kind"),
        "archetype": claim.get("archetype"),
        "claim_kind": claim.get("claim_kind"),
        "caveat_required": expect.get("caveat_required"),
        "refusal_kind": expect.get("refusal_kind"),
    }


def score_live(case, observed: ObservedOutcome, *, prompt_version: str,
               model: str) -> LiveResult:
    """Adjudicate a live observation against the probe's semantic envelope
    (D-104.2). Invariant violation -> auto-fail (ontology collapse);
    invariants-hold-but-diverges-from-primary -> drift (human-judged);
    benign-only difference -> neutral."""
    env = case.live["envelope"]
    violated = [
        f"{p['field']} {p['op']} {p.get('value', '')}".strip()
        for p in env.get("invariant", [])
        if not _eval_predicate(observed, p)
    ]
    if violated:
        return LiveResult(case.id, prompt_version, model, invariant_ok=False,
                          violated=violated, verdict="violation")

    benign = set(env.get("benign", []))
    primary = _primary(case.expect)
    divergences = []
    for f in _GRADED:
        if f in benign:
            continue
        exp, got = primary.get(f), getattr(observed, f)
        if exp is not None and got != exp:
            divergences.append(f"{f}: primary={exp!r} observed={got!r}")

    if not divergences:
        return LiveResult(case.id, prompt_version, model, invariant_ok=True,
                          verdict="neutral")
    return LiveResult(case.id, prompt_version, model, invariant_ok=True,
                      drift=True, divergences=divergences,
                      verdict="drift-unadjudicated")


def render_live(results: list[LiveResult]) -> str:
    """Render the live drift report (D-104.4) — the gate is no invariant
    violations; drift rows are for human adjudication, never auto-failed."""
    fails = [r for r in results if not r.invariant_ok]
    drifts = [r for r in results if r.invariant_ok and r.drift]
    lines = [
        "live eval — ontology-coherence gate (D-104)",
        f"  probes={len(results)}  invariant_violations={len(fails)}  drift={len(drifts)}",
    ]
    for r in fails:
        lines.append(f"  AUTO-FAIL {r.case_id} [{r.model}/{r.prompt_version}]: "
                     f"invariant violated — {'; '.join(r.violated)}")
    for r in drifts:
        lines.append(f"  DRIFT {r.case_id} [{r.model}/{r.prompt_version}] "
                     f"({r.verdict}): {'; '.join(r.divergences)}")
    return "\n".join(lines)
