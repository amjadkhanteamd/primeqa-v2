"""S3 generation console — v1's UI bridge to Substrate-3 generation + the S2
read of what it produced (D-165, UI Area 2 slice 2a).

v1 owns the bridge (the allowed v1→substrate direction, like ``s1_sync_console``
and ``substrate_insights``). Three operations for the ``/requirements`` surface:

  - :func:`trigger_s3_generation` — resolve a v1 requirement → ``{key, text}``
    (``s3_enqueue.resolve_requirement``) + validate the env, then pin a queued
    S3 job (``enqueue_s3_generation``). The worker's ``s3_generation_tick`` runs
    it async; there is no synchronous path.
  - :func:`read_requirement_claims` — the requirement's coverage test plan:
    ``coordinator.list_tests_by_requirement`` (COVERAGE_LINK_KINDS — the
    ``generated_from`` links the persister writes per D-166 plus human-curated
    ``verifies`` links) → per test ``get_latest_claim`` +
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
    """Pure: the requirement's coverage test plan on an open S2 session —
    ``generated_from`` links plus human-curated ``verifies`` links
    (COVERAGE_LINK_KINDS). Directly testable on the semantic harness with
    seeded links/claims/recipes. ``labels`` (the org label map) renders
    titles in business language."""
    from primeqa.test_representation.coordinator import (
        COVERAGE_LINK_KINDS, SemanticTransactionCoordinator,
    )
    coord = SemanticTransactionCoordinator()
    matches = coord.list_tests_by_requirement(
        session, external_system="jira", external_key=requirement_key,
        link_kind=COVERAGE_LINK_KINDS)
    claims = []
    seen_tests = set()                       # a test linked under both kinds shows once
    for m in matches:
        if m.test_id in seen_tests:
            continue
        seen_tests.add(m.test_id)
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
            # Conditions render the distinguishing "when …" clause (D-293).
            "title": claim_title(claim.claim_kind, _dump(claim.asserted_truth),
                                 labels,
                                 semantic_conditions=_dump(claim.semantic_conditions)),
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


def _read_test_sequence(session, requirement_key: str) -> list[dict]:
    """Pure: the requirement's live test plan as an ORDERED lean sequence — the
    walk-the-plan read (prev/next strips, live chips). Mirrors ``_read_claims``'
    membership + sort exactly (COVERAGE_LINK_KINDS links, deprecated excluded,
    archetype → claim_kind → test_id) so a position here IS a position on the
    requirement page; skips recipes/labels/titles to stay cheap."""
    from primeqa.test_representation.coordinator import (
        COVERAGE_LINK_KINDS, SemanticTransactionCoordinator,
    )
    coord = SemanticTransactionCoordinator()
    matches = coord.list_tests_by_requirement(
        session, external_system="jira", external_key=requirement_key,
        link_kind=COVERAGE_LINK_KINDS)
    tests = []
    seen_tests = set()                       # mirror _read_claims' dedupe
    for m in matches:
        if m.test_id in seen_tests:
            continue
        seen_tests.add(m.test_id)
        claim = coord.get_latest_claim(session, m.test_id)
        if claim is None:
            continue
        if claim.status == "deprecated":              # D-269: live plan only
            continue
        tests.append({"test_id": str(m.test_id), "status": claim.status,
                      "_sort": (claim.archetype or "", claim.claim_kind or "",
                                str(m.test_id))})
    tests.sort(key=lambda c: c["_sort"])
    for t in tests:
        del t["_sort"]
    return tests


def read_requirement_test_sequence(tenant_id: int, requirement_key: str) -> dict:
    """Best-effort ordered test sequence for the requirement's live plan.
    Never raises. Returns ``{available, tests}`` — each test ``{test_id, status}``."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        from sqlalchemy.orm import Session
        with get_tenant_connection(tenant_id) as conn:
            session = Session(bind=conn)
            try:
                return {"available": True,
                        "tests": _read_test_sequence(session, requirement_key)}
            finally:
                session.close()
    except Exception as exc:
        log.warning("read_requirement_test_sequence unavailable for tenant %s "
                    "key %s: %s", tenant_id, requirement_key, exc)
        return {"available": False, "tests": []}


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
        "request_id": row.get("request_id"),
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
        "       CAST(a.request_id AS text) AS request_id, "
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


