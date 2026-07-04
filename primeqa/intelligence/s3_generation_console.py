"""S3 generation console — v1's UI bridge to Substrate-3 generation + the S2
read of what it produced (D-165, UI Area 2 slice 2a).

v1 owns the bridge (the allowed v1→substrate direction, like ``s1_sync_console``
and ``substrate_insights``). Three operations for the ``/requirements`` surface:

  - :func:`trigger_s3_generation` — resolve a v1 requirement → ``{key, text}``
    (``s3_enqueue.resolve_requirement``) + validate the env, then pin a queued
    S3 job (``enqueue_s3_generation``). The worker's ``s3_generation_tick`` runs
    it async; there is no synchronous path.
  - :func:`read_requirement_claims` — the requirement's generated test plan:
    ``coordinator.list_tests_by_requirement`` (the ``generated_from`` links the
    persister writes per D-166) → per test ``get_latest_claim`` +
    ``list_active_recipes``, flattened for the template.
  - :func:`read_latest_s3_job` — the most-recent S3 job for the requirement key,
    so a page reload during generation re-renders progress + resumes polling.

All three are **best-effort** — never raise; a substrate hiccup returns
``available=False`` / ``ok=False`` rather than breaking the requirement page. The
requirement key is the substrate ``external_key``: ``jira_key`` or ``req-<id>``,
exactly as ``s3_enqueue._requirement_to_ref`` mints it (the single source of
truth — import it rather than re-deriving, so the read can never drift from what
generation wrote).
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from primeqa.intelligence.claim_presentation import claim_depth, claim_title, refusal_plain

log = logging.getLogger(__name__)

_LATEST_JOB_SQL = (
    "SELECT id, status, progress_pct, progress_msg, error_code, error_message, "
    "created_at, completed_at "
    "FROM s3_generation_jobs WHERE requirement_key = :rk "
    "ORDER BY created_at DESC LIMIT 1")

_ACTIVE = ("queued", "claimed", "running")


def _iso(v):
    return v.isoformat() if v is not None and hasattr(v, "isoformat") else v


# --- trigger (v1 read + substrate enqueue) -----------------------------------

def trigger_s3_generation(db, *, tenant_id: int, requirement_id: int,
                          environment_id: int, created_by=None,
                          force_rerun: bool = False) -> dict:
    """Resolve the v1 requirement + validate the env, then enqueue an S3 job. The
    worker runs it async. Best-effort — returns ``{ok: False, error: ...}`` on any
    failure (never raises).

    ``force_rerun`` (D-314): the UI "Generate/Regenerate" button passes True so an
    explicit click re-runs a terminal job in place (else a finished job at the
    current S1 version is a dead end — the click resolves to it and nothing runs).
    Default False keeps the plain idempotent enqueue for any non-UI caller."""
    try:
        from primeqa.core.repository import EnvironmentRepository
        from primeqa.intelligence.s3_enqueue import resolve_requirement
        from primeqa.generation.intake import enqueue_s3_generation

        ref = resolve_requirement(db, requirement_id, tenant_id)
        if ref is None:
            return {"ok": False, "error": "Requirement not found."}
        if EnvironmentRepository(db).get_environment(environment_id, tenant_id) is None:
            return {"ok": False, "error": "Environment not found."}
        job = enqueue_s3_generation(
            tenant_id=tenant_id, requirement_ref=ref,
            environment_id=environment_id, created_by=created_by,
            force_rerun=force_rerun)
        return {"ok": True, "job_id": job.id, "status": job.status,
                "requirement_key": ref["key"]}
    except Exception as exc:                          # e.g. no S1 version pinned yet
        log.warning("trigger_s3_generation failed for tenant %s req %s: %s",
                    tenant_id, requirement_id, exc)
        return {"ok": False, "error": str(exc)}


# --- read: the requirement's generated test plan (S2 claims + recipes) -------

def _read_claims(session, requirement_key: str, labels=None) -> list[dict]:
    """Pure: the requirement's ``generated_from`` test plan on an open S2 session.
    Directly testable on the semantic harness with seeded links/claims/recipes.
    ``labels`` (the org label map) renders titles in business language."""
    from primeqa.test_representation.coordinator import SemanticTransactionCoordinator
    coord = SemanticTransactionCoordinator()
    matches = coord.list_tests_by_requirement(
        session, external_system="jira", external_key=requirement_key,
        link_kind="generated_from")
    claims = []
    for m in matches:
        claim = coord.get_latest_claim(session, m.test_id)
        if claim is None:                             # link with no live claim — skip
            continue
        # D-269: business-facing coverage views render the LIVE/active test set —
        # a deprecated (superseded) claim still renders on its detail + lineage
        # pages, just not in the requirement plan, so each assertion shows once.
        # (TODO: an optional ?include_deprecated toggle if a business view ever
        # needs the full set — not built this slice.)
        if claim.status == "deprecated":
            continue
        recipes = coord.list_active_recipes(session, m.test_id)
        claims.append({
            "test_id": str(m.test_id),
            "archetype": claim.archetype,
            "claim_kind": claim.claim_kind,
            "status": claim.status,
            "version_seq": claim.version_seq,
            "recipe_count": len(recipes),
            "recipes": [{"trigger_kind": r.trigger_kind, "recipe_kind": r.recipe_kind,
                         "priority": r.priority, "status": r.status} for r in recipes],
            "linked_at": _iso(m.linked_at),
            # D-206 triage surface: the claim AS a sentence + the honesty badge.
            "title": claim_title(claim.claim_kind, _dump(claim.asserted_truth),
                                 labels),
            "depth": claim_depth([r.recipe_kind for r in recipes]),
        })
    # deterministic: archetype then claim_kind then test_id
    claims.sort(key=lambda c: (c["archetype"] or "", c["claim_kind"] or "", c["test_id"]))
    return claims


def read_requirement_claims(tenant_id: int, requirement_key: str) -> dict:
    """Best-effort read of the requirement's generated test plan. Never raises.
    Returns ``{available, claims}`` — ``available=False`` on any read error (e.g.
    the tenant has no substrate schema)."""
    try:
        from primeqa.intelligence.entity_labels import label_map
        from primeqa.semantic.connection import get_tenant_connection
        from sqlalchemy.orm import Session
        labels = label_map(tenant_id)        # business labels for the spine (D-267)
        with get_tenant_connection(tenant_id) as conn:
            session = Session(bind=conn)
            try:
                return {"available": True,
                        "claims": _read_claims(session, requirement_key, labels)}
            finally:
                session.close()
    except Exception as exc:
        log.warning("read_requirement_claims unavailable for tenant %s key %s: %s",
                    tenant_id, requirement_key, exc)
        return {"available": False, "claims": []}


# --- read: the latest S3 job for the requirement (in-flight progress) --------

def _read_latest_job(conn, requirement_key: str) -> dict | None:
    """Pure: the most-recent S3 job for a requirement key on an open tenant conn."""
    row = conn.execute(text(_LATEST_JOB_SQL), {"rk": requirement_key}).mappings().first()
    if row is None:
        return None
    return {
        "id": row["id"], "status": row["status"],
        "active": row["status"] in _ACTIVE,
        "progress_pct": row["progress_pct"] or 0,
        "progress_msg": row["progress_msg"],
        "error_code": row["error_code"], "error_message": row["error_message"],
        "created_at": _iso(row["created_at"]), "completed_at": _iso(row["completed_at"]),
    }


def read_latest_s3_job(tenant_id: int, requirement_key: str) -> dict:
    """Best-effort read of the requirement's most-recent S3 job. Never raises.
    Returns ``{available, job}`` — ``job=None`` when none exists yet;
    ``available=False`` on any read error."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            return {"available": True, "job": _read_latest_job(conn, requirement_key)}
    except Exception as exc:
        log.warning("read_latest_s3_job unavailable for tenant %s key %s: %s",
                    tenant_id, requirement_key, exc)
        return {"available": False, "job": None}


