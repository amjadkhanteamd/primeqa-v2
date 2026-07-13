#!/usr/bin/env python3
"""Convergence replay harness — deterministic, no LLM, no writes.

Replays every persisted propose-turn intent (llm_calls.raw_parameters)
for the given requirement keys through the CURRENT substrate at the
current S1 pin, via the production seam entry points
(``check_refs_exist`` → ``resolve_intent``). Failed proposals get the
bounded counterfactual variants from ``primeqa.generation.convergence``
(offer-follow / value-drop / kind-swap), whose convergence measures the
distance from convergence and recovery precision. Emits a JSON trace +
a printed summary; the markdown report is authored from the JSON.

Read-only by construction: GovernanceCore grounding reads S1; nothing
persists (LedgerPersister is never constructed).

Usage:
  python scripts/convergence_replay.py req-320 req-315 \
      [--prompt-version generation@v29] [--env 59] [--out traces.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from primeqa.db import init_db, get_db                     # noqa: E402
init_db(os.environ["DATABASE_URL"])

from sqlalchemy import text                                # noqa: E402
from primeqa.semantic.connection import get_tenant_connection  # noqa: E402
from primeqa.semantic.query import SemanticOrgModel        # noqa: E402
from primeqa.sync.credentials import resolve_connected_org_or_raise  # noqa: E402
from primeqa.generation.governance_core import GovernanceCore  # noqa: E402
from primeqa.generation.intake import resolve_current_s1_version  # noqa: E402
from primeqa.generation.convergence import (               # noqa: E402
    ReplayResult, classify, snapshot, variants_for)

TENANT = 1


class _MemoS1:
    """In-process memo over the read-only S1 reads a replay repeats
    thousands of times at ONE pinned version. Pure cache — the underlying
    model is untouched; safe because the snapshot at a fixed seq is
    immutable."""

    _MEMOED = {"get_entities", "get_related", "get_entity_details",
               "get_picklist_values"}

    def __init__(self, inner):
        self._inner = inner
        self._memo: dict = {}

    def _freeze(self, v):
        if isinstance(v, dict):
            return tuple(sorted((k, self._freeze(x)) for k, x in v.items()))
        if isinstance(v, (list, tuple, set)):
            return tuple(self._freeze(x) for x in v)
        return v

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if name not in self._MEMOED or not callable(attr):
            return attr

        def wrapper(*args, **kwargs):
            key = (name, self._freeze(args), self._freeze(kwargs))
            if key not in self._memo:
                self._memo[key] = attr(*args, **kwargs)
            return self._memo[key]
        return wrapper


def _offer_fields(payload_candidates) -> tuple:
    """(entity_type, top_api) from a B0 offer payload dict (or None)."""
    if not isinstance(payload_candidates, dict):
        return (None, None)
    cands = payload_candidates.get("candidates") or []
    top = cands[0].get("sf_api_name") if cands else None
    return (payload_candidates.get("entity_type"), top)


def replay_one(core, ctx, descriptor: dict) -> ReplayResult:
    """One deterministic replay through the production entry points."""
    intent_input = {"intent_descriptors": [dict(descriptor)]}
    try:
        rc = core.check_refs_exist(intent_input=intent_input, ctx=ctx)
        if not rc.ok:
            et, top = (None, None)
            for o in (getattr(rc, "offers", None) or []):
                et, top = _offer_fields(o)
                if top:
                    break
            return ReplayResult(stage="layer_a",
                                detail=str(getattr(rc, "feedback", ""))[:300],
                                offer_entity_type=et, offer_top=top)
        state = SimpleNamespace(control_facts=None, groundings=[])
        res = core.resolve_intent(intent_input=intent_input, ctx=ctx,
                                  state=state)
        if res.refusal is not None:
            pay = getattr(res.refusal, "payload", None) or {}
            kind = getattr(res.refusal, "refusal_kind", None)
            kind = getattr(kind, "value", None) or (
                str(kind) if kind is not None else None)
            cand = pay.get("candidates")
            et, top = _offer_fields(cand)
            return ReplayResult(
                stage="resolved",
                grounded_n=len(state.groundings),
                refusal_kind=kind,
                detail=str(pay.get("detail"))[:300],
                detail_layer=pay.get("detail_layer"),
                offer_entity_type=et, offer_top=top,
                offer_proposed=(cand or {}).get("proposed")
                if isinstance(cand, dict) else None)
        vals = tuple(str(getattr(g, "effect_value", None))[:40]
                     for g in state.groundings)
        return ReplayResult(stage="resolved",
                            grounded_n=len(state.groundings),
                            grounded_values=vals)
    except Exception as e:  # noqa: BLE001 — audit, never abort the corpus
        return ReplayResult(stage="error", error=f"{type(e).__name__}: {e}"[:200])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("keys", nargs="+", help="requirement keys, e.g. req-320")
    ap.add_argument("--prompt-version", default=None)
    ap.add_argument("--env", type=int, default=59)
    ap.add_argument("--out", default=None, help="JSON trace output path")
    ap.add_argument("--limit", type=int, default=None,
                    help="max propose turns per key")
    args = ap.parse_args()

    db = next(get_db())
    from primeqa.intelligence.s3_enqueue import resolve_requirement
    req_text: dict = {}
    for k in args.keys:
        try:
            rid = int(k.split("-")[-1])
            ref = resolve_requirement(db, rid, TENANT)
            req_text[k] = (ref or {}).get("text") or ""
        except Exception:
            req_text[k] = ""

    t0 = time.time()
    traces: list[dict] = []
    with get_tenant_connection(TENANT) as conn:
        org_id = resolve_connected_org_or_raise(conn, args.env)
        seq, _name = resolve_current_s1_version(TENANT, args.env)
        core = GovernanceCore(_MemoS1(
            SemanticOrgModel(conn, connected_org_id=org_id)))
        print(f"replaying at pin seq={seq} env={args.env} "
              f"keys={args.keys} pv={args.prompt_version or 'ALL'}")

        q = ("SELECT lc.ctid::text AS ord, lc.raw_parameters::text AS rp, "
             "       lc.operational_outcome::text AS oo, lc.prompt_version, "
             "       go.request_id, go.outcome_id, go.created_at, "
             "       go.requirement_ref->>'key' AS k "
             "FROM llm_calls lc "
             "JOIN generation_outcomes go "
             "  ON go.outcome_id = lc.generation_outcome_id "
             "WHERE lc.tool_name='propose_semantic_intent' "
             "  AND go.requirement_ref->>'key' = :k "
             + ("AND lc.prompt_version = :pv " if args.prompt_version else "")
             + "ORDER BY go.created_at, lc.ctid")
        for k in args.keys:
            params = {"k": k}
            if args.prompt_version:
                params["pv"] = args.prompt_version
            rows = conn.execute(text(q), params).mappings().all()
            if args.limit:
                rows = rows[:args.limit]
            ctx = SimpleNamespace(
                semantic_context=SimpleNamespace(s1_version_seq=seq),
                requirement_text=req_text.get(k, ""))
            # pass number = ordinal of SUCCESS propose turns per outcome
            pass_no: dict = defaultdict(int)
            for row in rows:
                rp = json.loads(row["rp"]) if row["rp"] else {}
                descs = rp.get("intent_descriptors") or (
                    [rp["intent_descriptor"]] if rp.get("intent_descriptor")
                    else [])
                turn_kind = row["oo"]
                if turn_kind == "success":
                    pass_no[row["outcome_id"]] += 1
                p = pass_no[row["outcome_id"]] if turn_kind == "success" else 0
                declared = {a.get("index"): a.get("label")
                            for a in (rp.get("acceptance_criteria") or [])
                            if isinstance(a, dict)}
                for i, d in enumerate(descs):
                    if not isinstance(d, dict):
                        continue
                    snap = snapshot(d)
                    as_prop = replay_one(core, ctx, d)
                    variants: dict = {}
                    if not as_prop.converged and not snap.no_admissible_test:
                        for name, vd in variants_for(snap, as_prop):
                            variants[name] = replay_one(core, ctx, vd)
                    code, note = classify(snap, as_prop, variants)
                    traces.append({
                        "key": k, "outcome_id": str(row["outcome_id"]),
                        "created_at": str(row["created_at"]),
                        "prompt_version": row["prompt_version"],
                        "turn_kind": turn_kind, "pass": p, "slot": i,
                        "ac_ref": snap.ac_ref,
                        "ac_label": declared.get(snap.ac_ref),
                        "claim_kind": snap.claim_kind,
                        "subject": snap.subject_api,
                        "field": snap.field_name,
                        "has_value": snap.has_expected_value,
                        "value": (str(snap.expected_value)[:60]
                                  if snap.has_expected_value else None),
                        "automation": snap.automation_name,
                        "code": code, "note": note,
                        "as_proposed": vars(as_prop),
                        "variants": {n: vars(v) for n, v in variants.items()},
                    })
            print(f"  {k}: {len([t for t in traces if t['key'] == k])} intents replayed "
                  f"({time.time()-t0:.0f}s)")

    out = args.out or "/tmp/convergence_traces.json"
    with open(out, "w") as f:
        json.dump(traces, f, indent=1, default=str)
    print(f"\ntraces -> {out}  ({len(traces)} intents, {time.time()-t0:.0f}s)")

    # ── summary ──────────────────────────────────────────────────────
    for k in args.keys:
        sub = [t for t in traces if t["key"] == k]
        if not sub:
            continue
        print(f"\n===== {k}: {len(sub)} intents =====")
        by = Counter(t["code"] for t in sub)
        conv = by.get("CONVERGED", 0)
        print(f"  as-proposed convergence: {conv}/{len(sub)} "
              f"({100*conv/len(sub):.0f}%)")
        for code, n in by.most_common():
            print(f"    {code}: {n}")
        # recovery: precision = the offer resolves the MISSED reference
        # (converges OR fails at a different layer than the miss);
        # yield = full convergence after following the offer
        offered = [t for t in sub
                   if t["variants"].get("offer") is not None]
        def _off(t):
            return t["variants"]["offer"]
        yielded = [t for t in offered
                   if _off(t).get("grounded_n", 0) > 0
                   and not _off(t).get("refusal_kind")]
        correct = [t for t in offered
                   if t in yielded
                   or (_off(t).get("stage") == "resolved"
                       and "does not" not in str(_off(t).get("detail"))
                       and "did not resolve" not in str(_off(t).get("detail")))]
        if offered:
            print(f"  recovery precision (offer resolves the miss): "
                  f"{len(correct)}/{len(offered)} "
                  f"({100*len(correct)/len(offered):.0f}%)")
            print(f"  recovery yield (offer -> full convergence): "
                  f"{len(yielded)}/{len(offered)} "
                  f"({100*len(yielded)/len(offered):.0f}%)")
        # variant rescue table
        for vn in ("value_drop", "kind_swap", "kind_swap+value_drop",
                   "offer+value_drop"):
            probed = [t for t in sub if t["variants"].get(vn) is not None]
            ok = [t for t in probed
                  if t["variants"][vn].get("grounded_n", 0) > 0
                  and not t["variants"][vn].get("refusal_kind")]
            if probed:
                print(f"  variant {vn}: rescues {len(ok)}/{len(probed)}")
        errs = [t for t in sub if t["as_proposed"].get("stage") == "error"]
        if errs:
            print(f"  REPLAY ERRORS: {len(errs)} (audit the trace)")


if __name__ == "__main__":
    main()
