"""S6 release-over-release comparison — detection + causal attribution
(LLD Phase 7 §a–§e).

Detection says something changed; attribution says what it broke. The
comparison unit is the verdict-grain diff between two PROCESSING RUNS,
walked down the DE-18 ladder in strict rung order:

  1. identity      — same inventory version (cross-inventory REFUSED:
                     a declared change is not drift) + per-claim join
                     on claim identity;
  2. environment   — the org-environment snapshots diffed; deltas are
                     recorded ENVIRONMENT-dimension candidates;
  3. execution ctx — tool pins + bindings hash diffed; a moved tool
                     dimension marks affected transitions DRIFT
                     (subtracted from regression, never mixed);
  4. state context — CONDITIONAL (the 2026-08-26 amendment):
                     fingerprint inequality is NOT_COMPARABLE only when
                     NO captured dimension moved; a moved dimension
                     turns the structural delta into causal EVIDENCE
                     and classification proceeds;
  5. classify      — the transition taxonomy + DE-13 causal ranking
                     (bundle > package > platform > tool), every moved
                     dimension retained under the headline.

Statuses never masquerade as transitions: NOT_RUN and NOT_COMPARABLE
are terminal report rows with named reasons, excluded from regression
counts. Deterministic, LLM-free, immutable persistence with idempotent
byte-identical re-compare (UNIQUE on the job pair).
"""
from __future__ import annotations

import json
import uuid as _uuid_mod
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

NEW_FAIL = "NEW_FAIL"
FIXED = "FIXED"
STILL_FAILING = "STILL_FAILING"
STILL_PASSING = "STILL_PASSING"
NEW_CLAIM = "NEW_CLAIM"
RETIRED_CLAIM = "RETIRED_CLAIM"
NOT_COMPARABLE = "NOT_COMPARABLE"
NOT_RUN = "NOT_RUN"

_DETERMINATE = frozenset({"PASS", "FAIL"})


class ComparisonRefusal(ValueError):
    """A refused comparison — recorded, and the message names the cause."""


# ---------------------------------------------------------------------------
# Pure pieces
# ---------------------------------------------------------------------------

def transition_for(from_verdict: str, to_verdict: str) -> str:
    """The determinate-verdict transition matrix (§b)."""
    if from_verdict == "PASS" and to_verdict == "FAIL":
        return NEW_FAIL
    if from_verdict == "FAIL" and to_verdict == "PASS":
        return FIXED
    if from_verdict == "FAIL" and to_verdict == "FAIL":
        return STILL_FAILING
    return STILL_PASSING


def diff_tool_pins(pins_a: dict, pins_b: dict,
                   bindings_a: str, bindings_b: str) -> dict:
    """Rung 3: the moved TOOL dimensions, {key: [a, b]}. Missing keys
    compare as None (an honest 'not recorded' side counts as moved when
    the other side recorded a value)."""
    moved = {}
    for key in ("axe_version", "axe_sha256", "catalogue_release_id",
                "catalogue_content_hash", "playwright_version",
                "worker_image_digest"):
        a, b = pins_a.get(key), pins_b.get(key)
        if a != b:
            moved[key] = [a, b]
    if bindings_a != bindings_b:
        moved["bindings_hash"] = [bindings_a, bindings_b]
    return moved


def diff_environment(snap_a: Optional[dict], snap_b: Optional[dict]) -> dict:
    """Rung 2: the ENVIRONMENT delta between two snapshots. A missing
    snapshot is recorded as not_captured on that side — never treated
    as 'no change'."""
    if snap_a is None or snap_b is None:
        return {"not_captured": {"baseline": snap_a is None,
                                 "candidate": snap_b is None}}
    delta: dict = {}
    if snap_a.get("platform_api_version") != snap_b.get("platform_api_version"):
        delta["platform"] = [snap_a.get("platform_api_version"),
                             snap_b.get("platform_api_version")]
    pk_a = {p["package_id"]: p.get("version_id")
            for p in (snap_a.get("packages") or [])}
    pk_b = {p["package_id"]: p.get("version_id")
            for p in (snap_b.get("packages") or [])}
    added = sorted(set(pk_b) - set(pk_a))
    removed = sorted(set(pk_a) - set(pk_b))
    changed = sorted(k for k in set(pk_a) & set(pk_b)
                     if pk_a[k] != pk_b[k])
    if added or removed or changed:
        delta["packages"] = {
            "added": [{"package_id": k, "version_id": pk_b[k]}
                      for k in added],
            "removed": [{"package_id": k, "version_id": pk_a[k]}
                        for k in removed],
            "version_changed": [{"package_id": k, "from": pk_a[k],
                                 "to": pk_b[k]} for k in changed],
        }
    return delta


