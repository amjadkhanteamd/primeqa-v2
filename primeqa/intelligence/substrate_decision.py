"""Substrate decision evidence — theme #3 slice 1 (D-198).

Assembles **decision-grade** per-claim evidence for a release: the D-172 console
chain (release requirement keys → ``generated_from`` links → claims → approved
version → grounding + latest run) hardened with the three correctness rules a
*decision* needs that an evidence *panel* didn't:

1. **Version-correct runs** — the counted run is the latest whose
   ``claim_version_seq`` is NULL or equals the **approved** version. A non-NULL
   mismatch is *superseded evidence* (a run of an older/newer version of the
   test): excluded from outcomes, surfaced as ``superseded_newer_run`` so the
   decision can warn that the freshest run isn't of the shipped version.
2. **NULL-seq tolerance** — ``claim_version_seq`` is legitimately Optional
   through the plan chain; a NULL-seq run **counts** (strict exclusion would
   zero out real evidence) but carries ``version_unknown=True``.
3. **Grounding staleness** — grounding evaluated at an S1 version older than
   the release's ORG current sequence is flagged ``stale`` (the org moved
   under it). Step B (D-479 B): the current sequence is NEVER computed here —
   ``primeqa.sync.readiness.resolve_current_sequence`` is the one resolver,
   org-REQUIRED; a caller without an org gets ``CANNOT_DETERMINE`` recorded
   on every evidence row (``row["sequence"]``) and the pure compute refuses
   to grade (``cannot_determine``), never a tenant-wide number, never a
   silent ``stale=None``.

Read-only over the caller's tenant-scoped session. The pure compute over this
evidence (risk rollup + GO/NO-GO) is slice 2; the v1 ``DecisionEngine`` is
untouched throughout (the composer isolation, D-198).
"""
from __future__ import annotations

import logging

from sqlalchemy import text

log = logging.getLogger(__name__)

# The recent COUNTED runs, newest-first: S4 as the base (true recency;
# interpret-failed runs still show, verdict NULL — the _CLAIM_RUNS_SQL
# discipline) + the D-198 version filter. Row 0 is the counted latest; the
# window (5) feeds the D-200 flakiness detection. One query for the WHOLE
# corpus (D-331 follow-up): per-claim round trips made the assembly
# O(claims × RTT) against a remote DB — the window is a per-claim
# ROW_NUMBER partition, with each claim's approved version_seq riding in
# via unnest (NULL seq disables the filter — unapproved claim).
_FLAKE_WINDOW = 5

# D-300 review-fix B1: the raw window is WIDER than the flake window because a
# bva batch's per-probe rows collapse to ONE logical outcome before counting —
# 25 raw rows cover 5 logical entries even at a 5-probe batch size.
_RAW_RUN_WINDOW = 25

_COUNTED_RUNS_BULK_SQL = (
    "SELECT tid, run_id, outcome, finished_at, claim_version_seq, "
    "batch_id, verdict, detail FROM ("
    "SELECT CAST(r.claim_test_id AS text) AS tid, "
    "CAST(r.run_id AS text) AS run_id, r.outcome::text AS outcome, "
    "r.finished_at, r.claim_version_seq, "
    "CAST(r.batch_id AS text) AS batch_id, "
    "i.verdict::text AS verdict, i.detail AS detail, "
    "ROW_NUMBER() OVER (PARTITION BY r.claim_test_id "
    "ORDER BY r.finished_at DESC) AS rn "
    "FROM s4_execution_runs r "
    "LEFT JOIN s6_interpretations i ON i.run_id = r.run_id "
    "JOIN unnest(CAST(:tids AS uuid[]), CAST(:seqs AS int[])) "
    "AS t(tid, seq) ON r.claim_test_id = t.tid "
    "WHERE (CAST(:env AS int) IS NULL OR r.environment_id = :env) "
    "AND (t.seq IS NULL "
    "     OR r.claim_version_seq IS NULL OR r.claim_version_seq = t.seq) "
    f") w WHERE rn <= {_RAW_RUN_WINDOW} ORDER BY tid, rn")


def _collapse_batches(rows) -> list:
    """D-300 review-fix B1: collapse a run-all batch's per-probe rows into ONE
    logical run entry so the flake window and the ``latest_run`` surface count
    BATCHES, not probes. Without this, a bva claim whose accept probe
    consistently fails reads as ``[failed, passed, failed, passed]`` — the
    chronic-flip signature — and D-200 quarantine would NEUTRALIZE the honest
    strict-AND red (a wrong-green at release grade).

    Rows arrive newest-first. A ``batch_id IS NULL`` row (every single-mode run)
    passes through unchanged — the single path is byte-identical. Rows sharing a
    ``batch_id`` collapse to one entry whose outcome is 'passed' iff EVERY probe
    passed, else the SINKING probe's outcome — and the representative row (its
    run_id / verdict / detail, for the D-237 cause) is that sinking probe, so a
    blocking bva claim renders the probe that sank it, never an arbitrary
    passing accept probe (review finding #4)."""
    logical: list = []
    seen_batches: set = set()
    by_batch: dict = {}
    for r in rows:
        b = r["batch_id"]
        if b is None:
            continue
        by_batch.setdefault(b, []).append(r)
    for r in rows:
        b = r["batch_id"]
        if b is None:
            logical.append(dict(r))
            continue
        if b in seen_batches:
            continue
        seen_batches.add(b)
        probes = by_batch[b]
        sinking = next((p for p in probes if p["outcome"] != "passed"), None)
        rep = dict(sinking if sinking is not None else probes[0])
        rep["outcome"] = "passed" if sinking is None else sinking["outcome"]
        logical.append(rep)
    return logical[:_FLAKE_WINDOW]


def _cause_phrase(detail, verdict, outcome) -> str | None:
    """One plain sentence for WHY a not-passed run failed (D-237). Prefers the
    S6 attribution sentence stashed at ``detail.cause.detail`` (D-229 — already
    English), falls back to the humanized verdict, then the bare outcome. Pure,
    tolerant of dict / json-str / None ``detail`` — never raises."""
    if isinstance(detail, str):
        import json
        try:
            detail = json.loads(detail)
        except Exception:
            detail = None
    if isinstance(detail, dict):
        cause = detail.get("cause")
        if isinstance(cause, dict):
            d = cause.get("detail")
            if isinstance(d, str) and d.strip():
                return d.strip()
    from primeqa.intelligence.claim_presentation import verdict_plain
    return verdict_plain(verdict, outcome)


def _is_flaky(outcomes) -> bool:
    """D-200 quarantine detection: ≥2 outcome transitions across the recent
    counted runs (newest-first) — the chronically-flipping signature. A stable
    pass→fail edge (one transition) is a REAL regression, never quarantined."""
    transitions = sum(1 for a, b in zip(outcomes, outcomes[1:]) if a != b)
    return transitions >= 2