# --- read: ONE generation run's assembled detail (the "View" page) -----------
# Everything recorded about a single attempt, rendered as a timeline: the
# attempt (timing/status/error), its outcome (claims written / refusal reasons /
# dedup / partial refusals), and the per-tool LLM telemetry (llm_calls). This is
# ASSEMBLED from recorded events — the worker keeps no line-by-line log (only a
# progress_msg snapshot), so the page says so. Raw prompt/response payloads
# (llm_calls.raw_parameters/raw_response) are deliberately NOT read here — raw
# LLM prompts are a superadmin-only surface.

def read_generation_run_detail(tenant_id: int, requirement_key: str,
                               request_id: str) -> dict:
    """Best-effort read of one generation run (attempt) by its ``request_id``,
    scoped to the requirement so a foreign request_id 404s. Never raises.
    Returns ``{available, run, outcome, llm_calls}`` — ``run=None`` when the
    attempt doesn't exist for this requirement."""
    from datetime import datetime, timezone
    empty = {"available": True, "run": None, "outcome": None, "llm_calls": []}
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            row = conn.execute(text(
                "SELECT a.attempt_no, a.status AS attempt_status, "
                "       CAST(a.request_id AS text) AS request_id, "
                "       a.error_code AS attempt_error_code, a.started_at, a.finished_at, "
                "       j.s1_version_seq, j.error_message AS job_error_message, "
                "       j.progress_msg, "
                "       o.outcome_kind::text AS outcome_kind, o.claims_written, "
                "       o.refusal_kind::text AS refusal_kind, o.refusals, "
                "       o.equivalent_existing, "
                "       o.attempted_interpretation->'partial_refusals' AS partial_refusals, "
                "       CAST(o.outcome_id AS text) AS outcome_id, "
                "       o.created_at AS outcome_at, "
                "       llm.models, llm.calls, llm.cost_usd, llm.in_tok, llm.out_tok "
                "FROM s3_generation_job_attempts a "
                "JOIN s3_generation_jobs j ON j.id = a.job_id "
                "LEFT JOIN generation_outcomes o ON o.request_id = a.request_id "
                "  AND o.requirement_ref->>'key' = :rk "
                "LEFT JOIN LATERAL ("
                "  SELECT array_agg(DISTINCT u.model) AS models, COUNT(*) AS calls, "
                "         COALESCE(SUM(u.cost_usd), 0) AS cost_usd, "
                "         COALESCE(SUM(u.input_tokens), 0) AS in_tok, "
                "         COALESCE(SUM(u.output_tokens), 0) AS out_tok "
                "  FROM public.llm_usage_log u "
                "  WHERE u.tenant_id = :tid AND u.task = :task "
                "    AND u.ts >= a.started_at AND u.ts <= COALESCE(a.finished_at, NOW())"
                ") llm ON true "
                "WHERE j.requirement_key = :rk AND a.request_id = CAST(:rid AS uuid)"),
                {"tid": tenant_id, "task": _GENERATION_TASK,
                 "rk": requirement_key, "rid": request_id}).mappings().first()
            if row is None:
                return empty
            d = dict(row)
            calls = []
            if d.get("outcome_id"):
                calls = conn.execute(text(
                    "SELECT tool_name, operational_outcome::text AS op_outcome, "
                    "       attempt_index, timing_start, timing_duration_ms, "
                    "       token_count_input, token_count_output, "
                    "       model_identifier, prompt_version "
                    "FROM llm_calls "
                    "WHERE generation_outcome_id = CAST(:oid AS uuid) "
                    "ORDER BY timing_start NULLS LAST, created_at"),
                    {"oid": d["outcome_id"]}).mappings().all()

        run = _shape_run_row(d, now=datetime.now(timezone.utc))
        partial = []
        for entry in d.get("partial_refusals") or ():
            if isinstance(entry, dict):
                _pl = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
                partial.append({
                    "ac_ref": entry.get("ac_ref"),
                    "refusal_kind": entry.get("refusal_kind"),
                    "plain": refusal_plain(entry.get("refusal_kind"), [entry]),
                    # B0 provenance: model-authored vs substrate-authored
                    # reason + the pipeline layer that recorded it.
                    "source": _pl.get("detail_source") or "substrate",
                    "layer": _pl.get("detail_layer"),
                })
        outcome = None
        if d.get("outcome_kind"):
            _refs = d.get("refusals") or []
            _r0 = _refs[0] if _refs and isinstance(_refs[0], dict) else {}
            _r0p = _r0.get("payload") if isinstance(_r0.get("payload"), dict) else {}
            outcome = {
                "kind": d["outcome_kind"],
                "at": _iso(d.get("outcome_at")),
                "claims_written": d.get("claims_written") or [],
                "equivalent_existing": d.get("equivalent_existing") or [],
                "refusal_kind": d.get("refusal_kind"),
                "refusal_plain": (refusal_plain(d.get("refusal_kind"), d.get("refusals"))
                                  if d.get("refusal_kind") else None),
                # B0 provenance for the banner: whose words the reason is
                # + the pipeline layer that recorded it.
                "refusal_source": ((_r0p.get("detail_source") or "substrate")
                                   if d.get("refusal_kind") else None),
                "refusal_layer": (_r0p.get("detail_layer")
                                  if d.get("refusal_kind") else None),
                "partial_refusals": partial,
            }
        return {
            "available": True,
            "run": run,
            "outcome": outcome,
            "progress_msg": d.get("progress_msg"),
            "llm_calls": [{
                "tool_name": c["tool_name"], "outcome": c["op_outcome"],
                "attempt_index": c["attempt_index"],
                "at": _iso(c["timing_start"]),
                "duration_ms": c["timing_duration_ms"],
                "tokens_in": c["token_count_input"],
                "tokens_out": c["token_count_output"],
                "model": c["model_identifier"],
                "prompt_version": c["prompt_version"],
            } for c in calls],
        }
    except Exception as exc:
        log.warning("read_generation_run_detail unavailable for tenant %s key %s: %s",
                    tenant_id, requirement_key, exc)
        return {"available": False, "run": None, "outcome": None, "llm_calls": []}



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
    conditions = _dump(claim.semantic_conditions)
    recipe_dicts = [{
        "recipe_id": str(r.recipe_id),
        "trigger_kind": r.trigger_kind,
        "recipe_kind": r.recipe_kind,
        "priority": r.priority,
        "status": r.status,
        "version_seq": r.version_seq,
        "causal_initiation": _dump(r.causal_initiation),
        "observation_realization": _dump(r.observation_realization),
        "execution_environment": _dump(r.execution_environment),
    } for r in recipes]
    result = {
        "test_id": str(test_id),
        "archetype": claim.archetype,
        "claim_kind": claim.claim_kind,
        "status": claim.status,
        "version_seq": claim.version_seq,
        "valid_from": _iso(claim.valid_from),
        "identity_hash": claim.identity_hash,
        # D-206 triage surface: the claim AS a sentence + the honesty badge.
        "title": claim_title(claim.claim_kind, asserted, labels,
                             semantic_conditions=conditions),
        "depth": claim_depth([r.recipe_kind for r in recipes]),
        "asserted_truth": asserted,
        "semantic_conditions": conditions,
        "recipes": recipe_dicts,
    }
    # Readable-body projection (Stage 1, deterministic): the plain-language test
    # case, derived at read time from the claim + its recipes + the same label
    # map the title uses. Best-effort — a projection failure must never break the
    # detail read, so it degrades to no readable body (the raw drawer still
    # renders). ``_readable_skeleton`` is the object handed to the optional
    # Stage-2 phrasing (the view resolves the api_key + flag); the template
    # renders ``readable_body`` only.
    try:
        from primeqa.intelligence.readable_body import (
            build_readable_body, to_display_dict)
        skeleton = build_readable_body(
            claim_kind=claim.claim_kind, archetype=claim.archetype,
            asserted_truth=asserted, semantic_conditions=conditions,
            recipes=recipe_dicts,
            strategy_kind=getattr(claim, "strategy_kind", None),
            data_recipe_ids=[str(x) for x in
                             coord.current_data_recipe_ids(session, test_id)],
            labels=labels)
        result["readable_body"] = to_display_dict(skeleton)
        result["_readable_skeleton"] = skeleton
    except Exception as exc:      # pragma: no cover — belt-and-suspenders
        log.warning("readable_body projection failed for test %s: %s",
                    test_id, exc)
        result["readable_body"] = None
        result["_readable_skeleton"] = None
    return result


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
        "       c.asserted_truth, c.semantic_conditions, c.updated_at, "
        "       l2.external_key "
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
        "title": claim_title(r["claim_kind"], r["asserted_truth"], labels,
                             semantic_conditions=r["semantic_conditions"]),
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
                 labels=None, environment_id=None):
    """Pure: (total, page-rows) of current claims on an open tenant conn. ``q``
    ILIKE-matches claim_kind / archetype / test_id (enum cols cast to text);
    ``status`` filters exactly (the drafts inbox passes ``'draft'``);
    ``environment_id`` keeps claims with at least one run in that env and makes
    ``last_run`` that env's latest (None = tenant-wide, claims org-agnostic).
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
    if environment_id is not None:
        # Multi-org filter: claims are org-agnostic (authored once), so "claims
        # for env N" means claims with at least one run in that env; the
        # last-run chip below is then that env's latest, not any org's.
        clause += (" AND EXISTS (SELECT 1 FROM s4_execution_runs er "
                   "WHERE er.claim_test_id = c.test_id "
                   "AND er.environment_id = :envf)")
    # The lastrun LATERAL always names :envf (None = any org's latest), so the
    # bind must always be present; the COUNT statement ignores the extra key.
    qp["envf"] = environment_id
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
        "c.version_seq, c.updated_at, c.asserted_truth, c.semantic_conditions, "
        "COALESCE(rk.kinds, ARRAY[]::text[]) AS recipe_kinds, "
        "req.external_key AS requirement_key, "
        "lastrun.run_id AS last_run_id, lastrun.outcome AS last_outcome, "
        "lastrun.finished_at AS last_finished, lastrun.environment_id AS last_env "
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
        "         lr.finished_at, lr.environment_id "
        "  FROM s4_execution_runs lr "
        "  WHERE lr.claim_test_id = c.test_id "
        "  AND (CAST(:envf AS int) IS NULL OR lr.environment_id = :envf) "
        "  ORDER BY lr.finished_at DESC LIMIT 1) lastrun ON true "
        f"WHERE c.valid_to IS NULL{clause} "
        "ORDER BY c.updated_at DESC, c.test_id LIMIT :limit OFFSET :offset"),
        {**qp, "limit": limit, "offset": offset}).mappings().all()
    claims = [{"test_id": r["test_id"], "archetype": r["archetype"],
               "claim_kind": r["claim_kind"], "status": r["status"],
               "version_seq": r["version_seq"], "updated_at": _iso(r["updated_at"]),
               "title": claim_title(r["claim_kind"], r["asserted_truth"], labels,
                                    semantic_conditions=r["semantic_conditions"]),
               "depth": claim_depth(r["recipe_kinds"]),
               "requirement_key": r["requirement_key"],
               "last_run": ({"run_id": r["last_run_id"],
                             "outcome": r["last_outcome"],
                             "finished_at": _iso(r["last_finished"]),
                             "environment_id": r["last_env"]}
                            if r["last_outcome"] else None)}
              for r in rows]
    return (total or 0), claims


def list_claims(tenant_id: int, *, page: int = 1, per_page: int = 20, q=None,
                status=None, environment_id=None) -> dict:
    """Best-effort paginated read of the tenant's current claims (the claims
    library; ``status='draft'`` powers the approval inbox). Never raises.
    ``per_page`` capped at 50. ``environment_id`` filters to claims with a run
    in that env and makes each row's ``last_run`` that env's latest (None =
    tenant-wide). Returns
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
                status=status, labels=labels, environment_id=environment_id)
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


