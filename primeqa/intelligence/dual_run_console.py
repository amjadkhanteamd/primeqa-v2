"""Dual-run parity console (D-212 — 5b-4): per-requirement v1 ↔ substrate triage.

For one release, pair the OLD engine's latest results with the NEW engine's
latest verdicts on the shared pivot — the requirement — and classify each row:

  - ``parity_pass``  / ``parity_fail``   — both engines agree
  - ``divergent_substrate_stricter``     — v1 passes, substrate fails (usually
                                           a substrate win; investigate)
  - ``divergent_v1_stricter``            — v1 fails, substrate passes — a
                                           **retirement blocker** (the new
                                           engine would miss a defect)
  - ``substrate_gap``                    — v1 has results, substrate has no
                                           claims/runs — a **coverage blocker**
  - ``v1_gap``                           — substrate-only coverage (fine)
  - ``untested``                         — neither engine has results

Pure assembly + classification (unit-tested without a DB); thin best-effort
readers around them (the D-198 pattern). ``errored`` results count on the
fail side for classification — an errored run blocks confidence either way,
and divergence rows get human triage by design. Read-time only; no migration.

The release-level window verdict is COMPUTED, never stored (D-212 §3):
``retirement_ready = no divergent_v1_stricter AND no substrate_gap``. The
3-consecutive-windows close is an operational judgment recorded in the
DECISIONS_LOG at 5b-4 close.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)

# worst-of ranking: lower = worse (min wins)
_V1_RANK = {"failed": 0, "error": 1, "skipped": 2, "passed": 3}
_SUB_RANK = {"failed": 0, "errored": 1, "passed": 2}

PARITY_CLASSES = (
    "parity_pass", "parity_fail", "divergent_substrate_stricter",
    "divergent_v1_stricter", "substrate_gap", "v1_gap", "untested",
)


def classify_parity(v1_status: Optional[str],
                    substrate_outcome: Optional[str]) -> str:
    """One requirement's parity class from each engine's worst-of-latest
    result. ``None`` means that engine has nothing evaluable for the
    requirement (no results / no claims / never run). v1 ``skipped`` counts as
    nothing-evaluable; errored counts on the fail side (see module doc)."""
    if v1_status == "skipped":
        v1_status = None
    v1_pass = v1_status == "passed"
    v1_fail = v1_status in ("failed", "error")
    sub_pass = substrate_outcome == "passed"
    sub_fail = substrate_outcome in ("failed", "errored")

    if v1_status is None and substrate_outcome is None:
        return "untested"
    if substrate_outcome is None:
        return "substrate_gap"
    if v1_status is None:
        return "v1_gap"
    if v1_pass and sub_pass:
        return "parity_pass"
    if v1_fail and sub_fail:
        return "parity_fail"
    if v1_pass and sub_fail:
        return "divergent_substrate_stricter"
    if v1_fail and sub_pass:
        return "divergent_v1_stricter"
    return "untested"


def assemble_release_parity(requirements: list, v1_by_key: dict,
                            substrate_by_key: dict) -> dict:
    """Pure: pair the two engines' per-requirement summaries and classify.

    ``requirements``: ``[{key, summary}]`` (the release's requirement set —
    the pivot; rows outside it are ignored). ``v1_by_key``:
    ``{key: {status, tc_count}}`` (worst-of-latest). ``substrate_by_key``:
    ``{key: {outcome, verdict, claim_count, never_run}}``."""
    rows = []
    counts = {c: 0 for c in PARITY_CLASSES}
    for r in requirements:
        key = r["key"]
        v1 = v1_by_key.get(key)
        sub = substrate_by_key.get(key)
        parity = classify_parity(
            v1["status"] if v1 else None,
            sub["outcome"] if sub else None)
        counts[parity] += 1
        rows.append({
            "key": key,
            "summary": r.get("summary") or "",
            "v1_status": v1["status"] if v1 else None,
            "v1_tc_count": v1["tc_count"] if v1 else 0,
            "substrate_outcome": sub["outcome"] if sub else None,
            "substrate_verdict": sub.get("verdict") if sub else None,
            "substrate_claim_count": sub["claim_count"] if sub else 0,
            "parity": parity,
        })
    # triage-first ordering: blockers, then divergences, then the rest
    order = {c: i for i, c in enumerate((
        "divergent_v1_stricter", "substrate_gap",
        "divergent_substrate_stricter", "parity_fail",
        "untested", "v1_gap", "parity_pass"))}
    rows.sort(key=lambda r: (order[r["parity"]], r["key"]))
    return {
        "rows": rows,
        "counts": counts,
        "requirement_count": len(rows),
        # the per-window gate (D-212 §3): the new engine misses nothing v1
        # catches AND covers every requirement v1 covers
        "retirement_ready": (counts["divergent_v1_stricter"] == 0
                             and counts["substrate_gap"] == 0),
    }


# ---------------------------------------------------------------------------
# Thin readers (best-effort) + the public entry
# ---------------------------------------------------------------------------

def _v1_results_by_key(db, tenant_id: int, pipeline_run_ids: list) -> dict:
    """Worst-of-LATEST v1 result per requirement key across the release's
    runs: newest ``RunTestResult`` per test case, grouped by the test case's
    requirement (jira_key or ``req-<id>``)."""
    if not pipeline_run_ids:
        return {}
    from primeqa.execution.models import RunTestResult
    from primeqa.test_management.models import Requirement, TestCase

    rows = (db.query(RunTestResult.test_case_id, RunTestResult.status,
                     RunTestResult.executed_at,
                     Requirement.id, Requirement.jira_key)
            .join(TestCase, TestCase.id == RunTestResult.test_case_id)
            .join(Requirement, Requirement.id == TestCase.requirement_id)
            .filter(RunTestResult.run_id.in_(pipeline_run_ids),
                    Requirement.tenant_id == tenant_id)
            .order_by(RunTestResult.executed_at.desc().nullslast())
            .all())
    latest_by_tc: dict = {}
    key_of_tc: dict = {}
    for tc_id, status, _executed, req_id, jira_key in rows:
        if tc_id not in latest_by_tc:               # newest-first: first wins
            latest_by_tc[tc_id] = status
            key_of_tc[tc_id] = jira_key or f"req-{req_id}"
    out: dict = {}
    for tc_id, status in latest_by_tc.items():
        key = key_of_tc[tc_id]
        cur = out.setdefault(key, {"status": status, "tc_count": 0})
        cur["tc_count"] += 1
        if _V1_RANK.get(status, 9) < _V1_RANK.get(cur["status"], 9):
            cur["status"] = status
    return out


def _substrate_by_key(tenant_id: int, external_keys: list) -> dict:
    """Worst-of-LATEST substrate outcome per requirement key: the
    ``generated_from`` claims' newest run + S6 verdict (the recency-correct
    spine). A requirement whose claims have never run reports
    ``outcome=None`` (→ substrate_gap)."""
    from sqlalchemy.orm import Session

    from primeqa.intelligence.s4_execution_console import _read_claim_runs
    from primeqa.semantic.connection import get_tenant_connection
    from primeqa.test_representation.coordinator import (
        SemanticTransactionCoordinator,
    )

    out: dict = {}
    coordinator = SemanticTransactionCoordinator()
    with get_tenant_connection(tenant_id) as conn:
        session = Session(bind=conn)
        try:
            for key in external_keys:
                matches = coordinator.list_tests_by_requirement(
                    session, external_system="jira", external_key=key,
                    link_kind="generated_from")
                if not matches:
                    continue                       # no claims → absent → gap
                worst_outcome, worst_verdict = None, None
                for m in matches:
                    runs = _read_claim_runs(session, m.test_id)
                    latest = runs[0] if runs else None
                    if latest is None:
                        continue                   # never ran
                    o = latest.get("outcome")
                    if (worst_outcome is None
                            or _SUB_RANK.get(o, 9) < _SUB_RANK.get(worst_outcome, 9)):
                        worst_outcome = o
                        worst_verdict = latest.get("verdict")
                out[key] = {
                    "outcome": worst_outcome,      # None = claims never ran
                    "verdict": worst_verdict,
                    "claim_count": len(matches),
                    "never_run": worst_outcome is None,
                }
        finally:
            session.close()
    return out


def get_release_parity(tenant_id: int, db, release: dict) -> dict:
    """Best-effort: the release's dual-run parity view (D-212). ``release`` is
    the service's detail dict (``requirements`` + ``runs``). Never raises."""
    try:
        from primeqa.release.decision_composer import (
            external_keys_for_requirements,
        )
        reqs = release.get("requirements") or []
        keys = external_keys_for_requirements(reqs)
        requirement_rows = [
            {"key": k, "summary": (r.get("summary")
                                   or r.get("jira_summary") or "")}
            for k, r in zip(keys, reqs)
        ]
        run_ids = [r.get("pipeline_run_id") for r in (release.get("runs") or [])
                   if r.get("pipeline_run_id")]
        v1_by_key = _v1_results_by_key(db, tenant_id, run_ids)
        substrate_by_key = _substrate_by_key(tenant_id, keys)
        result = assemble_release_parity(
            requirement_rows, v1_by_key, substrate_by_key)
        result["available"] = True
        return result
    except Exception as exc:
        log.warning("dual-run parity unavailable for tenant %s release %s: %s",
                    tenant_id, release.get("id"), exc)
        return {"available": False, "rows": [], "counts": {},
                "requirement_count": 0, "retirement_ready": False}