def _claim_verified(c, *, session=None) -> bool:
    """THE single place a claim's good state is decided for the release decision
    (D-280, Slice 4b). Replaces the inline ``latest_run.outcome == 'passed'`` rule
    that used to be scattered across the quarantine / pass-rate / blocker reads.

    Today every claim is ``single``: Verified iff the latest counted run passed —
    the N=1 identity Slice 2a proved byte-identical to the old rule over live data
    (``apply_strategy('single', [adapter(outcome)]) == (outcome == 'passed')``).
    Polarity is a NO-OP on the single arm (S4 already folds accept/reject into the
    outcome — Slice 2a's ``test_polarity_does_not_change_single_verdict``), so
    ``expect_rejection=False`` is exact, not an assumption.

    A **never-run** claim (no ``latest_run``) is NOT verified and is NEVER routed
    through the applier (``single`` requires N=1, and a never-run claim has no
    probe) — the separate ``never_run`` / ``has_runs`` blocker owns that case.

    Prefers the value :func:`_assemble_claim_evidence` already attached (computed
    once per claim); for a hand-built evidence dict that lacks it, derives the SAME
    rule — so there is exactly ONE definition, never an inline outcome check.

    **bva branch (D-283, Slice 4d) — a DEAD BRANCH today.** When a claim records
    ``strategy_kind == 'bva'`` (only once §4a authors it, 4f), good state comes from
    the claim's most-recent run-all batch (4c.1, :func:`read_batch_completeness`): an
    INCOMPLETE batch (a probe row absent, or no batch — the reader short-circuits
    with ``probes=None``) ⇒ NOT Verified; a COMPLETE batch ⇒ the bva strict-AND over
    its probes (4a). The arm is **only ever called with a reader-certified COMPLETE
    set** (never ``probes=None`` / empty, which the arm raises on) — preserving 4a's
    "trusts the set is complete" contract. The branch sits AFTER the attached-value
    short-circuit, so a claim whose ``verified`` was already attached (the production
    path, 4f) never needs a ``session`` here; the ``session`` is for the derive case.
    An ABSENT kind (``None`` — every claim today) takes the SINGLE path (the honest
    default); an UNRECOGNIZED kind RAISES (a wiring bug, never a silent single
    fallback — the 3.4 router discipline). Reachable by NO claim until §4a."""
    v = c.get("verified")
    if v is not None:
        return v
    strategy_kind = c.get("strategy_kind")
    if strategy_kind == "bva":
        if session is None:
            # Reached only on the DERIVE case (no attached ``verified``); the batch
            # read needs a session. 4f wires ``_assemble`` to attach the bva
            # ``verified`` (or pass a session), so this is unreachable until then —
            # fail LOUD with the contract, not a cryptic ``AttributeError`` on a
            # ``None`` session.
            raise ValueError(
                "bva claim reached _claim_verified with no attached `verified` and "
                "no session — the derive case requires a session to read the batch")
        from primeqa.interpretation.batch_reader import read_batch_completeness
        from primeqa.interpretation.strategy import apply_strategy
        result = read_batch_completeness(session, c.get("test_id"))
        if not result.complete:
            return False                      # incomplete/no-batch ⇒ not-Verified;
                                              # the arm is NEVER called (probes=None)
        return apply_strategy(c, "bva", list(result.probes))   # complete ⇒ strict-AND
    if strategy_kind not in (None, "single"):
        raise ValueError(f"unrecognized strategy kind {strategy_kind!r}")
    lr = c.get("latest_run")
    if lr is None:
        return False
    from primeqa.interpretation.strategy import (
        apply_strategy, probe_result_from_outcome)
    return apply_strategy(
        None, "single",
        [probe_result_from_outcome(lr["outcome"], expect_rejection=False)])


def _claim_quarantined(c) -> bool:
    """D-232: is a claim quarantined for the release decision? A MANUAL pin WINS
    (always quarantined); a MANUAL lift wins the other way (never); absent a manual
    entry, the live flake signal governs — flaky AND its latest run not Verified."""
    mq = c.get("manual_quarantine")          # 'pinned' | 'lifted' | None
    if mq == "pinned":
        return True
    if mq == "lifted":
        return False
    return bool(c.get("flaky") and not _claim_verified(c))

# Is there a NEWER run of a DIFFERENT (non-NULL) version than the approved one?
# (Superseded evidence newer than what we counted — the decision warns on it.)
# One query for the corpus: each claim's (approved seq, counted-latest cutoff)
# rides in via unnest; the result is the set of claim ids that have one.
_NEWER_SUPERSEDED_BULK_SQL = (
    "SELECT DISTINCT CAST(r.claim_test_id AS text) AS tid "
    "FROM s4_execution_runs r "
    "JOIN unnest(CAST(:tids AS uuid[]), CAST(:seqs AS int[]), "
    "CAST(:cutoffs AS timestamptz[])) AS t(tid, seq, cutoff) "
    "ON r.claim_test_id = t.tid "
    "WHERE (CAST(:env AS int) IS NULL OR r.environment_id = :env) "
    "AND r.claim_version_seq IS NOT NULL AND r.claim_version_seq != t.seq "
    "AND r.finished_at > COALESCE(t.cutoff, '-infinity')")


# Step B: the engine computes NO current sequence of its own — the former
# ``_current_s1_seq`` (an org-less ``SemanticOrgModel`` → tenant-wide MAX on
# the 0-1 env branch) is gone; ``_assemble_claim_evidence`` reads
# ``primeqa.sync.readiness.resolve_current_sequence`` (LLD_STEP_B §a/§b).


def _claim_test_ids(session, external_keys):
    """Memoised within a read scope (close 2): every console on a page asks for
    the same keys, and the answer cannot change inside one transaction. Outside
    a scope this is a plain call, byte-identical to before."""
    from primeqa.semantic.read_scope import memo
    return memo(session, ("claim_test_ids", tuple(sorted(external_keys or ()))),
                lambda: _claim_test_ids_uncached(session, external_keys))


def _claim_test_ids_uncached(session, external_keys):
    """The release's requirement keys → (ordered distinct claim test_ids,
    ``{test_id: {requirement keys}}``) via the COVERAGE_LINK_KINDS links
    (``generated_from`` + curated ``verifies``). ONE link query for all keys
    (the batched coordinator read); iteration order — keys as given, matches
    by ``(test_id, link_kind)`` within each — is the per-key read's exactly."""
    from primeqa.test_representation.coordinator import (
        COVERAGE_LINK_KINDS,
        SemanticTransactionCoordinator,
    )
    coord = SemanticTransactionCoordinator()
    matches = coord.list_tests_by_requirements(
        session, external_system="jira", external_keys=list(external_keys),
        link_kind=COVERAGE_LINK_KINDS)
    test_ids, seen = [], set()
    keys_by_tid: dict[str, set] = {}               # D-237: claim → its requirement key(s)
    for key in external_keys:
        for m in matches.get(key, ()):
            sid = str(m.test_id)
            keys_by_tid.setdefault(sid, set()).add(key)   # record even when shared
            if sid not in seen:                    # a claim shared by 2 reqs
                seen.add(sid)
                test_ids.append(m.test_id)
    return test_ids, keys_by_tid


