"""Step 6a — the Requirements board reader (LLD_STEP_6A_WORK_SURFACES §c).

One bulk read for the whole page: for every requirement key on the page it
returns the functional summary, the conformance summary and the readiness
pill. Best-effort — an unavailable substrate renders the list exactly as it
did before 6a, with the three columns blank rather than wrong.

**The lane split (D-488) is the point of this module.** A conformance claim
(``archetype = 'ui'``) never runs on the S4 execution lane, so:

  - the FUNCTIONAL summary and the READINESS pill are computed over functional
    claims only — a requirement whose only checks are conformance reads
    "no functional check", never NEVER RUN;
  - the CONFORMANCE summary is computed over declared surfaces and the browser
    plane's verdicts, and reads **"no surface declared"** where no declaration
    exists — which is a different fact from zero checks and is never rendered
    as zeros.

One tenant session for the page (never one per row).
"""
from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from sqlalchemy import bindparam, text

log = logging.getLogger(__name__)

FUNCTIONAL, CONFORMANCE = "functional", "conformance"
_UI_ARCHETYPE = "ui"
READINESS_ORDER = ("NEVER_RUN", "CANNOT_DETERMINE", "STALE", "CURRENT")
NO_FUNCTIONAL = "NO_FUNCTIONAL_CHECK"


def _blank(key: str) -> dict:
    return {"key": key,
            "functional": {"claims": 0, "run": 0, "passed": 0, "failed": 0, "never_run": 0,
                           "sentence": "no functional check"},
            "conformance": {"declared": 0, "checks": 0, "failures": 0, "human_review": 0,
                            "declared_any": False, "sentence": "no surface declared"},
            "readiness": {"state": NO_FUNCTIONAL, "environments": [],
                          "sentence": "no functional check to grade"}}


def board_rows(tenant_id: int, keys, *, session=None) -> dict:
    """``{available, rows: {key: {functional, conformance, readiness}}}``."""
    keys = [k for k in (keys or []) if k]
    if not keys:
        return {"available": True, "rows": {}}
    try:
        if session is not None:
            return {"available": True, "rows": _read(session, tenant_id, keys)}
        from sqlalchemy.orm import Session

        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            s = Session(bind=conn)
            try:
                return {"available": True, "rows": _read(s, tenant_id, keys)}
            finally:
                s.close()
    except Exception as exc:  # noqa: BLE001 — the list renders without the columns
        log.warning("requirements board unavailable for tenant %s: %s", tenant_id, exc)
        return {"available": False, "rows": {}}


