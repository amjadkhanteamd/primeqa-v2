"""S1 metadata-drift detector — VR, picklist, and flow-activation slices
(D-434, extended D-437).

A post-sync consumer that diffs S1's newly-opened entity versions against
their predecessors and surfaces metadata changes. **Detection instrument, not
attribution**: a drift event says *something changed in the org's metadata*;
what that change breaks (if anything) remains the claims' job. The D-432
incident is the motivating specimen — a validation rule deactivated org-side
sat unnoticed for seven weeks because only 13 of 52 active VRs carry a
covering claim; S1 had captured the change the same morning, at seq 66, and
nothing consumed it.

Design rules (D-434, extended by D-437):

* **Deterministic, no LLM, no org calls.** Reads S1 versioned rows only.
* **Event classes, never collapsed:**
    - ``ACTIVATION``    — an active flag flipped, either direction
      (VR rule / picklist value / flow).
    - ``FORMULA``       — a VR's formula text changed; ``before``/``after``
      verbatim, with the dead-rule call-outs (``false`` → real formula is
      **NEW ENFORCEMENT APPEARING** — the case no claim could ever catch;
      the symmetric edit is neutralization).
    - ``MEMBERSHIP``    — a picklist value appeared in / disappeared from a
      set whose chain already existed (values arriving or leaving WITH their
      set are folded into the set's LIFECYCLE event, never emitted
      individually).
    - ``VERSION_MOVED`` — a flow's ``version_number`` changed. The event
      asserts ONLY that a new flow version deployed; it carries no claim
      about what changed — S1 captures no flow logic, so flow CONTENT is out
      of scope by construction and the open-snapshot retrieve-diff (D-427)
      remains the flow content instrument.
    - ``LIFECYCLE``     — an artifact appeared in / disappeared from
      **capture**. Worded "capture", not "org": a deterministic diff cannot
      distinguish org creation/deletion from a capture-scope change.
    - ``CAPTURE_GENERATION`` — the per-seq summary of a capture-mark
      transition (D-437): at any seq where picklist capture marks transition,
      set/value appearances and disappearances are summarized into ONE event
      carrying counts — our capture changed, not the org. The distinction is
      in-data (the marks transition in the same rows at the same seq), not
      heuristic.
    - ``UNKNOWN``       — fail-loud: broken chains, unextractable facets,
      or membership on an unreliably-captured set (D-399.1 discipline: an
      ``inline_truncated`` or NULL capture mark cannot yield reliable
      membership events — reported UNKNOWN, never "no change").
* **First capture is not an event.** A chain whose first row opens at the
  type's baseline seq is first capture, never a LIFECYCLE event.
* **No notifications dependency.** The detector returns events; delivery is
  a separate concern gated on the D-428 NOTIFICATIONS_PROVIDER precondition.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional

from sqlalchemy import text

from primeqa.semantic.entity_attributes import vr_error_message, vr_formula_text

ACTIVATION = "ACTIVATION"
FORMULA = "FORMULA"
MESSAGE = "MESSAGE"
LIFECYCLE = "LIFECYCLE"
MEMBERSHIP = "MEMBERSHIP"
VERSION_MOVED = "VERSION_MOVED"
CAPTURE_GENERATION = "CAPTURE_GENERATION"
RELATED_ENTITY_CHANGED = "RELATED_ENTITY_CHANGED"
UNKNOWN = "UNKNOWN"

# Capture marks that cannot yield reliable membership events (D-437 /
# D-399.1): a truncated inline capture is an admitted-incomplete value list,
# and a NULL mark predates the capture generations entirely.
_UNRELIABLE_MARKS = frozenset({"inline_truncated", None})

_APPEARED_NOTE = ("appeared in capture (org creation or capture-scope "
                  "change — a version diff cannot distinguish them)")
_DISAPPEARED_NOTE = ("disappeared from capture (org deletion or "
                     "capture-scope change — a version diff cannot "
                     "distinguish them)")


@dataclass(frozen=True)
class DriftEvent:
    """One detected metadata change, pinned to the S1 seq that captured it."""

    seq: int                     # the version seq the change was captured at
    at: Optional[str]            # that version's timestamp (ISO), if resolvable
    rule: str                    # the artifact's fully-qualified sf_api_name
    kind: str                    # one of the module's event-class constants
    before: Optional[str]
    after: Optional[str]
    note: str = ""


@dataclass(frozen=True)
class _Facet:
    """One compared facet of a chained entity (generic walker input)."""

    kind: str                                     # event kind on change
    get: Callable[[Mapping[str, Any]], Any]       # row -> value (None=unknown)
    fmt: Callable[[Any], Optional[str]]           # value -> display
    unknown_note: str                             # one-side-None wording
    note: Callable[[Any, Any], str] = lambda p, n: ""


def _is_dead_formula(formula: Optional[str]) -> bool:
    """A formula that is literally ``false`` never fires — a dead rule."""
    return (formula or "").strip().lower() == "false"


def _vr_active_or_none(attributes: Optional[Mapping[str, Any]]) -> Optional[bool]:
    """The active flag with presence-or-None semantics — deliberately NOT
    :func:`~primeqa.semantic.entity_attributes.vr_is_active`, whose
    missing→True default is a presentation-layer posture (D-301: don't demote
    every negative to caveated). For a CHANGE detector that default is the
    wrong direction both ways: it could fabricate an ACTIVATION event (a row
    that merely lost the attribute reads as a flip to active) or mask one.
    Same shape tolerance as the extractor (designed ``is_active``; raw
    Tooling ``Active``; ``Metadata.active``), no default."""
    attrs = attributes or {}
    for key in ("is_active", "Active"):
        v = attrs.get(key)
        if v is not None:
            return bool(v)
    meta = attrs.get("Metadata")
    if isinstance(meta, Mapping):
        v = meta.get("active")
        if v is not None:
            if isinstance(v, str):
                return v.strip().lower() == "true"
            return bool(v)
    return None


def _active_repr(active: Optional[bool]) -> Optional[str]:
    if active is None:
        return None
    return "active" if active else "inactive"


# ---------------------------------------------------------------------------
# The generic chain walker (D-437 refactor of the D-434 VR walk — behavior
# identical for VRs; flows reuse it; picklists layer set-fold logic on top)
# ---------------------------------------------------------------------------

def _walk_chains(
    rows: Iterable[Mapping[str, Any]],
    times: Mapping[int, str],
    *,
    type_label: str,
    facets: tuple[_Facet, ...],
    state_repr: Callable[[Mapping[str, Any]], str],
    baseline: Optional[int] = None,
    on_appear: Optional[Callable[[str, Mapping[str, Any], int],
                                 Optional[DriftEvent]]] = None,
    on_disappear: Optional[Callable[[str, Mapping[str, Any], int],
                                    Optional[DriftEvent]]] = None,
) -> list[DriftEvent]:
    """Walk every artifact's version chain; return events (unsorted).

    ``on_appear``/``on_disappear`` may return None to swallow the lifecycle
    event (the picklist set-fold / capture-fold path); default emits the
    standard LIFECYCLE wording. ``baseline`` overrides the computed
    type-baseline (min valid_from_seq across rows).
    """
    by_name: dict[str, list[Mapping[str, Any]]] = {}
    events: list[DriftEvent] = []
    computed_baseline: Optional[int] = None
    for r in rows:
        vf = r.get("valid_from_seq")
        if vf is None:
            continue
        computed_baseline = (vf if computed_baseline is None
                             else min(computed_baseline, vf))
        name = r.get("sf_api_name")
        if not name:
            events.append(DriftEvent(
                seq=vf, at=times.get(vf), rule=f"<unnamed {type_label} row>",
                kind=UNKNOWN, before=None, after=None,
                note="row has no sf_api_name — cannot resolve its chain"))
            continue
        by_name.setdefault(name, []).append(r)
    base = baseline if baseline is not None else computed_baseline

    for name, chain in by_name.items():
        chain.sort(key=lambda r: r["valid_from_seq"])

        first = chain[0]
        if first["valid_from_seq"] != base:
            seq = first["valid_from_seq"]
            ev = (on_appear(name, first, seq) if on_appear is not None
                  else DriftEvent(seq=seq, at=times.get(seq), rule=name,
                                  kind=LIFECYCLE, before=None,
                                  after=state_repr(first),
                                  note=_APPEARED_NOTE))
            if ev is not None:
                events.append(ev)
        # else: first capture at the type's baseline — NOT an event.

        for prev, nxt in zip(chain, chain[1:]):
            seq = nxt["valid_from_seq"]
            at = times.get(seq)
            if prev.get("valid_to_seq") != seq:
                events.append(DriftEvent(
                    seq=seq, at=at, rule=name, kind=UNKNOWN,
                    before=state_repr(prev), after=state_repr(nxt),
                    note=f"version chain broken (predecessor closes at "
                         f"{prev.get('valid_to_seq')}, successor opens at "
                         f"{seq}) — state shown, change not attributable "
                         f"to a seq"))
                continue
            for facet in facets:
                p, n = facet.get(prev), facet.get(nxt)
                if p is None or n is None:
                    if p is not n:
                        events.append(DriftEvent(
                            seq=seq, at=at, rule=name, kind=UNKNOWN,
                            before=facet.fmt(p), after=facet.fmt(n),
                            note=facet.unknown_note))
                elif p != n:
                    events.append(DriftEvent(
                        seq=seq, at=at, rule=name, kind=facet.kind,
                        before=facet.fmt(p), after=facet.fmt(n),
                        note=facet.note(p, n)))

        last = chain[-1]
        if last.get("valid_to_seq") is not None:
            seq = last["valid_to_seq"]
            ev = (on_disappear(name, last, seq) if on_disappear is not None
                  else DriftEvent(seq=seq, at=times.get(seq), rule=name,
                                  kind=LIFECYCLE, before=state_repr(last),
                                  after=None, note=_DISAPPEARED_NOTE))
            if ev is not None:
                events.append(ev)
    return events


# ---------------------------------------------------------------------------
# Validation rules (D-434)
# ---------------------------------------------------------------------------

def _vr_state_repr(row: Mapping[str, Any]) -> str:
    attrs = row.get("attributes") or {}
    return (f"active={_active_repr(_vr_active_or_none(attrs))} "
            f"formula={vr_formula_text(attrs)!r}")


def _formula_note(p: str, n: str) -> str:
    if _is_dead_formula(p) and not _is_dead_formula(n):
        return ("NEW ENFORCEMENT APPEARING — the predecessor formula is "
                "literal `false` (a dead rule); this edit arms it. No claim "
                "could catch this: a rule that never fires can never have a "
                "covering behavioural claim.")
    if _is_dead_formula(n) and not _is_dead_formula(p):
        return ("neutralized to a never-firing formula (literal `false`) — "
                "deactivation in formula clothing")
    return ""


_VR_FACETS = (
    _Facet(kind=ACTIVATION,
           get=lambda r: _vr_active_or_none(r.get("attributes") or {}),
           fmt=_active_repr,
           unknown_note="active flag unextractable on one side — refusing "
                        "to infer an activation change"),
    _Facet(kind=FORMULA,
           get=lambda r: vr_formula_text(r.get("attributes") or {}),
           fmt=lambda v: v,
           unknown_note="formula unextractable on one side — refusing to "
                        "infer a formula change",
           note=_formula_note),
    # D-446: the error message is not behaviour, but it IS the run-time
    # expected-rejection pattern (D-297 grading) and the other_vr_fired
    # naming key (attribution) — message drift breaks grading/matching
    # while the rule's firing behaviour is unchanged.
    _Facet(kind=MESSAGE,
           get=lambda r: vr_error_message(r.get("attributes") or {}),
           fmt=lambda v: v,
           unknown_note="error message unextractable on one side — refusing "
                        "to infer a message change",
           note=lambda p, n: (
               "message-only drift: the rule's behaviour is unchanged, but "
               "this text is the D-297 expected-rejection pattern and the "
               "attribution naming key — stale-message runs grade "
               "rejected_unasserted_reason and other_vr_fired loses the "
               "rule name")),
)


def diff_vr_chains(
    rows: Iterable[Mapping[str, Any]],
    version_times: Optional[Mapping[int, str]] = None,
) -> list[DriftEvent]:
    """Diff every rule's version chain; return events ordered by (seq, rule).

    ``rows``: mappings with ``sf_api_name`` / ``valid_from_seq`` /
    ``valid_to_seq`` / ``attributes`` — every ValidationRule row for one org,
    ALL versions (the full bitemporal history, not just current).
    """
    events = _walk_chains(rows, version_times or {},
                          type_label="ValidationRule", facets=_VR_FACETS,
                          state_repr=_vr_state_repr)
    events.sort(key=lambda e: (e.seq, e.rule, e.kind))
    return events


def detect_vr_drift(
    conn, connected_org_id: str, *, since_seq: Optional[int] = None,
) -> list[DriftEvent]:
    """Run the diff over one org's full ValidationRule history in S1.

    ``conn`` is a tenant-scoped SQLAlchemy connection (``search_path`` already
    on the tenant schema — the ``semantic/diff.py`` in-package precedent).
    ``since_seq`` filters the RETURNED events (the diff itself always walks
    the full history — a chain cannot be diffed from its middle).
    """
    import json as _json
    rows = []
    for r in conn.execute(text(
        "SELECT sf_api_name, valid_from_seq, valid_to_seq, attributes "
        "FROM entities "
        "WHERE entity_type = 'ValidationRule' "
        "  AND connected_org_id = CAST(:org AS uuid)"),
            {"org": connected_org_id}).mappings():
        row = dict(r)
        if isinstance(row.get("attributes"), str):
            row["attributes"] = _json.loads(row["attributes"])
        rows.append(row)
    events = diff_vr_chains(rows, _version_times(conn, connected_org_id))
    if since_seq is not None:
        events = [e for e in events if e.seq >= since_seq]
    return events


# ---------------------------------------------------------------------------
# Flow activation (D-437 — the cheap slice; content is invisible by
# construction: S1 captures no flow logic, so VERSION_MOVED asserts only that
# a new version deployed; the open-snapshot diff is the content instrument)
# ---------------------------------------------------------------------------

def _flow_state_repr(row: Mapping[str, Any]) -> str:
    return (f"active={_active_repr(row.get('is_active'))} "
            f"version={row.get('version_number')}")


_FLOW_FACETS = (
    _Facet(kind=ACTIVATION,
           get=lambda r: r.get("is_active"),
           fmt=_active_repr,
           unknown_note="is_active missing on one side (no flow_details "
                        "row) — refusing to infer an activation change"),
    _Facet(kind=VERSION_MOVED,
           get=lambda r: r.get("version_number"),
           fmt=lambda v: None if v is None else str(v),
           unknown_note="version_number missing on one side (no flow_details "
                        "row) — refusing to infer a version move",
           note=lambda p, n: ("a new flow version deployed — content "
                              "unknown (S1 captures no flow logic; the "
                              "open-snapshot diff is the content "
                              "instrument)")),
)


def diff_flow_chains(
    rows: Iterable[Mapping[str, Any]],
    version_times: Optional[Mapping[int, str]] = None,
) -> list[DriftEvent]:
    """Diff every flow's version chain. ``rows``: ``sf_api_name`` /
    ``valid_from_seq`` / ``valid_to_seq`` / ``is_active`` /
    ``version_number`` (facets pre-joined from ``flow_details``; None =
    missing details row → UNKNOWN, never silence)."""
    events = _walk_chains(rows, version_times or {}, type_label="Flow",
                          facets=_FLOW_FACETS, state_repr=_flow_state_repr)
    events.sort(key=lambda e: (e.seq, e.rule, e.kind))
    return events


def detect_flow_drift(
    conn, connected_org_id: str, *, since_seq: Optional[int] = None,
) -> list[DriftEvent]:
    """Flow-activation drift over one org's full Flow history in S1."""
    rows = [dict(r) for r in conn.execute(text(
        "SELECT e.sf_api_name, e.valid_from_seq, e.valid_to_seq, "
        "       fd.is_active, fd.version_number "
        "FROM entities e "
        "LEFT JOIN flow_details fd ON fd.entity_id = e.id "
        "WHERE e.entity_type = 'Flow' "
        "  AND e.connected_org_id = CAST(:org AS uuid)"),
        {"org": connected_org_id}).mappings()]
    events = diff_flow_chains(rows, _version_times(conn, connected_org_id))
    if since_seq is not None:
        events = [e for e in events if e.seq >= since_seq]
    return events