# The distinct environments the release's claims have run in — the per-env
# decision loop's activation read (>= 2 ⇒ one verdict per env).
_ENVS_WITH_EVIDENCE_SQL = (
    "SELECT DISTINCT environment_id FROM s4_execution_runs "
    "WHERE CAST(claim_test_id AS text) = ANY(:tids) "
    "ORDER BY environment_id")

# Step 3 (LLD_STEP_3 §d — the release-scope interim): the scope's
# environments are the ACTIVE environments holding evidence for its claims.
# Legacy evidence on an inactive environment (env 78 "Prod1", found at
# VERIFICATION_STEP_2 §k.1) stays on the runs surfaces; it stops holding a
# release's scope. The same predicate EnvironmentRepository.list_environments
# applies (core/repository.py). The declared-target model replaces this
# interim in Step 4 (the Run Planner resolves required environments).
_ACTIVE_ENVS_WITH_EVIDENCE_SQL = (
    "SELECT DISTINCT r.environment_id FROM s4_execution_runs r "
    "JOIN public.environments e ON e.id = r.environment_id "
    "WHERE CAST(r.claim_test_id AS text) = ANY(:tids) "
    "  AND e.tenant_id = :tenant_id AND e.is_active "
    "ORDER BY r.environment_id")


def _environments_with_evidence(session, test_ids, *, tenant_id=None) -> list[int]:
    """The environments holding evidence for ``test_ids`` — filtered to the
    tenant's ACTIVE environments when ``tenant_id`` is given (every
    production caller passes it: the engine's per-environment loop and the
    release-scope check). ``tenant_id=None`` keeps the unfiltered read (the
    tenant-only test harness carries no ``public.environments``)."""
    if not test_ids:
        return []
    if tenant_id is None:
        rows = session.execute(text(_ENVS_WITH_EVIDENCE_SQL),
                               {"tids": [str(t) for t in test_ids]}).all()
    else:
        rows = session.execute(text(_ACTIVE_ENVS_WITH_EVIDENCE_SQL),
                               {"tids": [str(t) for t in test_ids],
                                "tenant_id": int(tenant_id)}).all()
    return [r[0] for r in rows]


def _assemble_claim_evidence(session, external_keys, *, tenant_id=None,
                             environment_id=None, connected_org_id=None,
                             manual_q=None) -> list[dict]:
    """Memoised within a read scope (close 2), otherwise a plain call.

    The decision tab assembles the same (keys x environment x org) evidence
    twice — once for the substrate verdict, once for the Step 5 quality
    preview. The key carries EVERY input, including the resolved manual
    quarantine map, so a memo hit can only serve a call with identical
    arguments. ``external_keys`` is keyed in its given order, not sorted: a
    different order misses the memo rather than risking a wrong hit.

    One deliberate strengthening: a caller that passes ``manual_q=None`` inside
    a scope gets the map read ONCE for the whole render, where before each
    console opened its own connection and read it again. Two consoles on one
    page can therefore no longer disagree about a pin made mid-render."""
    from primeqa.semantic.read_scope import in_scope, memo
    if not in_scope(session):
        return _assemble_claim_evidence_uncached(
            session, external_keys, tenant_id=tenant_id,
            environment_id=environment_id,
            connected_org_id=connected_org_id, manual_q=manual_q)
    if manual_q is None and tenant_id is not None:
        from primeqa.intelligence import quarantine as _q
        manual_q = memo(session, ("manual_states", tenant_id),
                        lambda: _q.manual_states(tenant_id, session=session))
    key = ("claim_evidence", tuple(external_keys or ()), tenant_id,
           environment_id, str(connected_org_id),
           tuple(sorted((manual_q or {}).items())))
    return memo(session, key, lambda: _assemble_claim_evidence_uncached(
        session, external_keys, tenant_id=tenant_id,
        environment_id=environment_id,
        connected_org_id=connected_org_id, manual_q=manual_q))