# --- read: the generation-run HISTORY table (D-315 panel → D-316 table) -------
# The "Generation history" table under the requirement's Test plan — ALL runs,
# newest first, each with status + tests produced + LLM model/cost + any error.
# The unit of a "run" is an ATTEMPT (s3_generation_job_attempts): one row per
# actual generation (each re-run/retry mints a fresh attempt with its own
# request_id), across every job for the requirement (a job per S1 version). Three
# joined sources per attempt: the ATTEMPT (status/timing/error_code) + its JOB
# (s1_version_seq, error_message), the OUTCOME (generation_outcomes by request_id
# — draft = tests produced, refusal = the declined reason), and the LLM COST
# (public.llm_usage_log). Cost is attributed by (tenant, task='generation', ts
# within the attempt's [started_at, finished_at] window): the worker runs one
# generation per tenant sequentially, so attempt windows never overlap and the
# 'generation' calls in a window are that attempt's. generation's gateway calls
# are NOT tagged with a requirement_id (build_tool_turn_fn passes only task+model),
# so the per-attempt time window is the available attribution — precise for the
# single-worker queue. public.llm_usage_log is reachable because the tenant conn's
# search_path includes public.

# == GENERATION_TASK in primeqa/generation/run.py (drift-guarded in tests). Kept
# as a literal so the requirement page doesn't import the generation runtime.
_GENERATION_TASK = "generation"