def fingerprint_delta(obs_a: dict, obs_b: dict) -> Optional[dict]:
    """Rung 4 input: None when the surface's structural fingerprints
    match; else the attached delta."""
    fp_a = (obs_a.get("fingerprint") or {})
    fp_b = (obs_b.get("fingerprint") or {})
    if fp_a.get("sha256") == fp_b.get("sha256"):
        return None
    sum_a, sum_b = fp_a.get("summary") or {}, fp_b.get("summary") or {}
    named_a = {tuple(n) for n in (sum_a.get("named") or [])}
    named_b = {tuple(n) for n in (sum_b.get("named") or [])}
    return {
        "baseline_sha256": fp_a.get("sha256"),
        "candidate_sha256": fp_b.get("sha256"),
        "element_count": [sum_a.get("element_count"),
                          sum_b.get("element_count")],
        "named_added": sorted(list(n) for n in named_b - named_a),
        "named_removed": sorted(list(n) for n in named_a - named_b),
    }


def rank_causes(*, bundle_evidence: Optional[dict], env_delta: dict,
                tool_drift: dict,
                fp_delta: Optional[dict]) -> dict:
    """DE-13 (§d): primary + confidence + contributing + evidence,
    ranked by specificity — bundle > package > platform > tool. Every
    moved dimension is retained; the fingerprint delta (when the
    amended rung 4 let the pair classify) rides the evidence."""
    candidates = []
    if bundle_evidence:
        candidates.append({"dimension": "CLIENT_BUNDLE",
                           "evidence": bundle_evidence})
    if env_delta.get("packages"):
        candidates.append({"dimension": "ENVIRONMENT_PACKAGE",
                           "evidence": env_delta["packages"]})
    if env_delta.get("platform"):
        candidates.append({"dimension": "ENVIRONMENT_PLATFORM",
                           "evidence": {"platform": env_delta["platform"]}})
    if tool_drift:
        candidates.append({"dimension": "TOOL",
                           "evidence": tool_drift})
    if not candidates:
        causal = {"primary": None, "confidence": "LOW",
                  "contributing": [],
                  "note": "no captured dimension moved — unexplained"}
    else:
        causal = {
            "primary": candidates[0]["dimension"],
            "confidence": "HIGH" if len(candidates) == 1 else "MEDIUM",
            "contributing": candidates[1:],
            "evidence": candidates[0]["evidence"],
        }
    if fp_delta is not None:
        causal["fingerprint_delta"] = fp_delta
    return causal


# ---------------------------------------------------------------------------
# The comparator
# ---------------------------------------------------------------------------