# ---------------------------------------------------------------------------
# Picklist value sets (D-437 — the primary slice)
# ---------------------------------------------------------------------------

def diff_picklist_chains(
    value_rows: Iterable[Mapping[str, Any]],
    set_rows: Iterable[Mapping[str, Any]],
    field_rows: Iterable[Mapping[str, Any]],
    version_times: Optional[Mapping[int, str]] = None,
) -> list[DriftEvent]:
    """Diff picklist value-set membership, value activation, and set
    lifecycle over full S1 history.

    ``value_rows``: PicklistValue versions — ``sf_api_name`` /
    ``valid_from_seq`` / ``valid_to_seq`` / ``is_active`` / ``set_row_id``
    (the owning set's version-row id, from ``picklist_value_details``).
    ``set_rows``: PicklistValueSet versions — ``id`` / ``sf_api_name`` /
    ``valid_from_seq`` / ``valid_to_seq``.
    ``field_rows``: picklist-typed Field versions — ``sf_api_name`` /
    ``valid_from_seq`` / ``valid_to_seq`` / ``picklist_capture`` /
    ``set_row_id``.

    The three D-437 rules layered on the generic walk:
      * values arriving/leaving WITH their set fold into the set's LIFECYCLE
        event (count noted), never individual MEMBERSHIP events;
      * at a capture-mark-transition seq, ALL appearances/disappearances
        summarize into one CAPTURE_GENERATION event for that seq;
      * membership on a set owned by an ``inline_truncated``/NULL-capture
        field is UNKNOWN, never MEMBERSHIP and never silent.
    """
    times = version_times or {}
    value_rows = [dict(r) for r in value_rows]
    set_rows = [dict(r) for r in set_rows]
    field_rows = [dict(r) for r in field_rows]

    set_key_by_rowid = {str(s["id"]): s["sf_api_name"] for s in set_rows
                        if s.get("id") is not None and s.get("sf_api_name")}

    # --- field capture-mark chains -> transition seqs + set ownership -----
    fields_by_name: dict[str, list[dict]] = {}
    for f in field_rows:
        if f.get("sf_api_name"):
            fields_by_name.setdefault(f["sf_api_name"], []).append(f)
    capture_transition_seqs: set[int] = set()
    mark_transitions: dict[int, int] = {}          # seq -> transition count
    owners_by_set: dict[str, set[str]] = {}        # set_key -> field names
    field_mark_intervals: dict[str, list[tuple[int, Optional[int],
                                               Optional[str]]]] = {}
    for name, chain in fields_by_name.items():
        chain.sort(key=lambda r: r["valid_from_seq"])
        for row in chain:
            sk = set_key_by_rowid.get(str(row.get("set_row_id")))
            if sk:
                owners_by_set.setdefault(sk, set()).add(name)
            field_mark_intervals.setdefault(name, []).append(
                (row["valid_from_seq"], row.get("valid_to_seq"),
                 row.get("picklist_capture")))
        for prev, nxt in zip(chain, chain[1:]):
            if prev.get("picklist_capture") != nxt.get("picklist_capture"):
                seq = nxt["valid_from_seq"]
                capture_transition_seqs.add(seq)
                mark_transitions[seq] = mark_transitions.get(seq, 0) + 1

    def _mark_at(field: str, seq: int) -> Optional[str]:
        best = None
        for vf, vt, mark in field_mark_intervals.get(field, ()):
            if vf <= seq and (vt is None or seq < vt or vf == seq):
                best = mark
                if vf == seq:
                    break
        return best

    def _membership_reliable(set_key: Optional[str], seq: int) -> bool:
        owners = owners_by_set.get(set_key or "", set())
        if not owners:
            # standard/global value sets: captured wholesale — reliable.
            return True
        return all(_mark_at(f, seq) not in _UNRELIABLE_MARKS for f in owners)

    # --- set chains: appearance/disappearance seqs per set key ------------
    baseline = min((r["valid_from_seq"] for r in set_rows + value_rows
                    if r.get("valid_from_seq") is not None), default=None)
    sets_appearing_at: dict[tuple[str, int], int] = {}   # (key, seq) -> folded
    sets_disappearing_at: dict[tuple[str, int], int] = {}
    capture_counts: dict[int, dict[str, int]] = {}       # seq -> counters

    def _cap(seq: int, bucket: str, n: int = 1) -> None:
        capture_counts.setdefault(
            seq, {"sets_appeared": 0, "sets_disappeared": 0,
                  "values_appeared": 0, "values_disappeared": 0})
        capture_counts[seq][bucket] += n

    set_appear_events: dict[tuple[str, int], DriftEvent] = {}

    def _set_on_appear(name, row, seq):
        if seq in capture_transition_seqs:
            _cap(seq, "sets_appeared")
            sets_appearing_at[(name, seq)] = 0
            return None
        sets_appearing_at[(name, seq)] = 0
        ev = DriftEvent(seq=seq, at=times.get(seq), rule=name, kind=LIFECYCLE,
                        before=None, after="value set present",
                        note=_APPEARED_NOTE)
        set_appear_events[(name, seq)] = ev
        return None                      # emitted later with the fold count

    def _set_on_disappear(name, row, seq):
        if seq in capture_transition_seqs:
            _cap(seq, "sets_disappeared")
            sets_disappearing_at[(name, seq)] = 0
            return None
        sets_disappearing_at[(name, seq)] = 0
        return DriftEvent(seq=seq, at=times.get(seq), rule=name,
                          kind=LIFECYCLE, before="value set present",
                          after=None, note=_DISAPPEARED_NOTE)

    events = _walk_chains(
        set_rows, times, type_label="PicklistValueSet", facets=(),
        state_repr=lambda r: "value set present", baseline=baseline,
        on_appear=_set_on_appear, on_disappear=_set_on_disappear)

    # --- value chains ------------------------------------------------------
    def _value_set_key(row) -> Optional[str]:
        return set_key_by_rowid.get(str(row.get("set_row_id")))

    def _value_on_appear(name, row, seq):
        if seq in capture_transition_seqs:
            _cap(seq, "values_appeared")
            return None
        sk = _value_set_key(row)
        if sk is None:
            return DriftEvent(
                seq=seq, at=times.get(seq), rule=name, kind=UNKNOWN,
                before=None, after="value present",
                note="value's owning set row is unresolvable — cannot "
                     "classify the appearance")
        if (sk, seq) in sets_appearing_at:
            sets_appearing_at[(sk, seq)] += 1        # fold into the set event
            return None
        if not _membership_reliable(sk, seq):
            return DriftEvent(
                seq=seq, at=times.get(seq), rule=name, kind=UNKNOWN,
                before=None, after="value present",
                note="membership not reliably diffable — the owning field's "
                     "capture mark is inline_truncated or NULL (D-399.1: "
                     "UNKNOWN, never 'no change')")
        return DriftEvent(seq=seq, at=times.get(seq), rule=name,
                          kind=MEMBERSHIP, before=None, after="value present",
                          note="value added to an existing captured set")

    def _value_on_disappear(name, row, seq):
        if seq in capture_transition_seqs:
            _cap(seq, "values_disappeared")
            return None
        sk = _value_set_key(row)
        if sk is None:
            return DriftEvent(
                seq=seq, at=times.get(seq), rule=name, kind=UNKNOWN,
                before="value present", after=None,
                note="value's owning set row is unresolvable — cannot "
                     "classify the disappearance")
        if (sk, seq) in sets_disappearing_at:
            sets_disappearing_at[(sk, seq)] += 1
            return None
        if not _membership_reliable(sk, seq):
            return DriftEvent(
                seq=seq, at=times.get(seq), rule=name, kind=UNKNOWN,
                before="value present", after=None,
                note="membership not reliably diffable — the owning field's "
                     "capture mark is inline_truncated or NULL (D-399.1: "
                     "UNKNOWN, never 'no change')")
        return DriftEvent(seq=seq, at=times.get(seq), rule=name,
                          kind=MEMBERSHIP, before="value present", after=None,
                          note="value removed from an existing captured set")

    value_facets = (
        _Facet(kind=ACTIVATION,
               get=lambda r: r.get("is_active"),
               fmt=_active_repr,
               unknown_note="is_active missing on one side (no "
                            "picklist_value_details row) — refusing to "
                            "infer an activation change"),
    )
    events += _walk_chains(
        value_rows, times, type_label="PicklistValue", facets=value_facets,
        state_repr=lambda r: f"active={_active_repr(r.get('is_active'))}",
        baseline=baseline,
        on_appear=_value_on_appear, on_disappear=_value_on_disappear)

    # --- emit deferred set-appearance events with their fold counts -------
    for (name, seq), ev in set_appear_events.items():
        folded = sets_appearing_at.get((name, seq), 0)
        note = ev.note if not folded else (
            f"{ev.note}; {folded} value{'s' if folded != 1 else ''} arrived "
            f"with the set (folded)")
        events.append(DriftEvent(seq=ev.seq, at=ev.at, rule=ev.rule,
                                 kind=ev.kind, before=ev.before,
                                 after=ev.after, note=note))

    # --- one CAPTURE_GENERATION summary per transition seq ----------------
    for seq in sorted(capture_transition_seqs):
        c = capture_counts.get(seq, {"sets_appeared": 0, "sets_disappeared": 0,
                                     "values_appeared": 0,
                                     "values_disappeared": 0})
        events.append(DriftEvent(
            seq=seq, at=times.get(seq), rule="<picklist capture generation>",
            kind=CAPTURE_GENERATION, before=None, after=None,
            note=(f"{mark_transitions.get(seq, 0)} field capture marks "
                  f"transitioned at this seq; {c['sets_appeared']} sets + "
                  f"{c['values_appeared']} values appeared, "
                  f"{c['sets_disappeared']} sets + "
                  f"{c['values_disappeared']} values disappeared — OUR "
                  f"capture changed, not the org (the mark transition is "
                  f"recorded in the same rows at the same seq)")))

    events.sort(key=lambda e: (e.seq, e.rule, e.kind))
    return events