_RUN_HISTORY_LIMIT = 50


def _shape_run_row(row, *, now) -> dict:
    """Pure: one generation-run (attempt) → the history-table row dict. Merges the
    attempt status with the outcome into a single ``status_label``/``status_tone``
    and a ``detail`` (the refusal reason or the failure error). ``now`` (tz-aware)
    closes the duration for an in-flight run. Directly unit-testable — no DB."""
    started = row.get("started_at")
    finished = row.get("finished_at")
    duration_s = None
    if started is not None:
        end = finished or now
        try:
            duration_s = max(0.0, (end - started).total_seconds())
        except Exception:                      # naive/aware mismatch — skip
            duration_s = None

    run_status = row.get("attempt_status") or "running"
    outcome_kind = row.get("outcome_kind")
    claims = row.get("claims_written")
    tests_written = len(claims) if isinstance(claims, list) else None

    # One merged status: the run's outcome is more informative than 'completed'.
    if run_status in ("queued", "claimed", "running"):
        label, tone = "running", "indigo"
    elif run_status == "failed":
        label, tone = "failed", "red"
    elif run_status == "cancelled":
        label, tone = "cancelled", "gray"
    elif outcome_kind == "refusal":
        label, tone = "refused", "amber"
    elif outcome_kind == "draft":
        if tests_written:
            label, tone = "generated", "green"
        elif row.get("equivalent_existing"):
            label, tone = "matched existing", "gray"
        else:
            label, tone = "no new tests", "gray"
    else:                                      # completed, but no outcome row
        label, tone = "completed", "gray"

    detail = None
    if outcome_kind == "refusal":
        detail = refusal_plain(row.get("refusal_kind"), row.get("refusals"))
    elif run_status in ("failed", "cancelled"):
        detail = row.get("attempt_error_code") or row.get("job_error_message")

    llm = None
    if row.get("calls"):
        llm = {
            "models": [m for m in (row.get("models") or []) if m],
            "cost_usd": float(row.get("cost_usd") or 0),
            "input_tokens": int(row.get("in_tok") or 0),
            "output_tokens": int(row.get("out_tok") or 0),
        }
    return {
        "when": _iso(started),
        "finished": _iso(finished),
        "s1_version_seq": row.get("s1_version_seq"),
        "attempt_no": row.get("attempt_no"),
        "run_status": run_status,
        "outcome_kind": outcome_kind,
        "status_label": label,
        "status_tone": tone,
        "tests_written": tests_written,
        "duration_s": duration_s,
        "llm": llm,
        "detail": detail,
    }