def _assemble_claim_evidence_uncached(session, external_keys, *, tenant_id=None,
                                      environment_id=None, connected_org_id=None,
                                      manual_q=None) -> list[dict]:
    """The release's requirement keys → one decision-grade evidence row per distinct
    claim. Each row::

        {test_id, approved_seq,
         grounding: {overall, stale, evaluated_at_version_seq} | None,
         latest_run: {run_id, outcome, verdict, finished_at,
                      version_unknown} | None,
         superseded_newer_run, never_run, flaky, recent_outcomes,
         manual_quarantine: 'pinned' | 'lifted' | None}

    ``manual_quarantine`` (D-232) carries the operator's persisted pin/unpin (read
    via ``tenant_id`` when given) so the pure decision honors it — a MANUAL pin/lift
    WINS over the live ``flaky`` signal.

    ``environment_id`` scopes every run read to one connected org (the per-env
    decision path); ``connected_org_id`` (that env's org) anchors grounding
    staleness to the org's own latest S1 version. Both ``None`` = the exact
    single-org read this function always did. ``manual_q`` accepts the
    quarantine map precomputed by a per-env loop (one read, not one per env).

    Assembly is SET-BASED: one query per evidence facet across the whole
    corpus (links → latest/approved versions → grounding → run windows →
    superseded check), never one per claim — the old per-claim loop was
    O(claims × round-trips), which a remote DB's RTT turned into minutes.
    """
    test_ids, keys_by_tid = _claim_test_ids(session, external_keys)
    if not test_ids:
        return []
    from primeqa.test_representation.coordinator import (
        SemanticTransactionCoordinator,
    )
    coord = SemanticTransactionCoordinator()

    # Step B: the ONE org-required resolver; ``None`` org → the recorded
    # refusal on every row (never a tenant-wide number).
    from primeqa.sync.readiness import (
        SEQ_CURRENT, covered_reads_changed, resolve_current_sequence,
        resolve_run_readiness_bulk,
    )
    resolution = resolve_current_sequence(session, connected_org_id=connected_org_id)
    sequence = resolution.as_dict()
    current_seq = (resolution.current_seq
                   if resolution.state == SEQ_CURRENT else None)
    # D-232: the persisted MANUAL quarantine overrides (own connection — never
    # poisons this session; empty without a tenant_id or before the migration).
    if manual_q is None:
        from primeqa.intelligence import quarantine as _q
        manual_q = _q.manual_states(tenant_id) if tenant_id is not None else {}

    # D-219: a deprecated claim is RETIRED from the corpus — its stale
    # run evidence must not grade the release (D-ε-1 deprecation is the
    # human's "this test no longer applies" signal).
    latest_by_tid = coord.get_latest_claims(session, test_ids)
    live_ids = [tid for tid in test_ids
                if getattr(latest_by_tid.get(tid), "status", None)
                != "deprecated"]
    if not live_ids:
        return []
    approved_by_tid = coord.get_current_approved_claims(session, live_ids)
    seq_by_tid = {tid: a.version_seq for tid, a in approved_by_tid.items()}

    # Grounding for the version the release ships: at the approved seq, else
    # the latest verdict when no approved version exists (the D-172 idiom).
    # ``connected_org_id`` reads THAT org's verdict; ``None`` reads worst-of
    # across orgs (identical on a single-org store).
    from primeqa.evolution import read_grounding_validity_bulk
    gv_by_tid = read_grounding_validity_bulk(
        session, [(tid, seq_by_tid.get(tid)) for tid in live_ids],
        connected_org_id=connected_org_id)

    run_rows = session.execute(
        text(_COUNTED_RUNS_BULK_SQL),
        {"tids": [str(t) for t in live_ids],
         "seqs": [seq_by_tid.get(t) for t in live_ids],
         "env": environment_id}).mappings().all()
    raw_by_tid: dict[str, list] = {}
    for r in run_rows:                      # newest-first within each claim
        raw_by_tid.setdefault(r["tid"], []).append(r)

    out = []
    sup_tids, sup_seqs, sup_cutoffs = [], [], []
    for tid in live_ids:
        sid = str(tid)
        approved = approved_by_tid.get(tid)
        approved_seq = seq_by_tid.get(tid)

        gv = gv_by_tid.get(tid)
        grounding = None
        if gv is not None:
            # Step 2, ruling R-ground: a grounding is stale only when a COVERED
            # read changed after its verdict — the same targeted predicate as
            # run readiness, never "org moved → all stale". ``None`` only under
            # a recorded CANNOT_DETERMINE (row["sequence"] carries the reason).
            if current_seq is not None and connected_org_id is not None:
                changed = covered_reads_changed(
                    session, claim_test_id=tid, connected_org_id=connected_org_id,
                    since_seq=gv.evaluated_at_version_seq)
                stale = bool(changed)
            else:
                changed, stale = (), None
            grounding = {"overall": gv.overall, "stale": stale,
                         "evaluated_at_version_seq": gv.evaluated_at_version_seq,
                         "changed_reads": [list(c) for c in changed]}

        # D-300 review-fix B1: batches collapse to ONE logical run each (the
        # single path passes through byte-identical — batch_id NULL).
        rows = _collapse_batches(raw_by_tid.get(sid, []))
        row = rows[0] if rows else None
        latest_run = None
        if row is not None:
            latest_run = {
                "run_id": row["run_id"], "outcome": row["outcome"],
                "verdict": row["verdict"],
                "finished_at": (row["finished_at"].isoformat()
                                if row["finished_at"] else None),
                "version_unknown": row["claim_version_seq"] is None,
                "cause": None,        # set below, off the Verified predicate
            }
        # D-280 (Slice 4b): the claim's good state, decided ONCE here via the single
        # `Verified` predicate (replaces the old inline `outcome == 'passed'`). A
        # never-run claim is NOT verified and skips the applier (the never_run
        # blocker owns it).
        # D-287 (Slice 4f.1): pass the recorded `strategy_kind` + `test_id` + the
        # `session` so the 4d bva branch is REACHABLE WITH ITS SESSION when a claim
        # becomes bva (4f.2) — satisfying the 4d guard (which raises on a bva claim
        # with no session). BEHAVIOR-NEUTRAL today: every claim's `strategy_kind` is
        # NULL (4f.0 shipped the column all-NULL), so `_claim_verified` takes the
        # single path exactly as before; the kind/test_id/session are inert on that
        # path. The wiring is dead until 4f.2 writes the first 'bva'.
        verified = _claim_verified(
            {"latest_run": latest_run, "test_id": sid,
             "strategy_kind": getattr(approved, "strategy_kind", None)},
            session=session)
        if latest_run is not None and not verified:
            # D-237: the plain-English cause for a not-Verified latest run.
            latest_run["cause"] = _cause_phrase(row["detail"], row["verdict"],
                                                row["outcome"])
        recent_outcomes = [r["outcome"] for r in rows]
        flaky = _is_flaky(recent_outcomes)

        if approved_seq is not None:
            sup_tids.append(sid)
            sup_seqs.append(approved_seq)
            sup_cutoffs.append(row["finished_at"] if row is not None else None)

        out.append({
            "test_id": sid,
            "external_keys": sorted(keys_by_tid.get(sid, ())),  # D-237
            "approved_seq": approved_seq,
            "grounding": grounding,
            "latest_run": latest_run,
            "verified": verified,                          # D-280: the decided good state
            "superseded_newer_run": False,                 # set below, one query
            "never_run": latest_run is None,
            "flaky": flaky,
            "recent_outcomes": recent_outcomes,
            "manual_quarantine": manual_q.get(sid),        # D-232: pinned|lifted|None
            "sequence": sequence,                          # Step B: the resolution
            # Step 2: a run with no grounding verdict is UNGRADED on the
            # grounding axis (ruling R-ungrounded) — recorded, never "fine".
            "ungrounded": latest_run is not None and gv is None,
        })

    if sup_tids:
        hit = {r[0] for r in session.execute(
            text(_NEWER_SUPERSEDED_BULK_SQL),
            {"tids": sup_tids, "seqs": sup_seqs, "cutoffs": sup_cutoffs,
             "env": environment_id}).all()}
        for c in out:
            if c["test_id"] in hit:
                c["superseded_newer_run"] = True
    # Step 2 (§b/§c): readiness for (claim, THIS environment) from the run
    # stamp — the org axis, targeted through what each claim reads. The
    # 0-env branch has no environment and no runs: every row is NEVER_RUN.
    if environment_id is not None:
        ready = resolve_run_readiness_bulk(
            session, [(c["test_id"], environment_id) for c in out])
        for c in out:
            c["readiness"] = ready[(c["test_id"], int(environment_id))].as_dict()
    else:
        from primeqa.sync.readiness import ReadinessResolution
        never = ReadinessResolution(state="NEVER_RUN").as_dict()
        for c in out:
            c["readiness"] = dict(never)
    return out


# ---------------------------------------------------------------------------
# Slice 2 — the pure decision compute + the best-effort wrapper (D-198).
# Output shape mirrors the v1 DecisionEngine.evaluate dict ({recommendation,
# confidence, reasoning[], criteria_met, metrics}) so the ledger / template /
# CI render both engines uniformly; `risk` is the substrate-native addition.
# ---------------------------------------------------------------------------

# Risk-level vocabulary kept identical to risk_engine._score_to_level for UI
# continuity (the internals are NOT reused — its inputs are v1-only).
def _risk_level(score: int) -> str:
    if score >= 75:
        return "critical"
    if score >= 50:
        return "high"
    if score >= 25:
        return "medium"
    return "low"


