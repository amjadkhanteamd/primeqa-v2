"""S4 execution console — v1's UI bridge to Substrate-4 execution + the S6 read of
the result (D-168, UI Area 3 slice 3a).

v1 owns the bridge (the allowed v1→substrate direction, like `s3_generation_console`
/ `s1_sync_console`). Area 3 leads with the **synchronous** run path (user decision,
D-168): one blocking call runs the claim's eligible recipe (any kind) and returns the
outcome + verdict in one shot — no queue, no poll, no job→run correlation gap.

  - :func:`trigger_claim_run` — run the eligible recipe for a claim `test_id` on an
    environment via `run_recipe_execution_for_tenant` (it owns its own tenant
    connection + commit, and self-resolves the SF client per recipe-kind). Returns
    the mapped outcome/verdict. Best-effort — never raises.
  - :func:`read_claim_runs` — the claim's recent run results via the S6 read API
    (`list_interpretations(claim_test_id=…)`), which carries outcome + verdict.

**Production safety is the caller's job** (D-168): the substrate has no prod guard
and a data-recipe run *mutates the org*, so the route gates with v1's
`environment_can_bulk_run` BEFORE calling :func:`trigger_claim_run`.
"""
from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import text

from primeqa.execution_engine.errors import UnexecutableClaimError
from primeqa.intelligence.claim_presentation import (
    claim_title, cause_plain, duration_human, time_ago, verdict_plain,
)

log = logging.getLogger(__name__)


def _iso(v):
    return v.isoformat() if v is not None and hasattr(v, "isoformat") else v


# --- run a claim (sync; returns outcome + verdict) ---------------------------

def _map_run_result(result) -> dict:
    """Pure: a ``RunPathResult`` → the template/flash dict. Duck-typed (reads
    ``ran`` / ``reason`` / ``selected_recipe_id`` / ``evidence.outcome`` /
    ``interpretation.verdict``) so it is directly unit-testable with stand-ins."""
    if not getattr(result, "ran", False):
        return {"ok": True, "ran": False, "reason": getattr(result, "reason", None)}
    if hasattr(result, "batch_id"):              # D-284: a run-all RunAllResult —
        probes = getattr(result, "probes", ()) or ()   # report the batch, not a
        return {                                        # single run. NO .evidence.
            "ok": True, "ran": True,
            "batch_id": str(result.batch_id),
            "probe_count": len(probes),
            # Verified is NOT computed here — the decision engine reads the batch
            # (4d). The console only reports that the batch ran.
            "recipe_id": None, "outcome": None, "verdict": None,
        }
    ev = getattr(result, "evidence", None)
    interp = getattr(result, "interpretation", None)
    rid = getattr(result, "selected_recipe_id", None)
    return {
        "ok": True, "ran": True,
        "recipe_id": str(rid) if rid else None,
        "outcome": getattr(ev, "outcome", None) if ev is not None else None,
        # the semantic verdict lives on S6 (None if the best-effort interpret
        # step failed — the run outcome is still authoritative).
        "verdict": getattr(interp, "verdict", None) if interp is not None else None,
    }


def trigger_claim_run(tenant_id: int, test_id, environment_id: int, *,
                      client=None, field_overrides=None, caller_tier=None) -> dict:
    """Run the eligible recipe for ``test_id`` on ``environment_id`` (synchronous).
    Best-effort — returns ``{ok: False, error}`` on any failure (never raises).
    On success: ``{ok: True, ran, outcome, verdict, recipe_id}`` (``ran=False`` +
    ``reason`` when the claim has no approved/eligible recipe for the env — a
    first-class non-error result). ``client`` is injectable for tests; the route
    passes ``None`` so the engine self-resolves the SF client per recipe-kind.

    ``field_overrides`` (D-235, run-time test-data injection) is an optional
    ``{bare_field_name: value}`` map passed straight to the engine; it applies only
    to the positive vertical's subject create (the executor enforces this)."""
    try:
        # D-278 (Slice 3.4): route by the claim's recorded strategy kind. DORMANT —
        # no claim records a kind today (recon case A), so this always takes the
        # single path, byte-identical to the prior direct call.
        from primeqa.execution_engine.run import run_claim_execution_for_tenant
        result = run_claim_execution_for_tenant(
            tenant_id, UUID(str(test_id)), environment_id=environment_id,
            client=client, field_overrides=field_overrides or None,
            caller_tier=caller_tier)
        return _map_run_result(result)
    except Exception as exc:                      # credential / SF / execution error
        log.warning("trigger_claim_run failed for tenant %s test %s env %s: %s",
                    tenant_id, test_id, environment_id, exc)
        return {"ok": False, "error": str(exc)}


# --- read the claim's recent runs (S6 verdict surface) -----------------------

# Read S4 runs (authoritative — they carry finished_at) LEFT JOINed to the S6
# verdict, newest-first. The S6 read alone has no time axis (it orders by the
# random-uuid run_id) and drops interpret-failed runs; reading S4 as the base
# fixes both (true recency + a run with a failed interpret still shows, verdict
# NULL). Tenant-scoped by the connection's search_path — no tenant_id column.
_CLAIM_RUNS_SQL = (
    "SELECT CAST(r.run_id AS text) AS run_id, CAST(r.recipe_id AS text) AS recipe_id, "
    "r.outcome::text AS outcome, r.failure_category, r.finished_at, "
    "i.verdict::text AS verdict "
    "FROM s4_execution_runs r "
    "LEFT JOIN s6_interpretations i ON i.run_id = r.run_id "
    "WHERE r.claim_test_id = CAST(:tid AS uuid) "
    "ORDER BY r.finished_at DESC LIMIT :limit")