def _load_run(session: Session, job_id: UUID) -> dict:
    from primeqa.browser_worker.manifest import get_manifest

    prun = session.execute(text("""
        SELECT manifest_id, claim_set_id, bindings_hash, engine_version
        FROM s6_ui_processing_runs WHERE job_id = :j
    """), {"j": str(job_id)}).fetchone()
    if prun is None:
        raise ComparisonRefusal(
            f"job {job_id} has no processing run — process both jobs "
            "before comparing")
    manifest = get_manifest(session, str(prun[0]))
    pins = (manifest["payload"] or {}).get("pins") or {}
    created_at = session.execute(text(
        "SELECT enqueued_at FROM s4_ui_inspection_jobs WHERE id = :j"),
        {"j": str(job_id)}).scalar_one()
    cs = session.execute(text("""
        SELECT inventory_version FROM claim_sets WHERE id = :i
    """), {"i": str(prun[1])}).fetchone()
    verdicts = {str(r[0]): {"verdict": r[1], "surface_key": r[2],
                            "plimsol_rule_id": r[3],
                            "owner_bundle_ref": str(r[4]) if r[4] else None}
                for r in session.execute(text("""
                    SELECT test_id, verdict, surface_key,
                           plimsol_rule_id, owner_bundle_ref
                    FROM s6_ui_verdicts WHERE job_id = :j
                """), {"j": str(job_id)}).fetchall()}
    no_verdict = session.execute(text(
        "SELECT no_verdict_members FROM s6_ui_processing_runs "
        "WHERE job_id = :j"), {"j": str(job_id)}).scalar_one() or {}
    members = {str(r[0]) for r in session.execute(text("""
        SELECT test_id FROM claim_set_members
        WHERE claim_set_id = :i AND revoked_at IS NULL
    """), {"i": str(prun[1])}).fetchall()}
    observations = {r[0]: r[1] for r in session.execute(text("""
        SELECT surface_key, observation FROM s4_ui_inspection_results
        WHERE job_id = :j"""), {"j": str(job_id)}).fetchall()}
    snapshot = None
    snap_id = pins.get("org_env_snapshot_id")
    if snap_id:
        row = session.execute(text("""
            SELECT platform_api_version, organization, packages
            FROM org_environment_snapshots WHERE id = :i
        """), {"i": snap_id}).fetchone()
        if row:
            snapshot = {"platform_api_version": row[0],
                        "organization": row[1], "packages": row[2]}
    return {"claim_set_id": str(prun[1]), "bindings_hash": prun[2],
            "pins": pins, "created_at": created_at,
            "inventory_version": cs[0] if cs else None,
            "verdicts": verdicts, "no_verdict": no_verdict,
            "members": members, "observations": observations,
            "snapshot": snapshot}


def _bundle_change_evidence(session: Session, bundle_ref: Optional[str],
                            t_a, t_b) -> Optional[dict]:
    """CLIENT dimension: did the owning bundle gain a new S1 version in
    the (A, B] window? Evidence = the bundle NAMED + the source-hash
    pair (SF-08 version history)."""
    if not bundle_ref:
        return None
    name = session.execute(text("""
        SELECT sf_api_name FROM entities WHERE id = :i
    """), {"i": bundle_ref}).scalar()
    if not name:
        return None
    changed = session.execute(text("""
        SELECT COUNT(*) FROM entities
        WHERE entity_type = 'LightningComponentBundle'
          AND sf_api_name = :n
          AND created_at > :ta AND created_at <= :tb
    """), {"n": name, "ta": t_a, "tb": t_b}).scalar_one()
    if not changed:
        return None
    hashes = [r[0] for r in session.execute(text("""
        SELECT attributes->>'_source_hash' FROM entities
        WHERE entity_type = 'LightningComponentBundle'
          AND sf_api_name = :n ORDER BY created_at DESC LIMIT 2
    """), {"n": name}).fetchall()]
    return {"bundle": name, "bundle_ref": bundle_ref,
            "source_hash_to": hashes[0] if hashes else None,
            "source_hash_from": hashes[1] if len(hashes) > 1 else None,
            "versions_in_window": int(changed)}