def detect_picklist_drift(
    conn, connected_org_id: str, *, since_seq: Optional[int] = None,
) -> list[DriftEvent]:
    """Picklist drift over one org's full history in S1."""
    value_rows = [dict(r) for r in conn.execute(text(
        "SELECT e.sf_api_name, e.valid_from_seq, e.valid_to_seq, "
        "       pvd.is_active, "
        "       CAST(pvd.picklist_value_set_entity_id AS text) AS set_row_id "
        "FROM entities e "
        "LEFT JOIN picklist_value_details pvd ON pvd.entity_id = e.id "
        "WHERE e.entity_type = 'PicklistValue' "
        "  AND e.connected_org_id = CAST(:org AS uuid)"),
        {"org": connected_org_id}).mappings()]
    set_rows = [dict(r) for r in conn.execute(text(
        "SELECT CAST(e.id AS text) AS id, e.sf_api_name, "
        "       e.valid_from_seq, e.valid_to_seq "
        "FROM entities e "
        "WHERE e.entity_type = 'PicklistValueSet' "
        "  AND e.connected_org_id = CAST(:org AS uuid)"),
        {"org": connected_org_id}).mappings()]
    field_rows = [dict(r) for r in conn.execute(text(
        "SELECT e.sf_api_name, e.valid_from_seq, e.valid_to_seq, "
        "       fd.picklist_capture, "
        "       CAST(fd.picklist_value_set_entity_id AS text) AS set_row_id "
        "FROM entities e "
        "JOIN field_details fd ON fd.entity_id = e.id "
        "WHERE e.entity_type = 'Field' "
        "  AND fd.field_type IN ('picklist', 'multipicklist') "
        "  AND e.connected_org_id = CAST(:org AS uuid)"),
        {"org": connected_org_id}).mappings()]
    events = diff_picklist_chains(
        value_rows, set_rows, field_rows,
        _version_times(conn, connected_org_id))
    if since_seq is not None:
        events = [e for e in events if e.seq >= since_seq]
    return events