# --- read: per-AC coverage from the latest generation (D-331) -----------------

def _assemble_ac_coverage(raw_parameters, partial_refusals) -> list[dict]:
    """Pure: join the propose call's declared ACs + intents with the outcome's
    per-intent refusals into per-AC rows. Statuses (per AC, worst-first):
    ``untestable`` (an intent declared no_admissible_test, with its reason),
    ``refused`` (an intent refused at grounding, with the recorded reason),
    ``proposed`` (>=1 intent, none refused), ``unaddressed`` (no intent carried
    the ac_ref). Intent-level truth only — intent→claim linkage is not
    persisted, so no claim mapping is invented here."""
    rp = raw_parameters or {}
    acs = rp.get("acceptance_criteria") or []
    intents = rp.get("intent_descriptors") or []
    refusals_by_ac: dict = {}
    for entry in (partial_refusals or ()):
        if isinstance(entry, dict) and entry.get("ac_ref") is not None:
            payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
            refusals_by_ac.setdefault(entry["ac_ref"], []).append({
                "plain": refusal_plain(entry.get("refusal_kind"), [entry]),
                # B0 provenance: who authored the recorded reason + the
                # pipeline layer that recorded it. Pre-B0 payloads carry no
                # tags and read as substrate with no layer (they were).
                "source": payload.get("detail_source") or "substrate",
                "layer": payload.get("detail_layer"),
            })
    rows = []
    for ac in acs:
        if not isinstance(ac, dict) or ac.get("index") is None:
            continue
        idx = ac["index"]
        mine = [i for i in intents
                if isinstance(i, dict) and i.get("ac_ref") == idx]
        untestable = [i for i in mine if i.get("no_admissible_test")]
        refused = refusals_by_ac.get(idx, [])
        # B0 provenance: an untestable reason is MODEL prose verbatim (D-247);
        # a grounding-refusal reason carries its payload's recorded source.
        if untestable:
            status, reason = "untestable", (
                untestable[0].get("no_admissible_test_reason") or "")
            reason_source, reason_layer = "model", "resolution"
        elif refused and len(refused) >= len(mine):
            status, reason = "refused", (refused[0]["plain"] or "")
            reason_source = refused[0]["source"]
            reason_layer = refused[0].get("layer")
        elif refused:
            status, reason = "partial", (refused[0]["plain"] or "")
            reason_source = refused[0]["source"]
            reason_layer = refused[0].get("layer")
        elif mine:
            status, reason, reason_source, reason_layer = "proposed", None, None, None
        else:
            status, reason, reason_source, reason_layer = "unaddressed", None, None, None
        rows.append({"index": idx, "label": ac.get("label") or f"AC{idx}",
                     "status": status, "reason": reason,
                     "reason_source": reason_source,
                     "reason_layer": reason_layer,
                     "intents": len(mine), "refused": len(refused)})
    return rows