def compute_substrate_decision(claim_evidence, criteria=None, *, now=None) -> dict:
    """Pure: slice-1 evidence rows + per-release ``decision_criteria`` → the
    substrate's recommendation. Checks:

    - ``has_runs``      — ALL claims never_run → blocker (no evidence at all).
    - ``pass_rate``     — counted latest runs vs ``substrate_min_pass_rate``
                          (default 95); errored counts as not-passed.
    - ``grounding_integrity`` — each ``broken`` grounding is a blocker when
                          ``substrate_block_on_broken_grounding`` (default True);
                          ``drifted`` / stale-``intact`` are warnings.
    - ``coverage``      — SOME claims never_run → warning.
    - ``version_currency`` — superseded-newer / version-unknown runs → warning.
    - ``freshness``     — newest counted run older than
                          ``substrate_max_run_age_hours`` (default 168) → warning.

    Recommendation: 0 blockers + 0 warnings → ``go`` (0.95); 0 blockers →
    ``conditional_go`` (0.75); else ``no_go`` (0.90) — v1's confidence scheme.
    """
    from datetime import datetime, timezone

    criteria = criteria or {}
    min_pass_rate = criteria.get("substrate_min_pass_rate", 95)
    block_on_broken = criteria.get("substrate_block_on_broken_grounding", True)
    max_age_hours = criteria.get("substrate_max_run_age_hours", 168)
    now = now or datetime.now(timezone.utc)

    if not claim_evidence:
        return {"applicable": False, "claim_count": 0}

    reasoning, criteria_met = [], {}
    blockers = warnings = 0

    # Step B — org_sequence, evaluated FIRST. The resolution rides on every
    # row (``sequence``); a CANNOT_DETERMINE (or a row that carries no
    # resolution at all — fail closed) means the engine cannot grade
    # staleness and refuses the grade. Every other check still runs and its
    # metrics still fill: facts stay facts; only the grade refuses.
    sequence = _sequence_of(claim_evidence)
    ungraded = sequence.get("state") != _SEQ_CURRENT
    if ungraded:
        reasoning.append({"check": "org_sequence", "status": "fail",
                          "detail": _sequence_refusal_sentence(sequence)})
        criteria_met["org_sequence"] = False
    else:
        criteria_met["org_sequence"] = True

    counted = [c for c in claim_evidence if c["latest_run"] is not None]
    never_run = [c for c in claim_evidence if c["never_run"]]

    # Step 2 (§c) — readiness. An unrun, unstampable or ungrounded claim is an
    # UNGRADED INPUT: per D9 it blocks GO and CONDITIONAL GO (a graded blocker
    # still makes NO GO), never NO GO alone; the line names the counts. A STALE
    # claim is graded evidence that is old → a warning naming its reads. A row
    # that carries no readiness at all is treated as CANNOT_DETERMINE.
    _rs = lambda c: ((c.get("readiness") or {}).get("state") or "CANNOT_DETERMINE")  # noqa: E731
    r_never = [c for c in claim_evidence if _rs(c) == "NEVER_RUN"]
    r_cannot = [c for c in claim_evidence if _rs(c) == "CANNOT_DETERMINE"]
    r_stale = [c for c in claim_evidence if _rs(c) == "STALE"]
    r_current = [c for c in claim_evidence if _rs(c) == "CURRENT"]
    r_ungrounded = [c for c in claim_evidence
                    if c.get("ungrounded") and _rs(c) != "NEVER_RUN"]
    ungraded_inputs = r_never + r_cannot + [c for c in r_ungrounded
                                            if c not in r_never and c not in r_cannot]
    if ungraded_inputs:
        parts = []
        if r_never:
            parts.append(f"{len(r_never)} never run in this environment")
        unst = [c for c in r_cannot if (c.get("readiness") or {}).get("reason") == "unstamped"]
        if unst:
            parts.append(f"{len(unst)} run without a stamp")
        if len(r_cannot) - len(unst):
            parts.append(f"{len(r_cannot) - len(unst)} of undeterminable readiness")
        if r_ungrounded:
            parts.append(f"{len(r_ungrounded)} with no grounding verdict for this org")
        reasoning.append({"check": "readiness", "status": "fail",
                          "detail": f"{len(ungraded_inputs)} claim(s) ungraded: "
                                    + ", ".join(parts)
                                    + " — unknown blocks GO and CONDITIONAL GO"})
        criteria_met["readiness"] = False
    elif r_stale:
        reasoning.append({"check": "readiness", "status": "warn",
                          "detail": f"{len(r_stale)} claim(s) STALE — something they "
                                    "read changed after their last run"})
        criteria_met["readiness"] = True
        warnings += 1
    else:
        criteria_met["readiness"] = True

    # D-200 flake quarantine: a chronically-flipping claim whose latest run is
    # not-passed is QUARANTINED — excluded from the pass-rate (it must not block
    # a good release) and surfaced as its own warning. A flaky claim that
    # currently PASSES counts normally. D-232: a persisted MANUAL pin/lift WINS
    # over the live signal (see _claim_quarantined). Opt out of the whole check
    # via criteria['substrate_quarantine_flaky'] = False.
    quarantine_on = criteria.get("substrate_quarantine_flaky", True)
    quarantined = [c for c in counted
                   if quarantine_on and _claim_quarantined(c)]
    scored = [c for c in counted if c not in quarantined]

    # D-280: the good-state count is the Verified predicate (was outcome=='passed').
    # failed / errored stay raw — they are outcome-category tallies for the metrics
    # breakdown, not a good-vs-not-good grade.
    passed = sum(1 for c in scored if _claim_verified(c))
    failed = sum(1 for c in scored if c["latest_run"]["outcome"] == "failed")
    errored = sum(1 for c in scored if c["latest_run"]["outcome"] == "errored")

    # has_runs — no evidence at all. Step 2: this is the fully UNGRADED case
    # (every claim NEVER_RUN → the readiness check above), so it is recorded
    # as a failed criterion but no longer counted as a graded BLOCKER — an
    # absence is not a NO GO (D9: unknown blocks GO, it does not condemn).
    if not counted:
        reasoning.append({"check": "has_runs", "status": "fail",
                          "detail": "No substrate runs exist for any of this "
                                    "release's claims"})
        criteria_met["has_runs"] = False
    else:
        criteria_met["has_runs"] = True

    # pass_rate over the scored latest runs (quarantined claims excluded).
    pass_rate = (passed / len(scored) * 100) if scored else 0.0
    if scored:
        if pass_rate >= min_pass_rate:
            reasoning.append({"check": "pass_rate", "status": "pass",
                              "detail": f"Pass rate {pass_rate:.1f}% meets "
                                        f"threshold of {min_pass_rate}%"})
            criteria_met["pass_rate"] = True
        else:
            reasoning.append({"check": "pass_rate", "status": "fail",
                              "detail": f"Pass rate {pass_rate:.1f}% below "
                                        f"threshold of {min_pass_rate}%"})
            criteria_met["pass_rate"] = False
            blockers += 1

    # grounding_integrity — a passing run of a broken claim is vacuous.
    broken = [c for c in claim_evidence
              if (c["grounding"] or {}).get("overall") == "broken"]
    drifted = [c for c in claim_evidence
               if (c["grounding"] or {}).get("overall") == "drifted"]
    stale = [c for c in claim_evidence
             if (c["grounding"] or {}).get("stale") is True
             and (c["grounding"] or {}).get("overall") == "intact"]
    # Step 2 (R-ungrounded): a claim with a run but NO grounding verdict is
    # named here too — this line must never read "all intact" over an
    # unknown (the HIGH FIX PLAN item, the vacuous-green class).
    _ungrounded_note = (f"; {len(r_ungrounded)} with no grounding verdict for this org"
                        if r_ungrounded else "")
    if broken and block_on_broken:
        reasoning.append({"check": "grounding_integrity", "status": "fail",
                          "detail": f"{len(broken)} claim(s) have BROKEN "
                                    "grounding — their run evidence is vacuous"
                                    + _ungrounded_note})
        criteria_met["grounding_integrity"] = False
        blockers += 1
    elif drifted or stale or broken or r_ungrounded:
        reasoning.append({"check": "grounding_integrity", "status": "warn",
                          "detail": f"{len(drifted)} drifted / {len(stale)} "
                                    f"stale grounding(s)"
                                    + (f"; {len(broken)} broken (blocking "
                                       "disabled)" if broken else "")
                                    + _ungrounded_note})
        criteria_met["grounding_integrity"] = True
        warnings += 1
    else:
        reasoning.append({"check": "grounding_integrity", "status": "pass",
                          "detail": "All claim groundings intact and current"})
        criteria_met["grounding_integrity"] = True

    # flake quarantine — surfaced, never silent (D-200).
    if quarantined:
        reasoning.append({"check": "flaky_quarantine", "status": "warn",
                          "detail": f"{len(quarantined)} chronically-flipping "
                                    "claim(s) quarantined from the pass rate — "
                                    "stabilize or review them"})
        warnings += 1

    # coverage — partial never_run is a warning (total never_run already blocked).
    if counted and never_run:
        reasoning.append({"check": "coverage", "status": "warn",
                          "detail": f"{len(never_run)} of {len(claim_evidence)} "
                                    "claim(s) have no current-version run"})
        warnings += 1

    # version_currency — evidence that isn't cleanly of the shipped version.
    superseded = [c for c in claim_evidence if c["superseded_newer_run"]]
    unknown = [c for c in counted if c["latest_run"]["version_unknown"]]
    if superseded or unknown:
        reasoning.append({"check": "version_currency", "status": "warn",
                          "detail": f"{len(superseded)} claim(s) have newer "
                                    f"superseded-version runs; {len(unknown)} "
                                    "counted run(s) carry no version pin"})
        warnings += 1

    # freshness — the newest counted run must be recent enough to trust.
    finished = [c["latest_run"]["finished_at"] for c in counted
                if c["latest_run"]["finished_at"]]
    if finished:
        newest = max(datetime.fromisoformat(f) for f in finished)
        age_hours = (now - newest).total_seconds() / 3600
        if age_hours > max_age_hours:
            reasoning.append({"check": "freshness", "status": "warn",
                              "detail": f"Newest run is {age_hours:.0f}h old "
                                        f"(window {max_age_hours}h)"})
            warnings += 1

    if ungraded:
        recommendation, confidence = "cannot_determine", 0.0
    elif ungraded_inputs and blockers == 0:
        # D9: unknown blocks GO and CONDITIONAL GO; a graded blocker (below)
        # still makes NO GO stand over it.
        recommendation, confidence = "cannot_determine", 0.0
    elif blockers == 0 and warnings == 0:
        recommendation, confidence = "go", 0.95
    elif blockers == 0:
        recommendation, confidence = "conditional_go", 0.75
    else:
        recommendation, confidence = "no_go", 0.90

    # Substrate-native risk: failure share + per-finding increments, capped.
    score = 0
    if counted:
        score += round((100 - pass_rate) * 0.5)
    score += 25 * blockers + 10 * warnings
    score = max(0, min(100, score))

    # D-237: the explainable blockers — name the claims that ACTUALLY drove a
    # blocker, so the list is empty whenever the recommendation is not no_go (a
    # failed run that stays inside the pass-rate threshold is NOT a blocker and
    # must not render "Blocked by …" under a GO hero). Two blocker classes carry
    # per-claim attribution: the pass_rate blocker (every scored claim whose
    # latest run didn't pass — failed / errored / any non-passed outcome) and the
    # grounding_integrity blocker (claims with BROKEN grounding, whose run
    # evidence is vacuous). Deduped by test_id (a run-failure entry wins over a
    # grounding one); sorted by requirement key for stable render.
    _blk_seen, blocking = set(), []
    if criteria_met.get("pass_rate") is False:
        for c in scored:
            if not _claim_verified(c):          # D-280: not Verified ⇒ a blocker
                _blk_seen.add(c["test_id"])
                blocking.append({
                    "test_id": c["test_id"],
                    "external_keys": c.get("external_keys", []),
                    "verdict": c["latest_run"].get("verdict"),
                    "outcome": c["latest_run"]["outcome"],
                    "cause": c["latest_run"].get("cause")})
    if criteria_met.get("grounding_integrity") is False:
        for c in broken:
            if c["test_id"] in _blk_seen:
                continue
            lr = c["latest_run"] or {}
            blocking.append({
                "test_id": c["test_id"],
                "external_keys": c.get("external_keys", []),
                "verdict": lr.get("verdict"),
                "outcome": "grounding_broken",
                "cause": "grounding broken — the org changed under this test, so "
                         "its run evidence can't be trusted"})
    blocking.sort(key=lambda b: (b["external_keys"][0] if b["external_keys"]
                                 else "~", b["test_id"]))

    return {
        "applicable": True,
        "recommendation": recommendation,
        "confidence": confidence,
        "reasoning": reasoning,
        "blocking": blocking,
        "criteria_met": criteria_met,
        "metrics": {
            "claim_count": len(claim_evidence),
            "counted_runs": len(counted),
            "passed": passed, "failed": failed, "errored": errored,
            "never_run": len(never_run),
            "quarantined": len(quarantined),
            "pass_rate": round(pass_rate, 1),
            "grounding": {"broken": len(broken), "drifted": len(drifted),
                          "stale": len(stale)},
            "readiness": {"never_run": len(r_never), "stale": len(r_stale),
                          "current": len(r_current),
                          "cannot_determine": len(r_cannot),
                          "ungrounded": len(r_ungrounded),
                          "ungraded": len(ungraded_inputs)},
            "blockers": blockers, "warnings": warnings,
        },
        "risk": {"score": score, "level": _risk_level(score)},
        "sequence": sequence,                    # Step B: the resolution graded against
    }