def _read(s, tenant_id: int, keys: list) -> dict:
    rows = {k: _blank(k) for k in keys}

    # -- 1. the scope's live claims, split by lane -------------------------
    claim_rows = s.execute(text("""
        SELECT l.external_key, CAST(l.test_id AS text), c.archetype
        FROM test_requirement_links l
        JOIN test_claims c ON c.test_id = l.test_id AND c.valid_to IS NULL
        WHERE l.external_key = ANY(:keys)
          AND l.link_kind IN ('generated_from', 'verifies')
          AND c.status <> 'deprecated'
    """), {"keys": keys}).fetchall()
    functional: dict = {}
    conformance: dict = {}
    for key, tid, archetype in claim_rows:
        (conformance if archetype == _UI_ARCHETYPE else functional).setdefault(key, set()).add(tid)

    # -- 2. the functional lane: latest run per claim ----------------------
    all_functional = sorted({t for ts in functional.values() for t in ts})
    latest: dict = {}
    envs_by_claim: dict = {}
    if all_functional:
        for tid, outcome, env in s.execute(text("""
            SELECT DISTINCT ON (r.claim_test_id) CAST(r.claim_test_id AS text),
                   r.outcome::text, r.environment_id
            FROM s4_execution_runs r
            WHERE CAST(r.claim_test_id AS text) = ANY(:ids)
            ORDER BY r.claim_test_id, r.finished_at DESC NULLS LAST
        """), {"ids": all_functional}).fetchall():
            latest[tid] = {"outcome": outcome, "environment_id": env}
        for tid, env in s.execute(text("""
            SELECT DISTINCT CAST(claim_test_id AS text), environment_id
            FROM s4_execution_runs WHERE CAST(claim_test_id AS text) = ANY(:ids)
        """), {"ids": all_functional}).fetchall():
            envs_by_claim.setdefault(tid, set()).add(env)

    # -- 3. readiness over (functional claim × the environments it ran in) --
    ready: dict = {}
    pairs = [(t, e) for t, es in envs_by_claim.items() for e in es]
    if pairs:
        try:
            from primeqa.sync.readiness import resolve_run_readiness_bulk
            with s.begin_nested():
                ready = {k: v.as_dict() for k, v in
                         resolve_run_readiness_bulk(s, pairs).items()}
        except Exception as exc:  # noqa: BLE001 — the pill degrades, the row renders
            log.warning("readiness unavailable for the requirements board: %s", exc)

    for key, tids in functional.items():
        run = passed = failed = never = 0
        worst, worst_envs = None, []
        for t in sorted(tids):
            lr = latest.get(t)
            if lr is None:
                never += 1
                continue
            run += 1
            if lr["outcome"] == "passed":
                passed += 1
            else:
                failed += 1
            for e in sorted(envs_by_claim.get(t, ())):
                st = (ready.get((t, int(e))) or {}).get("state")
                if st is None:
                    continue
                if worst is None or READINESS_ORDER.index(st) < READINESS_ORDER.index(worst):
                    worst, worst_envs = st, [e]
                elif st == worst and e not in worst_envs:
                    worst_envs.append(e)
        if never and worst is None:
            worst, worst_envs = "NEVER_RUN", []
        f = {"claims": len(tids), "run": run, "passed": passed, "failed": failed,
             "never_run": never}
        f["sentence"] = (f"{passed} passed" + (f" · {failed} failed" if failed else "")
                         + (f" · {never} never run" if never else "")
                         if tids else "no functional check")
        rows[key]["functional"] = f
        rows[key]["readiness"] = {
            "state": worst or NO_FUNCTIONAL, "environments": worst_envs,
            "sentence": ("no functional check to grade" if worst is None else
                         _readiness_sentence(worst, worst_envs))}

    # -- 4. the conformance lane: declared surfaces + the latest verdicts ---
    declared = s.execute(text("""
        SELECT requirement_key, surface_key FROM requirement_surface_links
        WHERE requirement_key = ANY(:keys) AND active
    """), {"keys": keys}).fetchall()
    by_key: dict = {}
    for key, surface in declared:
        by_key.setdefault(key, set()).add(surface)
    verdicts: dict = {}
    all_conf = sorted({t for ts in conformance.values() for t in ts})
    if all_conf:
        try:
            with s.begin_nested():
                for tid, verdict in s.execute(text("""
                    SELECT CAST(v.test_id AS text), v.verdict FROM s6_ui_verdicts v
                    WHERE CAST(v.test_id AS text) = ANY(:ids)
                      AND v.job_id = (SELECT job_id FROM s6_ui_processing_runs p
                                      WHERE p.claim_set_id = v.claim_set_id
                                      ORDER BY p.processed_at DESC LIMIT 1)
                """), {"ids": all_conf}).fetchall():
                    verdicts[tid] = verdict
        except Exception as exc:  # noqa: BLE001
            log.warning("conformance verdicts unavailable for the board: %s", exc)
    for key in keys:
        surfaces = by_key.get(key, set())
        tids = conformance.get(key, set())
        if not surfaces and not tids:
            continue                                    # stays "no surface declared"
        fails = sum(1 for t in tids if verdicts.get(t) == "FAIL")
        human = sum(1 for t in tids if verdicts.get(t) == "NEEDS_HUMAN")
        graded = sum(1 for t in tids if t in verdicts)
        c = {"declared": len(surfaces), "checks": len(tids), "failures": fails,
             "human_review": human, "graded": graded, "declared_any": bool(surfaces)}
        if not surfaces:
            c["sentence"] = "no surface declared"
        else:
            c["sentence"] = (f"{len(surfaces)} surface{'' if len(surfaces) == 1 else 's'} · "
                             f"{len(tids)} check{'' if len(tids) == 1 else 's'}"
                             + (f" · {fails} failing" if fails else "")
                             + (f" · {human} need a human" if human else "")
                             + ("" if graded else " · not yet run"))
        rows[key]["conformance"] = c
    return rows


def _readiness_sentence(state: str, envs) -> str:
    where = (" on env " + ", ".join(str(e) for e in envs)) if envs else ""
    return {
        "CURRENT": f"every functional check is current{where}",
        "STALE": f"something a functional check reads changed after its run{where}",
        "NEVER_RUN": "a functional check has never run",
        "CANNOT_DETERMINE": f"freshness unknown{where}",
    }.get(state, state)