def read_requirement_ac_coverage(tenant_id: int, requirement_key: str) -> dict:
    """Best-effort: the per-acceptance-criterion coverage picture of the
    requirement's LATEST generation (D-331) — the honest "why isn't X tested"
    surface. The model's declared AC list (D-247, persisted verbatim on the
    propose call's ``raw_parameters``) joined with its intents' ``ac_ref`` tags,
    ``no_admissible_test`` declarations, and the outcome's per-intent partial
    refusals (D-302, ``attempted_interpretation``). Never raises. Returns
    ``{available, generated_at, rows}`` — ``rows`` empty when the latest
    generation declared no AC list (freeform requirement)."""
    out = {"available": True, "generated_at": None, "rows": []}
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            row = conn.execute(text(
                "SELECT o.created_at, "
                "       o.attempted_interpretation->'partial_refusals' AS prs, "
                "       lc.raw_parameters "
                "FROM generation_outcomes o "
                "JOIN LATERAL ("
                "  SELECT raw_parameters FROM llm_calls "
                "  WHERE generation_outcome_id = o.outcome_id "
                "    AND tool_name = 'propose_semantic_intent' "
                "    AND raw_parameters ? 'intent_descriptors' "
                "  ORDER BY created_at DESC LIMIT 1) lc ON true "
                "WHERE o.requirement_ref->>'key' = :rk "
                "ORDER BY o.created_at DESC LIMIT 1"),
                {"rk": requirement_key}).mappings().first()
        if row is None:
            return out
        rp = row["raw_parameters"]
        if isinstance(rp, str):
            import json as _json
            rp = _json.loads(rp)
        out["generated_at"] = _iso(row["created_at"])
        out["rows"] = _assemble_ac_coverage(rp, row["prs"])
        return out
    except Exception as exc:
        log.warning("read_requirement_ac_coverage unavailable for tenant %s "
                    "key %s: %s", tenant_id, requirement_key, exc)
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
        # COVERAGE_LINK_KINDS: generated_from + human-curated verifies
        "WHERE l.external_system = 'jira' "
        "AND l.link_kind IN ('generated_from', 'verifies') "
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