_SEQ_CURRENT = "CURRENT"
_SEQ_REFUSAL_SENTENCES = {
    "org_unbound": ("Cannot determine staleness: no connected org is bound to "
                    "this release's evidence, so the engine will not grade "
                    "grounding against a tenant-wide sequence."),
    "org_never_synced": ("Cannot determine staleness: the bound org has never "
                         "synced, so no current sequence exists."),
    "sequence_unresolved": ("Cannot determine staleness: the evidence carries "
                            "no sequence resolution."),
}


def _sequence_of(claim_evidence) -> dict:
    """The resolution the evidence was assembled against (one per assembly,
    stamped on every row). A row set that carries none is treated as
    unresolved — fail closed, never graded as current."""
    for c in claim_evidence:
        seq = c.get("sequence")
        if seq:
            return seq
    return {"state": "CANNOT_DETERMINE", "current_seq": None, "as_of": None,
            "source": "org_current", "axis": "org_sequence",
            "connected_org_id": None, "reason": "sequence_unresolved"}


def _sequence_refusal_sentence(sequence: dict) -> str:
    reason = sequence.get("reason") or "sequence_unresolved"
    return _SEQ_REFUSAL_SENTENCES.get(
        reason, f"Cannot determine staleness: {reason}.")


# Verdict severity for the multi-env roll-up: worst wins. Ruling D9 (Step B):
# a definite NO GO stands over an ungraded org (the evidence says stop); an
# ungraded org blocks GO and CONDITIONAL GO.
_ENV_SEVERITY = {"no_go": 0, "cannot_determine": 1, "conditional_go": 2, "go": 3}
_ENV_CONFIDENCE = {"no_go": 0.90, "cannot_determine": 0.0,
                   "conditional_go": 0.75, "go": 0.95}


