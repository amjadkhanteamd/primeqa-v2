"""compare_jobs — result-processing side of ui-s2.4.

Pure comparison of two executions of ONE manifest. The worker records
observations; this module labels comparisons between them:

    NOT_SAME_MANIFEST  refuse: the jobs executed different manifests
    TOOL_DRIFT         refuse: observed tool versions contradict the
                       manifest pins (integrity assertion — pins live in
                       the shared manifest, so this fires only on data
                       corruption or a worker that ignored its manifest)
    NOT_COMPARABLE     per surface: no clean observation on one side, or
                       the fingerprints differ (the page was not the same
                       page — comparing observations would be dishonest)
    DIFFERS            per surface: same fingerprint, different
                       observation sets (causal assessment is Phase 7;
                       the spike stops at the honest label)
    SAME               per surface: same fingerprint, same observations

compare_jobs is pure — callers fetch rows, it judges nothing about the
page itself. load_job_bundle is the DB-loading convenience for the
manual verification CLI.
"""

from __future__ import annotations

NOT_SAME_MANIFEST = "NOT_SAME_MANIFEST"
TOOL_DRIFT = "TOOL_DRIFT"
NOT_COMPARABLE = "NOT_COMPARABLE"
DIFFERS = "DIFFERS"
SAME = "SAME"


def _observation_set(obs: dict) -> dict:
    eo = obs.get("engine_observations") or {}
    return {
        "violations": {v["id"]: len(v.get("nodes", []))
                       for v in eo.get("violations", [])},
        "counts": [eo.get("violations_count"), eo.get("passes_count"),
                   eo.get("incomplete_count")],
    }


def _fingerprint_delta(sum_a: dict, sum_b: dict) -> dict:
    roles_a, roles_b = sum_a.get("roles", {}), sum_b.get("roles", {})
    role_diffs = {
        r: [roles_a.get(r, 0), roles_b.get(r, 0)]
        for r in sorted(set(roles_a) | set(roles_b))
        if roles_a.get(r, 0) != roles_b.get(r, 0)
    }
    named_a = {tuple(p) for p in sum_a.get("named", [])}
    named_b = {tuple(p) for p in sum_b.get("named", [])}
    return {
        "element_count": [sum_a.get("element_count"),
                          sum_b.get("element_count")],
        "role_diffs": role_diffs,
        "named_removed": sorted(list(p) for p in named_a - named_b),
        "named_added": sorted(list(p) for p in named_b - named_a),
    }


def _surface_problem(obs: dict | None, side: str) -> str | None:
    if obs is None:
        return f"{side}: no result row"
    if obs.get("status") != "OK":
        return f"{side}: status={obs.get('status')}"
    if not (obs.get("fingerprint") or {}).get("sha256"):
        return f"{side}: no fingerprint"
    return None


def compare_jobs(job_a: dict, job_b: dict, *, manifest_payload: dict,
                 results_a: dict, results_b: dict) -> dict:
    """job_x: {"job_id", "manifest_id"}; results_x: {surface_key:
    observation dict}. Returns {"comparable", "reason"?, "detail"?,
    "surfaces": {key: {"label", "detail"?}}}."""
    if job_a["manifest_id"] != job_b["manifest_id"]:
        return {"comparable": False, "reason": NOT_SAME_MANIFEST,
                "detail": {"manifest_a": job_a["manifest_id"],
                           "manifest_b": job_b["manifest_id"]},
                "surfaces": {}}

    pins = manifest_payload.get("pins", {})
    drift = []
    for side, results in (("a", results_a), ("b", results_b)):
        for key, obs in results.items():
            if obs.get("status") != "OK":
                continue
            if pins.get("axe_version") and \
                    obs.get("axe_version") != pins["axe_version"]:
                drift.append(f"{side}/{key}: axe {obs.get('axe_version')}"
                             f" != pinned {pins['axe_version']}")
    for key in set(results_a) & set(results_b):
        oa, ob = results_a[key], results_b[key]
        if oa.get("status") == ob.get("status") == "OK" and \
                oa.get("browser_version") != ob.get("browser_version"):
            drift.append(f"{key}: browser {oa.get('browser_version')}"
                         f" != {ob.get('browser_version')}")
    if drift:
        return {"comparable": False, "reason": TOOL_DRIFT,
                "detail": drift, "surfaces": {}}

    surfaces = {}
    for surface in manifest_payload.get("surfaces", []):
        key = surface["key"]
        oa, ob = results_a.get(key), results_b.get(key)
        problem = _surface_problem(oa, "a") or _surface_problem(ob, "b")
        if problem:
            surfaces[key] = {"label": NOT_COMPARABLE, "detail": problem}
            continue
        fa, fb = oa["fingerprint"], ob["fingerprint"]
        if fa["sha256"] != fb["sha256"]:
            surfaces[key] = {
                "label": NOT_COMPARABLE,
                "detail": _fingerprint_delta(fa.get("summary", {}),
                                             fb.get("summary", {})),
            }
            continue
        sa, sb = _observation_set(oa), _observation_set(ob)
        if sa != sb:
            surfaces[key] = {"label": DIFFERS,
                             "detail": {"a": sa, "b": sb}}
        else:
            surfaces[key] = {"label": SAME}
    return {"comparable": True, "surfaces": surfaces}


def load_job_bundle(session, job_id: str) -> dict:
    """DB convenience for the manual CLI: job row + manifest payload +
    results keyed by surface."""
    from sqlalchemy import text
    job = session.execute(text("""
        SELECT id, manifest_id FROM s4_ui_inspection_jobs WHERE id = :id
    """), {"id": job_id}).fetchone()
    if job is None:
        raise ValueError(f"job {job_id} not found")
    manifest = session.execute(text("""
        SELECT payload FROM s4_ui_run_manifests WHERE id = :id
    """), {"id": str(job[1])}).fetchone()
    rows = session.execute(text("""
        SELECT surface_key, observation FROM s4_ui_inspection_results
        WHERE job_id = :id
    """), {"id": job_id}).fetchall()
    return {"job": {"job_id": str(job[0]), "manifest_id": str(job[1])},
            "manifest_payload": manifest[0],
            "results": {r[0]: r[1] for r in rows}}
