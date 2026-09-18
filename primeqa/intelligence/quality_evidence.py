"""Step 5 — the evidence assembler (LLD_STEP_5_QUALITY_POLICY §b, §d).

Assembles the six evidence axes for a release scope — functional,
conformance, regressions, waivers, human reviews, environments — plus the
grading census, read-only, best-effort, over ONE tenant session. It computes
OBSERVATIONS only (counts, names, sentences); it derives no effect and no
recommendation — that is the policy engine's (:mod:`quality_policy`), and
nothing else's.

Sources, all existing: the D-198 substrate assembler's rows per target
environment (functional + readiness), the Step 4 declared targets and the
latest EXECUTED plan (ruling 6), the Step 3 ``verifies`` links for the
scope's conformance claims → their verdicts on the latest processing run of
the ACTIVE claim set (3A-4) with each rule's level from the ACTIVE map set
(Phase 4), the latest comparison of that run (Phase 7, drift subtracted),
the waivers (§c) and the NEEDS_HUMAN / HUMAN_REVIEW state (ruling 5).

§d: on a declared production read-only target, a claim the plan EXCLUDED by
reason (a data recipe against a read-only target) is "not graded here by
design" — never ungraded; an ADMITTED inspection check with no current run
on that target is the §d rule's observation.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

_LEVEL_RANK = {"A": 0, "AA": 1, "AAA": 2}
_UI_ARCHETYPE = "ui"


def _short(tid: str) -> str:
    return str(tid)[:8]


def _names(keys_by_tid: dict, tid: str) -> str:
    ks = sorted(keys_by_tid.get(str(tid), ()))
    return ", ".join(ks) if ks else _short(tid)


def _user_name_reader(session: Session, tenant_id: Optional[int]) -> Callable:
    """The actor's name for an observed sentence ("waived by AK until …");
    best-effort under a savepoint — the tenant-only harness has no
    ``public.users`` and falls back to "user N"."""
    cache: dict = {}

    def _read(user_id) -> str:
        if user_id is None:
            return "unknown"
        if user_id not in cache:
            name = None
            if tenant_id is not None:
                try:
                    with session.begin_nested():
                        r = session.execute(text(
                            "SELECT full_name, email FROM public.users WHERE tenant_id = :t AND id = :u"),
                            {"t": int(tenant_id), "u": int(user_id)}).first()
                    name = (r[0] or r[1]) if r else None
                except Exception:  # noqa: BLE001
                    name = None
            cache[user_id] = name or f"user {user_id}"
        return cache[user_id]
    return _read


# --------------------------------------------------------------------------
# the plan (ruling 6) and the targets
# --------------------------------------------------------------------------

def latest_executed_plan(session: Session, *, release_id: int) -> Optional[dict]:
    from primeqa.execution_engine import planner
    r = session.execute(text("""
        SELECT CAST(id AS text) FROM run_plans
        WHERE scope_kind = 'release' AND scope_ref = :r AND executed_at IS NOT NULL
        ORDER BY executed_at DESC LIMIT 1
    """), {"r": str(release_id)}).first()
    return planner.get_plan(session, r[0]) if r else None


def _plan_admission(plan: Optional[dict]) -> tuple[dict, dict]:
    """The plan's per-environment admitted claims and design-excluded claims:
    ``admitted[env] = {test_id}``, ``excluded[env] = {test_id: reason}``."""
    admitted: dict = {}
    excluded: dict = {}
    if not plan:
        return admitted, excluded
    for p in ((plan.get("resolution") or {}).get("manifests") or {}).get("functional", {}).get("pairs", []):
        admitted.setdefault(int(p["environment_id"]), set()).add(str(p["test_id"]))
    for x in ((plan.get("exclusions") or {}).get("items") or []):
        if x.get("kind") == "claim" and x.get("environment_id") is not None:
            excluded.setdefault(int(x["environment_id"]), {})[str(x["ref"])] = x.get("reason")
    return admitted, excluded


# --------------------------------------------------------------------------
# the SCOPE — the canonical read (AUD-014 containment), bulk by construction
# --------------------------------------------------------------------------
# A decision requires a non-empty graded scope. The scope of a release is its
# live test cases (latest version not deprecated), split by lane, x its target
# environments (the DECLARED targets, else the ACTIVE environments holding a
# run for the scope), minus the pairs the latest EXECUTED plan excluded by
# reason. A CHECK is one (functional test case, active target) pair the plan
# did not exclude, or one conformance test case (graded on the browser plane,
# target-independent). Zero checks = an EMPTY scope, and emptiness is a
# pre-condition of evaluation, never a rule outcome: the engine refuses
# before the policy runs (quality_decision_console.grade_release), the
# composer refuses before Step 2's currency check, and the release board
# reads "will refuse" from THIS function — the same call the act makes.

EMPTY_NO_REQUIREMENT = "no_requirement"
EMPTY_NO_TEST_CASE = "no_test_case"
EMPTY_NO_ACTIVE_TARGET = "no_active_target"
EMPTY_ALL_EXCLUDED = "all_excluded"

_LATEST_EXECUTED_PLANS_SQL = """
    SELECT DISTINCT ON (scope_ref) scope_ref, CAST(id AS text)
    FROM run_plans
    WHERE scope_kind = 'release' AND scope_ref = ANY(:refs) AND executed_at IS NOT NULL
    ORDER BY scope_ref, executed_at DESC
