"""Eval aggregator (D-090(a)) — rolls case results into a per-category /
per-archetype / per-refusal-kind report. Correctness framing: pass/fail against
governed-outcome expectations (D-090(f) architectural-compliance), not absolute
semantic correctness."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

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
