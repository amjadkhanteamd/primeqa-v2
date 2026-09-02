"""The UI-conformance REPORT bridge — read-only tenant reads for the
report slice (SDLC v3 sequence item 2; D-474).

The web tier's window onto the substrate's stored UI-conformance
records: processing runs, verdict listings, stored comparisons, the
coverage read, and on-demand evidence link minting. Everything here is
a READ over recorded rows — no verdict is computed, no comparison is
persisted, no scan is enqueued (the pages this feeds are the demo
surface, not an execution surface). Best-effort like the S4 console
bridge: every entry returns ``{available: bool, ...}`` and never
raises into a page render.

The bearer rule (evidence): signed URLs are minted ON DEMAND for the
authorised session's tenant through ``evidence.sign_url`` (which
refuses + audits a foreign-tenant key), returned in the response body
only, and NEVER logged — no URL string reaches a logger or a stored
row here.
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("primeqa.ui_report")

PUBLIC_STANDARDS = ("WCAG22", "EN301549", "SECTION508")


def _tenant_session(tenant_id: int):
    from primeqa.semantic.connection import get_tenant_connection
    return get_tenant_connection(tenant_id)


def _best_effort(tenant_id: int, fn, label: str) -> dict:
    try:
        with _tenant_session(tenant_id) as conn:
            session = Session(bind=conn)
            try:
                out = fn(session)
                return {"available": True, **out}
            finally:
                session.close()
    except Exception as exc:  # noqa: BLE001 — page renders degrade, never 500
        log.warning("ui_report %s unavailable for tenant %s: %s",
                    label, tenant_id, exc)
        return {"available": False}


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

def list_processing_runs(tenant_id: int) -> dict:
    def _read(session):
        rows = session.execute(text("""
            SELECT p.job_id, p.claim_set_id, p.engine, p.engine_version,
                   p.verdict_counts, p.no_verdict_members, p.processed_at,
                   m.payload->'pins'->>'catalogue_release_id' AS release,
                   jsonb_array_length(m.payload->'surfaces') AS surfaces,
                   m.payload->'auth'->>'mode' AS auth_mode,
                   j.status AS job_status
            FROM s6_ui_processing_runs p
            JOIN s4_ui_run_manifests m ON m.id = p.manifest_id
            LEFT JOIN s4_ui_inspection_jobs j ON j.id = p.job_id
            ORDER BY p.processed_at DESC
            LIMIT 50
        """)).fetchall()
        return {"runs": [{
            "job_id": str(r[0]), "claim_set_id": str(r[1]),
            "engine": f"{r[2]} {r[3]}", "verdict_counts": r[4] or {},
            "no_verdict_members": r[5], "processed_at": r[6],
            "catalogue_release_id": r[7], "surfaces": r[8],
            "auth_mode": r[9] or "guest",
            "job_status": r[10] or "(job row absent)",
        } for r in rows]}
    return _best_effort(tenant_id, _read, "list_processing_runs")


def available_standards(session) -> list:
    """The three platform standards plus every ACTIVE customer profile,
    as selector options."""
    out = list(PUBLIC_STANDARDS)
    if session.execute(text(
            "SELECT to_regclass('cust_profile_sets')")).scalar() is not None:
        for (key,) in session.execute(text(
                "SELECT profile_key FROM cust_profile_sets "
                "WHERE state='ACTIVE' ORDER BY profile_key")):
            out.append(f"CUSTOM:{key}")
    return out


def _rule_titles(session, rule_ids: set) -> dict:
    titles: dict = {}
    public = [r for r in rule_ids if not r.startswith("PLM-CUST-")]
    custom = [r for r in rule_ids if r.startswith("PLM-CUST-")]
    if public:
        for rid, name in session.execute(text("""
            SELECT DISTINCT ON (rule_id) rule_id, name
            FROM s5_rule_versions WHERE rule_id = ANY(:ids)
            ORDER BY rule_id, version DESC
        """), {"ids": public}):
            titles[rid] = name
    if custom:
        for rid, name in session.execute(text("""
            SELECT DISTINCT ON (rule_id) rule_id, name
            FROM cust_rule_versions WHERE rule_id = ANY(:ids)
            ORDER BY rule_id, version DESC
        """), {"ids": custom}):
            titles[rid] = name
    return titles


def _standard_rule_ids(session, standard: str) -> set | None:
    """The rule ids the chosen standard projects — the filter set. None
    means 'no projection filter' (the all-rules listing)."""
    if not standard:
        return None
    if standard.startswith("CUSTOM:"):
        key = standard[len("CUSTOM:"):]
        rows = session.execute(text("""
            SELECT v.rule_id FROM cust_rule_versions v
            WHERE v.state = 'ACTIVE'
              AND v.definition->'criterion'->>'profile' IN (
                  SELECT c.criterion FROM cust_profile_criteria c
                  JOIN cust_profile_sets s ON s.id = c.set_id
                  WHERE s.profile_key = :k AND s.state = 'ACTIVE')
        """), {"k": key}).fetchall()
    else:
        rows = session.execute(text("""
            SELECT DISTINCT m.rule_id
            FROM s5_standard_maps m
            JOIN s5_standard_map_sets s ON s.id = m.map_set_id
            WHERE s.standard = :s AND s.state = 'ACTIVE'
        """), {"s": standard}).fetchall()
    return {r[0] for r in rows}


def run_report(tenant_id: int, job_id: str, *, standard: str = "WCAG22",
               verdict: str | None = None, surface: str | None = None,
               page: int = 1, per_page: int = 50) -> dict:
    """One processing run's verdict listing, filtered, with the honesty
    header VERBATIM from standard_view for the chosen standard."""
    def _read(session):
        from primeqa.interpretation.standard_view import (
            StandardViewError, standard_view)

        per = min(max(int(per_page), 1), 50)
        off = (max(int(page), 1) - 1) * per

        header = None
        header_error = None
        try:
            view = standard_view(session, standard=standard,
                                 job_id=job_id)
            header = view["header"]
            denominator = view.get("denominator")
        except StandardViewError as exc:
            header_error = str(exc)
            denominator = None

        rule_filter = _standard_rule_ids(session, standard)
        where = ["v.job_id = :j"]
        params: dict = {"j": str(job_id), "lim": per, "off": off}
        if verdict:
            where.append("v.verdict = :v")
            params["v"] = verdict
        if surface:
            where.append("v.surface_key = :sk")
            params["sk"] = surface
        if rule_filter is not None:
            where.append("v.plimsol_rule_id = ANY(:rids)")
            params["rids"] = sorted(rule_filter)
        w = " AND ".join(where)
        total = session.execute(text(
            f"SELECT COUNT(*) FROM s6_ui_verdicts v WHERE {w}"),
            params).scalar_one()
        rows = session.execute(text(f"""
            SELECT v.test_id, v.plimsol_rule_id, v.surface_key, v.verdict,
                   v.verdict_basis->>'reason' AS reason,
                   v.ownership, v.owner_bundle_ref,
                   r.evidence_state,
                   (r.evidence_keys IS NOT NULL) AS has_evidence
            FROM s6_ui_verdicts v
            LEFT JOIN s4_ui_inspection_results r
              ON r.job_id = v.job_id AND r.surface_key = v.surface_key
            WHERE {w}
            ORDER BY CASE v.verdict WHEN 'FAIL' THEN 0
                     WHEN 'NEEDS_HUMAN' THEN 1 WHEN 'NOT_DETERMINED' THEN 2
                     ELSE 3 END, v.plimsol_rule_id, v.surface_key
            LIMIT :lim OFFSET :off
        """), params).fetchall()
        titles = _rule_titles(session, {r[1] for r in rows})
        surfaces = [s0 for (s0,) in session.execute(text(
            "SELECT DISTINCT surface_key FROM s6_ui_verdicts "
            "WHERE job_id = :j ORDER BY 1"), {"j": str(job_id)})]
        counts = dict(session.execute(text(
            "SELECT verdict, COUNT(*) FROM s6_ui_verdicts "
            "WHERE job_id = :j GROUP BY 1"), {"j": str(job_id)}).fetchall())
        return {
            "header": header, "header_error": header_error,
            "denominator": denominator,
            "standards": available_standards(session),
            "surfaces": surfaces, "verdict_counts": counts,
            "total": total, "page": max(int(page), 1), "per_page": per,
            "verdicts": [{
                "test_id": str(r[0]), "rule_id": r[1],
                "rule_title": titles.get(r[1], ""),
                "surface_key": r[2], "verdict": r[3], "reason": r[4],
                "ownership": r[5], "owner_bundle_ref": r[6],
                "evidence_state": r[7], "has_evidence": bool(r[8]),
            } for r in rows],
        }
    return _best_effort(tenant_id, _read, "run_report")


# ---------------------------------------------------------------------------
# Comparison (stored runs only — this surface never computes one)
# ---------------------------------------------------------------------------

def comparison_report(tenant_id: int, baseline_job_id: str,
                      candidate_job_id: str) -> dict:
    def _read(session):
        run = session.execute(text("""
            SELECT id, outcome, refusal_reason, tool_drift, env_delta,
                   transition_counts, created_at
            FROM s6_ui_comparison_runs
            WHERE baseline_job_id = :b AND candidate_job_id = :c
            ORDER BY created_at DESC LIMIT 1
        """), {"b": str(baseline_job_id),
               "c": str(candidate_job_id)}).fetchone()
        if run is None:
            return {"found": False,
                    "note": "no recorded comparison for this pair — "
                            "comparisons are computed by the pipeline, "
                            "never by this page (read-only)"}
        rows = session.execute(text("""
            SELECT transition, from_verdict, to_verdict, drift,
                   fingerprint_delta, causal, surface_key, plimsol_rule_id
            FROM s6_ui_verdict_transitions
            WHERE comparison_id = :i
            ORDER BY surface_key, plimsol_rule_id
        """), {"i": run[0]}).fetchall()
        titles = _rule_titles(session, {r[7] for r in rows if r[7]})
        groups: dict = {}
        for r in rows:
            causal = r[5] or {}
            # the comparator RECORDS the non-comparability reason on the
            # transition row itself (causal.reason) — read it, derive
            # nothing (D-281 posture)
            reason = (causal.get("reason")
                      if r[0] == "NOT_COMPARABLE" else None)
            groups.setdefault(r[0], []).append({
                "from_verdict": r[1], "to_verdict": r[2],
                "drift": bool(r[3]), "fingerprint_delta": r[4],
                "causal": causal, "surface_key": r[6],
                "rule_id": r[7], "rule_title": titles.get(r[7], ""),
                "not_comparable_reason": reason,
            })
        return {
            "found": True, "comparison_id": str(run[0]),
            "outcome": run[1], "refusal_reason": run[2],
            "tool_drift": run[3] or {}, "env_delta": run[4] or {},
            "transition_counts": run[5] or {}, "created_at": run[6],
            "groups": groups,
            "taxonomy": ["NEW_FAIL", "FIXED", "STILL_FAILING",
                         "STILL_PASSING", "NEW_CLAIM", "RETIRED_CLAIM",
                         "NOT_COMPARABLE", "NOT_RUN"],
        }
    return _best_effort(tenant_id, _read, "comparison_report")


# ---------------------------------------------------------------------------
# Coverage (the Part 3 read, rendered)
# ---------------------------------------------------------------------------

def coverage_report(tenant_id: int, job_id: str) -> dict:
    def _read(session):
        from primeqa.interpretation.standard_view import (
            StandardViewError, standard_view)
        out = []
        for std in available_standards(session):
            try:
                v = standard_view(session, standard=std, job_id=job_id)
            except StandardViewError as exc:
                out.append({"standard": std, "error": str(exc)})
                continue
            not_covered = [r for r in v["criteria"]
                           if r.get("in_scope", True)
                           and r["coverage"] == "NOT_COVERED"]
            out.append({
                "standard": std, "header": v["header"],
                "denominator": v.get("denominator"),
                "coverage_counts": v["coverage_counts"],
                "criterion_verdict_counts": v["criterion_verdict_counts"],
                "not_covered": [{"criterion": r["criterion"],
                                 "title": r.get("title"),
                                 "level": r.get("level")}
                                for r in not_covered],
                "refusals": v.get("refusals"),
                "orphan_rules": v.get("orphan_rules", []),
            })
        return {"standards": out}
    return _best_effort(tenant_id, _read, "coverage_report")


# ---------------------------------------------------------------------------
# Evidence links — minted on demand, returned in-body, never logged
# ---------------------------------------------------------------------------

def evidence_links(tenant_id: int, job_id: str, surface_key: str) -> dict:
    def _read(session):
        from primeqa.browser_worker import evidence as ev
        row = session.execute(text("""
            SELECT evidence_keys, evidence_state
            FROM s4_ui_inspection_results
            WHERE job_id = :j AND surface_key = :sk
        """), {"j": str(job_id), "sk": surface_key}).fetchone()
        if row is None or not row[0]:
            return {"found": False, "links": [],
                    "note": "no evidence keys recorded for this surface"}
        try:
            s3 = ev.client()
            bucket = ev.bucket_name()
        except Exception:
            # the store's credentials live on the browser-worker service;
            # a web tier without them degrades honestly, never 500s
            return {"found": True, "links": [],
                    "evidence_state": row[1],
                    "note": "evidence store not configured on this "
                            "service — links cannot be minted here"}
        links = []
        # evidence_keys is {kind: key} (e.g. screenshot / observation)
        items = (row[0].items() if isinstance(row[0], dict)
                 else [(k.rsplit(".", 1)[-1], k) for k in row[0]])
        for kind, key in items:
            if not isinstance(key, str):
                continue
            links.append({
                "kind": kind,
                "url": ev.sign_url(session, s3, bucket, key),
            })
        return {"found": True, "evidence_state": row[1], "links": links}
    return _best_effort(tenant_id, _read, "evidence_links")