"""
_DECLARED_TARGETS_SQL = """
    SELECT release_id, environment_id FROM release_targets
    WHERE release_id = ANY(:ids) AND active
    ORDER BY environment_id, declared_at
"""


def scope_emptiness(scope: dict) -> Optional[dict]:
    """PURE. ``None`` for a scope with at least one check; else ``{reason,
    sentence, ...}`` naming what is empty. The sentence is the one every
    surface shows (the board prefixes "Evaluate will refuse — ")."""
    keys = scope.get("keys") or []
    functional = scope.get("functional_ids") or []
    conformance = scope.get("conformance_ids") or []
    if not keys:
        return {"reason": EMPTY_NO_REQUIREMENT,
                "sentence": "no requirement is in scope — nothing to grade"}
    if not functional and not conformance:
        n = len(keys)
        return {"reason": EMPTY_NO_TEST_CASE, "requirements": n,
                "sentence": (f"{n} requirement{'' if n == 1 else 's'} in scope "
                             f"hold{'s' if n == 1 else ''} no current test case — nothing to grade")}
    if functional:
        active = scope.get("active_targets") or []
        if not active:
            if scope.get("targets_source") == "declared":
                names = ", ".join(t["name"] for t in scope.get("target_rows") or [])
                return {"reason": EMPTY_NO_ACTIVE_TARGET, "targets": list(scope.get("targets") or []),
                        "sentence": (f"every declared target environment is inactive ({names}) — "
                                     "no active environment is in scope")}
            return {"reason": EMPTY_NO_ACTIVE_TARGET, "targets": [],
                    "sentence": ("no target environment — none is declared, and no active "
                                 "environment holds a run for this scope")}
        if scope.get("checks", 0) == 0 and not conformance:
            return {"reason": EMPTY_ALL_EXCLUDED, "targets": list(active),
                    "sentence": ("the plan excludes every test case on every target — "
                                 "no check remains to grade")}
    return None


def resolve_release_scopes(session: Session, *, tenant_id: Optional[int], keys_by_release: dict,
                           env_reader: Optional[Callable] = None, evidence_envs: Optional[Callable] = None,
                           targets: Optional[dict] = None, plans: Optional[dict] = None) -> dict:
    """The canonical scope read for every release in ``keys_by_release``
    (``{release_id: [identity keys]}``) in ONE pass: one links query, one
    latest-claims read, one declared-targets query, one evidence-environments
    query, one latest-plans query (+ one plan read per release that has one).
    Returns ``{release_id: scope}`` where scope is::

        {release_id, keys, requirement_count, test_ids, keys_by_tid, latest,
         live_ids, functional_ids, conformance_ids,
         targets, targets_source, target_rows, active_targets,
         plan, admitted_by_env, excluded_by_env, checks, empty}

    ``targets`` (``{release_id: [env ids]}``) and ``plans`` (``{release_id:
    plan}``) are the assembler's seams (a named target set; the tenant-only
    harness); ``env_reader`` / ``evidence_envs`` are the planner's."""
    from primeqa.execution_engine import planner
    from primeqa.intelligence.substrate_decision import (
        _claim_ids_from_matches, _claim_matches, _environments_with_evidence_by_claim, _latest_claims,
    )

    own_reader = env_reader is None
    env_reader = env_reader or planner.read_env_info(session, tenant_id)
    clean = {rid: [k for k in (ks or []) if k] for rid, ks in keys_by_release.items()}
    all_keys = sorted({k for ks in clean.values() for k in ks})
    matches = _claim_matches(session, all_keys) if all_keys else {}
    per_release_ids = {rid: _claim_ids_from_matches(matches, ks) for rid, ks in clean.items()}
    all_tids = []
    seen: set = set()
    for tids, _ in per_release_ids.values():
        for t in tids:
            if t not in seen:
                seen.add(t); all_tids.append(t)
    latest = _latest_claims(session, all_tids)

    # per release: the live split
    scopes: dict = {}
    for rid, ks in clean.items():
        test_ids, keys_by_tid = per_release_ids[rid]
        live = [t for t in test_ids if getattr(latest.get(t), "status", None) != "deprecated"]
        conformance_ids = [str(t) for t in live if getattr(latest.get(t), "archetype", None) == _UI_ARCHETYPE]
        functional_ids = [str(t) for t in live if str(t) not in set(conformance_ids)]
        scopes[rid] = {"release_id": rid, "keys": ks, "requirement_count": len(ks),
                       "test_ids": test_ids, "keys_by_tid": keys_by_tid,
                       "latest": {t: latest.get(t) for t in test_ids},
                       "live_ids": live, "functional_ids": functional_ids,
                       "conformance_ids": conformance_ids}

    # the declared targets (one query) and the evidence environments (one query)
    real_ids = [rid for rid in clean if rid is not None]
    declared_by_release: dict = {rid: [] for rid in clean}
    if real_ids:
        for rid, env in session.execute(text(_DECLARED_TARGETS_SQL), {"ids": [int(r) for r in real_ids]}).fetchall():
            declared_by_release[int(rid)].append(int(env))
    need_evidence = [rid for rid, sc in scopes.items()
                     if not (targets and rid in targets) and not declared_by_release.get(rid) and sc["functional_ids"]]
    envs_by_claim: dict = {}
    if need_evidence and evidence_envs is None:
        # over EVERY functional claim on the page (not only the fallback
        # releases'): the readiness read asks for the same set, and the
        # close-2 memo then serves both from one query
        envs_by_claim = _environments_with_evidence_by_claim(
            session, [UUID(t) for sc in scopes.values() for t in sc["functional_ids"]],
            tenant_id=tenant_id)

    # the latest executed plan per release (one query + one read per plan)
    plan_by_release: dict = dict(plans or {})
    want_plan = [rid for rid in real_ids if rid not in plan_by_release]
    if want_plan:
        for ref, pid in session.execute(text(_LATEST_EXECUTED_PLANS_SQL),
                                        {"refs": [str(r) for r in want_plan]}).fetchall():
            plan_by_release[int(ref)] = planner.get_plan(session, pid)

    if own_reader and tenant_id is not None:
        # one query for every target environment on the page, warming the
        # planner's per-environment reader instead of one query per environment
        want = sorted({int(e) for rid in scopes for e in (
            (targets or {}).get(rid) or declared_by_release.get(rid)
            or ([e for t in scopes[rid]["functional_ids"] for e in envs_by_claim.get(t, ())]))})
        if want:
            env_reader = planner.read_env_info_many(session, tenant_id, want)

    for rid, sc in scopes.items():
        if targets and rid in targets:
            source, tg = "named", list(targets[rid])
        elif declared_by_release.get(rid):
            source, tg = "declared", declared_by_release[rid]
        elif evidence_envs is not None:
            source, tg = "fallback:evidence-active", evidence_envs([UUID(t) for t in sc["functional_ids"]])
        else:
            source = "fallback:evidence-active"
            tg = sorted({e for t in sc["functional_ids"] for e in envs_by_claim.get(t, ())})
        tg = sorted({int(e) for e in tg})
        rows = []
        for env in tg:
            info = env_reader(env)
            rows.append({"environment_id": env, "name": (info.name if info else f"env {env}"),
                         "is_active": (bool(info.is_active) if info else False)})
        active = [r["environment_id"] for r in rows if r["is_active"]]
        plan = plan_by_release.get(rid)
        admitted_by_env, excluded_by_env = _plan_admission(plan)
        checks = sum(1 for t in sc["functional_ids"] for e in active
                     if t not in excluded_by_env.get(e, {}))
        sc.update({"targets": tg, "targets_source": source, "target_rows": rows,
                   "active_targets": active, "plan": plan,
                   "admitted_by_env": admitted_by_env, "excluded_by_env": excluded_by_env,
                   "checks": checks + len(sc["conformance_ids"])})
        sc["empty"] = scope_emptiness(sc)
    return scopes