def _read_claim_runs(session, test_id, *, limit: int = 50) -> list[dict]:
    """Pure: the claim's runs newest-first — S4 outcome + finished_at LEFT JOINed
    to the S6 verdict (verdict NULL when the best-effort interpret step failed).
    ``failure_category`` (D-261) rides along so the plain-words line can split a
    permanent not_evaluated from a re-runnable one (D-272 Slice 1)."""
    rows = session.execute(
        text(_CLAIM_RUNS_SQL), {"tid": str(test_id), "limit": limit}).mappings().all()
    return [{
        "run_id": r["run_id"], "recipe_id": r["recipe_id"],
        "outcome": r["outcome"], "verdict": r["verdict"],
        "failure_category": r["failure_category"],
        "finished_at": _iso(r["finished_at"]),
    } for r in rows]


def read_claim_runs(tenant_id: int, test_id) -> dict:
    """Best-effort read of the claim's recent run results. Never raises. Returns
    ``{available, runs}`` — ``available=False`` on any read error."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        from sqlalchemy.orm import Session
        with get_tenant_connection(tenant_id) as conn:
            session = Session(bind=conn)
            try:
                return {"available": True, "runs": _read_claim_runs(session, test_id)}
            finally:
                session.close()
    except Exception as exc:
        log.warning("read_claim_runs unavailable for tenant %s test %s: %s",
                    tenant_id, test_id, exc)
        return {"available": False, "runs": []}


# --- read one run's detail (S4 evidence steps + S6 verdict/cause) (3c) --------

_RUN_DETAIL_SQL = (
    "SELECT CAST(r.run_id AS text) AS run_id, "
    "CAST(r.claim_test_id AS text) AS claim_test_id, "
    "CAST(r.recipe_id AS text) AS recipe_id, r.recipe_version_seq, "
    "r.claim_version_seq, "
    "r.environment_id, r.outcome::text AS outcome, r.started_at, r.finished_at, "
    "r.duration_ms, r.evidence, r.failure_category, r.sf_error_code, r.source, "
    "c.claim_kind::text AS claim_kind, c.asserted_truth, c.semantic_conditions, "
    "c.version_seq AS current_claim_version_seq, "
    "req.external_key AS requirement_key "
    "FROM s4_execution_runs r "
    "LEFT JOIN test_claims c ON c.test_id = r.claim_test_id AND c.valid_to IS NULL "
    "LEFT JOIN LATERAL ("
    "  SELECT l.external_key FROM test_requirement_links l "
    "  WHERE l.test_id = r.claim_test_id AND l.link_kind = 'generated_from' "
    "  ORDER BY l.linked_at DESC LIMIT 1) req ON true "
    "WHERE r.run_id = CAST(:rid AS uuid)")


def _read_run_detail(session, run_id) -> dict | None:
    """Pure: one S4 run row (the evidence trace) joined to its S6 interpretation
    (verdict / attribution / cause), the current claim (kind + asserted_truth —
    the page's title inputs) and the generated_from requirement key, or None
    when no run matches ``run_id``. The claim/requirement joins are 1:0-or-1
    LEFT JOINs — a deleted claim degrades to NULLs, never drops the run."""
    row = session.execute(
        text(_RUN_DETAIL_SQL), {"rid": str(run_id)}).mappings().first()
    if row is None:
        return None
    ev = row["evidence"] if isinstance(row["evidence"], dict) else {}
    interp = None
    try:
        from primeqa.interpretation.result_store import read_interpretation
        ir = read_interpretation(session, UUID(str(run_id)))
        if ir is not None:
            # D-201: the substrate agent's deterministic, human-gated repair
            # suggestions over the S6 vocabulary (empty for passing verdicts).
            # failure_category forks the not_evaluated copy — a deterministic
            # setup rejection must not read "re-run".
            from primeqa.evolution.repair import suggest_repairs
            interp = {
                "verdict": ir.verdict,
                "attribution": ir.attribution,
                "cause": ({"cause_kind": ir.cause.cause_kind, "vr_name": ir.cause.vr_name}
                          if ir.cause is not None else None),
                "phrasing": ir.phrasing,
                "repair_suggestions": suggest_repairs(
                    ir.verdict,
                    cause_kind=(ir.cause.cause_kind if ir.cause else None),
                    vr_name=(ir.cause.vr_name if ir.cause else None),
                    failure_category=row["failure_category"]),
            }
    except Exception:                                  # S6 read is best-effort
        interp = None
    # The readable-run inputs (best-effort each; the page renders without them):
    # the claim version PINNED by the run — an edited claim must not misreport
    # what an old run checked — and the recipe's semantic (authored) staged
    # field keys, the k16 set that splits TEST DATA from S4's padding.
    asserted_pinned = None
    claim_version_drift = False
    try:
        cvs, cur = row["claim_version_seq"], row["current_claim_version_seq"]
        if cvs is not None and cur is not None and cvs != cur:
            from primeqa.test_representation.coordinator import (
                SemanticTransactionCoordinator)
            pinned = SemanticTransactionCoordinator().get_claim_version(
                session, UUID(row["claim_test_id"]), cvs)
            if pinned is not None:
                body = pinned.asserted_truth
                asserted_pinned = (body.model_dump(mode="json")
                                   if hasattr(body, "model_dump") else
                                   body if isinstance(body, dict) else None)
                claim_version_drift = True
    except Exception:
        pass
    recipe_semantic_fields = None
    try:
        if row["recipe_id"] and row["recipe_version_seq"] is not None:
            from primeqa.test_representation.coordinator import (
                SemanticTransactionCoordinator)
            recipe = SemanticTransactionCoordinator().get_recipe_version(
                session, UUID(row["recipe_id"]), row["recipe_version_seq"])
            obs = getattr(recipe, "observation_realization", None)
            obs = (obs.model_dump(mode="json") if hasattr(obs, "model_dump")
                   else obs if isinstance(obs, dict) else {})
            for s in (obs.get("steps") or []):
                if not isinstance(s, dict):
                    continue
                fields = (s.get("field_values") if s.get("kind") == "create"
                          else s.get("field_changes") if s.get("kind") == "update"
                          else None)
                if isinstance(fields, dict) and fields:
                    recipe_semantic_fields = [
                        str(k).split(".", 1)[-1] for k in fields]
                    break
    except Exception:
        pass
    return {
        "run_id": row["run_id"], "claim_test_id": row["claim_test_id"],
        "recipe_id": row["recipe_id"], "recipe_version_seq": row["recipe_version_seq"],
        "environment_id": row["environment_id"], "outcome": row["outcome"],
        "started_at": _iso(row["started_at"]), "finished_at": _iso(row["finished_at"]),
        "duration_ms": row["duration_ms"],
        "failure_category": row["failure_category"],
        "sf_error_code": row["sf_error_code"],
        "source": row["source"],
        "claim_kind": row["claim_kind"],
        "asserted_truth": row["asserted_truth"],
        "semantic_conditions": row["semantic_conditions"],
        "asserted_truth_pinned": asserted_pinned,
        "claim_version_drift": claim_version_drift,
        "recipe_semantic_fields": recipe_semantic_fields,
        "requirement_key": row["requirement_key"],
        "api_choice": ev.get("api_choice"),
        "steps": ev.get("steps") or [],
        "error": ev.get("error"),
        "interpretation": interp,
    }


def read_run_detail(tenant_id: int, run_id) -> dict:
    """Best-effort read of one run's detail. Never raises. Returns
    ``{available, found, run}`` — ``found=False`` when no run matches;
    ``available=False`` on any read error."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        from sqlalchemy.orm import Session
        with get_tenant_connection(tenant_id) as conn:
            session = Session(bind=conn)
            try:
                d = _read_run_detail(session, run_id)
                return {"available": True, "found": d is not None, "run": d}
            finally:
                session.close()
    except Exception as exc:
        log.warning("read_run_detail unavailable for tenant %s run %s: %s",
                    tenant_id, run_id, exc)
        return {"available": False, "found": False, "run": None}


# --- list all runs (the global runs index) (3b) ------------------------------

# D-231: the runs index is filterable (the failures front door). The COUNT uses
# this bare FROM; the page query appends two display-only joins (test_claims-current
# for the title + a LATERAL generated_from for the Jira key). Both are 1:0-or-1 — the
# LATERAL by LIMIT 1, the claim join by the one-current-version SCD invariant (valid_to
# IS NULL) — so they do NOT change row cardinality and total/page parity holds. The
# base s6_interpretations LEFT JOIN is likewise 1:0-or-1 (one S6 reading per run).
_RUNS_FROM = ("FROM s4_execution_runs r "
              "LEFT JOIN s6_interpretations i ON i.run_id = r.run_id")

# The run_outcome enum surface — the caller validates against this before passing
# an outcome filter (a bad value would just match nothing, but validating keeps the
# chips honest).
_RUN_OUTCOMES = frozenset({"passed", "failed", "errored", "skipped"})


def _runs_where(outcome, verdict, environment_id, since):
    """Build the shared WHERE fragment + bind params from the non-None filters
    (D-231). Only a supplied filter contributes a clause; an empty filter set
    yields no WHERE (the original newest-first-all behavior)."""
    clauses, params = [], {}
    if outcome:
        clauses.append("r.outcome::text = :outcome")
        params["outcome"] = outcome
    if verdict:
        clauses.append("i.verdict::text = :verdict")
        params["verdict"] = verdict
    if environment_id is not None:
        clauses.append("r.environment_id = :env")
        params["env"] = environment_id
    if since is not None:
        clauses.append("r.finished_at >= :since")
        params["since"] = since
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def _list_runs(conn, *, limit: int, offset: int, outcome=None, verdict=None,
               environment_id=None, since=None, labels=None):
    """Pure: (total, page-rows) of runs newest-first on an open tenant conn — S4
    outcome/timing LEFT JOINed to the S6 verdict (verdict NULL when absent),
    filtered by the optional (outcome / verdict / environment_id / since) facets
    (D-231). The COUNT uses the SAME FROM+WHERE so the total reflects the filter.
    ``labels`` (the org label map) renders titles in business language (D-267)."""
    where, params = _runs_where(outcome, verdict, environment_id, since)
    total = conn.execute(
        text(f"SELECT COUNT(*) {_RUNS_FROM}{where}"), params).scalar() or 0
    sql = (
        "SELECT CAST(r.run_id AS text) AS run_id, "
        "CAST(r.claim_test_id AS text) AS claim_test_id, "
        "r.outcome::text AS outcome, r.finished_at, r.duration_ms, r.environment_id, "
        "i.verdict::text AS verdict, "
        "c.claim_kind::text AS claim_kind, c.asserted_truth, c.semantic_conditions, "
        "req.external_key AS requirement_key "
        f"{_RUNS_FROM} "
        "LEFT JOIN test_claims c ON c.test_id = r.claim_test_id AND c.valid_to IS NULL "
        "LEFT JOIN LATERAL ("
        "  SELECT l.external_key FROM test_requirement_links l "
        "  WHERE l.test_id = r.claim_test_id AND l.link_kind = 'generated_from' "
        "  ORDER BY l.linked_at DESC LIMIT 1) req ON true "
        f"{where} "
        "ORDER BY r.finished_at DESC LIMIT :limit OFFSET :offset")
    rows = conn.execute(
        text(sql), {**params, "limit": limit, "offset": offset}).mappings().all()
    runs = [{"run_id": r["run_id"], "claim_test_id": r["claim_test_id"],
             "outcome": r["outcome"], "verdict": r["verdict"],
             "verdict_plain": verdict_plain(r["verdict"], r["outcome"]),
             "finished_at": _iso(r["finished_at"]),
             "finished_ago": time_ago(_iso(r["finished_at"])),
             "duration_ms": r["duration_ms"],
             "duration_h": duration_human(r["duration_ms"]),
             "environment_id": r["environment_id"],
             "requirement_key": r["requirement_key"],
             "title": (claim_title(r["claim_kind"], r["asserted_truth"], labels,
                                   semantic_conditions=r["semantic_conditions"])
                       if r["claim_kind"] else None)}
            for r in rows]
    return total, runs


def list_runs(tenant_id: int, *, page: int = 1, per_page: int = 20,
              outcome=None, verdict=None, environment_id=None, since=None) -> dict:
    """Best-effort paginated read of the tenant's S4 runs (newest-first), with
    optional triage filters (D-231: outcome / verdict / environment_id / since —
    the failures front door). Never raises. ``per_page`` capped at 50. Returns
    ``{available, runs, total, page, per_page, total_pages, filters}`` (``filters``
    echoes the applied facets so the surface can render active state)."""
    page = max(1, page)
    per_page = max(1, min(per_page, 50))
    filters = {"outcome": outcome, "verdict": verdict,
               "environment_id": environment_id, "since": since}
    try:
        from primeqa.intelligence.entity_labels import label_map
        from primeqa.semantic.connection import get_tenant_connection
        labels = label_map(tenant_id)        # business labels for the spine (D-267)
        with get_tenant_connection(tenant_id) as conn:
            total, runs = _list_runs(
                conn, limit=per_page, offset=(page - 1) * per_page,
                outcome=outcome, verdict=verdict,
                environment_id=environment_id, since=since, labels=labels)
        total_pages = max(1, (total + per_page - 1) // per_page)
        return {"available": True, "runs": runs, "total": total,
                "page": page, "per_page": per_page, "total_pages": total_pages,
                "filters": filters}
    except Exception as exc:
        log.warning("list_runs unavailable for tenant %s: %s", tenant_id, exc)
        return {"available": False, "runs": [], "total": 0,
                "page": page, "per_page": per_page, "total_pages": 1,
                "filters": filters}


# --- results overview: group by requirement / by root cause (triage at scale) -
# The Results page reads the *current state* of each test in scope — the LATEST
# run per claim_test within (environment, time-window) — then aggregates it two
# ways in pure Python: by the requirement (Jira) the test came from, and by the
# S6 cause_kind of each failure. The "real defect vs fixable" split reuses the
# repair agent's deterministic triage map (proposal_for). One DB hit feeds the
# health banner + both lenses.

_FAIL_OUTCOMES = ("failed", "errored")

_SCOPED_LATEST_SQL = (
    "WITH scoped AS ("
    "  SELECT DISTINCT ON (r.claim_test_id) "
    "    r.claim_test_id, CAST(r.run_id AS text) AS run_id, "
    "    r.outcome::text AS outcome, r.finished_at, r.duration_ms, r.environment_id, "
    "    i.verdict::text AS verdict, i.cause_kind "
    "  FROM s4_execution_runs r "
    "  LEFT JOIN s6_interpretations i ON i.run_id = r.run_id "
    "  {where} "
    "  ORDER BY r.claim_test_id, r.finished_at DESC) "
    "SELECT CAST(s.claim_test_id AS text) AS test_id, s.run_id, s.outcome, "
    "       s.finished_at, s.duration_ms, s.environment_id, s.verdict, s.cause_kind, "
    "       c.claim_kind::text AS claim_kind, c.asserted_truth, "
    "       c.semantic_conditions, "
    "       req.external_key AS requirement_key "
    "FROM scoped s "
    "LEFT JOIN test_claims c ON c.test_id = s.claim_test_id AND c.valid_to IS NULL "
    "LEFT JOIN LATERAL ("
    "  SELECT l.external_key FROM test_requirement_links l "
    "  WHERE l.test_id = s.claim_test_id AND l.link_kind = 'generated_from' "
    "  ORDER BY l.linked_at DESC LIMIT 1) req ON true")


def _scoped_latest(conn, *, environment_id=None, since=None):
    """Pure: the latest run per claim_test within (environment, since), enriched
    with the test's current claim_kind/asserted_truth (for the title) and its
    source requirement key. One row per test."""
    where, params = _runs_where(None, None, environment_id, since)
    sql = _SCOPED_LATEST_SQL.format(where=where)
    return conn.execute(text(sql), params).mappings().all()


def _row_title(r, labels=None):
    return (claim_title(r["claim_kind"], r["asserted_truth"], labels,
                        semantic_conditions=r.get("semantic_conditions"))
            if r["claim_kind"] else (r["test_id"][:8] if r["test_id"] else "test"))


def _aggregate_overview(rows, *, now=None, labels=None) -> dict:
    """Pure: turn the scoped latest-per-test rows into the health summary + the
    two lenses (by requirement, by root cause). ``now`` is injectable so the
    relative-time strings are deterministic in tests. ``labels`` (the org label
    map) renders titles in business language. No DB, no I/O beyond the
    ``proposal_for`` triage map."""
    from primeqa.intelligence.repair_agent import proposal_for

    rows = list(rows)
    failing = [r for r in rows if r["outcome"] in _FAIL_OUTCOMES]
    total, passed = len(rows), sum(1 for r in rows if r["outcome"] == "passed")
    summary = {
        "total": total, "passed": passed, "failing": len(failing),
        "pass_rate": round(passed / total * 100) if total else None,
        # count the cluster BUCKETS (an unattributed bucket is a cluster in the
        # by-cause lens too), so the banner number == len(by_cause).
        "causes": len({r["cause_kind"] or "__unattributed__" for r in failing}),
        "reqs_failing": len({r["requirement_key"] for r in failing
                             if r["requirement_key"]}),
    }

    # --- by requirement (every test, failing tests first within a group) ---
    req_map: dict = {}
    for r in rows:
        key = r["requirement_key"]
        g = req_map.setdefault(key or " unlinked", {
            "requirement_key": key, "tests": [], "passed": 0, "failed": 0,
            "last_finished": None})
        fin = _iso(r["finished_at"])
        g["tests"].append({
            "test_id": r["test_id"], "title": _row_title(r, labels),
            "outcome": r["outcome"], "verdict": r["verdict"],
            "verdict_plain": verdict_plain(r["verdict"], r["outcome"]),
            "finished_ago": time_ago(fin, now)})
        if r["outcome"] == "passed":
            g["passed"] += 1
        elif r["outcome"] in _FAIL_OUTCOMES:
            g["failed"] += 1
        if fin and (g["last_finished"] is None or fin > g["last_finished"]):
            g["last_finished"] = fin
    by_requirement = []
    for g in req_map.values():
        g["total"] = len(g["tests"])
        g["last_ago"] = time_ago(g["last_finished"], now)
        g["tests"].sort(key=lambda t: (t["outcome"] == "passed", t["title"] or ""))
        by_requirement.append(g)
    by_requirement.sort(key=lambda g: (g["failed"] == 0, -g["failed"], -g["total"]))

    # --- by root cause (failures only; a cause is a real defect unless EVERY
    #     failure under it is agent-fixable per proposal_for) ---
    cause_map: dict = {}
    for r in failing:
        ck = r["cause_kind"]
        g = cause_map.setdefault(ck or " unattributed", {
            "cause_kind": ck, "count": 0, "requirement_keys": set(),
            "tests": [], "_all_fixable": True})
        g["count"] += 1
        if r["requirement_key"]:
            g["requirement_keys"].add(r["requirement_key"])
        if proposal_for(r["verdict"], r["cause_kind"], r["outcome"]) is None:
            g["_all_fixable"] = False
        if len(g["tests"]) < 5:
            g["tests"].append({
                "test_id": r["test_id"], "title": _row_title(r, labels),
                "requirement_key": r["requirement_key"],
                "verdict_plain": verdict_plain(r["verdict"], r["outcome"]),
                "finished_ago": time_ago(_iso(r["finished_at"]), now)})
    by_cause = []
    for g in cause_map.values():
        g["cause_class"] = "fixable" if g.pop("_all_fixable") else "real_defect"
        g["cause_plain"] = cause_plain(g["cause_kind"]) or "Unattributed failure"
        g["requirement_count"] = len(g["requirement_keys"])
        del g["requirement_keys"]
        by_cause.append(g)
    by_cause.sort(key=lambda g: -g["count"])

    return {"summary": summary, "by_requirement": by_requirement,
            "by_cause": by_cause}


def runs_overview(tenant_id: int, *, environment_id=None, since=None) -> dict:
    """Best-effort: the Results-page overview — health summary + by-requirement +
    by-cause lenses over the latest run per test in scope. Never raises. Returns
    ``{available, summary, by_requirement, by_cause}``; ``available=False`` on any
    read error (page degrades to the empty state)."""
    try:
        from primeqa.intelligence.entity_labels import label_map
        from primeqa.semantic.connection import get_tenant_connection
        labels = label_map(tenant_id)        # business labels for the spine (D-267)
        with get_tenant_connection(tenant_id) as conn:
            rows = _scoped_latest(conn, environment_id=environment_id, since=since)
        return {"available": True, **_aggregate_overview(rows, labels=labels)}
    except Exception as exc:
        log.warning("runs_overview unavailable for tenant %s: %s", tenant_id, exc)
        return {"available": False, "summary": None,
                "by_requirement": [], "by_cause": []}


# --- approve a claim (the run-enabler) ---------------------------------------
# S3 generates claims as `draft` and recipes as `generated_unapproved`, neither
# of which `select_recipe_for_execution` will run (it needs an APPROVED claim +
# an active/approved recipe). This is the minimal human-approval step that makes
# a claim runnable — the generate→approve→run loop. (A richer review UI is the
# Area-2 deferred "reviews need a semantic rethink" bucket; this is the seam.)

def _approve_claim(session, test_id) -> dict:
    """Pure: promote the current claim version to ``approved`` and its
    unapproved current recipes to ``approved`` on an open session (humans-only
    per D-ε-1). Idempotent — an already-approved claim/recipe is a no-op.

    D-226: a DEPRECATED claim is refused — deprecation required an explicit
    human reason (D-ε-5); the generic Approve button must not silently undo it
    (and auto-enqueue runs of a superseded recipe). Reinstatement stays a
    deliberate coordinator capability (``promote_claim_to_approved`` keeps the
    documented deprecated→approved transition for explicit callers). The
    recipe loop likewise never resurrects a deprecated recipe."""
    from primeqa.test_representation import SemanticTransactionCoordinator
    coord = SemanticTransactionCoordinator()
    tid = UUID(str(test_id))
    claim = coord.get_latest_claim(session, tid)
    if claim is None:
        return {"ok": False, "error": "No current claim for this id."}
    if claim.status == "deprecated":
        return {"ok": False,
                "error": ("This claim is deprecated (a recorded human "
                          "decision). It cannot be re-approved from here — "
                          "regenerate the requirement for a fresh claim, or "
                          "reinstate it deliberately via the coordinator.")}
    coord.promote_claim_to_approved(
        session, actor="human", test_id=tid, version_seq=claim.version_seq)
    promoted = 0
    for r in coord.list_active_recipes(session, tid):
        if r.status == "deprecated":
            continue                    # D-226: never silently un-deprecate
        if r.status not in ("active", "approved"):
            coord.promote_recipe_to_approved(
                session, actor="human", recipe_id=r.recipe_id, version_seq=r.version_seq)
            promoted += 1
    return {"ok": True, "status": "approved", "recipes_approved": promoted}


def approve_claim(tenant_id: int, test_id) -> dict:
    """Best-effort: approve the claim + its recipes so it becomes runnable. Never
    raises. ``get_tenant_connection`` commits on clean exit (the promotes are one
    atomic transaction). Returns ``{ok, status, recipes_approved}`` or
    ``{ok: False, error}``."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        from sqlalchemy.orm import Session
        with get_tenant_connection(tenant_id) as conn:
            session = Session(bind=conn)
            try:
                out = _approve_claim(session, test_id)
                session.flush()
            finally:
                session.close()
        # Approval is DECISION-ONLY. D-199's trigger 1 (auto-enqueue the
        # approved claim on every "safe" environment) was REVERSED by AK
        # 2026-07-07: the env fan-out ran each approval on every connected
        # sandbox-flagged env — including a mis-flagged real production org —
        # and cross-org runs of single-org-grounded claims are noise anyway.
        # Runs are explicit: the requirement page's Run button, /run, the
        # schedules, and the CI webhook. If a run-on-approval trigger ever
        # returns, it must be org-scoped (the claim's own grounding org) and
        # opt-in per tenant.
        return out
    except Exception as exc:
        log.warning("approve_claim failed for tenant %s test %s: %s",
                    tenant_id, test_id, exc)
        return {"ok": False, "error": str(exc)}


def _deprecate_claim(session, test_id, reason: str) -> dict:
    """Pure: deprecate the current claim version with a REQUIRED human reason
    (D-228 / F3 — supersession is a human judgment, never auto-derived). The
    reason lands in provenance per D-ε-5. Claim-only: deprecating the claim
    makes every recipe unselectable (selection requires a current APPROVED
    claim), so the recipes keep their own status untouched. Idempotent — an
    already-deprecated claim is a no-op."""
    from primeqa.test_representation import SemanticTransactionCoordinator
    if not isinstance(reason, str) or not reason.strip():
        return {"ok": False, "error": "A reason is required to deprecate."}
    coord = SemanticTransactionCoordinator()
    tid = UUID(str(test_id))
    claim = coord.get_latest_claim(session, tid)
    if claim is None:
        return {"ok": False, "error": "No current claim for this id."}
    if claim.status == "deprecated":
        return {"ok": True, "status": "deprecated", "already": True}
    coord.deprecate_claim(session, actor="human", test_id=tid,
                          version_seq=claim.version_seq, reason=reason.strip())
    return {"ok": True, "status": "deprecated", "already": False}


def deprecate_claim(tenant_id: int, test_id, reason: str) -> dict:
    """Best-effort: deprecate the claim (with reason) so it stops grading
    releases and stops being selectable for runs. Never raises;
    ``get_tenant_connection`` commits on clean exit."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        from sqlalchemy.orm import Session
        with get_tenant_connection(tenant_id) as conn:
            session = Session(bind=conn)
            try:
                out = _deprecate_claim(session, test_id, reason)
                session.flush()
            finally:
                session.close()
        return out
    except Exception as exc:
        log.warning("deprecate_claim failed for tenant %s test %s: %s",
                    tenant_id, test_id, exc)
        return {"ok": False, "error": str(exc)}


# (D-199 trigger 1 — auto-enqueue on approval — REMOVED 2026-07-07 per AK:
# approval is decision-only; see the note in approve_claim.)


# --- D-199 trigger 3: bulk-enqueue a release's claims (the CI gate re-verify) --

def enqueue_claims_for_keys(tenant_id: int, external_keys, environment_id: int,
                            *, created_by=None) -> dict:
    """Enqueue every ``generated_from`` claim behind ``external_keys`` for
    execution on ``environment_id`` (deduped; best-effort per claim). The CI
    webhook's re-verify path — CI then polls /status for the D-198 substrate
    verdict over FRESH evidence. Never raises."""
    keys = [k for k in (external_keys or []) if k]
    if not keys:
        return {"enqueued": [], "claim_count": 0}
    try:
        from sqlalchemy.orm import Session

        from primeqa.execution_engine.intake import enqueue_s4_execution
        from primeqa.semantic.connection import get_tenant_connection
        from primeqa.test_representation.coordinator import (
            SemanticTransactionCoordinator,
        )
        coord = SemanticTransactionCoordinator()
        test_ids, seen = [], set()
        with get_tenant_connection(tenant_id) as conn:
            session = Session(bind=conn)
            try:
                for key in keys:
                    for m in coord.list_tests_by_requirement(
                            session, external_system="jira", external_key=key,
                            link_kind="generated_from"):
                        sid = str(m.test_id)
                        if sid not in seen:
                            seen.add(sid)
                            test_ids.append(m.test_id)
            finally:
                session.close()
        jobs = []
        skipped_unexecutable = 0
        for tid in test_ids:
            try:
                job = enqueue_s4_execution(
                    tenant_id=tenant_id, test_id=tid,
                    environment_id=environment_id, created_by=created_by)
                jobs.append(job.id)
            except UnexecutableClaimError as exc:
                # D-223: shape refusal — skip this claim, never the batch.
                log.info("release-claim enqueue gated tenant %s test %s: %s",
                         tenant_id, tid, exc)
                skipped_unexecutable += 1
            except Exception as exc:
                log.warning("release-claim enqueue failed tenant %s test %s: %s",
                            tenant_id, tid, exc)
        return {"enqueued": jobs, "claim_count": len(test_ids),
                "skipped_unexecutable": skipped_unexecutable}
    except Exception as exc:
        log.warning("enqueue_claims_for_keys failed for tenant %s: %s",
                    tenant_id, exc)
        return {"enqueued": [], "claim_count": 0}


# --- D-214 trigger: enqueue EVERY approved claim (the scheduled regression) ---

def enqueue_all_approved_claims(tenant_id: int, environment_id: int,
                                *, created_by=None) -> dict:
    """Enqueue every currently-APPROVED claim for execution on
    ``environment_id`` — the scheduled-regression trigger's body (D-214).
    Deduped by the job store's active-set semantics; best-effort per claim.
    Never raises."""
    try:
        from primeqa.execution_engine.intake import enqueue_s4_execution
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            test_ids = [r[0] for r in conn.execute(text(
                "SELECT test_id FROM test_claims "
                "WHERE status = 'approved' AND valid_to IS NULL "
                "ORDER BY test_id")).fetchall()]
        jobs = []
        skipped_unexecutable = 0
        for tid in test_ids:
            try:
                job = enqueue_s4_execution(
                    tenant_id=tenant_id, test_id=tid,
                    environment_id=environment_id, created_by=created_by)
                jobs.append(job.id)
            except UnexecutableClaimError as exc:
                # D-223: shape refusal — skip this claim, never the batch.
                log.info("scheduled enqueue gated tenant %s test %s: %s",
                         tenant_id, tid, exc)
                skipped_unexecutable += 1
            except Exception as exc:
                log.warning("scheduled enqueue failed tenant %s test %s: %s",
                            tenant_id, tid, exc)
        return {"enqueued": jobs, "claim_count": len(test_ids),
                "skipped_unexecutable": skipped_unexecutable}
    except Exception as exc:
        log.warning("enqueue_all_approved_claims failed for tenant %s: %s",
                    tenant_id, exc)
        return {"enqueued": [], "claim_count": 0}

# --- D-219 slice 3: the substrate run page's requirement list --------------

def list_runnable_requirements(tenant_id: int) -> dict:
    """Best-effort: every requirement external key with >=1 APPROVED claim,
    with the approved-claim count (the /run page's picker rows). Never
    raises. Returns ``{available, rows: [{key, approved_claims}]}``."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            rows = conn.execute(text(
                "SELECT l.external_key AS key, "
                "       COUNT(DISTINCT l.test_id) AS approved_claims "
                "FROM test_requirement_links l "
                "JOIN test_claims c ON c.test_id = l.test_id "
                "  AND c.valid_to IS NULL AND c.status = 'approved' "
                "WHERE l.link_kind = 'generated_from' "
                "GROUP BY l.external_key ORDER BY l.external_key"
            )).mappings().all()
            return {"available": True,
                    "rows": [dict(r) for r in rows]}
    except Exception as exc:
        log.warning("list_runnable_requirements unavailable for tenant %s: %s",
                    tenant_id, exc)
        return {"available": False, "rows": []}


# --- per-requirement last-run health (the /run picker's decide signal) --------
# So the bulk-run picker isn't blind: for each requirement, the latest run per
# APPROVED test → passed/failed/never-run, so "re-run the red ones" beats "run
# everything and hope". Environment-agnostic (the page picks the env after
# landing): the latest run for each test in ANY environment is its current state.

def _requirement_health_rows(conn, keys):
    """Pure: one row per approved test for the given requirement keys, carrying
    that test's LATEST run outcome (NULL when never run) + finished_at.

    One row per (test, key): the link PK is unique per (test_id, external_system,
    external_key, link_kind) and external_system has a single value today, so the
    per-key row count equals the picker's COUNT(DISTINCT test_id) — the health
    ``total`` and the "N tests" count stay in lockstep."""
    from sqlalchemy import bindparam
    stmt = text(
        "SELECT l.external_key AS key, lastrun.outcome AS outcome, "
        "       lastrun.finished_at AS finished_at "
        "FROM test_requirement_links l "
        "JOIN test_claims c ON c.test_id = l.test_id "
        "  AND c.valid_to IS NULL AND c.status = 'approved' "
        "LEFT JOIN LATERAL ("
        "  SELECT r.outcome::text AS outcome, r.finished_at "
        "  FROM s4_execution_runs r "
        "  WHERE r.claim_test_id = l.test_id "
        "  ORDER BY r.finished_at DESC LIMIT 1) lastrun ON true "
        "WHERE l.link_kind = 'generated_from' AND l.external_key IN :keys"
    ).bindparams(bindparam("keys", expanding=True))
    return conn.execute(stmt, {"keys": list(keys)}).mappings().all()


def _aggregate_req_health(rows, *, now=None) -> dict:
    """Pure: ``{key: {total, run, passed, failed, last_ago}}`` over the latest run
    per approved test. ``total`` counts every approved test; ``run`` only those
    with a run. ``now`` is injectable for deterministic relative-time tests."""
    out: dict = {}
    for r in rows:
        g = out.setdefault(r["key"], {"total": 0, "run": 0, "passed": 0,
                                      "failed": 0, "last_finished": None})
        g["total"] += 1
        if r["outcome"] is not None:
            g["run"] += 1
            if r["outcome"] == "passed":
                g["passed"] += 1
            elif r["outcome"] in ("failed", "errored"):
                g["failed"] += 1
        fin = _iso(r["finished_at"])
        if fin and (g["last_finished"] is None or fin > g["last_finished"]):
            g["last_finished"] = fin
    for g in out.values():
        g["last_ago"] = time_ago(g["last_finished"], now)
    return out


def requirement_run_health(tenant_id: int, keys) -> dict:
    """Best-effort: per-requirement last-run health for the /run picker. Never
    raises → ``{}`` on any read error (the page still renders without chips)."""
    keys = list(keys)
    if not keys:
        return {}
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            rows = _requirement_health_rows(conn, keys)
        return _aggregate_req_health(rows)
    except Exception as exc:
        log.warning("requirement_run_health unavailable for tenant %s: %s",
                    tenant_id, exc)
        return {}


# --- per-test latest run (the requirement detail's last-run chips) ------------

def _latest_run_rows_by_test(conn, test_ids, environment_id=None):
    """Pure: the latest S4 run per claim test id, restricted to the passed ids.
    One row per test (DISTINCT ON … finished_at DESC → latest-wins). Ids compare
    as text so callers pass the string test_ids the S2 read already carries.
    ``environment_id`` restricts to one connected org's runs (None = any org,
    the tenant-wide latest)."""
    from sqlalchemy import bindparam
    stmt = text(
        "SELECT DISTINCT ON (claim_test_id) "
        "  CAST(claim_test_id AS text) AS test_id, CAST(run_id AS text) AS run_id, "
        "  outcome::text AS outcome, finished_at, environment_id "
        "FROM s4_execution_runs "
        "WHERE CAST(claim_test_id AS text) IN :ids "
        "AND (CAST(:env AS int) IS NULL OR environment_id = :env) "
        "ORDER BY claim_test_id, finished_at DESC"
    ).bindparams(bindparam("ids", expanding=True))
    return conn.execute(stmt, {"ids": list(test_ids),
                               "env": environment_id}).mappings().all()


def latest_runs_by_test(tenant_id: int, test_ids, environment_id=None) -> dict:
    """Best-effort: ``{test_id: {run_id, outcome, finished_at, last_ago,
    environment_id}}`` — the latest run per test, batched (one query). Powers
    the requirement detail's per-test last-run chip, each linking to the run's
    evidence page (``/runs/<run_id>``). ``environment_id`` scopes the read to
    one connected org; the payload always carries the run's env so multi-org
    surfaces can label which org the chip's outcome came from. Never raises →
    ``{}`` (the page renders without chips)."""
    ids = [str(t) for t in (test_ids or []) if t]
    if not ids:
        return {}
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            rows = _latest_run_rows_by_test(conn, ids, environment_id)
        return {r["test_id"]: {
            "run_id": r["run_id"], "outcome": r["outcome"],
            "finished_at": _iso(r["finished_at"]),
            "last_ago": time_ago(_iso(r["finished_at"])),
            "environment_id": r["environment_id"],
        } for r in rows}
    except Exception as exc:
        log.warning("latest_runs_by_test unavailable for tenant %s: %s",
                    tenant_id, exc)
        return {}


# --- per-org RESULT diff (per-org Slice 5, Leg B) ----------------------------
# Compare the SAME claim's latest run OUTCOME across two orgs (environments). The
# claim identity (``claim_test_id`` == ``TestClaim.test_id``) is shared across orgs
# (claims are org-blind, D-257), so a claim run against both A and B yields a
# cross-org pass/fail comparison — the per-org payoff: "same test, different org,
# different result". v1 compares OUTCOME (passed/failed/errored/skipped); the S6
# cause attribution (cause_kind/vr_name) is a DEFERRED richer overlay. Pure read;
# no new index (179 runs today — an optional (claim_test_id, environment_id,
# finished_at DESC) index is deferred until volume warrants it). Latest-run-wins is
# the SQL DISTINCT ON; ``_aggregate_result_diff`` also defends it in Python (max
# finished_at per (claim, env)).

_RESULT_DIFF_SQL = (
    "SELECT DISTINCT ON (claim_test_id, environment_id) "
    "  CAST(claim_test_id AS text) AS claim_test_id, environment_id, "
    "  outcome::text AS outcome, finished_at "
    "FROM s4_execution_runs "
    "WHERE environment_id IN (:env_a, :env_b) "
    "ORDER BY claim_test_id, environment_id, finished_at DESC")


def _result_diff_rows(conn, environment_id_a, environment_id_b):
    """Pure read: the latest run per (claim_test_id, environment_id) restricted to
    the two environments (DISTINCT ON … ORDER BY finished_at DESC → latest-wins).
    One row per (claim, env)."""
    return conn.execute(
        text(_RESULT_DIFF_SQL),
        {"env_a": environment_id_a, "env_b": environment_id_b}).mappings().all()


def _aggregate_result_diff(rows, environment_id_a, environment_id_b) -> dict:
    """Pure: group the latest-per-(claim, env) rows by ``claim_test_id`` into a
    cross-org comparison. Defensive latest-run-wins — if >1 row arrives for a
    (claim, env), the later ``finished_at`` wins (the SQL already dedups; this
    keeps the aggregator correct on un-deduplicated input + unit-testable).

    ``status`` per claim:
      * ``agree``        — run in BOTH envs, same outcome
      * ``differ``       — run in both, DIFFERENT outcome
      * ``missing_in_a`` — run in B only (never run in A)
      * ``missing_in_b`` — run in A only (never run in B)

    Returns ``{claims: {claim_test_id: {outcome_a, outcome_b, status}}, totals}``.
    A claim with no run in EITHER env is absent (nothing to compare)."""
    latest: dict = {}      # claim_test_id -> {"a": (finished_at, outcome), "b": …}
    for r in rows:
        env = r["environment_id"]
        if env == environment_id_a:
            slot = "a"
        elif env == environment_id_b:
            slot = "b"
        else:
            continue                                  # defensive: not one of the two
        fin = r["finished_at"]
        g = latest.setdefault(r["claim_test_id"], {})
        cur = g.get(slot)
        if cur is None or (fin is not None and (cur[0] is None or fin > cur[0])):
            g[slot] = (fin, r["outcome"])

    claims: dict = {}
    totals = {"agree": 0, "differ": 0, "missing_in_a": 0, "missing_in_b": 0}
    for cid, g in latest.items():
        oa = g["a"][1] if "a" in g else None
        ob = g["b"][1] if "b" in g else None
        if oa is None:
            status = "missing_in_a"
        elif ob is None:
            status = "missing_in_b"
        elif oa == ob:
            status = "agree"
        else:
            status = "differ"
        totals[status] += 1
        claims[cid] = {"outcome_a": oa, "outcome_b": ob, "status": status}
    return {"claims": claims, "totals": totals}


def result_diff(tenant_id: int, environment_id_a: int, environment_id_b: int) -> dict:
    """Best-effort per-org RESULT diff: for each claim, the latest run OUTCOME in
    environment A vs B (the two orgs), classified agree | differ | missing_in_a |
    missing_in_b (per-org Slice 5, Leg B). Resolves each ``environment_id`` → org
    via ``get_connected_org_for_environment`` (the single per-org resolution seam,
    D-257) for the output labels. v1 compares OUTCOME only; S6 cause attribution is
    a deferred overlay. **Pure read.** Never raises → ``available=False`` on any
    read error. Returns ``{available, environment_id_a, environment_id_b, org_a,
    org_b, totals, claims}``."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        from primeqa.sync.credentials import get_connected_org_for_environment
        with get_tenant_connection(tenant_id) as conn:
            org_a = get_connected_org_for_environment(conn, environment_id_a)
            org_b = get_connected_org_for_environment(conn, environment_id_b)
            rows = _result_diff_rows(conn, environment_id_a, environment_id_b)
            agg = _aggregate_result_diff(rows, environment_id_a, environment_id_b)
        return {"available": True,
                "environment_id_a": environment_id_a,
                "environment_id_b": environment_id_b,
                "org_a": org_a, "org_b": org_b,
                "totals": agg["totals"], "claims": agg["claims"]}
    except Exception as exc:
        log.warning("result_diff unavailable for tenant %s envs %s/%s: %s",
                    tenant_id, environment_id_a, environment_id_b, exc)
        return {"available": False,
                "environment_id_a": environment_id_a,
                "environment_id_b": environment_id_b,
                "org_a": None, "org_b": None,
                "totals": {"agree": 0, "differ": 0,
                           "missing_in_a": 0, "missing_in_b": 0},
                "claims": {}}
