#!/usr/bin/env python3
"""Shadow semantic-resolution replay — deterministic, no LLM, no writes (D-376).

Replays every persisted propose-turn intent (llm_calls.raw_parameters) for the
given requirement keys through the SHADOW resolver at the current S1 pin:
reconstructs the intent's BusinessGraph, resolves it with the joint verifier
(``primeqa.resolution``), computes the pipeline's ACTUAL subject resolution by
the same exact-match rule ``_resolve_one`` uses, and emits per-intent shadow
verdicts. Because it bypasses ``check_refs_exist``, Layer-A subject misses —
which the live hook never sees on single-intent proposals — ARE covered here.

Read-only by construction (SELECTs + in-memory resolution; nothing persists).

Usage:
  python scripts/shadow_resolution_replay.py req-320 req-315 \
      [--prompt-version generation@v29] [--env 59] [--out traces.json] [--limit N]
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
from primeqa.generation.intake import resolve_current_s1_version  # noqa: E402
from primeqa.generation import shadow_resolution as sr     # noqa: E402
from primeqa.resolution import solve                       # noqa: E402
from primeqa.resolution.knowledge import S1KnowledgeSource  # noqa: E402

TENANT = 1


def _actual_subject(model, memo: dict, hint: dict, seq: int):
    """The pipeline's own subject resolution for this hint — the exact-match
    rule from ``_resolve_one`` with the D-317 injection semantics (a plain
    ``object`` string becomes an Object/sf_api_name ref)."""
    et = hint.get("entity_type")
    api = hint.get("sf_api_name")
    if not api and hint.get("object"):
        et, api = "Object", hint.get("object")
    if not et or not api:
        return "miss", None
    key = (et, api)
    if key not in memo:
        memo[key] = model.get_entities(et, at_seq=seq,
                                       filters={"sf_api_name": api})
    matches = memo[key]
    if len(matches) == 1:
        return "resolved", matches[0].sf_api_name
    return ("ambiguous", None) if matches else ("miss", None)


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
        model = SemanticOrgModel(conn, connected_org_id=org_id)
        table = S1KnowledgeSource(model).symbol_table(seq)
        memo: dict = {}
        print(f"shadow replay at pin seq={seq} env={args.env} "
              f"keys={args.keys} pv={args.prompt_version or 'ALL'} "
              f"objects={len(table.objects)}")

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
            for row in rows:
                rp = json.loads(row["rp"]) if row["rp"] else {}
                descs = rp.get("intent_descriptors") or (
                    [rp["intent_descriptor"]] if rp.get("intent_descriptor")
                    else [])
                for i, d in enumerate(descs):
                    if not isinstance(d, dict) or d.get("no_admissible_test"):
                        continue
                    excerpt = (d.get("requirement_excerpt")
                               or rp.get("requirement_excerpt") or "")
                    got = sr.intent_graph(d, excerpt)
                    if got is None:
                        continue
                    graph, slots = got
                    hint = d.get("target_subject_hint") or {}
                    actual_outcome, actual_api = _actual_subject(
                        model, memo, hint, seq)
                    resolved = solve.resolve(
                        graph, table, requirement_text=req_text.get(k) or None)
                    v = sr.shadow_verdict(
                        graph, resolved, table,
                        actual_outcome=actual_outcome, actual_api=actual_api,
                        claim_kind=d.get("claim_kind_hint"),
                        ac_ref=d.get("ac_ref"), slots=slots)
                    traces.append({
                        "key": k, "outcome_id": str(row["outcome_id"]),
                        "created_at": str(row["created_at"]),
                        "prompt_version": row["prompt_version"],
                        "turn_kind": row["oo"], "slot": i,
                        "verdict": v,
                    })
            print(f"  {k}: {len([t for t in traces if t['key'] == k])} intents "
                  f"shadow-replayed ({time.time()-t0:.0f}s)")

    out = args.out or "/tmp/shadow_resolution_traces.json"
    with open(out, "w") as f:
        json.dump(traces, f, indent=1, default=str)
    print(f"\ntraces -> {out}  ({len(traces)} intents, {time.time()-t0:.0f}s)")

    # ── summary ──────────────────────────────────────────────────────
    for k in args.keys + (["ALL"] if len(args.keys) > 1 else []):
        sub = (traces if k == "ALL"
               else [t for t in traces if t["key"] == k])
        if not sub:
            continue
        vs = [t["verdict"] for t in sub]
        print(f"\n===== {k}: {len(vs)} intents =====")
        by = Counter(v["agreement"] for v in vs)
        agree = by.get("agree", 0)
        print(f"  agreement: {agree}/{len(vs)} ({100*agree/len(vs):.0f}%)")
        for cls, n in by.most_common():
            print(f"    {cls}: {n}")
        grades = Counter(v["shadow"]["grade"] for v in vs)
        for g, n in grades.most_common():
            print(f"  grade {g}: {n}")
        veto = [v for v in vs if v.get("would_veto")]
        print(f"  would-veto rate: {len(veto)}/{len(vs)}")
        for v in veto:
            print(f"    VETO {v['term']!r} -> actual "
                  f"{v['actual']['sf_api_name']!r} vs winner "
                  f"{v['shadow']['winner']!r} "
                  f"(discriminators {v['veto_evidence']['discriminators']})")
        # ambiguity persistence (the glossary-pin input): same term AMBIGUOUS
        # in >=2 distinct outcomes
        amb: dict = defaultdict(set)
        for t in sub:
            v = t["verdict"]
            if v["shadow"]["grade"] == "ambiguous":
                amb[v["term"]].add(t["outcome_id"])
        persistent = {term: len(o) for term, o in amb.items() if len(o) >= 2}
        if persistent:
            print("  ambiguity persistence (term -> #outcomes): "
                  + ", ".join(f"{t}={n}" for t, n in
                              sorted(persistent.items())))
        else:
            print("  ambiguity persistence: none")


if __name__ == "__main__":
    main()
