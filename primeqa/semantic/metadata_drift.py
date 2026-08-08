"""S1 metadata-drift detector — VR slice (D-434).

A post-sync consumer that diffs S1's newly-opened entity versions against
their predecessors and surfaces metadata changes. **Detection instrument, not
attribution**: a drift event says *something changed in the org's metadata*;
what that change breaks (if anything) remains the claims' job. The D-432
incident is the motivating specimen — a validation rule deactivated org-side
sat unnoticed for seven weeks because only 13 of 52 active VRs carry a
covering claim; S1 had captured the change the same morning, at seq 66, and
nothing consumed it.

Design rules (D-434):

* **Deterministic, no LLM, no org calls.** Reads S1 versioned rows only.
* **Three event classes, never collapsed:**
    - ``ACTIVATION`` — the active flag flipped, either direction.
    - ``FORMULA``    — the formula text changed; ``before``/``after`` carried
      verbatim. A predecessor formula that is literally ``false`` (a dead,
      never-firing rule) edited to a real formula is flagged **NEW
      ENFORCEMENT APPEARING** — the case no claim could ever catch, because a
      rule that never fires can never have a covering behavioural claim. The
      symmetric live→``false`` edit is flagged as neutralization
      (deactivation in formula clothing).
    - ``LIFECYCLE``  — the rule appeared in / disappeared from **capture**.
      Worded "capture", not "org": a deterministic diff cannot distinguish
      org creation/deletion from a capture-scope change.
* **First capture is not an event.** A chain whose first row opens at the
  type's baseline seq (the org's first version holding ANY ValidationRule
  row) is first capture, never a LIFECYCLE event.
* **Fail loud.** A broken version chain (predecessor close != successor
  open) or a facet unextractable on one side of a pair yields an ``UNKNOWN``
  event carrying what is known — never a silent no-change, never a guessed
  change at a guessed seq (D-382's never-invent rule, applied to the diff).
* **No notifications dependency.** The detector returns events; delivery is
  a separate concern gated on the D-428 NOTIFICATIONS_PROVIDER precondition.
* **Flows are out of scope by construction**: S1 captures no flow logic
  (``parsed_logic`` NULL; version counter + active flag only), so an S1 diff
  can see a flow's version move but never what changed — the open-snapshot
  retrieve-diff (D-427) remains the flow instrument.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

from sqlalchemy import text

from primeqa.semantic.entity_attributes import vr_formula_text

ACTIVATION = "ACTIVATION"
FORMULA = "FORMULA"
LIFECYCLE = "LIFECYCLE"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class DriftEvent:
    """One detected metadata change, pinned to the S1 seq that captured it."""

    seq: int                     # the version seq the change was captured at
    at: Optional[str]            # that version's timestamp (ISO), if resolvable
    rule: str                    # fully-qualified sf_api_name
    kind: str                    # ACTIVATION | FORMULA | LIFECYCLE | UNKNOWN
    before: Optional[str]
    after: Optional[str]
    note: str = ""


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


def _state_repr(attrs: Mapping[str, Any]) -> str:
    return (f"active={_active_repr(_vr_active_or_none(attrs))} "
            f"formula={vr_formula_text(attrs)!r}")


def diff_vr_chains(
    rows: Iterable[Mapping[str, Any]],
    version_times: Optional[Mapping[int, str]] = None,
) -> list[DriftEvent]:
    """Diff every rule's version chain; return events ordered by (seq, rule).

    ``rows``: mappings with ``sf_api_name`` / ``valid_from_seq`` /
    ``valid_to_seq`` / ``attributes`` — every ValidationRule row for one org,
    ALL versions (the full bitemporal history, not just current).
    ``version_times``: optional ``{seq: iso-timestamp}`` for event stamping.
    """
    times = version_times or {}
    by_rule: dict[str, list[Mapping[str, Any]]] = {}
    events: list[DriftEvent] = []
    baseline: Optional[int] = None
    for r in rows:
        vf = r.get("valid_from_seq")
        if vf is None:
            continue
        baseline = vf if baseline is None else min(baseline, vf)
        name = r.get("sf_api_name")
        if not name:
            # A versioned row with no identity cannot be chained — fail loud.
            events.append(DriftEvent(
                seq=vf, at=times.get(vf), rule="<unnamed ValidationRule row>",
                kind=UNKNOWN, before=None, after=None,
                note="row has no sf_api_name — cannot resolve its chain"))
            continue
        by_rule.setdefault(name, []).append(r)

    for name, chain in by_rule.items():
        chain.sort(key=lambda r: r["valid_from_seq"])

        first = chain[0]
        if first["valid_from_seq"] != baseline:
            # Appeared in capture after the baseline — a lifecycle event.
            seq = first["valid_from_seq"]
            events.append(DriftEvent(
                seq=seq, at=times.get(seq), rule=name, kind=LIFECYCLE,
                before=None, after=_state_repr(first.get("attributes") or {}),
                note="appeared in capture (org creation or capture-scope "
                     "change — a version diff cannot distinguish them)"))
        # else: first capture at the type's baseline — NOT an event.

        for prev, nxt in zip(chain, chain[1:]):
            seq = nxt["valid_from_seq"]
            at = times.get(seq)
            if prev.get("valid_to_seq") != seq:
                # Chain discontinuity: versions are missing between these two
                # rows, so any facet change cannot be pinned to a seq.
                events.append(DriftEvent(
                    seq=seq, at=at, rule=name, kind=UNKNOWN,
                    before=_state_repr(prev.get("attributes") or {}),
                    after=_state_repr(nxt.get("attributes") or {}),
                    note=f"version chain broken (predecessor closes at "
                         f"{prev.get('valid_to_seq')}, successor opens at "
                         f"{seq}) — state shown, change not attributable "
                         f"to a seq"))
                continue

            pa, na = prev.get("attributes") or {}, nxt.get("attributes") or {}
            p_act, n_act = _vr_active_or_none(pa), _vr_active_or_none(na)
            if p_act is None or n_act is None:
                if p_act is not n_act:
                    events.append(DriftEvent(
                        seq=seq, at=at, rule=name, kind=UNKNOWN,
                        before=_active_repr(p_act), after=_active_repr(n_act),
                        note="active flag unextractable on one side — "
                             "refusing to infer an activation change"))
            elif p_act != n_act:
                events.append(DriftEvent(
                    seq=seq, at=at, rule=name, kind=ACTIVATION,
                    before=_active_repr(p_act), after=_active_repr(n_act)))

            p_f, n_f = vr_formula_text(pa), vr_formula_text(na)
            if p_f is None or n_f is None:
                if p_f != n_f:
                    events.append(DriftEvent(
                        seq=seq, at=at, rule=name, kind=UNKNOWN,
                        before=p_f, after=n_f,
                        note="formula unextractable on one side — refusing "
                             "to infer a formula change"))
            elif p_f != n_f:
                note = ""
                if _is_dead_formula(p_f) and not _is_dead_formula(n_f):
                    note = ("NEW ENFORCEMENT APPEARING — the predecessor "
                            "formula is literal `false` (a dead rule); this "
                            "edit arms it. No claim could catch this: a rule "
                            "that never fires can never have a covering "
                            "behavioural claim.")
                elif _is_dead_formula(n_f) and not _is_dead_formula(p_f):
                    note = ("neutralized to a never-firing formula (literal "
                            "`false`) — deactivation in formula clothing")
                events.append(DriftEvent(
                    seq=seq, at=at, rule=name, kind=FORMULA,
                    before=p_f, after=n_f, note=note))

        last = chain[-1]
        if last.get("valid_to_seq") is not None:
            # The chain ends closed with no successor — gone from capture.
            seq = last["valid_to_seq"]
            events.append(DriftEvent(
                seq=seq, at=times.get(seq), rule=name, kind=LIFECYCLE,
                before=_state_repr(last.get("attributes") or {}), after=None,
                note="disappeared from capture (org deletion or "
                     "capture-scope change — a version diff cannot "
                     "distinguish them)"))

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
    times = {
        r["version_seq"]: r["created_at"].isoformat()
        for r in conn.execute(text(
            "SELECT version_seq, created_at FROM logical_versions "
            "WHERE connected_org_id = CAST(:org AS uuid)"),
            {"org": connected_org_id}).mappings()
        if r["created_at"] is not None
    }
    events = diff_vr_chains(rows, times)
    if since_seq is not None:
        events = [e for e in events if e.seq >= since_seq]
    return events
