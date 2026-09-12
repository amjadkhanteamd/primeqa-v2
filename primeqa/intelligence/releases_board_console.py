"""Step 6b — the Releases board reader (LLD_STEP_6B_DECISION_SETUP §a).

The list's two missing facts, for every release on the page in one tenant
session: what evidence it has, and what its decision state actually IS right
now — not merely what was last recorded.

Four states, because the current list can express only two:

  ``recorded``        a decision row exists and the scope is current
  ``refuses``         the scope is NOT current, so Evaluate would refuse
                      (the D-484 act), whatever a stale row may say
  ``not_evaluated``   the scope is current and nothing has been recorded
  ``stale``           a decision row exists but a run has landed since it

Ruling 6: every per-release read is BEST-EFFORT. A release whose evidence
cannot be read renders "unavailable" on that cell; it never fails the list.
The lane split (D-488) holds here too — the functional count and the readiness
are the functional lane's, and a release whose only checks are conformance
says so rather than reading empty.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text

log = logging.getLogger(__name__)

RECORDED, REFUSES, NOT_EVALUATED, STALE = ("recorded", "refuses", "not_evaluated", "stale")
UNAVAILABLE = "unavailable"


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def board(tenant_id: int, releases, *, db=None, session=None) -> dict:
    """``{available, rows: {release_id: {...}}}`` for the releases on the page.

    ``releases`` is the list of release dicts the page already read (needing
    only ``id``). ``db`` is the PUBLIC session the view already holds; the
    tenant session is opened once here."""
    ids = [int(r["id"]) for r in (releases or []) if r.get("id") is not None]
    if not ids:
        return {"available": True, "rows": {}}
    rows = {i: {"release_id": i, "state": UNAVAILABLE, "available": False,
                "sentence": "evidence unavailable"} for i in ids}
    try:
        keys_by_release, decisions = _public_reads(tenant_id, ids, db)
    except Exception as exc:  # noqa: BLE001
        log.warning("releases board (public) unavailable for tenant %s: %s", tenant_id, exc)
        return {"available": False, "rows": rows}
    try:
        _tenant_reads(tenant_id, ids, keys_by_release, decisions, rows, session)
    except Exception as exc:  # noqa: BLE001 — the list still renders
        log.warning("releases board (tenant) unavailable for tenant %s: %s", tenant_id, exc)
        return {"available": False, "rows": rows}
    return {"available": True, "rows": rows}


def _public_reads(tenant_id: int, ids, db):
    """The release → requirement keys map and the latest decision per release,
    both from the PUBLIC schema, in two queries."""
    close = False
    if db is None:
        from primeqa.db import get_db
        db = next(get_db()); close = True
    try:
        keys_by_release: dict = {i: [] for i in ids}
        for rid, key in db.execute(text("""
            SELECT rr.release_id, COALESCE(r.external_key, r.jira_key, 'req-' || r.id)
            FROM release_requirements rr
            JOIN requirements r ON r.id = rr.requirement_id
            WHERE rr.release_id = ANY(:ids) AND r.deleted_at IS NULL AND r.tenant_id = :t
        """), {"ids": ids, "t": tenant_id}).fetchall():
            keys_by_release[int(rid)].append(key)
        decisions: dict = {}
        for row in db.execute(text("""
            SELECT DISTINCT ON (release_id) release_id, id, recommendation, final_decision,
                   created_at, policy_version, CAST(plan_id AS text), CAST(policy_id AS text)
            FROM release_decisions WHERE release_id = ANY(:ids)
            ORDER BY release_id, created_at DESC
        """), {"ids": ids}).fetchall():
            decisions[int(row[0])] = {
                "id": row[1], "recommendation": row[2], "final_decision": row[3],
                "created_at": _iso(row[4]), "policy_version": row[5],
                "plan_id": row[6], "policy_id": row[7]}
        return keys_by_release, decisions
    finally:
        if close:
            db.close()


def _tenant_reads(tenant_id, ids, keys_by_release, decisions, rows, session) -> None:
    """Readiness + the functional/conformance split + the newest run, for every
    release on the page, over ONE tenant session."""
    from sqlalchemy.orm import Session

    from primeqa.semantic.connection import get_tenant_connection

    def _run(s):
        from primeqa.intelligence.substrate_decision import _claim_test_ids
        from primeqa.sync.readiness import resolve_run_readiness_bulk
        all_keys = sorted({k for ks in keys_by_release.values() for k in ks})
        claims_by_key: dict = {}
        archetype: dict = {}
        newest_run: dict = {}
        if all_keys:
            for key, tid, arch in s.execute(text("""
                SELECT l.external_key, CAST(l.test_id AS text), c.archetype
                FROM test_requirement_links l
                JOIN test_claims c ON c.test_id = l.test_id AND c.valid_to IS NULL
                WHERE l.external_key = ANY(:keys)
                  AND l.link_kind IN ('generated_from', 'verifies')
                  AND c.status <> 'deprecated'
            """), {"keys": all_keys}).fetchall():
                claims_by_key.setdefault(key, set()).add(tid)
                archetype[tid] = arch
        functional_ids = sorted({t for t, a in archetype.items() if a != "ui"})
        # The SAME environment filter the canonical scope read uses (D-485's
        # active-environment interim). Without it this board counts evidence on
        # an INACTIVE environment — env 78 on production — and a release reads
        # "15 items not current" beside its own page saying the scope is clean.
        # The list must not contradict the page it links to.
        allowed_envs = None
        if functional_ids:
            from primeqa.intelligence.substrate_decision import _environments_with_evidence
            from uuid import UUID
            allowed_envs = set(_environments_with_evidence(
                s, [UUID(t) for t in functional_ids], tenant_id=tenant_id))
        pairs, envs_by_claim = [], {}
        if functional_ids:
            for tid, env, finished in s.execute(text("""
                SELECT DISTINCT ON (CAST(claim_test_id AS text), environment_id)
                       CAST(claim_test_id AS text), environment_id, finished_at
                FROM s4_execution_runs
                WHERE CAST(claim_test_id AS text) = ANY(:ids)
                ORDER BY CAST(claim_test_id AS text), environment_id, finished_at DESC
            """), {"ids": functional_ids}).fetchall():
                if allowed_envs is not None and int(env) not in allowed_envs:
                    continue                      # inactive environment — not in scope
                envs_by_claim.setdefault(tid, set()).add(int(env))
                pairs.append((tid, int(env)))
                if finished and (tid not in newest_run or finished > newest_run[tid]):
                    newest_run[tid] = finished
        ready = resolve_run_readiness_bulk(s, pairs) if pairs else {}

        for rid in ids:
            keys = keys_by_release.get(rid, [])
            tids = {t for k in keys for t in claims_by_key.get(k, ())}
            fn = sorted(t for t in tids if archetype.get(t) != "ui")
            cf = sorted(t for t in tids if archetype.get(t) == "ui")
            # TWO counts, and they count different things — so each says which.
            #  * the REFUSAL count is per (claim, environment) ITEM, because
            #    that is exactly what release_scope_readiness reports and what
            #    Evaluate will name when it refuses (D-484). A claim that ran in
            #    two environments is two items.
            #  * the EVIDENCE count is per CLAIM, worst-of across its
            #    environments, because "3 of 4 checks current" is a statement
            #    about checks. Mixing the two made a release read "1 item not
            #    current" beside "1 of 1 check current" — both true, together
            #    nonsense.
            item_states = [r.state for r in
                           (ready.get((t, e)) for t in fn for e in envs_by_claim.get(t, ()))
                           if r is not None]
            never_run_claims = [t for t in fn if not envs_by_claim.get(t)]
            non_current = (sum(1 for w in item_states if w != "CURRENT")
                           + len(never_run_claims))
            current = 0
            for t in fn:
                envs = envs_by_claim.get(t, ())
                words = [r.state for r in (ready.get((t, e)) for e in envs) if r is not None]
                if words and all(w == "CURRENT" for w in words):
                    current += 1
            latest_run = max((newest_run[t] for t in fn if t in newest_run), default=None)
            d = decisions.get(rid)

            if not keys:
                state, sentence = NOT_EVALUATED, "no requirement in scope"
            elif non_current:
                state = REFUSES
                sentence = (f"Evaluate will refuse — {non_current} item"
                            f"{'' if non_current == 1 else 's'} in scope "
                            f"{'is' if non_current == 1 else 'are'} not current")
            elif d is None:
                state, sentence = NOT_EVALUATED, "not evaluated"
            elif latest_run and d["created_at"] and _iso(latest_run) > d["created_at"]:
                state = STALE
                sentence = "recorded before the latest run"
            else:
                state = RECORDED
                sentence = (d["recommendation"] or "").replace("_", " ").upper()

            rows[rid] = {
                "release_id": rid, "available": True, "state": state, "sentence": sentence,
                "requirements": len(keys), "functional": len(fn), "conformance": len(cf),
                "current": current, "non_current": non_current,
                "latest_run_at": _iso(latest_run), "decision": d,
                "evidence_sentence": _evidence_sentence(len(keys), len(fn), len(cf), current),
            }

    if session is not None:
        _run(session); return
    with get_tenant_connection(tenant_id) as conn:
        s = Session(bind=conn)
        try:
            _run(s)
        finally:
            s.close()


def _evidence_sentence(requirements: int, functional: int, conformance: int, current: int) -> str:
    """``current`` is a CLAIM count (worst-of across environments), never an
    item count — see the note where it is computed."""
    if not requirements:
        return "no requirement attached"
    parts = [f"{requirements} requirement{'' if requirements == 1 else 's'}"]
    if functional:
        parts.append(f"{current} of {functional} functional check"
                     f"{'' if functional == 1 else 's'} current")
    else:
        parts.append("no functional check")
    if conformance:
        parts.append(f"{conformance} conformance check{'' if conformance == 1 else 's'}")
    return " · ".join(parts)
