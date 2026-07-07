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
3. **Grounding staleness** — grounding evaluated at an S1 version older than the
   tenant's current one is flagged ``stale`` (the org may have moved under it);
   ``stale=None`` when either side is unknowable (no S1 version yet — tolerant,
   never raises).

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
# window (5) feeds the D-200 flakiness detection. :seq is the approved
# version_seq; pass NULL to disable the filter (unapproved claim).
_FLAKE_WINDOW = 5

# D-300 review-fix B1: the raw window is WIDER than the flake window because a
# bva batch's per-probe rows collapse to ONE logical outcome before counting —
# 25 raw rows cover 5 logical entries even at a 5-probe batch size.
_RAW_RUN_WINDOW = 25

_COUNTED_RUNS_SQL = (
    "SELECT CAST(r.run_id AS text) AS run_id, r.outcome::text AS outcome, "
    "r.finished_at, r.claim_version_seq, "
    "CAST(r.batch_id AS text) AS batch_id, "
    "i.verdict::text AS verdict, i.detail AS detail "
    "FROM s4_execution_runs r "
    "LEFT JOIN s6_interpretations i ON i.run_id = r.run_id "
    "WHERE r.claim_test_id = CAST(:tid AS uuid) "
    "AND (CAST(:env AS int) IS NULL OR r.environment_id = :env) "
    "AND (CAST(:seq AS int) IS NULL "
    "     OR r.claim_version_seq IS NULL OR r.claim_version_seq = :seq) "
    f"ORDER BY r.finished_at DESC LIMIT {_RAW_RUN_WINDOW}")


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
_NEWER_SUPERSEDED_SQL = (
    "SELECT 1 FROM s4_execution_runs "
    "WHERE claim_test_id = CAST(:tid AS uuid) "
    "AND (CAST(:env AS int) IS NULL OR environment_id = :env) "
    "AND claim_version_seq IS NOT NULL AND claim_version_seq != :seq "
    "AND finished_at > COALESCE(CAST(:after AS timestamptz), '-infinity') "
    "LIMIT 1")


def _current_s1_seq(session, connected_org_id=None):
    """The current S1 version_seq — the given org's latest when
    ``connected_org_id`` is set, else the tenant-wide MAX (the single-org
    read). ``None`` when no version exists yet (tolerant — staleness is then
    unknowable, not an error). On a multi-org tenant the org-bound read is the
    only correct staleness anchor: org A's grounding must not read stale just
    because org B synced later."""
    from primeqa.semantic.query import SemanticOrgModel, VersionNotFoundError
    try:
        return SemanticOrgModel(
            session.connection(),
            connected_org_id=connected_org_id).current_version_seq()
    except VersionNotFoundError:
        return None


def _claim_grounding(session, coord, test_id, approved_seq,
                     connected_org_id=None):
    """Grounding for the version the release ships: at the approved seq, else the
    latest verdict when no approved version exists (the D-172 idiom).
    ``connected_org_id`` (the per-env decision path) reads THAT org's verdict;
    ``None`` reads worst-of across orgs (identical on a single-org store)."""
    from primeqa.evolution import list_grounding_validity, read_grounding_validity
    if approved_seq is not None:
        return read_grounding_validity(session, test_id, approved_seq,
                                       connected_org_id=connected_org_id)
    rows = list_grounding_validity(session, test_id=test_id,
                                   connected_org_id=connected_org_id)
    return rows[-1] if rows else None


def _claim_test_ids(session, external_keys):
    """The release's requirement keys → (ordered distinct claim test_ids,
    ``{test_id: {requirement keys}}``) via the COVERAGE_LINK_KINDS links
    (``generated_from`` + curated ``verifies``)."""
    from primeqa.test_representation.coordinator import (
        COVERAGE_LINK_KINDS,
        SemanticTransactionCoordinator,
    )
    coord = SemanticTransactionCoordinator()
    test_ids, seen = [], set()
    keys_by_tid: dict[str, set] = {}               # D-237: claim → its requirement key(s)
    for key in external_keys:
        for m in coord.list_tests_by_requirement(
                session, external_system="jira", external_key=key,
                link_kind=COVERAGE_LINK_KINDS):
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


def _environments_with_evidence(session, test_ids) -> list[int]:
    if not test_ids:
        return []
    rows = session.execute(text(_ENVS_WITH_EVIDENCE_SQL),
                           {"tids": [str(t) for t in test_ids]}).all()
    return [r[0] for r in rows]