def release_scope(session: Session, *, tenant_id: Optional[int], release_id: Optional[int], keys,
                  env_reader: Optional[Callable] = None, evidence_envs: Optional[Callable] = None,
                  targets: Optional[list] = None, plan: Optional[dict] = None) -> dict:
    """The one-release form of :func:`resolve_release_scopes` — the engine's,
    the composer's and the page's read."""
    return resolve_release_scopes(
        session, tenant_id=tenant_id, keys_by_release={release_id: list(keys or [])},
        env_reader=env_reader, evidence_envs=evidence_envs,
        targets=({release_id: targets} if targets is not None else None),
        plans=({release_id: plan} if plan is not None else None))[release_id]


# --------------------------------------------------------------------------
# the assembly
# --------------------------------------------------------------------------

def assemble(session: Session, *, tenant_id: int, keys, release_id: Optional[int] = None,
             targets: Optional[list] = None, env_reader: Optional[Callable] = None,
             evidence_envs: Optional[Callable] = None, plan: Optional[dict] = None,
             rule_levels: Optional[dict] = None, user_name: Optional[Callable] = None,
             now: Optional[datetime] = None, scope: Optional[dict] = None) -> dict:
    """The evidence for ``keys`` (the release's requirement identity keys).

    ``targets`` (env ids) overrides the DECLARED targets read; ``env_reader``
    / ``evidence_envs`` are the planner's seams (the tenant-only harness has
    no ``public.environments``); ``plan`` overrides the latest-executed-plan
    read; ``rule_levels`` (rule id → level) overrides the ACTIVE map set read
    (public tables the harness lacks); ``scope`` is the canonical scope
    already resolved by the caller (:func:`release_scope`) — the engine
    resolves it once, refuses on emptiness, then hands it here. Returns the
    evidence dict the engine grades (see the module doc)."""
    from primeqa.execution_engine import planner
    from primeqa.intelligence.substrate_decision import _assemble_claim_evidence
    from primeqa.sync.credentials import get_connected_org_for_environment
    from primeqa.test_representation import surface_links

    now = now or datetime.now(timezone.utc)
    keys = [k for k in (keys or []) if k]
    env_reader = env_reader or planner.read_env_info(session, tenant_id)
    user_name = user_name or _user_name_reader(session, tenant_id)
    conn = session.connection()
    ev: dict = {"keys": keys, "release_id": release_id, "assembled_at": now.isoformat()}

    # -- the scope: the canonical read (live claims by lane, the plan, the targets)
    if scope is None:
        scope = release_scope(session, tenant_id=tenant_id, release_id=release_id, keys=keys,
                              env_reader=env_reader, evidence_envs=evidence_envs, targets=targets, plan=plan)
    keys_by_tid = scope["keys_by_tid"]
    live = scope["live_ids"]
    conformance_ids = scope["conformance_ids"]
    functional_ids = scope["functional_ids"]
    ev["scope"] = {"claim_count": len(live), "functional": len(functional_ids), "conformance": len(conformance_ids)}

    # -- the plan (ruling 6) + the targets -----------------------------------
    plan = scope["plan"]
    ev["plan"] = ({"id": plan["id"], "executed_at": plan.get("executed_at"), "planned_at": plan.get("planned_at"),
                   "job_count": ((plan.get("resolution") or {}).get("manifests") or {}).get("functional", {}).get("job_count"),
                   "targets_source": (plan.get("inputs") or {}).get("targets_source")}
                  if plan else None)
    admitted_by_env, excluded_by_env = scope["admitted_by_env"], scope["excluded_by_env"]
    targets_source, targets = scope["targets_source"], list(scope["targets"])

    # -- waivers (§c) ---------------------------------------------------------
    from primeqa.intelligence import quality_policy as qp
    all_waivers = qp.waivers_for_release(session, release_id, now=now)
    active_w = [w for w in all_waivers if w["state"] == "active"]
    expired_w = [w for w in all_waivers if w["state"] == "expired"]

    def waived(axis: str, *, claim: Optional[str] = None, rule: Optional[str] = None,
               surface: Optional[str] = None) -> Optional[dict]:
        for w in active_w:
            if w["axis"] != axis:
                continue
            if (w["item_kind"] == "claim" and claim and w["item_ref"] == claim) \
               or (w["item_kind"] == "rule" and rule and w["item_ref"] == rule) \
               or (w["item_kind"] == "surface" and surface and w["item_ref"] == surface):
                return w
        return None

    applied: list[dict] = []

    # -- functional + environments: per target ------------------------------
    f_counted = f_passed = f_failed = f_errored = 0
    f_items: list[dict] = []
    functional_new_fail = 0
    env_rows: list[dict] = []
    ungraded: list[dict] = []
    total_drift = total_non_current = total_prod_ungraded = 0
    for env in targets:
        info = env_reader(env)
        org = get_connected_org_for_environment(conn, env) if info is not None else None
        rows = _assemble_claim_evidence(session, keys, tenant_id=tenant_id, environment_id=env,
                                        connected_org_id=org) if keys else []
        rows = [c for c in rows if c["test_id"] in set(functional_ids)]
        is_prod_ro = bool(info and info.is_production and info.execution_policy == "read_only")
        admitted = admitted_by_env.get(env, set())
        design_excluded = excluded_by_env.get(env, {})
        counted = stale = non_current = design = metadata_missing = waived_here = 0
        for c in rows:
            tid = c["test_id"]
            state = (c.get("readiness") or {}).get("state") or "CANNOT_DETERMINE"
            w = waived("functional", claim=tid)
            if tid in design_excluded and c["latest_run"] is None:
                design += 1                                   # §d: excluded by reason → not graded here by design
                continue
            if c["latest_run"] is not None:
                counted += 1
                f_counted += 1
                if w is not None:
                    waived_here += 1
                    applied.append({"axis": "functional", "item": _names(keys_by_tid, tid), "waiver": w["id"],
                                    "reviewer": w["reviewer_user_id"], "until": w["expires_at"]})
                elif c.get("verified"):
                    f_passed += 1
                else:
                    out = c["latest_run"]["outcome"]
                    if out == "errored":
                        f_errored += 1
                    else:
                        f_failed += 1
                    f_items.append({"item": _names(keys_by_tid, tid), "test_id": tid, "environment_id": env,
                                    "outcome": out, "cause": c["latest_run"].get("cause")})
                    ro = c.get("recent_outcomes") or []
                    if len(ro) >= 2 and ro[1] == "passed":
                        functional_new_fail += 1
                if state == "STALE":
                    stale += 1
            if state in ("NEVER_RUN", "CANNOT_DETERMINE") or c.get("ungrounded"):
                if is_prod_ro and tid in admitted and c["latest_run"] is None:
                    metadata_missing += 1
                non_current += 1
                ungraded.append({"axis": "functional", "item": _names(keys_by_tid, tid), "test_id": tid,
                                 "environment_id": env, "state": ("ungrounded" if c.get("ungrounded") and state not in ("NEVER_RUN", "CANNOT_DETERMINE") else state),
                                 "sentence": (c.get("readiness") or {}).get("sentence")
                                 or ("no grounding verdict for this org" if c.get("ungrounded") else "")})
        seq = next((c.get("sequence") for c in rows if c.get("sequence")), None)
        if rows and seq and seq.get("state") != "CURRENT":
            ungraded.append({"axis": "environments", "item": (info.name if info else f"env {env}"),
                             "environment_id": env, "state": "CANNOT_DETERMINE",
                             "sentence": f"the org's current sequence could not be resolved ({seq.get('reason')})"})
            non_current += 1
        total_drift += stale
        total_non_current += non_current
        total_prod_ungraded += metadata_missing
        env_rows.append({
            "environment_id": env, "name": (info.name if info else f"env {env}"),
            "is_active": (info.is_active if info else None), "is_production": (info.is_production if info else None),
            "execution_policy": (info.execution_policy if info else None),
            "claims": len(rows), "counted": counted, "stale": stale, "non_current": non_current,
            "design_excluded": design, "metadata_missing": metadata_missing, "waived": waived_here,
            "sequence": seq,
            "sentence": (f"{info.name if info else 'env ' + str(env)}: {counted} of {len(rows)} checks current"
                         + (f" · {stale} stale" if stale else "")
                         + (f" · {non_current} not current" if non_current else "")
                         + ((f" · {design} data checks not run here by policy (read-only) · not ungraded")
                            if design else "")
                         + ((f" · {metadata_missing} inspection check(s) admitted but never run here")
                            if metadata_missing else "")),
        })
    scored = f_counted - len([a for a in applied if a["axis"] == "functional"])
    pass_rate = round(f_passed / scored * 100, 1) if scored else None
    ev["functional"] = {
        "claims": len(functional_ids), "counted": f_counted, "passed": f_passed, "failed": f_failed,
        "errored": f_errored, "pass_rate": pass_rate, "functional_new_fail": functional_new_fail,
        "items": f_items,
        "observed": (f"{len(functional_ids)} functional check(s) in scope · {f_counted} with a counted run"
                     + (f" · {f_failed} failed" if f_failed else "") + (f" · {f_errored} errored" if f_errored else "")
                     + (f" · pass rate {pass_rate}%" if pass_rate is not None else " · no pass rate (no counted run)")
                     + (f" · {len([a for a in applied if a['axis'] == 'functional'])} waived" if any(a["axis"] == "functional" for a in applied) else "")),
    }

    # -- conformance: the latest processing run of the ACTIVE set -------------
    active_v = surface_links.active_inventory_version(session)
    active_set = surface_links.active_claim_set(session, active_v) if active_v is not None else None
    run = None
    verdicts: list = []
    if active_set is not None:
        run = session.execute(text("""
            SELECT CAST(job_id AS text), processed_at, verdict_counts FROM s6_ui_processing_runs
            WHERE claim_set_id = CAST(:cs AS uuid) ORDER BY processed_at DESC LIMIT 1
        """), {"cs": str(active_set)}).first()
        if run is not None and conformance_ids:
            verdicts = session.execute(text("""
                SELECT CAST(test_id AS text), plimsol_rule_id, verdict, surface_key
                FROM s6_ui_verdicts WHERE job_id = CAST(:j AS uuid) AND CAST(test_id AS text) = ANY(:ids)
            """), {"j": run[0], "ids": conformance_ids}).fetchall()
    profile = None
    levels: dict = {}
    levels_available = True
    if active_set is not None:
        profile = session.execute(text("SELECT standard_profile FROM claim_sets WHERE id = CAST(:cs AS uuid)"),
                                  {"cs": str(active_set)}).scalar() or "WCAG22"
        if rule_levels is not None:
            levels = dict(rule_levels)
        else:
            # the ACTIVE map set of the set's standard — PUBLIC tables (065/066),
            # absent on the tenant-only harness: a savepoint keeps the assembly alive
            try:
                with session.begin_nested():
                    for rid, lvl in session.execute(text("""
                        SELECT m.rule_id, m.level FROM s5_standard_maps m
                        JOIN s5_standard_map_sets ms ON ms.id = m.map_set_id
                        WHERE ms.standard = :s AND ms.state = 'ACTIVE'
                    """), {"s": profile}).fetchall():
                        if lvl and (rid not in levels or _LEVEL_RANK.get(lvl, 9) < _LEVEL_RANK.get(levels[rid], 9)):
                            levels[rid] = lvl
            except Exception as exc:  # noqa: BLE001
                levels_available = False
                log.warning("standard map levels unavailable (%s): %s", profile, exc)
    failed_by_level = {"A": 0, "AA": 0, "AAA": 0, "unmapped": 0}
    c_pass = c_nd = c_needs_human = 0
    c_items: list[dict] = []
    needs_human_items: list[dict] = []
    seen: set = set()
    for tid, rule_id, verdict, surface in verdicts:
        seen.add(tid)
        if verdict == "PASS":
            c_pass += 1
        elif verdict == "NOT_DETERMINED":
            c_nd += 1
        elif verdict == "NEEDS_HUMAN":
            c_needs_human += 1
            needs_human_items.append({"test_id": tid, "rule": rule_id, "surface": surface})
        elif verdict == "FAIL":
            w = waived("conformance", claim=tid, rule=rule_id, surface=surface)
            if w is not None:
                applied.append({"axis": "conformance", "item": f"{rule_id} on {surface}", "waiver": w["id"],
                                "reviewer": w["reviewer_user_id"], "until": w["expires_at"]})
                continue
            lvl = levels.get(rule_id) or "unmapped"
            failed_by_level[lvl] = failed_by_level.get(lvl, 0) + 1
            c_items.append({"item": f"{rule_id} on {surface}", "test_id": tid, "level": lvl, "rule": rule_id,
                            "surface": surface})
    for tid in conformance_ids:
        if tid not in seen:
            ungraded.append({"axis": "conformance", "item": _names(keys_by_tid, tid), "test_id": tid,
                             "state": "NEVER_RUN" if run is None else "NO_VERDICT",
                             "sentence": ("no processing run on the active set" if run is None
                                          else "no verdict on the latest processing run")})
    ev["conformance"] = {
        "claims": len(conformance_ids), "active_set": (str(active_set) if active_set else None),
        "standard": profile, "levels_available": levels_available, "job_id": (run[0] if run else None),
        "processed_at": (run[1].isoformat() if run and run[1] else None),
        "passed": c_pass, "failed_by_level": failed_by_level, "not_determined": c_nd,
        "needs_human": c_needs_human, "items": c_items,
        "observed": (f"{len(conformance_ids)} conformance check(s) in scope"
                     + (" · no active claim set" if active_set is None
                        else (" · no processing run yet" if run is None
                              else (f" · latest run {run[0][:8]} ({profile})"
                                    + f" · {c_pass} pass"
                                    + "".join(f" · {n} level-{l} FAIL" for l, n in failed_by_level.items() if n and l != "unmapped")
                                    + (f" · {failed_by_level['unmapped']} FAIL unmapped to a level" if failed_by_level["unmapped"] else "")
                                    + (f" · {c_nd} not determined" if c_nd else "")
                                    + (f" · {c_needs_human} needs human" if c_needs_human else ""))))
                     + (f" · {len([a for a in applied if a['axis'] == 'conformance'])} waived" if any(a["axis"] == "conformance" for a in applied) else "")),
    }

    # -- regressions: the latest comparison of that run, drift subtracted -----
    cmp_row = None
    new_fail = not_comparable = drift_subtracted = 0
    if run is not None:
        cmp_row = session.execute(text("""
            SELECT CAST(id AS text), tool_drift, env_delta, transition_counts, created_at
            FROM s6_ui_comparison_runs WHERE candidate_job_id = CAST(:j AS uuid) AND outcome = 'completed'
            ORDER BY created_at DESC LIMIT 1
        """), {"j": run[0]}).first()
        if cmp_row is not None and conformance_ids:
            for transition, drift in session.execute(text("""
                SELECT transition, drift FROM s6_ui_verdict_transitions
                WHERE comparison_id = CAST(:c AS uuid) AND CAST(test_id AS text) = ANY(:ids)
            """), {"c": cmp_row[0], "ids": conformance_ids}).fetchall():
                if transition == "NEW_FAIL":
                    if drift:
                        drift_subtracted += 1
                    else:
                        new_fail += 1
                elif transition == "NOT_COMPARABLE":
                    not_comparable += 1
    tool_drift = bool(cmp_row and cmp_row[1])
    env_delta = bool(cmp_row and cmp_row[2])
    if cmp_row:
        reg_obs = (f"comparison {cmp_row[0][:8]}: {new_fail} new conformance failure(s)"
                   + (f" ({drift_subtracted} drift-flagged, subtracted)" if drift_subtracted else "")
                   + (f" · {not_comparable} not comparable" if not_comparable else "")
                   + (" · tool pins moved" if tool_drift else "") + (" · environment moved" if env_delta else ""))
    else:
        reg_obs = "no comparison recorded for the latest conformance run"
    if functional_new_fail:
        reg_obs += f" · {functional_new_fail} functional pass→fail"
    ev["regressions"] = {
        "comparison_id": (cmp_row[0] if cmp_row else None), "new_fail": new_fail,
        "functional_new_fail": functional_new_fail, "drift_subtracted": drift_subtracted,
        "not_comparable": not_comparable, "tool_drift": tool_drift, "env_delta": env_delta,
        "tool_drift_only": bool(tool_drift and not env_delta and new_fail == 0 and functional_new_fail == 0),
        "observed": reg_obs,
    }

    # -- human reviews (ruling 5: waived, never "resolved") -------------------
    members = []
    if active_set is not None and conformance_ids:
        members = [r[0] for r in session.execute(text("""
            SELECT CAST(test_id AS text) FROM claim_set_members
            WHERE claim_set_id = CAST(:cs AS uuid) AND applicability = 'HUMAN_REVIEW' AND revoked_at IS NULL
              AND CAST(test_id AS text) = ANY(:ids)
        """), {"cs": str(active_set), "ids": conformance_ids}).fetchall()]
    pending: list[dict] = []
    waived_reviews: list[dict] = []
    for it in needs_human_items + [{"test_id": t, "rule": None, "surface": None} for t in members
                                   if t not in {n["test_id"] for n in needs_human_items}]:
        w = waived("human_reviews", claim=it["test_id"], rule=it.get("rule"), surface=it.get("surface"))
        label = (f"{it['rule']} on {it['surface']}" if it.get("rule") else _names(keys_by_tid, it["test_id"]))
        if w is not None:
            waived_reviews.append({"item": label, "waiver": w["id"], "reviewer": w["reviewer_user_id"],
                                   "until": w["expires_at"]})
            applied.append({"axis": "human_reviews", "item": label, "waiver": w["id"],
                            "reviewer": w["reviewer_user_id"], "until": w["expires_at"]})
        else:
            pending.append({"item": label, "test_id": it["test_id"]})
    ev["human_reviews"] = {
        "pending": len(pending), "waived": len(waived_reviews), "items": pending, "waived_items": waived_reviews,
        "observed": (f"{len(pending)} pending"
                     + "".join(f", waived by {user_name(w['reviewer'])} until {str(w['until'])[:10]}: {w['item']}"
                               for w in waived_reviews)
                     + ("" if pending or waived_reviews else " · no human review outstanding")),
    }

    # -- waivers line ----------------------------------------------------------
    ev["waivers"] = {
        "active": len(active_w), "expired": len(expired_w), "applied": len(applied), "items": applied,
        "observed": (f"{len(active_w)} active waiver(s), {len(applied)} applied"
                     + "".join(f" · {a['item']} ({a['axis']}) by {user_name(a['reviewer'])} until {str(a['until'])[:10]}"
                               for a in applied[:5])
                     + (f" · {len(expired_w)} expired, not counted" if expired_w else "")),
    }

    # -- environments line -------------------------------------------------------
    ev["environments"] = {
        "targets": env_rows, "targets_source": targets_source, "drift": total_drift,
        "non_current": total_non_current, "production_ungraded_on_metadata": total_prod_ungraded,
        "observed": ((f"targets ({targets_source}): " + " | ".join(e["sentence"] for e in env_rows))
                     if env_rows else "no target environment holds evidence for this scope")
                    + (f" · plan {ev['plan']['id'][:8]} executed {str(ev['plan']['executed_at'])[:16]}"
                       if ev["plan"] else " · evidence predates plans"),
    }

    # -- grading census ------------------------------------------------------------
    ev["grading"] = {
        "count": len(ungraded), "items": ungraded,
        "observed": ("Nothing ungraded." if not ungraded
                     else f"{len(ungraded)} ungraded: " + ", ".join(
                         f"{u['item']} ({u['state']}" + (f" on env {u['environment_id']}" if u.get("environment_id") else "") + ")"
                         for u in ungraded[:6]) + (" …" if len(ungraded) > 6 else "")),
    }
    return ev
