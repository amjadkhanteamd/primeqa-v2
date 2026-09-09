"""Step 4 — the Run Planner (LLD_STEP_4_RUN_PLANNER §a–§e).

Every execution starts from a RECORDED plan: a deterministic resolution of a
scope (a requirement, a release, a schedule's template, or the tenant-wide
successor of "Run all approved") into the claims by kind, the eligible
recipes, the target environments, the personas and the manifests that will
run — with every exclusion named and its reason stated. ``execute_plan``
executes THAT row by id, once (ruling 3): the enqueued set is the recorded
set, the jobs and the runs carry the plan id.

The planner reads S2 (claims, recipes, links, sets, inventory, the Step 3
declarations — consumed, never guessed), the tenant's environments and the
release's DECLARED targets; it writes only its own rows (``run_plans``,
``release_targets``) and the audit trail. It applies the dispatch
chokepoint's rules as EXCLUSIONS (a read-only target admits inspection
recipes only; a disabled environment admits nothing; production needs an
Admin) — it does not own policy (Step 5).

Authority (ruling 2, extends D-477): a plan is made by a person
(``planned_by``) or by a system actor carrying a schedule's borrowed
authority (``authorised_by``); a schedule with neither is REFUSED loudly
with the exit "claim this schedule".
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

JIRA = "jira"
SCOPE_REQUIREMENT, SCOPE_RELEASE, SCOPE_SCHEDULE, SCOPE_TENANT = (
    "requirement", "release", "schedule", "tenant")
SCOPE_KINDS = (SCOPE_REQUIREMENT, SCOPE_RELEASE, SCOPE_SCHEDULE, SCOPE_TENANT)

LANE_FUNCTIONAL, LANE_CONFORMANCE = "functional", "conformance"
_CONFORMANCE_RECIPE_KIND = "ui-inspection"

# --- exclusion reasons (§b) --------------------------------------------------
X_CLAIM_DEPRECATED = "claim_deprecated"
X_CLAIM_NOT_APPROVED = "claim_not_approved"
X_ORIGIN_FIXTURE = "origin_fixture"
X_ORIGIN_PROBE = "origin_probe"
X_NO_ELIGIBLE_RECIPE = "no_eligible_recipe"
X_UNEXECUTABLE_SHAPE = "unexecutable_shape"
X_ENV_NOT_TARGET = "env_not_target"
X_ENV_UNKNOWN = "env_unknown"
X_ENV_INACTIVE = "env_inactive"
X_ENV_DISABLED = "env_disabled"
X_READ_ONLY_TARGET = "read_only_target_admits_inspection_only"
X_PRODUCTION_REQUIRES_ADMIN = "production_requires_admin"
X_SURFACE_NOT_DECLARED = "surface_not_declared"
X_SURFACE_NOT_IN_ACTIVE_SET = "surface_not_in_active_set"
X_JOB_ALREADY_ACTIVE = "job_already_active"

REASON_SENTENCES = {
    X_CLAIM_DEPRECATED: "The claim is deprecated — it no longer applies.",
    X_CLAIM_NOT_APPROVED: "The claim has no approved version — drafts do not run.",
    X_ORIGIN_FIXTURE: "Every requirement this claim verifies is a fixture (hidden by default).",
    X_ORIGIN_PROBE: "Every requirement this claim verifies is a probe (hidden by default).",
    X_NO_ELIGIBLE_RECIPE: "The claim has no active or approved recipe.",
    X_UNEXECUTABLE_SHAPE: "Every eligible recipe fails S4's shape check — it would die at run time.",
    X_ENV_NOT_TARGET: "The environment holds evidence for this scope but is not a target.",
    X_ENV_UNKNOWN: "The environment does not exist for this tenant.",
    X_ENV_INACTIVE: "The environment is inactive.",
    X_ENV_DISABLED: "The environment's execution policy is disabled — no runs permitted.",
    X_READ_ONLY_TARGET: "A read-only target admits inspection (metadata) recipes only — a data recipe is never enqueued against it.",
    X_PRODUCTION_REQUIRES_ADMIN: "A production environment needs an Admin to plan against it.",
    X_SURFACE_NOT_DECLARED: "The surface is in the active claim set but not declared on this scope's requirements.",
    X_SURFACE_NOT_IN_ACTIVE_SET: "The declared surface is not in the active claim set's inventory version.",
    X_JOB_ALREADY_ACTIVE: "A job for this claim and environment was already active — attached, not duplicated.",
}

# Seams (§c): named, not built — v1 selects everything.
SEAMS = {
    "impact": "not yet — every scoped item",
    "risk": "not yet — every scoped item",
    "persona": "not yet — every declared persona",
    "regression": "not yet — every eligible recipe",
}

REFUSE_NO_AUTHORITY = "no_authorising_user"
REFUSE_NO_ENVIRONMENT = "no_environment"
REFUSE_UNKNOWN_SCOPE = "unknown_scope"
REFUSE_UNKNOWN_SCHEDULE = "unknown_schedule"
REFUSE_UNKNOWN_RELEASE = "unknown_release"
REFUSE_ALREADY_EXECUTED = "plan_already_executed"
REFUSE_UNKNOWN_PLAN = "unknown_plan"

REFUSAL_SENTENCES = {
    REFUSE_NO_AUTHORITY: "No authorising user — claim this schedule before it can fire.",
    REFUSE_NO_ENVIRONMENT: "Name an environment to plan against.",
    REFUSE_UNKNOWN_SCOPE: "Unknown plan scope.",
    REFUSE_UNKNOWN_SCHEDULE: "Unknown schedule.",
    REFUSE_UNKNOWN_RELEASE: "Unknown release.",
    REFUSE_ALREADY_EXECUTED: "This plan was already run — plan again to run again.",
    REFUSE_UNKNOWN_PLAN: "Unknown plan.",
}


class PlanRefused(Exception):
    def __init__(self, reason: str, detail: str = ""):
        self.reason, self.detail = reason, detail
        super().__init__(f"{reason}: {REFUSAL_SENTENCES.get(reason, reason)}"
                         + (f" ({detail})" if detail else ""))


@dataclass(frozen=True)
class EnvInfo:
    id: int
    name: str
    is_active: bool
    is_production: bool
    execution_policy: str


def read_env_info(session: Session, tenant_id: int) -> Callable[[int], Optional[EnvInfo]]:
    """The default environment reader: ``public.environments`` for the
    tenant (the tenant session's search_path includes public). Tests inject
    their own reader — the tenant-only harness carries no such table."""
    cache: dict = {}

    def _read(env_id: int) -> Optional[EnvInfo]:
        if env_id in cache:
            return cache[env_id]
        r = session.execute(text(
            "SELECT id, name, is_active, is_production, execution_policy "
            "FROM public.environments WHERE id = :e AND tenant_id = :t"),
            {"e": int(env_id), "t": int(tenant_id)}).first()
        cache[env_id] = (EnvInfo(id=r[0], name=r[1] or f"env {r[0]}",
                                 is_active=bool(r[2]), is_production=bool(r[3]),
                                 execution_policy=r[4] or "full") if r else None)
        return cache[env_id]
    return _read


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


# --------------------------------------------------------------------------
# release targets (§a) — DECLARED with actor; removal is a state change
# --------------------------------------------------------------------------

def list_targets(session: Session, release_id: int, *, include_inactive=False) -> list[dict]:
    rows = session.execute(text(f"""
        SELECT CAST(id AS text), release_id, environment_id, declared_by, declared_at,
               active, deactivated_by, deactivated_at, deactivation_reason
        FROM release_targets WHERE release_id = :r {"" if include_inactive else "AND active"}
        ORDER BY environment_id, declared_at
    """), {"r": int(release_id)}).mappings().all()
    return [dict(r, declared_at=_iso(r["declared_at"]), deactivated_at=_iso(r["deactivated_at"]))
            for r in rows]


def declare_target(session: Session, *, tenant_id: int, release_id: int,
                   environment_id: int, actor_user_id: int) -> dict:
    existing = session.execute(text(
        "SELECT CAST(id AS text) FROM release_targets WHERE release_id = :r "
        "AND environment_id = :e AND active"), {"r": int(release_id), "e": int(environment_id)}).scalar()
    if existing:
        return {"id": existing, "created": False}
    tid = session.execute(text("""
        INSERT INTO release_targets (release_id, environment_id, declared_by)
        VALUES (:r, :e, :u) RETURNING CAST(id AS text)
    """), {"r": int(release_id), "e": int(environment_id), "u": int(actor_user_id)}).scalar()
    _audit(session, tenant_id=tenant_id, user_id=actor_user_id, action="s4.release_target.declare",
           details={"release_id": release_id, "environment_id": environment_id, "target_id": tid})
    session.flush()
    return {"id": tid, "created": True}


def remove_target(session: Session, *, tenant_id: int, release_id: int,
                  environment_id: int, actor_user_id: int, reason: str = "") -> dict:
    n = session.execute(text("""
        UPDATE release_targets
        SET active = FALSE, deactivated_by = :u, deactivated_at = now(),
            deactivation_reason = :why
        WHERE release_id = :r AND environment_id = :e AND active
    """), {"u": int(actor_user_id), "why": (reason or "").strip() or None,
           "r": int(release_id), "e": int(environment_id)}).rowcount
    if n:
        _audit(session, tenant_id=tenant_id, user_id=actor_user_id, action="s4.release_target.remove",
               details={"release_id": release_id, "environment_id": environment_id, "reason": reason})
    session.flush()
    return {"removed": bool(n)}


def _audit(session, *, tenant_id: Optional[int], user_id: Optional[int], action: str, details: dict) -> None:
    if tenant_id is None:
        return
    # A savepoint: a failed INSERT must not abort the caller's transaction
    # (the tenant-only harness has no public.activity_log; production does).
    try:
        with session.begin_nested():
            session.execute(text("""
                INSERT INTO public.activity_log (tenant_id, user_id, action, entity_type, entity_id, details)
                VALUES (:t, :u, :a, 'run_plan', NULL, CAST(:d AS JSONB))
            """), {"t": int(tenant_id), "u": user_id, "a": action, "d": json.dumps(details, default=str)})
    except Exception as exc:  # noqa: BLE001
        log.warning("plan audit row not written (%s): %s", action, exc)


# --------------------------------------------------------------------------
# the plan (§a)
# --------------------------------------------------------------------------

def _release_keys(session: Session, tenant_id: int, release_id: int) -> list[str]:
    rows = session.execute(text("""
        SELECT r.external_key FROM public.release_requirements rr
        JOIN public.requirements r ON r.id = rr.requirement_id
        WHERE rr.release_id = :rel AND r.tenant_id = :t AND r.deleted_at IS NULL
          AND r.external_key IS NOT NULL
        ORDER BY r.external_key
    """), {"rel": int(release_id), "t": int(tenant_id)}).fetchall()
    return [r[0] for r in rows]


def _release_exists(session: Session, tenant_id: int, release_id: int) -> bool:
    return bool(session.execute(text(
        "SELECT 1 FROM public.releases WHERE id = :r AND tenant_id = :t"),
        {"r": int(release_id), "t": int(tenant_id)}).scalar())


def _schedule_row(session: Session, schedule_id: int) -> Optional[dict]:
    r = session.execute(text("""
        SELECT id, environment_id, cron_expr, enabled, created_by, authorised_by, plan_template
        FROM s4_run_schedules WHERE id = :i
    """), {"i": int(schedule_id)}).mappings().first()
    return dict(r) if r else None


def schedule_template(row: dict) -> dict:
    """A schedule's plan template; a legacy row (no template) READS as the
    tenant-wide plan on its environment — the honest successor of "Run all
    approved" (§d)."""
    t = row.get("plan_template")
    if isinstance(t, str):
        t = json.loads(t)
    if t and t.get("scope_kind") in (SCOPE_REQUIREMENT, SCOPE_RELEASE, SCOPE_TENANT):
        t = dict(t)
        t.setdefault("environment_id", row["environment_id"])
        return t
    return {"scope_kind": SCOPE_TENANT, "environment_id": row["environment_id"],
            "include_hidden": False, "legacy": True}


def schedule_authority(row: dict) -> Optional[int]:
    """Ruling 2: ``authorised_by`` ("claim this schedule") else ``created_by``;
    both NULL → no authority → the tick refuses."""
    return row.get("authorised_by") or row.get("created_by")


def _evidence_envs(session: Session, test_ids: list, tenant_id: int) -> list[int]:
    from primeqa.intelligence.substrate_decision import _environments_with_evidence
    return _environments_with_evidence(session, test_ids, tenant_id=tenant_id)


def _evidence_envs_unfiltered(session: Session, test_ids: list) -> list[int]:
    if not test_ids:
        return []
    rows = session.execute(text(
        "SELECT DISTINCT environment_id FROM s4_execution_runs "
        "WHERE CAST(claim_test_id AS text) = ANY(:tids) ORDER BY environment_id"),
        {"tids": [str(t) for t in test_ids]}).all()
    return [r[0] for r in rows]


def plan(session: Session, *, tenant_id: int, scope: dict,
         planned_by: Optional[int] = None, authorised_by: Optional[int] = None,
         planner_tier: Optional[int] = None, env_reader=None,
         evidence_envs=None) -> dict:
    """Resolve ``scope`` deterministically, record the plan, return the row.

    scope: ``{"scope_kind": requirement|release|schedule|tenant, "key" |
    "release_id" | "schedule_id", "environment_id"?, "include_hidden"?}``.
    Raises :class:`PlanRefused` (no row written) on a refusal.
    """
    from primeqa.core.authz import Tier
    from primeqa.execution_engine.executability import check_recipe_executability
    from primeqa.execution_engine.modes import READ_ONLY, mode_for
    from primeqa.sync.credentials import get_connected_org_for_environment
    from primeqa.sync.readiness import SEQ_CURRENT, resolve_current_sequence
    from primeqa.test_representation import identity, surface_links
    from primeqa.test_representation.coordinator import (
        COVERAGE_LINK_KINDS, SemanticTransactionCoordinator,
    )

    kind = (scope or {}).get("scope_kind")
    if kind not in SCOPE_KINDS:
        raise PlanRefused(REFUSE_UNKNOWN_SCOPE, str(kind))
    env_reader = env_reader or read_env_info(session, tenant_id)
    evidence_envs = evidence_envs or (lambda tids: _evidence_envs(session, tids, tenant_id))
    coord = SemanticTransactionCoordinator()
    conn = session.connection()

    # -- the schedule scope resolves to its template under borrowed authority
    schedule_row = None
    if kind == SCOPE_SCHEDULE:
        schedule_row = _schedule_row(session, scope["schedule_id"])
        if schedule_row is None:
            raise PlanRefused(REFUSE_UNKNOWN_SCHEDULE, str(scope.get("schedule_id")))
        authority = schedule_authority(schedule_row)
        if authority is None:
            raise PlanRefused(REFUSE_NO_AUTHORITY, f"schedule {schedule_row['id']}")
        template = schedule_template(schedule_row)
        inner = dict(template)
        inner_kind = inner["scope_kind"]
        authorised_by = authority
        planned_by = None
    else:
        inner = dict(scope)
        inner_kind = kind
    if planned_by is None and authorised_by is None:
        raise PlanRefused(REFUSE_NO_AUTHORITY, "a plan needs a person or a borrowed authority")

    include_hidden = bool(inner.get("include_hidden", False))
    exclusions: list[dict] = []

    def exclude(x_kind, ref, reason, **detail):
        exclusions.append({"kind": x_kind, "ref": ref, "reason": reason,
                           "sentence": REASON_SENTENCES.get(reason, reason), **detail})

    # -- 1. claims ----------------------------------------------------------
    keys: list[str] = []
    claim_keys: dict[str, set] = {}          # test_id -> {keys}
    claim_link_kinds: dict[str, set] = {}
    scope_ref = ""
    if inner_kind == SCOPE_REQUIREMENT:
        keys = [inner["key"]]; scope_ref = inner["key"]
    elif inner_kind == SCOPE_RELEASE:
        if not _release_exists(session, tenant_id, inner["release_id"]):
            raise PlanRefused(REFUSE_UNKNOWN_RELEASE, str(inner.get("release_id")))
        keys = _release_keys(session, tenant_id, inner["release_id"]); scope_ref = str(inner["release_id"])
    elif inner_kind == SCOPE_TENANT:
        scope_ref = str(inner.get("environment_id"))
    for key in keys:
        for m in coord.list_tests_by_requirement(
                session, external_system=JIRA, external_key=key, link_kind=COVERAGE_LINK_KINDS):
            sid = str(m.test_id)
            claim_keys.setdefault(sid, set()).add(key)
            claim_link_kinds.setdefault(sid, set()).add(str(getattr(m, "link_kind", "")))
    if inner_kind == SCOPE_TENANT:
        rows = session.execute(text(
            "SELECT DISTINCT CAST(test_id AS text) FROM test_claims "
            "WHERE status = 'approved' AND valid_to IS NULL ORDER BY 1")).fetchall()
        tids = [r[0] for r in rows]
        for tid in tids:
            claim_keys.setdefault(tid, set())
        if tids:
            for k, t in session.execute(text(
                    "SELECT external_key, CAST(test_id AS text) FROM test_requirement_links "
                    "WHERE CAST(test_id AS text) = ANY(:ids) AND link_kind IN ('generated_from', 'verifies')"),
                    {"ids": tids}).fetchall():
                claim_keys[t].add(k); claim_link_kinds.setdefault(t, set())
        keys = sorted({k for ks in claim_keys.values() for k in ks})
    test_ids = sorted(claim_keys)
    origins = identity.origins_for_keys(conn, keys) if keys else {}
    latest = coord.get_latest_claims(session, [UUID(t) for t in test_ids]) if test_ids else {}
    approved = coord.get_current_approved_claims(session, [UUID(t) for t in test_ids]) if test_ids else {}

    admitted_claims: dict[str, dict] = {}
    for tid in test_ids:
        lat = latest.get(UUID(tid))
        if lat is not None and getattr(lat, "status", None) == "deprecated":
            exclude("claim", tid, X_CLAIM_DEPRECATED, keys=sorted(claim_keys[tid])); continue
        app = approved.get(UUID(tid))
        if app is None:
            exclude("claim", tid, X_CLAIM_NOT_APPROVED, keys=sorted(claim_keys[tid])); continue
        ks = sorted(claim_keys[tid])
        origin_set = {(origins.get(k) or {}).get("origin", "CANNOT_CLASSIFY") if origins.get(k) else "CANNOT_CLASSIFY" for k in ks}
        hidden_only = bool(ks) and origin_set <= {identity.FIXTURE, identity.PROBE}
        if hidden_only and not include_hidden:
            exclude("claim", tid, X_ORIGIN_PROBE if origin_set == {identity.PROBE} else X_ORIGIN_FIXTURE,
                    keys=ks, origins=sorted(origin_set)); continue
        admitted_claims[tid] = {
            "test_id": tid, "version_seq": app.version_seq,
            "archetype": getattr(app, "archetype", None), "claim_kind": getattr(app, "claim_kind", None),
            "keys": ks, "link_kinds": sorted(claim_link_kinds.get(tid, set())),
            "origins": sorted(origin_set), "unclassified": ("CANNOT_CLASSIFY" in origin_set),
            "recipes": [], "lane": None,
        }

    # -- 2. recipes ---------------------------------------------------------
    from primeqa.execution_engine.errors import ExecutionEngineError
    from primeqa.execution_engine.executability import _ELIGIBLE_STATUSES
    for tid, c in list(admitted_claims.items()):
        eligible = [r for r in coord.list_active_recipes(session, UUID(tid)) if r.status in _ELIGIBLE_STATUSES]
        if not eligible:
            exclude("claim", tid, X_NO_ELIGIBLE_RECIPE, keys=c["keys"]); del admitted_claims[tid]; continue
        kinds = {r.recipe_kind for r in eligible}
        if kinds == {_CONFORMANCE_RECIPE_KIND}:
            c["lane"] = LANE_CONFORMANCE
            c["recipes"] = [{"recipe_id": str(r.recipe_id), "version_seq": r.version_seq,
                             "recipe_kind": r.recipe_kind, "mode": mode_for(r.recipe_kind)} for r in eligible]
            continue
        c["lane"] = LANE_FUNCTIONAL
        ok, reasons = [], []
        for r in eligible:
            if r.recipe_kind == _CONFORMANCE_RECIPE_KIND:
                continue
            try:
                check_recipe_executability(r)
                ok.append(r)
            except ExecutionEngineError as exc:
                reasons.append(f"{r.recipe_id}: {exc}")
                # a failing recipe is named even when the claim survives through another
                exclude("recipe", str(r.recipe_id), X_UNEXECUTABLE_SHAPE, test_id=tid,
                        recipe_kind=r.recipe_kind, detail=str(exc)[:200])
        if not ok:
            exclude("claim", tid, X_UNEXECUTABLE_SHAPE, keys=c["keys"], reasons=reasons[:5]); del admitted_claims[tid]; continue
        c["recipes"] = [{"recipe_id": str(r.recipe_id), "version_seq": r.version_seq,
                         "recipe_kind": r.recipe_kind, "mode": mode_for(r.recipe_kind)} for r in ok]

    # -- 3. required environments ------------------------------------------
    functional_ids = [t for t, c in admitted_claims.items() if c["lane"] == LANE_FUNCTIONAL]
    targets_source, target_ids = "named", []
    if inner_kind == SCOPE_RELEASE:
        declared = list_targets(session, inner["release_id"])
        if declared:
            targets_source, target_ids = "declared", [t["environment_id"] for t in declared]
        else:
            targets_source, target_ids = "fallback:evidence-active", evidence_envs(functional_ids)
    else:
        if inner.get("environment_id") is None:
            raise PlanRefused(REFUSE_NO_ENVIRONMENT, inner_kind)
        target_ids = [int(inner["environment_id"])]
    target_ids = sorted(set(int(e) for e in target_ids))
    # every environment holding evidence for the scope that is NOT a target — named
    for e in _evidence_envs_unfiltered(session, functional_ids):
        if e not in target_ids:
            info = env_reader(e)
            exclude("environment", e, X_ENV_NOT_TARGET, name=(info.name if info else f"env {e}"),
                    is_active=(info.is_active if info else None),
                    is_production=(info.is_production if info else None),
                    execution_policy=(info.execution_policy if info else None))

    environments = []
    admitted_envs = []
    for e in target_ids:
        info = env_reader(e)
        row = {"environment_id": e, "name": (info.name if info else f"env {e}"),
               "is_active": (info.is_active if info else None),
               "is_production": (info.is_production if info else None),
               "execution_policy": (info.execution_policy if info else None),
               "admitted": True, "pin": None}
        if info is None:
            exclude("environment", e, X_ENV_UNKNOWN); row["admitted"] = False
        elif not info.is_active:
            exclude("environment", e, X_ENV_INACTIVE, name=info.name); row["admitted"] = False
        elif info.execution_policy == "disabled":
            exclude("environment", e, X_ENV_DISABLED, name=info.name); row["admitted"] = False
        elif info.is_production and planner_tier is not None and planner_tier < Tier.ADMIN:
            exclude("environment", e, X_PRODUCTION_REQUIRES_ADMIN, name=info.name); row["admitted"] = False
        if row["admitted"]:
            try:
                with session.begin_nested():          # a pin failure never aborts the plan
                    org = get_connected_org_for_environment(conn, e)
                    cur = resolve_current_sequence(session, connected_org_id=org) if org else None
                row["pin"] = {"connected_org_id": org,
                              "org_version_seq": (cur.current_seq if (cur and cur.state == SEQ_CURRENT) else None),
                              "state": (cur.state if cur else "org_unbound")}
            except Exception as exc:  # noqa: BLE001 — a pin failure is shown, never fatal
                row["pin"] = {"connected_org_id": None, "org_version_seq": None, "state": f"unavailable: {exc}"[:120]}
            admitted_envs.append(row)
        environments.append(row)

    # -- 4. admission per (recipe, environment) → the functional manifest ---
    from primeqa.execution_engine.modes import READ_ONLY as _RO
    functional_pairs = []
    for tid in functional_ids:
        c = admitted_claims[tid]
        for env in admitted_envs:
            e = env["environment_id"]
            admitted_recipes, dropped = [], []
            for r in c["recipes"]:
                if env["execution_policy"] == "read_only" and r["mode"] != _RO:
                    dropped.append(r); continue
                admitted_recipes.append(r)
            for r in dropped:
                exclude("recipe", r["recipe_id"], X_READ_ONLY_TARGET, test_id=tid, environment_id=e,
                        recipe_kind=r["recipe_kind"])
            if admitted_recipes:
                functional_pairs.append({"test_id": tid, "environment_id": e,
                                         "recipes": [r["recipe_id"] for r in admitted_recipes]})
            else:
                exclude("claim", tid, X_READ_ONLY_TARGET, environment_id=e, keys=c["keys"],
                        detail="no inspection recipe to admit")

    # -- 5. personas + 6. conformance manifests (Step 3 declarations, consumed) -
    declared_surfaces = []
    for key in keys:
        for d in surface_links.declared_surfaces(session, requirement_key=key):
            declared_surfaces.append({"key": key, "surface_key": d.surface_key,
                                      "inventory_version": d.inventory_version,
                                      "persona_scope": d.persona_scope, "display_name": d.display_name or d.path})
    personas = sorted({d["persona_scope"] for d in declared_surfaces})
    conformance_ids = [t for t, c in admitted_claims.items() if c["lane"] == LANE_CONFORMANCE]
    conformance_manifests = []
    active_v = surface_links.active_inventory_version(session)
    active_set = surface_links.active_claim_set(session, active_v) if active_v is not None else None
    if conformance_ids and not declared_surfaces:
        for tid in conformance_ids:
            exclude("claim", tid, X_SURFACE_NOT_DECLARED, keys=admitted_claims[tid]["keys"]); del admitted_claims[tid]
    elif conformance_ids and active_set:
        set_surfaces = {r[0] for r in session.execute(text("""
            SELECT DISTINCT m.surface_key FROM ui_surface_inventory_members m
            JOIN claim_sets cs ON cs.inventory_version = m.inventory_version
            WHERE cs.id = CAST(:cs AS uuid)""" ), {"cs": active_set}).fetchall()}
        declared_keys = sorted({d["surface_key"] for d in declared_surfaces})
        in_set = [k for k in declared_keys if k in set_surfaces]
        for k in declared_keys:
            if k not in set_surfaces:
                exclude("surface", k, X_SURFACE_NOT_IN_ACTIVE_SET, inventory_version=active_v)
        excluded_surfaces = sorted(set_surfaces - set(in_set))
        for k in excluded_surfaces:
            exclude("surface", k, X_SURFACE_NOT_DECLARED)
        if in_set:
            persona = session.execute(text("SELECT persona_scope FROM claim_sets WHERE id = CAST(:cs AS uuid)"),
                                      {"cs": active_set}).scalar()
            conformance_manifests.append({
                "persona_scope": persona, "claim_set_id": active_set,
                "inventory_version": active_v, "surface_keys": in_set,
                "excluded_surfaces": excluded_surfaces,
                "claims": sorted(t for t in conformance_ids), "environment_id": None,
            })
    elif conformance_ids:
        for tid in conformance_ids:
            exclude("claim", tid, X_SURFACE_NOT_IN_ACTIVE_SET, keys=admitted_claims[tid]["keys"]); del admitted_claims[tid]

    # -- the record ---------------------------------------------------------
    by_kind: dict[str, dict] = {}
    for c in admitted_claims.values():
        k = f"{c['archetype']} / {c['claim_kind']}"
        by_kind.setdefault(k, {"archetype": c["archetype"], "claim_kind": c["claim_kind"], "lane": c["lane"], "claims": []})
        by_kind[k]["claims"].append({"test_id": c["test_id"], "version_seq": c["version_seq"],
                                     "keys": c["keys"], "link_kinds": c["link_kinds"], "origins": c["origins"],
                                     "unclassified": c["unclassified"], "recipes": c["recipes"]})
    counts = {}
    for x in exclusions:
        counts[x["reason"]] = counts.get(x["reason"], 0) + 1
    inputs = {"scope_kind": kind, "scope": {k: v for k, v in (scope or {}).items()},
              "resolved_scope_kind": inner_kind, "environment_id": inner.get("environment_id"),
              "include_hidden": include_hidden, "targets_source": targets_source,
              "schedule": ({"id": schedule_row["id"], "template": schedule_template(schedule_row)} if schedule_row else None),
              "planner_tier": planner_tier}
    resolution = {"requirement_keys": keys, "claims_by_kind": by_kind,
                  "claim_count": len(admitted_claims),
                  "environments": environments, "personas": personas,
                  "declared_surfaces": declared_surfaces,
                  "manifests": {"functional": {"pairs": functional_pairs, "job_count": len(functional_pairs)},
                                "conformance": conformance_manifests},
                  "selection": dict(SEAMS)}
    plan_id = session.execute(text("""
        INSERT INTO run_plans (scope_kind, scope_ref, inputs, resolution, exclusions, planned_by, authorised_by)
        VALUES (:k, :r, CAST(:i AS JSONB), CAST(:res AS JSONB), CAST(:x AS JSONB), :p, :a)
        RETURNING CAST(id AS text)
    """), {"k": kind, "r": (str(scope.get("schedule_id")) if kind == SCOPE_SCHEDULE else scope_ref),
           "i": json.dumps(inputs, default=str), "res": json.dumps(resolution, default=str),
           "x": json.dumps({"items": exclusions, "counts": counts}, default=str),
           "p": planned_by, "a": authorised_by}).scalar()
    _audit(session, tenant_id=tenant_id, user_id=planned_by, action="s4.plan.create",
           details={"plan_id": plan_id, "scope_kind": kind, "scope_ref": scope_ref,
                    "authorised_by": authorised_by, "jobs": len(functional_pairs),
                    "conformance_manifests": len(conformance_manifests), "excluded": len(exclusions)})
    session.flush()
    return get_plan(session, plan_id)


def get_plan(session: Session, plan_id: str) -> Optional[dict]:
    r = session.execute(text("""
        SELECT CAST(id AS text) AS id, scope_kind, scope_ref, inputs, resolution, exclusions,
               planned_by, authorised_by, planned_at, executed_by, executed_at, execution
        FROM run_plans WHERE id = CAST(:i AS uuid)
    """), {"i": str(plan_id)}).mappings().first()
    if r is None:
        return None
    d = dict(r)
    for k in ("inputs", "resolution", "exclusions", "execution"):
        if isinstance(d[k], str):
            d[k] = json.loads(d[k])
    d["planned_at"] = _iso(d["planned_at"]); d["executed_at"] = _iso(d["executed_at"])
    return d


def list_plans(session: Session, *, scope_kind: Optional[str] = None, scope_ref: Optional[str] = None,
               limit: int = 20) -> list[dict]:
    where, params = [], {"n": int(limit)}
    if scope_kind:
        where.append("scope_kind = :k"); params["k"] = scope_kind
    if scope_ref is not None:
        where.append("scope_ref = :r"); params["r"] = str(scope_ref)
    rows = session.execute(text(f"""
        SELECT CAST(id AS text) AS id, scope_kind, scope_ref, planned_by, authorised_by, planned_at,
               executed_by, executed_at,
               (resolution->'manifests'->'functional'->>'job_count') AS job_count,
               jsonb_array_length(COALESCE(resolution->'manifests'->'conformance', '[]'::jsonb)) AS conformance_count,
               (resolution->>'claim_count') AS claim_count
        FROM run_plans {("WHERE " + " AND ".join(where)) if where else ""}
        ORDER BY planned_at DESC LIMIT :n
    """), params).mappings().all()
    return [dict(r, planned_at=_iso(r["planned_at"]), executed_at=_iso(r["executed_at"])) for r in rows]


# --------------------------------------------------------------------------
# "Run this plan" (§a, ruling 3): execute the ROW, once
# --------------------------------------------------------------------------

def execute_plan(session: Session, *, tenant_id: int, plan_id: str,
                 executed_by: Optional[int], subject: Optional[dict] = None,
                 enqueue=None, enqueue_ui=None, sf_client=None,
                 trigger_extra: Optional[dict] = None) -> dict:
    """Enqueue exactly the recorded plan. ``enqueue`` / ``enqueue_ui`` are the
    S4 and conformance enqueue seams (injected by tests). Raises
    :class:`PlanRefused` on an unknown or already-executed plan."""
    row = get_plan(session, plan_id)
    if row is None:
        raise PlanRefused(REFUSE_UNKNOWN_PLAN, str(plan_id))
    if row["executed_at"] is not None:
        raise PlanRefused(REFUSE_ALREADY_EXECUTED, str(plan_id))
    if enqueue is None:
        from primeqa.execution_engine.intake import enqueue_s4_execution
        enqueue = enqueue_s4_execution
    receipt = {"s4_jobs": [], "ui_jobs": [], "refusals": []}
    for pair in row["resolution"]["manifests"]["functional"]["pairs"]:
        try:
            job = enqueue(tenant_id=tenant_id, test_id=pair["test_id"],
                          environment_id=pair["environment_id"], created_by=executed_by,
                          plan_id=str(plan_id))
            attached = (getattr(job, "plan_id", None) not in (None, str(plan_id)))
            receipt["s4_jobs"].append({"test_id": pair["test_id"], "environment_id": pair["environment_id"],
                                       "job_id": getattr(job, "id", None), "attached": attached,
                                       "reason": (X_JOB_ALREADY_ACTIVE if attached else None)})
        except Exception as exc:  # noqa: BLE001 — one refusal never loses the batch; it is recorded
            receipt["refusals"].append({"test_id": pair["test_id"], "environment_id": pair["environment_id"],
                                        "error": str(exc)[:200]})
    if row["resolution"]["manifests"]["conformance"]:
        if enqueue_ui is None:
            from primeqa.execution_engine.ui_manifest import enqueue_ui_run
            enqueue_ui = enqueue_ui_run
        for m in row["resolution"]["manifests"]["conformance"]:
            try:
                out = enqueue_ui(session, subject=subject, claim_set_id=UUID(m["claim_set_id"]),
                                 surface_keys=list(m["surface_keys"]), sf_client=sf_client,
                                 trigger={"plan_id": str(plan_id), "executed_by": executed_by,
                                          "authorised_by": row["authorised_by"], **(trigger_extra or {})})
                receipt["ui_jobs"].append({"persona_scope": m["persona_scope"], "claim_set_id": m["claim_set_id"],
                                           "surface_keys": m["surface_keys"],
                                           "manifest_id": (out or {}).get("manifest_id"),
                                           "job_id": (out or {}).get("job_id")})
            except Exception as exc:  # noqa: BLE001
                receipt["refusals"].append({"claim_set_id": m["claim_set_id"], "error": str(exc)[:200]})
    session.execute(text("""
        UPDATE run_plans SET executed_by = :u, executed_at = now(), execution = CAST(:x AS JSONB)
        WHERE id = CAST(:i AS uuid)
    """), {"u": executed_by, "x": json.dumps(receipt, default=str), "i": str(plan_id)})
    _audit(session, tenant_id=tenant_id, user_id=executed_by, action="s4.plan.execute",
           details={"plan_id": str(plan_id), "s4_jobs": len(receipt["s4_jobs"]),
                    "ui_jobs": len(receipt["ui_jobs"]), "refusals": len(receipt["refusals"]),
                    "authorised_by": row["authorised_by"]})
    session.flush()
    return receipt