def _read_generation_runs(conn, tenant_id, requirement_key, *, now, limit) -> list:
    """Pure-ish: every generation run (attempt) for a requirement, newest first,
    on an open tenant conn. One query joins each attempt to its job, its outcome
    (by request_id), and a per-attempt LLM cost aggregate (time-window LATERAL)."""
    rows = conn.execute(text(
        "SELECT a.attempt_no, a.status AS attempt_status, "
        "       a.error_code AS attempt_error_code, a.started_at, a.finished_at, "
        "       j.s1_version_seq, j.error_message AS job_error_message, "
        "       o.outcome_kind::text AS outcome_kind, o.claims_written, "
        "       o.refusal_kind::text AS refusal_kind, o.refusals, "
        "       o.equivalent_existing, "
        "       llm.models, llm.calls, llm.cost_usd, llm.in_tok, llm.out_tok "
        "FROM s3_generation_job_attempts a "
        "JOIN s3_generation_jobs j ON j.id = a.job_id "
        "LEFT JOIN generation_outcomes o ON o.request_id = a.request_id "
        "LEFT JOIN LATERAL ("
        "  SELECT array_agg(DISTINCT u.model) AS models, COUNT(*) AS calls, "
        "         COALESCE(SUM(u.cost_usd), 0) AS cost_usd, "
        "         COALESCE(SUM(u.input_tokens), 0) AS in_tok, "
        "         COALESCE(SUM(u.output_tokens), 0) AS out_tok "
        "  FROM public.llm_usage_log u "
        "  WHERE u.tenant_id = :tid AND u.task = :task "
        "    AND u.ts >= a.started_at AND u.ts <= COALESCE(a.finished_at, NOW())"
        ") llm ON true "
        "WHERE j.requirement_key = :rk "
        "ORDER BY a.started_at DESC LIMIT :limit"),
        {"tid": tenant_id, "task": _GENERATION_TASK, "rk": requirement_key,
         "limit": limit}).mappings().all()
    return [_shape_run_row(dict(r), now=now) for r in rows]


def read_generation_runs(tenant_id: int, requirement_key: str) -> dict:
    """Best-effort read of the requirement's generation-run history — ALL runs,
    newest first, each with status + tests-produced + LLM model/cost + any error
    (the Test-plan "Generation history" table, D-316). Never raises. Returns
    ``{available, runs}`` — ``runs=[]`` when never generated; ``available=False``
    on any read error."""
    from datetime import datetime, timezone
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            runs = _read_generation_runs(
                conn, tenant_id, requirement_key,
                now=datetime.now(timezone.utc), limit=_RUN_HISTORY_LIMIT)
        return {"available": True, "runs": runs}
    except Exception as exc:
        log.warning("read_generation_runs unavailable for tenant %s key %s: %s",
                    tenant_id, requirement_key, exc)
        return {"available": False, "runs": []}



# --- read: a single claim's semantic detail + its recipes (2b) ---------------

def _dump(body):
    """A Pydantic body (asserted_truth / a recipe leg) → a JSON-safe dict for the
    template. Tolerant: returns ``{}`` for None / a non-model value."""
    if body is not None and hasattr(body, "model_dump"):
        return body.model_dump(mode="json")
    return body if isinstance(body, dict) else {}