def _rollup_env_decisions(env_decisions) -> dict:
    """Pure: per-environment decision dicts → ONE shape-compatible top-level
    decision (worst-of). The composer / ledger / notify / CI read only the
    top-level keys, so the roll-up preserves every one of them; the per-env
    dicts ride along under ``environments`` for the verdict cards.

    - ``recommendation``: worst across envs; ``confidence``: that verdict's
      scheme constant.
    - ``reasoning``: one ``environment`` check line per env (fail/warn/pass ←
      no_go/conditional_go/go).
    - ``blocking``: concatenation, each entry annotated with its
      ``environment_id``.
    - ``criteria_met``: per-check AND across envs.
    - ``metrics``: counts summed; ``pass_rate`` = min; ``claim_count`` = max
      (the same shared claims, not a sum); ``environments`` = the env ids.
    - ``risk``: max score.
    """
    worst = min(env_decisions,
                key=lambda d: _ENV_SEVERITY.get(d["recommendation"], 0))
    recommendation = worst["recommendation"]
    _status = {"no_go": "fail", "cannot_determine": "fail",
               "conditional_go": "warn", "go": "pass"}
    reasoning = []
    for d in env_decisions:
        m = d["metrics"]
        reasoning.append({
            "check": "environment",
            "status": _status.get(d["recommendation"], "warn"),
            "detail": (f"Environment {d['environment_id']}: "
                       f"{d['recommendation'].replace('_', ' ').upper()} — "
                       f"{m['blockers']} blocker(s), {m['warnings']} "
                       f"warning(s), pass rate {m['pass_rate']}%")})
    blocking = []
    for d in env_decisions:
        for b in d.get("blocking", ()):
            blocking.append({**b, "environment_id": d["environment_id"]})
    all_keys = {k for d in env_decisions for k in d["criteria_met"]}
    criteria_met = {k: all(d["criteria_met"].get(k, True)
                           for d in env_decisions) for k in sorted(all_keys)}
    sums = ("counted_runs", "passed", "failed", "errored", "never_run",
            "quarantined", "blockers", "warnings")
    metrics = {k: sum(d["metrics"][k] for d in env_decisions) for k in sums}
    metrics["claim_count"] = max(d["metrics"]["claim_count"]
                                 for d in env_decisions)
    metrics["pass_rate"] = min(d["metrics"]["pass_rate"]
                               for d in env_decisions)
    metrics["grounding"] = {
        k: sum(d["metrics"]["grounding"][k] for d in env_decisions)
        for k in ("broken", "drifted", "stale")}
    metrics["readiness"] = {                       # Step 2: summed per env
        k: sum((d["metrics"].get("readiness") or {}).get(k, 0) for d in env_decisions)
        for k in ("never_run", "stale", "current", "cannot_determine",
                  "ungrounded", "ungraded")}
    metrics["environments"] = [d["environment_id"] for d in env_decisions]
    score = max(d["risk"]["score"] for d in env_decisions)
    return {
        "applicable": True,
        "recommendation": recommendation,
        "confidence": _ENV_CONFIDENCE.get(recommendation, 0.75),
        "reasoning": reasoning,
        "blocking": blocking,
        "criteria_met": criteria_met,
        "metrics": metrics,
        "risk": {"score": score, "level": _risk_level(score)},
        "environments": env_decisions,
    }


def _decide_for_session(session, conn, keys, criteria, *, tenant_id) -> dict:
    """The decision over one tenant-scoped ``session`` (+ its ``conn`` for
    the env→org seam) — the testable body of
    :func:`get_release_substrate_decision`. Step B: EVERY branch consumes the
    org-required resolver through ``_assemble_claim_evidence``; the engine
    never computes a current sequence.

    - **0 envs**: nothing binds — releases carry no environment; the
      evidence IS the binding — so the assembly runs with no org and every
      row records ``CANNOT_DETERMINE / org_unbound``: the release is
      ungraded (``cannot_determine``), never GO.
    - **1 env**: the evidence's environment; its org via the ONE seam
      (``get_connected_org_for_environment``); the assembly is env- and
      org-scoped (the run window is unchanged in effect — only that env
      holds evidence — and the grounding read becomes org-exact). An env
      with no ``connected_orgs`` row → the same recorded refusal.
    - **2+ envs**: one decision per env (unchanged in number for a
      provisioned org — byte-identical to before), rolled up worst-of under
      ruling D9."""
    from primeqa.sync.credentials import get_connected_org_for_environment
    test_ids, _ = _claim_test_ids(session, keys)
    envs = _environments_with_evidence(session, test_ids, tenant_id=tenant_id)
    if len(envs) <= 1:
        env = envs[0] if envs else None
        org = get_connected_org_for_environment(conn, env) if env is not None else None
        evidence = _assemble_claim_evidence(
            session, keys, tenant_id=tenant_id,
            environment_id=env, connected_org_id=org)
        out = compute_substrate_decision(evidence, criteria)
        out["available"] = True
        if out.get("applicable"):
            out["environments"] = ([{"environment_id": env,
                                     "connected_org_id": org,
                                     **{k: v for k, v in out.items()
                                        if k != "environments"}}]
                                   if env is not None else [])
        return out
    from primeqa.intelligence import quarantine as _q
    manual_q = _q.manual_states(tenant_id, session=session) if tenant_id is not None else {}
    env_decisions = []
    for env in envs:
        org = get_connected_org_for_environment(conn, env)
        evidence = _assemble_claim_evidence(
            session, keys, tenant_id=tenant_id,
            environment_id=env, connected_org_id=org,
            manual_q=manual_q)
        d = compute_substrate_decision(evidence, criteria)
        if not d.get("applicable"):
            continue        # e.g. every claim deprecated
        env_decisions.append({"environment_id": env,
                              "connected_org_id": org, **d})
    if not env_decisions:
        return {"available": True, "applicable": False, "claim_count": 0}
    out = _rollup_env_decisions(env_decisions)
    out["available"] = True
    return out