# ---------------------------------------------------------------------------
# Shared reader helper
# ---------------------------------------------------------------------------

def _version_times(conn, connected_org_id: str) -> dict[int, str]:
    return {
        r["version_seq"]: r["created_at"].isoformat()
        for r in conn.execute(text(
            "SELECT version_seq, created_at FROM logical_versions "
            "WHERE connected_org_id = CAST(:org AS uuid)"),
            {"org": connected_org_id}).mappings()
        if r["created_at"] is not None
    }


# ---------------------------------------------------------------------------
# Related-entity changes (D-446): a rule's behaviour can move while its own
# text does not — the change lives on an entity the formula references. Two
# relatable classes, both from already-derivable data (no invented graph):
# a referenced field's TYPE change (field_details is captured per version)
# and a RecordType DeveloperName rename (rows carry a stable sf_id, so the
# rename is joinable even though the api-name chain re-keys). Everything
# else a formula can reference ($-globals, custom metadata, parent data)
# stays out of scope — those facts are not in S1 at all.
# ---------------------------------------------------------------------------

import re as _re

# The D-229 lenient extractor (attribution `_formula_fields`), copied rather
# than imported — S1 must not depend on S6 (substrate boundary). Lenient +
# parse-independent on purpose: the TEXT()-family rules never parse, and an
# AST walk would go blind exactly where the census says the parser stops.
_STRLIT_RE = _re.compile(r"'[^']*'|\"[^\"]*\"")
_IDENT_RE = _re.compile(r"([A-Za-z_][A-Za-z0-9_.]*)\s*(\()?")
_FORMULA_KEYWORDS = frozenset({"TRUE", "FALSE", "NULL", "AND", "OR", "NOT"})