def _read_claim_detail(session, test_id, labels=None) -> dict | None:
    """Pure: the current claim version + its active recipes, bodies dumped to
    JSON-safe dicts. Returns ``None`` when no live claim matches ``test_id``.
    Directly testable on the generation harness. ``labels`` (the org label map)
    renders the title in business language."""
    from primeqa.test_representation.coordinator import SemanticTransactionCoordinator
    coord = SemanticTransactionCoordinator()
    claim = coord.get_latest_claim(session, test_id)
    if claim is None:
        return None
    recipes = coord.list_active_recipes(session, test_id)
    asserted = _dump(claim.asserted_truth)
    return {
        "test_id": str(test_id),
        "archetype": claim.archetype,
        "claim_kind": claim.claim_kind,
        "status": claim.status,
        "version_seq": claim.version_seq,
        "valid_from": _iso(claim.valid_from),
        "identity_hash": claim.identity_hash,
        # D-206 triage surface: the claim AS a sentence + the honesty badge.
        "title": claim_title(claim.claim_kind, asserted, labels),
        "depth": claim_depth([r.recipe_kind for r in recipes]),
        "asserted_truth": asserted,
        "semantic_conditions": _dump(claim.semantic_conditions),
        "recipes": [{
            "recipe_id": str(r.recipe_id),
            "trigger_kind": r.trigger_kind,
            "recipe_kind": r.recipe_kind,
            "priority": r.priority,
            "status": r.status,
            "version_seq": r.version_seq,
            "causal_initiation": _dump(r.causal_initiation),
            "observation_realization": _dump(r.observation_realization),
            "execution_environment": _dump(r.execution_environment),
        } for r in recipes],
    }


def read_claim_detail(tenant_id: int, test_id) -> dict:
    """Best-effort read of a single claim's semantic detail + recipes. Never
    raises. Returns ``{available, found, claim}`` — ``found=False`` when no live
    claim matches; ``available=False`` on any read error."""
    try:
        from primeqa.intelligence.entity_labels import label_map
        from primeqa.semantic.connection import get_tenant_connection
        from sqlalchemy.orm import Session
        labels = label_map(tenant_id)        # business labels for the spine (D-267)
        with get_tenant_connection(tenant_id) as conn:
            session = Session(bind=conn)
            try:
                detail = _read_claim_detail(session, test_id, labels)
                return {"available": True, "found": detail is not None, "claim": detail}
            finally:
                session.close()
    except Exception as exc:
        log.warning("read_claim_detail unavailable for tenant %s test %s: %s",
                    tenant_id, test_id, exc)
        return {"available": False, "found": False, "claim": None}


# --- read: sibling claims (D-228 / F3 — the supersession context) -------------
# "Siblings" = other CURRENT claims sharing a requirement link AND the same
# claim_kind — the set where a predecessor would live. Supersession is a human
# judgment (the D-226 fork closed against auto-deprecate: same requirement +
# same kind legitimately coexist, e.g. SQ-205's two automation-effect claims);
# this read gives the human that context at the moment of approval/deprecation.

def _read_claim_siblings(session, test_id, labels=None) -> list[dict]:
    """Pure: current claims linked to any of this claim's requirements with the
    same claim_kind, excluding the claim itself. Status chips + titles for the
    panel; newest first. ``labels`` renders titles in business language."""
    from sqlalchemy import text as _text
    rows = session.execute(_text(
        "SELECT DISTINCT c.test_id, c.claim_kind, c.archetype, c.status, "
        "       c.asserted_truth, c.updated_at, l2.external_key "
        "FROM test_requirement_links l1 "
        "JOIN test_requirement_links l2 "
        "  ON l2.external_system = l1.external_system "
        " AND l2.external_key = l1.external_key "
        "JOIN test_claims c "
        "  ON c.test_id = l2.test_id AND c.valid_to IS NULL "
        "WHERE l1.test_id = CAST(:tid AS uuid) "
        "  AND l2.test_id <> CAST(:tid AS uuid) "
        "  AND c.claim_kind = (SELECT claim_kind FROM test_claims "
        "                      WHERE test_id = CAST(:tid AS uuid) "
        "                        AND valid_to IS NULL) "
        "ORDER BY c.updated_at DESC"
    ), {"tid": str(test_id)}).mappings().all()
    return [{
        "test_id": str(r["test_id"]),
        "claim_kind": r["claim_kind"],
        "archetype": r["archetype"],
        "status": r["status"],
        "title": claim_title(r["claim_kind"], r["asserted_truth"], labels),
        "requirement_key": r["external_key"],
        "updated_at": _iso(r["updated_at"]),
    } for r in rows]