def compare_processing_runs(session: Session, *, baseline_job_id: UUID,
                            candidate_job_id: UUID) -> dict:
    """The DE-18 walk. Persists one immutable comparison run + its
    transition rows; re-compare UPSERTs byte-identical rows."""
    a = _load_run(session, baseline_job_id)
    b = _load_run(session, candidate_job_id)

    # ---- rung 1: identity ------------------------------------------
    if a["inventory_version"] != b["inventory_version"]:
        reason = (f"cross-inventory comparison refused — baseline is "
                  f"inventory v{a['inventory_version']}, candidate "
                  f"v{b['inventory_version']}: an inventory change is a "
                  f"DECLARED act (D-281), not drift")
        cid = _persist_run(session, baseline_job_id, candidate_job_id,
                           a, b, outcome="refused", refusal=reason,
                           tool_drift={}, env_delta={}, counts={})
        return {"comparison_id": cid, "outcome": "refused",
                "refusal_reason": reason}

    # ---- rung 2: environment ---------------------------------------
    env_delta = diff_environment(a["snapshot"], b["snapshot"])
    env_moved = bool(env_delta.get("platform") or env_delta.get("packages"))

    # ---- rung 3: execution context (tool) --------------------------
    tool_drift = diff_tool_pins(a["pins"], b["pins"],
                                a["bindings_hash"], b["bindings_hash"])

    # ---- per-claim walk --------------------------------------------
    rows = []
    counts: dict = {}

    def add(test_id, transition, *, from_v=None, to_v=None, drift=False,
            fp=None, causal=None, surface=None, rule=None):
        rows.append({"test_id": test_id, "transition": transition,
                     "from": from_v, "to": to_v, "drift": drift,
                     "fp": fp, "causal": causal, "surface": surface,
                     "rule": rule})
        key = f"{transition}_drift" if drift else transition
        counts[key] = counts.get(key, 0) + 1

    for tid in sorted(b["members"] - a["members"]):
        add(tid, NEW_CLAIM)
    for tid in sorted(a["members"] - b["members"]):
        add(tid, RETIRED_CLAIM)

    # The CLIENT dimension is SURFACE-scoped (the amendment's wording:
    # "owning-bundle version change ... for that claim's surface"): a
    # bundle that renders on the surface moved => the dimension moved
    # for EVERY claim on that surface. The owning bundles per surface
    # are the owner_bundle_refs observed on either run's verdict rows.
    surface_bundle_ev: dict = {}
    for run in (a, b):
        for v in run["verdicts"].values():
            ref = v.get("owner_bundle_ref")
            sk = v["surface_key"]
            if ref and sk not in surface_bundle_ev:
                ev = _bundle_change_evidence(
                    session, ref, a["created_at"], b["created_at"])
                if ev is not None:
                    surface_bundle_ev[sk] = ev

    for tid in sorted(a["members"] & b["members"]):
        va, vb = a["verdicts"].get(tid), b["verdicts"].get(tid)
        if va is None or vb is None:
            side = "baseline" if va is None else "candidate"
            run = a if va is None else b
            reason = run["no_verdict"].get(tid, "no verdict row")
            surface = (vb or va or {}).get("surface_key")
            rule = (vb or va or {}).get("plimsol_rule_id")
            if str(reason).startswith("surface_status:"):
                add(tid, NOT_RUN, surface=surface, rule=rule,
                    causal={"side": side, "status": reason})
            else:
                add(tid, NOT_COMPARABLE, surface=surface, rule=rule,
                    causal={"reason": f"no_verdict_{side}:{reason}"})
            continue
        surface, rule = vb["surface_key"], vb["plimsol_rule_id"]
        if (va["verdict"] not in _DETERMINATE
                or vb["verdict"] not in _DETERMINATE):
            add(tid, NOT_COMPARABLE, from_v=va["verdict"],
                to_v=vb["verdict"], surface=surface, rule=rule,
                causal={"reason": "indeterminate_side"})
            continue

        # rung 4 — CONDITIONAL (the 2026-08-26 amendment)
        fp = fingerprint_delta(a["observations"].get(surface) or {},
                               b["observations"].get(surface) or {})
        own_ev = _bundle_change_evidence(
            session, vb.get("owner_bundle_ref") or va.get("owner_bundle_ref"),
            a["created_at"], b["created_at"])
        surface_ev = surface_bundle_ev.get(surface)
        bundle_ev = own_ev or (
            {**surface_ev, "scope": "surface"} if surface_ev else None)
        dimension_moved = env_moved or bool(tool_drift) or bool(bundle_ev)
        if fp is not None and not dimension_moved:
            add(tid, NOT_COMPARABLE, from_v=va["verdict"],
                to_v=vb["verdict"], fp=fp, surface=surface, rule=rule,
                causal={"reason": "state_changed_unexplained"})
            continue

        # rung 5 — classify
        tr = transition_for(va["verdict"], vb["verdict"])
        causal = None
        drift = False
        if tr in (NEW_FAIL, FIXED):
            causal = rank_causes(bundle_evidence=bundle_ev,
                                 env_delta=env_delta,
                                 tool_drift=tool_drift, fp_delta=fp)
            drift = causal.get("primary") == "TOOL"
        add(tid, tr, from_v=va["verdict"], to_v=vb["verdict"],
            drift=drift, fp=fp, causal=causal, surface=surface,
            rule=rule)

    cid = _persist_run(session, baseline_job_id, candidate_job_id, a, b,
                       outcome="completed", refusal=None,
                       tool_drift=tool_drift, env_delta=env_delta,
                       counts=counts)
    for r in rows:
        session.execute(text("""
            INSERT INTO s6_ui_verdict_transitions
                (comparison_id, test_id, transition, from_verdict,
                 to_verdict, drift, fingerprint_delta, causal,
                 surface_key, plimsol_rule_id)
            VALUES (:c, :t, :tr, :f, :v, :d, CAST(:fp AS JSONB),
                    CAST(:ca AS JSONB), :s, :r)
            ON CONFLICT (comparison_id, test_id) DO UPDATE SET
                transition = EXCLUDED.transition,
                from_verdict = EXCLUDED.from_verdict,
                to_verdict = EXCLUDED.to_verdict,
                drift = EXCLUDED.drift,
                fingerprint_delta = EXCLUDED.fingerprint_delta,
                causal = EXCLUDED.causal,
                surface_key = EXCLUDED.surface_key,
                plimsol_rule_id = EXCLUDED.plimsol_rule_id
        """), {"c": cid, "t": r["test_id"], "tr": r["transition"],
               "f": r["from"], "v": r["to"], "d": r["drift"],
               "fp": json.dumps(r["fp"], sort_keys=True)
                     if r["fp"] is not None else None,
               "ca": json.dumps(r["causal"], sort_keys=True)
                     if r["causal"] is not None else None,
               "s": r["surface"], "r": r["rule"]})
    session.flush()
    return {"comparison_id": cid, "outcome": "completed",
            "transition_counts": counts, "tool_drift": tool_drift,
            "env_delta": env_delta, "rows": len(rows)}