def _assemble_claim_evidence(session, external_keys, *, tenant_id=None,
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
    """
    test_ids, keys_by_tid = _claim_test_ids(session, external_keys)
    from primeqa.test_representation.coordinator import (
        SemanticTransactionCoordinator,
    )
    coord = SemanticTransactionCoordinator()

    current_seq = _current_s1_seq(session, connected_org_id)
    # D-232: the persisted MANUAL quarantine overrides (own connection — never
    # poisons this session; empty without a tenant_id or before the migration).
    if manual_q is None:
        from primeqa.intelligence import quarantine as _q
        manual_q = _q.manual_states(tenant_id) if tenant_id is not None else {}
    out = []
    for tid in test_ids:
        # D-219: a deprecated claim is RETIRED from the corpus — its stale
        # run evidence must not grade the release (D-ε-1 deprecation is the
        # human's "this test no longer applies" signal).
        latest = coord.get_latest_claim(session, tid)
        if latest is not None and latest.status == "deprecated":
            continue
        approved = coord.get_current_approved_claim(session, tid)
        approved_seq = approved.version_seq if approved is not None else None

        gv = _claim_grounding(session, coord, tid, approved_seq,
                              connected_org_id=connected_org_id)
        grounding = None
        if gv is not None:
            stale = (gv.evaluated_at_version_seq < current_seq
                     if current_seq is not None else None)
            grounding = {"overall": gv.overall, "stale": stale,
                         "evaluated_at_version_seq": gv.evaluated_at_version_seq}

        raw_rows = session.execute(text(_COUNTED_RUNS_SQL),
                                   {"tid": str(tid), "seq": approved_seq,
                                    "env": environment_id}
                                   ).mappings().all()
        # D-300 review-fix B1: batches collapse to ONE logical run each (the
        # single path passes through byte-identical — batch_id NULL).
        rows = _collapse_batches(raw_rows)
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
            {"latest_run": latest_run, "test_id": str(tid),
             "strategy_kind": getattr(approved, "strategy_kind", None)},
            session=session)
        if latest_run is not None and not verified:
            # D-237: the plain-English cause for a not-Verified latest run.
            latest_run["cause"] = _cause_phrase(row["detail"], row["verdict"],
                                                row["outcome"])
        recent_outcomes = [r["outcome"] for r in rows]
        flaky = _is_flaky(recent_outcomes)

        superseded_newer = False
        if approved_seq is not None:
            superseded_newer = session.execute(
                text(_NEWER_SUPERSEDED_SQL),
                {"tid": str(tid), "seq": approved_seq, "env": environment_id,
                 "after": row["finished_at"] if row is not None else None},
            ).first() is not None

        out.append({
            "test_id": str(tid),
            "external_keys": sorted(keys_by_tid.get(str(tid), ())),  # D-237
            "approved_seq": approved_seq,
            "grounding": grounding,
            "latest_run": latest_run,
            "verified": verified,                          # D-280: the decided good state
            "superseded_newer_run": superseded_newer,
            "never_run": latest_run is None,
            "flaky": flaky,
            "recent_outcomes": recent_outcomes,
            "manual_quarantine": manual_q.get(str(tid)),   # D-232: pinned|lifted|None
        })
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

    counted = [c for c in claim_evidence if c["latest_run"] is not None]
    never_run = [c for c in claim_evidence if c["never_run"]]

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

    # has_runs — no evidence at all is a blocker (the v1 no-runs parallel).
    if not counted:
        reasoning.append({"check": "has_runs", "status": "fail",
                          "detail": "No substrate runs exist for any of this "
                                    "release's claims"})
        criteria_met["has_runs"] = False
        blockers += 1
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
    if broken and block_on_broken:
        reasoning.append({"check": "grounding_integrity", "status": "fail",
                          "detail": f"{len(broken)} claim(s) have BROKEN "
                                    "grounding — their run evidence is vacuous"})
        criteria_met["grounding_integrity"] = False
        blockers += 1
    elif drifted or stale or broken:
        reasoning.append({"check": "grounding_integrity", "status": "warn",
                          "detail": f"{len(drifted)} drifted / {len(stale)} "
                                    f"stale grounding(s)"
                                    + (f"; {len(broken)} broken (blocking "
                                       "disabled)" if broken else "")})
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

    if blockers == 0 and warnings == 0:
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
            "blockers": blockers, "warnings": warnings,
        },
        "risk": {"score": score, "level": _risk_level(score)},
    }


# Verdict severity for the multi-env roll-up: worst wins (mirrors the
# composer's ordering — no_go < conditional_go < go).
_ENV_SEVERITY = {"no_go": 0, "conditional_go": 1, "go": 2}
_ENV_CONFIDENCE = {"no_go": 0.90, "conditional_go": 0.75, "go": 0.95}


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
    _status = {"no_go": "fail", "conditional_go": "warn", "go": "pass"}
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


def get_release_substrate_decision(tenant_id: int, external_keys,
                                   criteria=None) -> dict:
    """Best-effort: assemble + compute in one tenant connection. Never raises —
    ``{available: False}`` on any read error; zero claims → ``{available: True,
    applicable: False}`` (the composer skips cleanly). The release_substrate_console
    wrapper discipline.

    Multi-org (3e): when the release's claims have run evidence in >= 2
    environments, the decision is computed ONCE PER ENV (evidence + staleness
    scoped to that env's org) and rolled up worst-of; the per-env dicts ride
    under ``environments``. With 0-1 envs the single tenant-wide compute runs
    exactly as before — ``environments`` is then the one-element echo (or
    empty), so the template has a stable key either way."""
    keys = [k for k in (external_keys or []) if k]
    if not keys:
        return {"available": True, "applicable": False, "claim_count": 0}
    try:
        from sqlalchemy.orm import Session

        from primeqa.semantic.connection import get_tenant_connection
        from primeqa.sync.credentials import get_connected_org_for_environment
        with get_tenant_connection(tenant_id) as conn:
            session = Session(bind=conn)
            try:
                test_ids, _ = _claim_test_ids(session, keys)
                envs = _environments_with_evidence(session, test_ids)
                if len(envs) <= 1:
                    evidence = _assemble_claim_evidence(session, keys,
                                                        tenant_id=tenant_id)
                    out = compute_substrate_decision(evidence, criteria)
                    out["available"] = True
                    if out.get("applicable"):
                        out["environments"] = ([{"environment_id": envs[0],
                                                 **{k: v for k, v in out.items()
                                                    if k != "environments"}}]
                                               if envs else [])
                    return out
                from primeqa.intelligence import quarantine as _q
                manual_q = _q.manual_states(tenant_id)
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
                    return {"available": True, "applicable": False,
                            "claim_count": 0}
                out = _rollup_env_decisions(env_decisions)
                out["available"] = True
                return out
            finally:
                session.close()
    except Exception as exc:
        log.warning("substrate decision unavailable for tenant %s: %s",
                    tenant_id, exc)
        return {"available": False, "applicable": False, "claim_count": 0}
