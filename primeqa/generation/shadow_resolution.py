"""Shadow semantic-resolution — READ-ONLY observation of subject naming (D-376).

For every data-behavior intent, this adapter reconstructs a BusinessGraph
from the hints the model ALREADY emits (subject name as a business term, field
names, staged/expected values, effect endpoints), resolves it with the joint
verifier (``primeqa.resolution``), and records a verdict comparing what the
pipeline actually bound against what structural evidence supports. The verdict
rides ``attempted_interpretation["shadow_resolution"]`` (``extra='allow'``);
``explanation_hash`` canonicalizes only its four fixed keys, so attaching the
map cannot re-key any outcome (test-asserted).

**PROMOTION BOUNDARY (D-376, the D-361 idiom): recovery, prompting, and
grounding MUST NOT depend on this telemetry.** No selector, re-prompt loop,
prompt builder, or grounding gate may read ``shadow_resolution`` or
``state.shadow_verdicts`` to change what it does — the verdicts are an
observation of the pipeline, never an input to it. The one sanctioned
promotion is the flag-gated wrong-but-real VETO (Slice 2), which reads ONLY
``would_veto`` at the subject-resolution site and is a separate, explicitly
gated decision.

Exception-safe by contract: every entry point either returns cleanly or
swallows (the governance call site also wraps) — a shadow failure must never
change a generation outcome. ``automation_name`` is deliberately NOT
reconstructed (behavioural identity is never lexically resolved, D-362).
"""
from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Optional

from primeqa.resolution import solve
from primeqa.resolution.graph import BusinessGraph, GraphEdge, GraphNode
from primeqa.resolution.knowledge import S1KnowledgeSource
from primeqa.resolution.resolved import ResolvedGraph
from primeqa.resolution.candidates import resolve_field
from primeqa.resolution.symbols import SymbolTable

log = logging.getLogger(__name__)

SHADOW_VERSION = 2   # v2 (F0): per-field records with slot provenance

# Cache sentinel: a failed hydration must not retry per intent.
_HYDRATION_FAILED = "hydration_failed"

AGREEMENTS = ("agree", "conflict", "shadow_only", "model_only", "neither")


# ---------------------------------------------------------------------------
# intent -> BusinessGraph reconstruction (deterministic, no LLM)
# ---------------------------------------------------------------------------