def _count_claims_by_requirement_status(conn, keys) -> dict:
    """Pure: ``{requirement_key: {status: n, ..., 'total': n}}`` for the given
    keys on an open tenant conn. Same population as
    :func:`_count_claims_by_requirement` (current, non-deprecated, generated_from)
    but grouped by claim status so the list chips can show approved/draft/review
    instead of one flat count — the totals stay in lockstep with the flat read."""
    keys = list(keys)
    if not keys:
        return {}
    from sqlalchemy import bindparam
    stmt = text(
        "SELECT l.external_key AS k, c.status::text AS status, "
        "       COUNT(DISTINCT l.test_id) AS n "
        "FROM test_requirement_links l "
        "JOIN test_claims c ON c.test_id = l.test_id AND c.valid_to IS NULL "
        "  AND c.status::text <> 'deprecated' "
        # COVERAGE_LINK_KINDS: generated_from + human-curated verifies
        "WHERE l.external_system = 'jira' "
        "AND l.link_kind IN ('generated_from', 'verifies') "
        "AND l.external_key IN :keys GROUP BY l.external_key, c.status"
    ).bindparams(bindparam("keys", expanding=True))
    rows = conn.execute(stmt, {"keys": keys}).mappings().all()
    out: dict = {}
    for r in rows:
        g = out.setdefault(r["k"], {"total": 0})
        g[r["status"]] = g.get(r["status"], 0) + r["n"]
        g["total"] += r["n"]
    return out