def get_release_substrate_decision(tenant_id: int, external_keys,
                                   criteria=None, *, session=None) -> dict:
    """Best-effort: assemble + compute in one tenant connection. Never raises —
    ``{available: False}`` on any read error; zero claims → ``{available: True,
    applicable: False}`` (the composer skips cleanly). The release_substrate_console
    wrapper discipline. The branch logic lives in :func:`_decide_for_session`
    (Step B: every branch reads the org-required resolver)."""
    keys = [k for k in (external_keys or []) if k]
    if not keys:
        return {"available": True, "applicable": False, "claim_count": 0}
    try:
        if session is not None:            # close 2: the request's own session
            return _decide_for_session(session, session.connection(), keys,
                                       criteria, tenant_id=tenant_id)
        from sqlalchemy.orm import Session

        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            own = Session(bind=conn)
            try:
                return _decide_for_session(own, conn, keys, criteria,
                                           tenant_id=tenant_id)
            finally:
                own.close()
    except Exception as exc:
        log.warning("substrate decision unavailable for tenant %s: %s",
                    tenant_id, exc)
        return {"available": False, "applicable": False, "claim_count": 0}


# ---------------------------------------------------------------------------
# Step 2 (§c) — the release SCOPE's readiness: every claim in scope × every
# environment with evidence. The Evaluate act REFUSES on a non-current scope
# (a readiness FACT, not policy — the policy object is Step 5) and names the
# items; "Run the scope" is one click away. Best-effort wrapper discipline.
# ---------------------------------------------------------------------------

def release_scope_readiness(tenant_id: int, external_keys, *, session=None) -> dict:
    """``{available, items: [{test_id, external_keys, environment_id, state,
    reason, sentence, stamp_seq, current_seq}], non_current: n, environments:
    [ids], claim_count}``. Zero environments with evidence = every claim
    NEVER_RUN once (environment None) so the refusal still names them."""
    keys = [k for k in (external_keys or []) if k]
    if not keys:
        return {"available": True, "items": [], "non_current": 0,
                "environments": [], "claim_count": 0}
    try:
        from contextlib import nullcontext

        from sqlalchemy.orm import Session

        from primeqa.semantic.connection import get_tenant_connection
        from primeqa.sync.readiness import resolve_run_readiness_bulk
        shared = session
        with (nullcontext(None) if shared is not None
              else get_tenant_connection(tenant_id)) as conn:
            session = shared if shared is not None else Session(bind=conn)
            try:
                test_ids, keys_by_tid = _claim_test_ids(session, keys)
                if not test_ids:
                    return {"available": True, "items": [], "non_current": 0,
                            "environments": [], "claim_count": 0}
                from primeqa.test_representation.coordinator import (
                    SemanticTransactionCoordinator,
                )
                latest = SemanticTransactionCoordinator().get_latest_claims(session, test_ids)
                # Step 5 (R6): a conformance claim (archetype ``ui``) never runs on the
                # S4 lane — its contemporaneity is the browser plane's processing run,
                # graded by the policy engine's conformance axis (a claim with no
                # verdict on the latest run is UNGRADED there). The S4 readiness
                # census — and the Evaluate refusal — read the functional lane only.
                live = [t for t in test_ids
                        if getattr(latest.get(t), "status", None) != "deprecated"
                        and getattr(latest.get(t), "archetype", None) != "ui"]
                envs = _environments_with_evidence(session, live, tenant_id=tenant_id)
                items = []
                if not envs:
                    for t in live:
                        items.append({"test_id": str(t),
                                      "external_keys": sorted(keys_by_tid.get(str(t), ())),
                                      "environment_id": None, "state": "NEVER_RUN",
                                      "reason": None, "sentence": "No run in any environment.",
                                      "stamp_seq": None, "current_seq": None})
                else:
                    ready = resolve_run_readiness_bulk(
                        session, [(t, e) for t in live for e in envs])
                    for t in live:
                        for e in envs:
                            r = ready[(str(t), int(e))]
                            items.append({"test_id": str(t),
                                          "external_keys": sorted(keys_by_tid.get(str(t), ())),
                                          "environment_id": int(e), "state": r.state,
                                          "reason": r.reason, "sentence": r.sentence,
                                          "stamp_seq": r.stamp_seq,
                                          "current_seq": r.current_seq})
                non_current = [i for i in items if i["state"] != "CURRENT"]
                return {"available": True, "items": items,
                        "non_current": len(non_current), "environments": envs,
                        "claim_count": len(live)}
            finally:
                session.close()
    except Exception as exc:
        log.warning("release scope readiness unavailable for tenant %s: %s",
                    tenant_id, exc)
        return {"available": False, "items": [], "non_current": 0,
                "environments": [], "claim_count": 0}


def readiness_for_pairs(tenant_id: int, pairs, *, session=None) -> dict:
    """``{available, map: {(test_id_str, environment_id): readiness dict}}`` —
    the list/detail pages' read (best-effort; a tenant with no substrate
    schema renders no pills). ``pairs`` = ``[(claim_test_id, environment_id)]``."""
    pairs = [(str(t), int(e)) for t, e in (pairs or []) if t is not None and e is not None]
    if not pairs:
        return {"available": True, "map": {}}
    try:
        from primeqa.sync.readiness import resolve_run_readiness_bulk
        if session is not None:
            ready = resolve_run_readiness_bulk(session, pairs)
            return {"available": True, "map": {k: v.as_dict() for k, v in ready.items()}}
        from sqlalchemy.orm import Session

        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            own = Session(bind=conn)
            try:
                ready = resolve_run_readiness_bulk(own, pairs)
                return {"available": True,
                        "map": {k: v.as_dict() for k, v in ready.items()}}
            finally:
                own.close()
    except Exception as exc:
        log.warning("readiness unavailable for tenant %s: %s", tenant_id, exc)
        return {"available": False, "map": {}}