def _formula_bare_fields(formula_text: str) -> set:
    text_ = _STRLIT_RE.sub(" ", formula_text or "")
    out = set()
    for m in _IDENT_RE.finditer(text_):
        ident, is_call = m.group(1), m.group(2)
        if is_call:
            continue
        bare = ident.split(".")[-1]
        if not bare or bare[:1].isdigit() or bare.upper() in _FORMULA_KEYWORDS:
            continue
        out.add(bare.lower())
    return out


def _active_current_rules(conn, connected_org_id: str) -> list[tuple[str, str]]:
    """``(sf_api_name, formula_text)`` for every currently-active rule."""
    import json as _json
    out = []
    for r in conn.execute(text(
        "SELECT sf_api_name, attributes FROM entities "
        "WHERE entity_type = 'ValidationRule' AND valid_to_seq IS NULL "
        "  AND connected_org_id = CAST(:org AS uuid)"),
            {"org": connected_org_id}).mappings():
        attrs = r["attributes"]
        if isinstance(attrs, str):
            attrs = _json.loads(attrs)
        if _vr_active_or_none(attrs) is False:
            continue                      # inactive cannot fire (D-301)
        formula = vr_formula_text(attrs)
        if r["sf_api_name"] and formula:
            out.append((r["sf_api_name"], formula))
    return out