def count_claims_by_requirement_status(tenant_id: int, requirement_keys) -> dict:
    """Best-effort bulk read of per-status generated-claim counts per requirement
    key (the richer list chips). Never raises. Returns ``{available, counts}``
    with ``counts[key] = {status: n, ..., 'total': n}``; missing keys simply
    don't appear (caller defaults to 0)."""
    keys = list(requirement_keys or [])
    if not keys:
        return {"available": True, "counts": {}}
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            return {"available": True,
                    "counts": _count_claims_by_requirement_status(conn, keys)}
    except Exception as exc:
        log.warning("count_claims_by_requirement_status unavailable for tenant %s: %s",
                    tenant_id, exc)
        return {"available": False, "counts": {}}


def keys_with_claims(tenant_id: int) -> dict:
    """Best-effort: the set of requirement external_keys that currently have at
    least one live generated test (same population as the count reads). Powers
    the requirements-list coverage filter — the caller intersects these keys
    with the v1 rows in SQL so pagination totals stay honest. Never raises.
    Returns ``{available, keys}`` — ``available=False`` means the caller must
    DROP the filter (not silently render wrong results)."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            rows = conn.execute(text(
                "SELECT DISTINCT l.external_key AS k "
                "FROM test_requirement_links l "
                "JOIN test_claims c ON c.test_id = l.test_id AND c.valid_to IS NULL "
                "  AND c.status::text <> 'deprecated' "
                # COVERAGE_LINK_KINDS: generated_from + human-curated verifies
                "WHERE l.external_system = 'jira' "
                "AND l.link_kind IN ('generated_from', 'verifies')"
            )).mappings().all()
        return {"available": True, "keys": {r["k"] for r in rows}}
    except Exception as exc:
        log.warning("keys_with_claims unavailable for tenant %s: %s", tenant_id, exc)
        return {"available": False, "keys": set()}
