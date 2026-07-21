#!/usr/bin/env python3
"""Field-resolution probe (F0, D-376 follow-on) — deterministic, no LLM, no writes.

Classifies EVERY field mention in the persisted propose-turn corpus, per hint
slot, against the production field ladder at the current S1 pin:

  - ``exact``        — the qualified name is already the org's (rule 1);
  - ``ladder``       — the ladder canonicalizes it uniquely (rules 2-4);
  - ``unbound``      — the ladder cannot land it (the offer/hop territory).

Cross-referenced with which slots the PRODUCTION pipeline silently
canonicalizes today (``field_name`` on value/automation-effect claims only):
a ``ladder`` fate in any OTHER subject-owned slot is an **F1 win** — today it
costs a refusal-hop (condition slots, invent-nothing) or a silent DROP
(trigger slots, drop-never-refuse, i.e. a weakened staged test). ``unbound``
counts size F2 (offer quality). Also estimates cross-turn hop recovery: an
unbound term on an earlier propose turn of the same outcome whose slot gets a
resolving name on a later turn.

Usage:
  python scripts/field_resolution_probe.py req-320 req-315 [--env 59] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from primeqa.db import init_db                              # noqa: E402
init_db(os.environ["DATABASE_URL"])

from sqlalchemy import text                                 # noqa: E402
from primeqa.semantic.connection import get_tenant_connection  # noqa: E402
from primeqa.semantic.query import SemanticOrgModel         # noqa: E402
from primeqa.sync.credentials import resolve_connected_org_or_raise  # noqa: E402
from primeqa.generation.intake import resolve_current_s1_version  # noqa: E402
from primeqa.generation import shadow_resolution as sr      # noqa: E402
from primeqa.resolution.field_ladder import resolve_field_name  # noqa: E402
from primeqa.resolution.knowledge import S1KnowledgeSource  # noqa: E402

TENANT = 1

# The one slot production silently canonicalizes today (B1 arc), on
# value-claim + automation-effect-claim only.
PROD_COVERED = {("field_name", "value-claim"),
                ("field_name", "automation-effect-claim")}
# Subject-owned slots whose miss REFUSES (invent-nothing) vs silently DROPS.
REFUSE_SLOTS = {"rejection_conditions", "acceptance_conditions",
                "update_conditions", "rejection_conditions.compared_to",
                "acceptance_conditions.compared_to",
                "update_conditions.compared_to"}
DROP_SLOTS = {"trigger_fields", "update_trigger_fields", "trigger_field"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("keys", nargs="+")
    ap.add_argument("--env", type=int, default=59)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    rows_out: list[dict] = []
    with get_tenant_connection(TENANT) as conn:
        org_id = resolve_connected_org_or_raise(conn, args.env)
        seq, _ = resolve_current_s1_version(TENANT, args.env)
        model = SemanticOrgModel(conn, connected_org_id=org_id)
        table = S1KnowledgeSource(model).symbol_table(seq)
        memo: dict = {}
        print(f"probing at pin seq={seq} env={args.env} keys={args.keys}")

        q = ("SELECT lc.ctid::text AS ord, lc.raw_parameters::text AS rp, "
             "       go.outcome_id, go.created_at, "
             "       go.requirement_ref->>'key' AS k "
             "FROM llm_calls lc "
             "JOIN generation_outcomes go "
             "  ON go.outcome_id = lc.generation_outcome_id "
             "WHERE lc.tool_name='propose_semantic_intent' "
             "  AND go.requirement_ref->>'key' = :k "
             "ORDER BY go.created_at, lc.ctid")
        for k in args.keys:
            rows = conn.execute(text(q), {"k": k}).mappings().all()
            if args.limit:
                rows = rows[:args.limit]
            turn_no: dict = defaultdict(int)
            for row in rows:
                turn_no[row["outcome_id"]] += 1
                turn = turn_no[row["outcome_id"]]
                rp = json.loads(row["rp"]) if row["rp"] else {}
                descs = rp.get("intent_descriptors") or (
                    [rp["intent_descriptor"]] if rp.get("intent_descriptor")
                    else [])
                for d in descs:
                    if not isinstance(d, dict) or d.get("no_admissible_test"):
                        continue
                    got = sr.intent_graph(d, "")
                    if got is None:
                        continue
                    graph, slots = got
                    hint = d.get("target_subject_hint") or {}
                    et, api = hint.get("entity_type"), hint.get("sf_api_name")
                    if not api and hint.get("object"):
                        et, api = "Object", hint.get("object")
                    if not (et and api):
                        continue
                    mk = (et, api)
                    if mk not in memo:
                        memo[mk] = model.get_entities(
                            et, at_seq=seq, filters={"sf_api_name": api})
                    matches = memo[mk]
                    if len(matches) != 1:
                        continue                # subject itself unresolved
                    subject_api = matches[0].sf_api_name
                    obj = table.by_api(subject_api)
                    if obj is None:
                        continue
                    inv = [(f.qualified_api_name, f.label) for f in obj.fields]
                    for n in graph.attributes_of("subject"):
                        resolved = resolve_field_name(inv, n.term)
                        fate = ("unbound" if resolved is None
                                else "exact" if resolved == n.term
                                else "ladder")
                        slot = slots.get(n.node_id, {}).get("slot")
                        rows_out.append({
                            "key": k, "outcome_id": str(row["outcome_id"]),
                            "turn": turn, "slot": slot,
                            "claim_kind": d.get("claim_kind_hint"),
                            "ac_ref": d.get("ac_ref"),
                            "term": n.term, "resolved": resolved,
                            "fate": fate, "subject": subject_api})

    total = len(rows_out)
    print(f"\n{total} subject-owned field mentions probed\n")

    by_fate = Counter(r["fate"] for r in rows_out)
    print("== fate overall ==")
    for f, n in by_fate.most_common():
        print(f"  {f}: {n} ({100*n/total:.0f}%)")

    print("\n== fate x slot (subject-owned) ==")
    per_slot = defaultdict(Counter)
    for r in rows_out:
        per_slot[r["slot"]][r["fate"]] += 1
    for slot, c in sorted(per_slot.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"  {slot:<34} exact={c.get('exact', 0):<5} "
              f"ladder={c.get('ladder', 0):<5} unbound={c.get('unbound', 0)}")

    # F1 wins: ladder-resolvable mentions in slots production does NOT
    # silently canonicalize today.
    f1_refuse, f1_drop, prod_fixed = [], [], []
    for r in rows_out:
        if r["fate"] != "ladder":
            continue
        if (r["slot"], r["claim_kind"]) in PROD_COVERED:
            prod_fixed.append(r)
        elif r["slot"] in REFUSE_SLOTS:
            f1_refuse.append(r)
        elif r["slot"] in DROP_SLOTS:
            f1_drop.append(r)
        elif r["slot"] == "field_name":
            f1_refuse.append(r)      # field_name on kinds outside PROD_COVERED
        elif r["slot"] == "effect_via_lookup_field":
            f1_refuse.append(r)      # D-375 offer-hop class, subject-owned
    print(f"\n== F1 sizing (ladder-resolvable, not silently fixed today) ==")
    print(f"  already prod-fixed (field_name value/auto-effect): {len(prod_fixed)}")
    print(f"  F1 wins in REFUSE slots (today: refusal + hop): {len(f1_refuse)}")
    for r in f1_refuse[:10]:
        print(f"    [{r['key']} t{r['turn']}] {r['slot']}: {r['term']} -> "
              f"{r['resolved']}")
    print(f"  F1 wins in DROP slots (today: silently weakened test): "
          f"{len(f1_drop)}")
    for r in f1_drop[:10]:
        print(f"    [{r['key']} t{r['turn']}] {r['slot']}: {r['term']} -> "
              f"{r['resolved']}")

    # F2 sizing: unbound mentions (per slot), top terms
    unbound = [r for r in rows_out if r["fate"] == "unbound"]
    print(f"\n== F2 sizing (unbound after the full ladder): {len(unbound)} ==")
    for term, n in Counter(
            (r["term"], r["subject"]) for r in unbound).most_common(12):
        print(f"  {n:3d}x {term[0]}  (subject {term[1]})")

    # cross-turn hop recovery: same outcome+slot(+ac), unbound on an earlier
    # turn, resolving on a later one
    hop_fixed = 0
    by_group = defaultdict(list)
    for r in rows_out:
        by_group[(r["outcome_id"], r["ac_ref"], r["slot"])].append(r)
    for grp in by_group.values():
        turns = sorted({g["turn"] for g in grp})
        if len(turns) < 2:
            continue
        early_unbound = any(g["fate"] == "unbound" and g["turn"] == turns[0]
                            for g in grp)
        late_ok = any(g["fate"] in ("exact", "ladder") and g["turn"] > turns[0]
                      for g in grp)
        if early_unbound and late_ok:
            hop_fixed += 1
    print(f"\n== cross-turn hop recovery (unbound early -> resolving later, "
          f"per outcome+AC+slot): {hop_fixed} ==")


if __name__ == "__main__":
    main()