def _persist_run(session, baseline_job_id, candidate_job_id, a, b, *,
                 outcome, refusal, tool_drift, env_delta, counts) -> str:
    return str(session.execute(text("""
        INSERT INTO s6_ui_comparison_runs
            (id, baseline_job_id, candidate_job_id,
             baseline_claim_set_id, candidate_claim_set_id,
             inventory_version, outcome, refusal_reason, tool_drift,
             env_delta, transition_counts)
        VALUES (:i, :bj, :cj, :bcs, :ccs, :inv, :o, :rr,
                CAST(:td AS JSONB), CAST(:ed AS JSONB),
                CAST(:tc AS JSONB))
        ON CONFLICT (baseline_job_id, candidate_job_id) DO UPDATE SET
            outcome = EXCLUDED.outcome,
            refusal_reason = EXCLUDED.refusal_reason,
            tool_drift = EXCLUDED.tool_drift,
            env_delta = EXCLUDED.env_delta,
            transition_counts = EXCLUDED.transition_counts
        RETURNING id
    """), {"i": str(_uuid_mod.uuid4()), "bj": str(baseline_job_id),
           "cj": str(candidate_job_id), "bcs": a["claim_set_id"],
           "ccs": b["claim_set_id"],
           "inv": a["inventory_version"], "o": outcome, "rr": refusal,
           "td": json.dumps(tool_drift, sort_keys=True),
           "ed": json.dumps(env_delta, sort_keys=True),
           "tc": json.dumps(counts, sort_keys=True)}).scalar_one())


def list_transitions(session: Session, *, comparison_id: UUID,
                     transition: Optional[str] = None,
                     limit: int = 50, offset: int = 0) -> list[dict]:
    """The minimal listing (§g): transition rows with their causal
    record, regression-first ordering."""
    where = "comparison_id = :c"
    params: dict = {"c": str(comparison_id), "lim": min(limit, 50),
                    "off": offset}
    if transition:
        where += " AND transition = :t"
        params["t"] = transition
    rows = session.execute(text(f"""
        SELECT test_id, transition, from_verdict, to_verdict, drift,
               fingerprint_delta, causal, surface_key, plimsol_rule_id
        FROM s6_ui_verdict_transitions
        WHERE {where}
        ORDER BY (transition = 'NEW_FAIL') DESC, transition,
                 plimsol_rule_id, surface_key
        LIMIT :lim OFFSET :off
    """), params).fetchall()
    return [{"test_id": str(r[0]), "transition": r[1],
             "from_verdict": r[2], "to_verdict": r[3], "drift": r[4],
             "fingerprint_delta": r[5], "causal": r[6],
             "surface_key": r[7], "plimsol_rule_id": r[8]}
            for r in rows]