def _related_field_type_events(rule_fields, field_rows, times):
    """Pure core: referenced-field TYPE changes -> per-rule events."""
    events: list[DriftEvent] = []
    for fname, chain in field_rows.items():
        chain = sorted(chain, key=lambda r: r["valid_from_seq"])
        obj, _, bare = fname.rpartition(".")
        for prev, nxt in zip(chain, chain[1:]):
            p, n = prev.get("field_type"), nxt.get("field_type")
            if p is None or n is None or p == n:
                continue
            seq = nxt["valid_from_seq"]
            for rname, robj, rfields, _f in rule_fields:
                if robj != obj or bare.lower() not in rfields:
                    continue
                events.append(DriftEvent(
                    seq=seq, at=times.get(seq), rule=rname,
                    kind=RELATED_ENTITY_CHANGED,
                    before=f"{fname}: {p}", after=f"{fname}: {n}",
                    note=("a field this rule references changed type — the "
                          "rule's text is unchanged but its comparison "
                          "semantics may have moved")))
    return events


def _related_recordtype_events(rule_fields, rt_rows, times):
    """Pure core: RecordType DeveloperName renames (stable sf_id join) ->
    per-rule events for rules comparing RecordType.DeveloperName."""
    events: list[DriftEvent] = []
    rt_groups: dict[str, list] = {}
    for r in rt_rows:
        sid = (r.get("sf_id") or "")[:15]
        if sid and r.get("sf_api_name") and r.get("valid_from_seq") is not None:
            rt_groups.setdefault(sid, []).append(r)
    for sid, grp in rt_groups.items():
        grp = sorted(grp, key=lambda r: r["valid_from_seq"])
        for prev, nxt in zip(grp, grp[1:]):
            p_api, n_api = prev["sf_api_name"], nxt["sf_api_name"]
            if p_api == n_api:
                continue
            p_dev = p_api.split(".", 1)[-1]
            n_dev = n_api.split(".", 1)[-1]
            obj = p_api.split(".", 1)[0]
            seq = nxt["valid_from_seq"]
            for rname, robj, _rf, formula in rule_fields:
                if robj != obj or "RecordType.DeveloperName" not in formula:
                    continue
                if p_dev not in formula and n_dev not in formula:
                    continue
                events.append(DriftEvent(
                    seq=seq, at=times.get(seq), rule=rname,
                    kind=RELATED_ENTITY_CHANGED,
                    before=f"RecordType {sid}: {p_dev}",
                    after=f"RecordType {sid}: {n_dev}",
                    note=("a RecordType this rule compares by DeveloperName "
                          "was renamed — the rule's text is unchanged but "
                          "its RecordType arm now selects differently")))
    return events