def _term(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def business_graph_from_intent(descriptor: dict,
                               requirement_excerpt: str = ""
                               ) -> Optional[BusinessGraph]:
    """Back-compat wrapper over :func:`intent_graph` (graph only)."""
    got = intent_graph(descriptor, requirement_excerpt)
    return got[0] if got else None


def intent_graph(descriptor: dict, requirement_excerpt: str = ""
                 ) -> Optional[tuple[BusinessGraph, dict]]:
    """Reconstruct the intent's business structure from its v29 hints, plus a
    ``{node_id: {"slot": <hint slot>, "owner": <entity node>}}`` provenance
    map for attribute nodes (F0: per-slot field telemetry). Returns ``None``
    for shapes the shadow does not observe (config/permission/ui archetypes,
    non-Object subjects, no subject term). The subject entity node is always
    ``node_id="subject"`` (the primary); a cross-object effect endpoint
    becomes ``node_id="effect"``."""
    desc = descriptor or {}
    if desc.get("archetype_hint") in ("configuration", "permission", "ui"):
        return None
    hint = desc.get("target_subject_hint") or {}
    if not isinstance(hint, dict):
        return None
    et = hint.get("entity_type")
    if et not in (None, "Object"):
        return None
    subject_term = _term(hint.get("object")) or _term(hint.get("sf_api_name"))
    if subject_term is None:
        return None

    nodes: list[GraphNode] = [GraphNode("subject", "entity", subject_term,
                                        excerpt=requirement_excerpt or "")]
    edges: list[GraphEdge] = []
    effect_term = _term(hint.get("effect_object"))
    if effect_term is not None:
        nodes.append(GraphNode("effect", "entity", effect_term))
        edges.append(GraphEdge("effect_on", "subject", "effect"))

    seen: dict[tuple[str, str], str] = {}   # (owner, term.lower()) -> node_id
    slots: dict[str, dict] = {}             # node_id -> {slot, owner}

    def add_attr(owner: str, term: Any, slot: str) -> Optional[str]:
        t = _term(term)
        if t is None:
            return None
        key = (owner, t.lower())
        if key in seen:
            return seen[key]
        node_id = f"f{len(seen)}"
        seen[key] = node_id
        nodes.append(GraphNode(node_id, "attribute", t))
        edges.append(GraphEdge("attribute_of", node_id, owner))
        slots[node_id] = {"slot": slot, "owner": owner}
        return node_id

    n_states = 0

    def add_state(attr_id: Optional[str], value: Any) -> None:
        nonlocal n_states
        v = _term(value)
        if attr_id is None or v is None:
            return
        node_id = f"s{n_states}"
        n_states += 1
        nodes.append(GraphNode(node_id, "state", v))
        edges.append(GraphEdge("state_of", node_id, attr_id))

    effect_owner = "effect" if effect_term is not None else "subject"

    fid = add_attr("subject", hint.get("field_name"), "field_name")
    add_state(fid, hint.get("expected_value"))
    eid = add_attr(effect_owner, hint.get("effect_field"), "effect_field")
    add_state(eid, hint.get("effect_value"))
    add_attr(effect_owner, hint.get("effect_lookup_field"),
             "effect_lookup_field")
    add_attr("subject", hint.get("effect_via_lookup_field"),
             "effect_via_lookup_field")
    tid = add_attr("subject", hint.get("trigger_field"), "trigger_field")
    add_state(tid, hint.get("trigger_value"))
    for row_key in ("trigger_fields", "update_trigger_fields"):
        for row in hint.get(row_key) or []:
            if not isinstance(row, dict):
                continue
            rid = add_attr("subject",
                           row.get("field_name") or row.get("field"), row_key)
            add_state(rid, row.get("value"))
    for row_key in ("rejection_conditions", "acceptance_conditions",
                    "update_conditions"):
        for row in hint.get(row_key) or []:
            if isinstance(row, dict):
                add_attr("subject",
                         row.get("field") or row.get("field_name"), row_key)
                if isinstance(row.get("compared_to"), str):
                    add_attr("subject", row.get("compared_to"),
                             row_key + ".compared_to")
    return (BusinessGraph(nodes=tuple(nodes), edges=tuple(edges)), slots)


# ---------------------------------------------------------------------------
# verdict computation
# ---------------------------------------------------------------------------

def _field_terms(graph: BusinessGraph) -> list[str]:
    return [n.term for n in graph.attributes_of("subject")]


def _foreign_qualified(mention: str, subject_term: str) -> bool:
    """A qualified field mention whose qualifier names an object OTHER than
    the subject self-declares a foreign owner ("PLS_FB_Order_Line__c.Order__c"
    under subject "PLS_FB_Order__c") — cross-object framing, not evidence
    about the subject. Replay-observed FP class 1 (2026-07-21)."""
    if "." not in mention:
        return False
    return mention.split(".", 1)[0].strip().lower() != subject_term.strip().lower()


def _related(table: SymbolTable, api_a: Optional[str],
             api_b: Optional[str]) -> bool:
    """True when either object carries a lookup/MD field referencing the
    other. An adjacent winner signals a cross-object effect framing (the
    field rides the relationship), not a wrong subject — replay-observed FP
    class 2 (2026-07-21)."""
    for near, far in ((api_a, api_b), (api_b, api_a)):
        obj = table.by_api(near)
        if obj is None or not far:
            continue
        for f in obj.fields:
            if f.references_object and f.references_object.lower() == far.lower():
                return True
    return False


def _binds(table: SymbolTable, api_name: Optional[str],
           field_terms: list[str]) -> Optional[int]:
    obj = table.by_api(api_name)
    if obj is None:
        return None
    return sum(1 for t in field_terms if resolve_field(obj, t) is not None)


def _field_fate(table: SymbolTable, api_name: Optional[str],
                term: str) -> Optional[str]:
    """How the ladder lands ``term`` on ``api_name``'s object: ``"exact"``
    (rule-1 verbatim qualified), ``"ladder"`` (canonicalized to a different
    name), or ``None`` (unresolved — the offer/hop territory)."""
    obj = table.by_api(api_name)
    if obj is None:
        return None
    f = resolve_field(obj, term)
    if f is None:
        return None
    return "exact" if f.qualified_api_name == term else "ladder"


def shadow_verdict(graph: BusinessGraph, resolved: ResolvedGraph,
                   table: SymbolTable, *, actual_outcome: str,
                   actual_api: Optional[str], claim_kind: Optional[str] = None,
                   ac_ref: Optional[str] = None,
                   slots: Optional[dict] = None) -> dict:
    """One persisted verdict entry. ``actual_outcome`` is what the pipeline's
    own resolution did (``resolved`` / ``miss`` / ``ambiguous``); the shadow
    side is the joint verifier's dominant winner for the subject node."""
    subject = graph.node("subject")
    term = subject.term if subject else ""
    subject_binding = resolved.binding("subject")
    winner = solve.dominant_entity(resolved, "subject")
    winner_api = winner.sf_api_name if winner else None
    field_terms = _field_terms(graph)
    # The veto's evidence set: mentions that self-declare a foreign owner are
    # cross-object framing and carry no evidence about the SUBJECT.
    veto_terms = [t for t in field_terms if not _foreign_qualified(t, term)]

    model_binds = (_binds(table, actual_api, veto_terms)
                   if actual_outcome == "resolved" else None)
    winner_binds = _binds(table, winner_api, veto_terms)

    if actual_outcome == "resolved" and winner_api:
        agreement = "agree" if winner_api == actual_api else "conflict"
    elif actual_outcome == "resolved":
        agreement = "model_only"
    elif winner_api:
        agreement = "shadow_only"
    else:
        agreement = "neither"

    veto = bool(
        actual_outcome == "resolved" and winner is not None
        and winner_api != actual_api
        and winner.grade == "bound_unique"
        and veto_terms
        and model_binds == 0
        and winner_binds == len(veto_terms)
        # adjacency suppression: a winner reachable from the actual subject
        # by a lookup is cross-object framing, not a wrong subject — and the
        # non-silent field miss downstream already carries B0 offers.
        and not _related(table, actual_api, winner_api))

    entry: dict[str, Any] = {
        "shadow_version": SHADOW_VERSION,
        "term": term,
        "claim_kind": claim_kind,
        "ac_ref": ac_ref,
        "actual": {"outcome": actual_outcome, "sf_api_name": actual_api},
        "shadow": {
            "grade": subject_binding.grade if subject_binding else "unresolved",
            "winner": winner_api,
            "structural_coverage": list(
                subject_binding.structural_coverage) if subject_binding else [0, 0],
            "runner_up": (subject_binding.candidates[1].sf_api_name
                          if subject_binding and len(subject_binding.candidates) > 1
                          else None),
            "field_mentions": field_terms,
            "veto_mentions": veto_terms,
            "model_binds": model_binds,
            "winner_binds": winner_binds,
            # v2 (F0): per-field records with slot provenance — the field-
            # resolution telemetry (which slot proposed it, how the ladder
            # lands it on the actual subject and on the shadow winner).
            "fields": [
                {"term": n.term,
                 "slot": (slots or {}).get(n.node_id, {}).get("slot"),
                 "actual": (_field_fate(table, actual_api, n.term)
                            if actual_outcome == "resolved" else None),
                 "winner": _field_fate(table, winner_api, n.term)}
                for n in graph.attributes_of("subject")],
        },
        "agreement": agreement,
        "would_veto": veto,
        "connected_org_id": (str(resolved.connected_org_id)
                             if resolved.connected_org_id else None),
        "s1_version_seq": resolved.s1_version_seq,
    }
    if veto:
        winner_obj = table.by_api(winner_api)
        entry["veto_evidence"] = {
            "winner": winner_api,
            "discriminators": [
                f.api_name for f in
                (resolve_field(winner_obj, t) for t in veto_terms) if f],
        }
    return entry


def would_veto(entry: dict) -> bool:
    """Pure read of a computed verdict (the Slice-2 gate reads ONLY this)."""
    return bool(entry.get("would_veto"))


# ---------------------------------------------------------------------------
# governance-facing observation + persistence payload
# ---------------------------------------------------------------------------

def _table_for(s1_model, tables: dict, at_seq: int) -> Optional[SymbolTable]:
    """Per-seq symbol table with a failure sentinel (one hydration attempt per
    seq per GovernanceCore instance — a failed org never retries per intent)."""
    cached = tables.get(at_seq)
    if cached is _HYDRATION_FAILED:
        return None
    if cached is not None:
        return cached
    try:
        table = S1KnowledgeSource(s1_model).symbol_table(at_seq)
    except Exception:
        log.debug("D-376 shadow symbol-table hydration failed at seq %s "
                  "(shadow disabled for this seq)", at_seq, exc_info=True)
        tables[at_seq] = _HYDRATION_FAILED
        return None
    tables[at_seq] = table
    return table


def _stash_shadow_verdict(state: Any, entry: dict) -> None:
    """Accumulate on the state (the ``_stash_control_facts`` shape). Dedup by
    the observation identity — the D-247 re-prompt re-sends the full intent
    array, and a re-observed identical intent must not double-count."""
    if state is None:
        return
    if not hasattr(state, "shadow_verdicts") or state.shadow_verdicts is None:
        state.shadow_verdicts = []
    key = (entry.get("term"), tuple(entry["shadow"]["field_mentions"]),
           entry["actual"]["outcome"], entry["actual"]["sf_api_name"])
    for existing in state.shadow_verdicts:
        ek = (existing.get("term"),
              tuple(existing["shadow"]["field_mentions"]),
              existing["actual"]["outcome"], existing["actual"]["sf_api_name"])
        if ek == key:
            return
    state.shadow_verdicts.append(entry)


def observe_subject_resolution(s1_model, tables: dict, desc: dict,
                               excerpt: str, ctx, matches: list,
                               state: Any) -> None:
    """The single governance hook (called right after ``resolve_subject`` in
    ``_resolve_one``). Read-only: derives the actual outcome from ``matches``,
    never touches them."""
    got = intent_graph(desc, excerpt)
    if got is None:
        return
    graph, slots = got
    at = getattr(getattr(ctx, "semantic_context", None), "s1_version_seq", None)
    if at is None or s1_model is None:
        return
    table = _table_for(s1_model, tables, at)
    if table is None:
        return
    resolved = solve.resolve(
        graph, table,
        requirement_text=getattr(ctx, "requirement_text", None))
    if len(matches) == 1:
        actual_outcome, actual_api = "resolved", matches[0].sf_api_name
    elif matches:
        actual_outcome, actual_api = "ambiguous", None
    else:
        actual_outcome, actual_api = "miss", None
    entry = shadow_verdict(
        graph, resolved, table, actual_outcome=actual_outcome,
        actual_api=actual_api, claim_kind=desc.get("claim_kind_hint"),
        ac_ref=desc.get("ac_ref"), slots=slots)
    _stash_shadow_verdict(state, entry)


def attach_payload(verdicts: list[dict]) -> dict:
    """The persisted ``attempted_interpretation["shadow_resolution"]`` map."""
    counts = Counter(v.get("agreement") for v in verdicts)
    return {
        "version": SHADOW_VERSION,
        "verdicts": list(verdicts),
        "counts": {
            **{k: counts.get(k, 0) for k in AGREEMENTS if counts.get(k)},
            "would_veto": sum(1 for v in verdicts if v.get("would_veto")),
            "total": len(verdicts),
        },
    }