def read_claim_siblings(tenant_id: int, test_id) -> dict:
    """Best-effort read of the claim's same-requirement same-kind siblings.
    Never raises. Returns ``{available, siblings}``."""
    try:
        from primeqa.intelligence.entity_labels import label_map
        from primeqa.semantic.connection import get_tenant_connection
        from sqlalchemy.orm import Session
        labels = label_map(tenant_id)        # business labels for the spine (D-267)
        with get_tenant_connection(tenant_id) as conn:
            session = Session(bind=conn)
            try:
                return {"available": True,
                        "siblings": _read_claim_siblings(session, test_id, labels)}
            finally:
                session.close()
    except Exception as exc:
        log.warning("read_claim_siblings unavailable for tenant %s test %s: %s",
                    tenant_id, test_id, exc)
        return {"available": False, "siblings": []}


# --- read: the source requirement key (D-233 — the back-link) -----------------

def read_claim_requirement(tenant_id: int, test_id) -> dict:
    """Best-effort: the requirement external_key this claim was generated from
    (the most recent ``generated_from`` link). Never raises. Returns
    ``{available, requirement_key}`` — ``requirement_key`` is None when the claim
    has no such link. Resolving the key to a viewable requirement id is the
    caller's job (it needs the v1 session — the substrate stores only the key)."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            row = conn.execute(text(
                "SELECT external_key FROM test_requirement_links "
                "WHERE test_id = CAST(:tid AS uuid) "
                "AND link_kind = 'generated_from' "
                "ORDER BY linked_at DESC LIMIT 1"),
                {"tid": str(test_id)}).mappings().first()
        return {"available": True,
                "requirement_key": row["external_key"] if row else None}
    except Exception as exc:
        log.warning("read_claim_requirement unavailable for tenant %s test %s: %s",
                    tenant_id, test_id, exc)
        return {"available": False, "requirement_key": None}


# --- read: the claims library (all current claims, paginated + search) (2c) --
# No coordinator method lists claims cross-requirement, so the library reads
# test_claims directly (the s1_sync_console pattern: a tenant-scoped raw read).
# "Current" = valid_to IS NULL, matching get_latest_claim's filter.

def _list_claims(conn, *, limit: int, offset: int, q=None, status=None,
                 labels=None):
    """Pure: (total, page-rows) of current claims on an open tenant conn. ``q``
    ILIKE-matches claim_kind / archetype / test_id (enum cols cast to text);
    ``status`` filters exactly (the drafts inbox passes ``'draft'``).
    ``labels`` (the org label map) renders titles in business language.

    Each row also carries the D-206 triage surface — ``title`` (the claim AS a
    plain-English sentence, from ``asserted_truth``), ``depth`` (behavioral vs
    configuration-check, from the current recipes' kinds), the most recent
    ``generated_from`` requirement key, and (D-233) ``last_run`` — the single
    most recent s4 run for the claim ``{run_id, outcome, finished_at}`` or None —
    all batched in one statement (no N+1). ``last_run`` is by recency (matches
    ``read_claim_runs``), NOT the decision's version-correct flake window."""
    clause, qp = "", {}
    if q:
        clause += (" AND (c.claim_kind::text ILIKE :q OR c.archetype::text ILIKE :q "
                   "OR CAST(c.test_id AS text) ILIKE :q)")
        qp["q"] = f"%{q}%"
    if status:
        clause += " AND c.status::text = :status"
        qp["status"] = status
    else:
        # D-269: the Test Library (unfiltered) is a business-facing coverage view
        # — render the live/active set, not superseded claims. An explicit status
        # filter (e.g. the drafts inbox's 'draft', or someone asking for
        # 'deprecated') is honored as-is; a deprecated claim still opens on its
        # own detail page. The clause feeds BOTH the COUNT and the page rows, so
        # the total stays consistent with the filtered list. (TODO: an optional
        # include_deprecated arg if a business view ever needs the full set.)
        clause += " AND c.status::text <> 'deprecated'"
    total = conn.execute(text(
        f"SELECT COUNT(*) FROM test_claims c WHERE c.valid_to IS NULL{clause}"),
        qp).scalar()
    rows = conn.execute(text(
        "SELECT CAST(c.test_id AS text) AS test_id, c.archetype::text AS archetype, "
        "c.claim_kind::text AS claim_kind, c.status::text AS status, "
        "c.version_seq, c.updated_at, c.asserted_truth, "
        "COALESCE(rk.kinds, ARRAY[]::text[]) AS recipe_kinds, "
        "req.external_key AS requirement_key, "
        "lastrun.run_id AS last_run_id, lastrun.outcome AS last_outcome, "
        "lastrun.finished_at AS last_finished "
        "FROM test_claims c "
        "LEFT JOIN LATERAL ("
        "  SELECT array_agg(DISTINCT r.recipe_kind::text) AS kinds "
        "  FROM test_recipes r "
        "  WHERE r.claim_test_id = c.test_id AND r.valid_to IS NULL) rk ON true "
        "LEFT JOIN LATERAL ("
        "  SELECT l.external_key FROM test_requirement_links l "
        "  WHERE l.test_id = c.test_id AND l.link_kind = 'generated_from' "
        "  ORDER BY l.linked_at DESC LIMIT 1) req ON true "
        "LEFT JOIN LATERAL ("
        "  SELECT CAST(lr.run_id AS text) AS run_id, lr.outcome::text AS outcome, "
        "         lr.finished_at "
        "  FROM s4_execution_runs lr "
        "  WHERE lr.claim_test_id = c.test_id "
        "  ORDER BY lr.finished_at DESC LIMIT 1) lastrun ON true "
        f"WHERE c.valid_to IS NULL{clause} "
        "ORDER BY c.updated_at DESC, c.test_id LIMIT :limit OFFSET :offset"),
        {**qp, "limit": limit, "offset": offset}).mappings().all()
    claims = [{"test_id": r["test_id"], "archetype": r["archetype"],
               "claim_kind": r["claim_kind"], "status": r["status"],
               "version_seq": r["version_seq"], "updated_at": _iso(r["updated_at"]),
               "title": claim_title(r["claim_kind"], r["asserted_truth"], labels),
               "depth": claim_depth(r["recipe_kinds"]),
               "requirement_key": r["requirement_key"],
               "last_run": ({"run_id": r["last_run_id"],
                             "outcome": r["last_outcome"],
                             "finished_at": _iso(r["last_finished"])}
                            if r["last_outcome"] else None)}
              for r in rows]
    return (total or 0), claims


def list_claims(tenant_id: int, *, page: int = 1, per_page: int = 20, q=None,
                status=None) -> dict:
    """Best-effort paginated read of the tenant's current claims (the claims
    library; ``status='draft'`` powers the approval inbox). Never raises.
    ``per_page`` capped at 50. Returns
    ``{available, claims, total, page, per_page, total_pages}``."""
    page = max(1, page)
    per_page = max(1, min(per_page, 50))
    try:
        from primeqa.intelligence.entity_labels import label_map
        from primeqa.semantic.connection import get_tenant_connection
        labels = label_map(tenant_id)        # business labels for the spine (D-267)
        with get_tenant_connection(tenant_id) as conn:
            total, claims = _list_claims(
                conn, limit=per_page, offset=(page - 1) * per_page, q=q,
                status=status, labels=labels)
        total_pages = max(1, (total + per_page - 1) // per_page)
        return {"available": True, "claims": claims, "total": total,
                "page": page, "per_page": per_page, "total_pages": total_pages}
    except Exception as exc:
        log.warning("list_claims unavailable for tenant %s: %s", tenant_id, exc)
        return {"available": False, "claims": [], "total": 0,
                "page": page, "per_page": per_page, "total_pages": 1}


# --- read: the latest generation outcome's dedup note (D-206) -----------------

def read_latest_generation_note(tenant_id: int, requirement_key: str) -> dict:
    """Best-effort: what did the requirement's most recent generation actually
    DO? Surfaces the notes the page would otherwise hide (all read as
    "Generate silently did nothing/less"): a DEDUP (matched an already-existing
    equivalent test, minted nothing new), a REFUSAL (the engine declined,
    with its recorded reason — D-206.1, the SQ-205 confusion), and PARTIAL
    refusals (D-302 — intents the engine declined inside an otherwise-drafting
    batch, each with its recorded reason). Never raises.
    Returns ``{available, deduped, outcome_kind, refusal_kind, refusal_plain,
    partial_refusals}``."""
    out = {"available": True, "deduped": False, "outcome_kind": None,
           "refusal_kind": None, "refusal_plain": None, "partial_refusals": []}
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            row = conn.execute(text(
                "SELECT outcome_kind::text AS outcome_kind, equivalent_existing, "
                "       refusal_kind::text AS refusal_kind, refusals, "
                "       attempted_interpretation->'partial_refusals' AS partial_refusals "
                "FROM generation_outcomes "
                "WHERE requirement_ref->>'key' = :rk "
                "ORDER BY created_at DESC LIMIT 1"),
                {"rk": requirement_key}).mappings().first()
        if row is None:
            return out
        out["deduped"] = bool(row["equivalent_existing"])
        out["outcome_kind"] = row["outcome_kind"]
        out["refusal_kind"] = row["refusal_kind"]
        out["refusal_plain"] = refusal_plain(row["refusal_kind"], row["refusals"])
        for entry in row["partial_refusals"] or ():
            if isinstance(entry, dict):
                out["partial_refusals"].append({
                    "ac_ref": entry.get("ac_ref"),
                    "refusal_kind": entry.get("refusal_kind"),
                    "plain": refusal_plain(entry.get("refusal_kind"), [entry]),
                })
        return out
    except Exception as exc:
        log.warning("read_latest_generation_note unavailable for tenant %s key %s: %s",
                    tenant_id, requirement_key, exc)
        out["available"] = False
        return out


# --- read: per-requirement current-claim counts (the list chips) (2c/#143) ----

def _count_claims_by_requirement(conn, keys) -> dict:
    """Pure: ``{requirement_key: current-generated-claim count}`` for the given
    keys on an open tenant conn. Counts distinct tests with a ``generated_from``
    link whose claim is still current (valid_to IS NULL) — matches the detail."""
    keys = list(keys)
    if not keys:
        return {}
    from sqlalchemy import bindparam
    # D-269: the requirements-list "N test cases" badge is a business-facing
    # coverage count — exclude deprecated (superseded) claims so the badge matches
    # the filtered plan (which also drops deprecated). Active = valid_to IS NULL.
    stmt = text(
        "SELECT l.external_key AS k, COUNT(DISTINCT l.test_id) AS n "
        "FROM test_requirement_links l "
        "JOIN test_claims c ON c.test_id = l.test_id AND c.valid_to IS NULL "
        "  AND c.status::text <> 'deprecated' "
        "WHERE l.external_system = 'jira' AND l.link_kind = 'generated_from' "
        "AND l.external_key IN :keys GROUP BY l.external_key"
    ).bindparams(bindparam("keys", expanding=True))
    rows = conn.execute(stmt, {"keys": keys}).mappings().all()
    return {r["k"]: r["n"] for r in rows}


def count_claims_by_requirement(tenant_id: int, requirement_keys) -> dict:
    """Best-effort bulk read of current generated-claim counts per requirement key
    (the requirements-list chips). Never raises. Returns ``{available, counts}``;
    missing keys simply don't appear in ``counts`` (caller defaults to 0)."""
    keys = list(requirement_keys or [])
    if not keys:
        return {"available": True, "counts": {}}
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            return {"available": True, "counts": _count_claims_by_requirement(conn, keys)}
    except Exception as exc:
        log.warning("count_claims_by_requirement unavailable for tenant %s: %s",
                    tenant_id, exc)
        return {"available": False, "counts": {}}