def _rule_fields_index(rules):
    """``(name, object, bare-fields, formula)`` per active rule."""
    return [(name, name.rsplit(".", 1)[0],
             _formula_bare_fields(formula), formula)
            for name, formula in rules]


def detect_related_entity_drift(
    conn, connected_org_id: str, *, since_seq: Optional[int] = None,
) -> list[DriftEvent]:
    """RELATED_ENTITY_CHANGED events: for every ACTIVE rule, a change on an
    entity its formula references — the rule is named, the dependency and
    its before/after shown. The rule's own text never changes in these
    events; that is the point."""
    times = _version_times(conn, connected_org_id)
    rule_fields = _rule_fields_index(
        _active_current_rules(conn, connected_org_id))

    field_rows: dict[str, list] = {}
    for r in conn.execute(text(
        "SELECT e.sf_api_name, e.valid_from_seq, e.valid_to_seq, "
        "       d.field_type "
        "FROM entities e JOIN field_details d ON d.entity_id = e.id "
        "WHERE e.entity_type = 'Field' "
        "  AND e.connected_org_id = CAST(:org AS uuid)"),
            {"org": connected_org_id}).mappings():
        if r["sf_api_name"] and r["valid_from_seq"] is not None:
            field_rows.setdefault(r["sf_api_name"], []).append(dict(r))

    rt_rows = [dict(r) for r in conn.execute(text(
        "SELECT sf_id, sf_api_name, valid_from_seq FROM entities "
        "WHERE entity_type = 'RecordType' "
        "  AND connected_org_id = CAST(:org AS uuid)"),
        {"org": connected_org_id}).mappings()]

    events = (_related_field_type_events(rule_fields, field_rows, times)
              + _related_recordtype_events(rule_fields, rt_rows, times))
    events.sort(key=lambda e: (e.seq, e.rule, e.kind))
    if since_seq is not None:
        events = [e for e in events if e.seq >= since_seq]
    return events
