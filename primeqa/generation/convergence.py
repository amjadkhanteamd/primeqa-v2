"""Generation Convergence — the read-only funnel, taxonomy, and classifier.

The convergence pipeline, per proposed intent (the unit of analysis):

    proposal ──(Layer A: lexical ref resolution, B0 offers)──▶ admitted?
             ──(Layer 1 admission)──▶ resolution tail (grounding + witnesses)
             ──▶ emission bundle ──▶ persisted claim ──▶ executed claim

This module is PURE — no DB, no LLM, no product side effects. It owns:

- :class:`IntentSnapshot` — the normalized view of one proposed intent
  (extracted from a persisted propose-turn ``raw_parameters`` payload);
- the **failure taxonomy** (:data:`TAXONOMY`) — every failed path belongs to
  exactly one category, assigned by :func:`classify`;
- the **counterfactual variant generators** (:func:`variants_for`) — the
  minimal deterministic transformations of a failed proposal whose replay
  outcome MEASURES the distance from convergence (a value-drop that
  converges ⇒ the failure was value shape; a kind-swap that converges ⇒
  claim-kind misframe; following the substrate's own top recovery offer ⇒
  lexical, and its success rate IS recovery precision).

The DB-facing extractor/replayer lives in ``scripts/convergence_replay.py``
(operational harness). Telemetry-only discipline (the D-361 pattern):
nothing in the product reads these classifications — the classifier exists
to rank engineering work, never to steer a live generation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Taxonomy — every failed path belongs to exactly ONE of these.
# ---------------------------------------------------------------------------

TAXONOMY: dict = {
    "CONVERGED": "the proposal grounded as proposed (no failure)",
    "MODEL_SELF_REFUSAL":
        "the model declared no_admissible_test (honest self-refusal; "
        "correctness judged separately against the capability map)",
    "LEXICAL_SUBJECT":
        "the subject reference did not resolve (Layer A); B0 recovery "
        "offers apply — `recovered` says whether following the top offer "
        "converges",
    "LEXICAL_FIELD":
        "a field reference did not resolve (resolution-time miss with a "
        "Field candidates offer); B0.2 recovery applies",
    "VALUE_SHAPE":
        "refused as proposed, but the VALUE-DROPPED variant converges — "
        "the org defines the value (canonical form / relative date / "
        "classification arm) and the model invented or misplaced one",
    "KIND_MISFRAME":
        "refused as proposed, but the same facts re-framed as an "
        "automation-effect claim converge — the claim kind, not the "
        "content, blocked convergence",
    "ADMISSION_DISMISSAL":
        "dismissed at Layer-1 admission (insufficient grounding) and no "
        "bounded variant converges",
    "GROUNDING_WITNESS":
        "reached the resolution tail; the substrate could not derive a "
        "witness (scale unreadable, empty band, out-of-grammar guard, "
        "unsynthesizable format)",
    "GROUNDING_AMBIGUITY":
        "reached the tail; >1 verifiable producers — attribution refused "
        "by count (honest)",
    "NO_PRODUCER":
        "same-record effect no automation verifiably produces (either "
        "model fiction or an org gap) — honest refusal with arm "
        "disclosure where a ladder exists",
    "CAPABILITY_LIMIT_CROSS_OBJECT":
        "cross-object effect whose producer family is deliberately "
        "unbuilt (FL04/05/07/... class) — honest refusal by design",
    "EMISSION_GAP":
        "groundable but the emission for this claim_kind sub-shape is "
        "not built (substrate gap, not model failure)",
    "NEGATIVE_UNDERIVABLE":
        "a negative control whose violation the substrate cannot derive "
        "from the org's own constraints (the VR negative-derivation "
        "boundary) — honest refusal by design",
    "GROUNDING_OTHER":
        "a named tail refusal outside the buckets above (audit the note)",
    "MODEL_ABANDONMENT":
        "outcome-level: the AC was requested on the recovery hop and the "
        "model proposed nothing for it (computed per AC, not per intent)",
}

# Failed-path categories that indicate SUBSTRATE work vs MODEL-side gap vs
# honest limits — used by the ranking report, never by the product.
SUBSTRATE_SIDE = {"EMISSION_GAP", "GROUNDING_WITNESS", "ADMISSION_DISMISSAL"}
MODEL_SIDE = {"LEXICAL_SUBJECT", "LEXICAL_FIELD", "VALUE_SHAPE",
              "KIND_MISFRAME", "MODEL_ABANDONMENT", "MODEL_SELF_REFUSAL"}
HONEST_LIMIT = {"CAPABILITY_LIMIT_CROSS_OBJECT", "NO_PRODUCER",
                "GROUNDING_AMBIGUITY", "NEGATIVE_UNDERIVABLE"}


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------

@dataclass
class IntentSnapshot:
    """The normalized, analysis-facing view of ONE proposed intent."""
    ac_ref: Optional[int]
    claim_kind: Optional[str]
    archetype: Optional[str]
    polarity: Optional[str]
    subject_type: Optional[str]
    subject_api: Optional[str]
    field_name: Optional[str]
    expected_value: Any
    has_expected_value: bool
    automation_name: Optional[str]
    effect_object: Optional[str]
    has_trigger_fields: bool
    has_update_trigger_fields: bool
    no_admissible_test: bool
    descriptor: dict = field(repr=False, default_factory=dict)


def snapshot(descriptor: dict) -> IntentSnapshot:
    """Extract the snapshot from one persisted intent descriptor (the
    ``intent_descriptors[i]`` element of a propose turn's raw_parameters —
    or the legacy singular ``intent_descriptor``). Tolerant of both shapes."""
    d = descriptor.get("intent_descriptor") or descriptor
    h = d.get("target_subject_hint") or {}
    ev = h.get("expected_value")
    r = d.get("ac_ref")
    ac = r if isinstance(r, int) and not isinstance(r, bool) and r > 0 else None
    return IntentSnapshot(
        ac_ref=ac,
        claim_kind=d.get("claim_kind_hint"),
        archetype=d.get("archetype_hint"),
        polarity=d.get("polarity_hint"),
        subject_type=h.get("entity_type"),
        subject_api=h.get("sf_api_name"),
        field_name=h.get("field_name"),
        expected_value=ev,
        has_expected_value=ev is not None,
        automation_name=h.get("automation_name"),
        effect_object=h.get("effect_object"),
        has_trigger_fields=bool(h.get("trigger_fields")),
        has_update_trigger_fields=bool(h.get("update_trigger_fields")),
        no_admissible_test=bool(d.get("no_admissible_test")),
        descriptor=d,
    )


@dataclass
class ReplayResult:
    """One deterministic replay of one intent (or variant) against the
    CURRENT substrate at a pinned S1 version."""
    stage: str                      # 'layer_a' | 'resolved' | 'error'
    grounded_n: int = 0
    grounded_values: tuple = ()
    refusal_kind: Optional[str] = None
    detail: Optional[str] = None
    detail_layer: Optional[str] = None
    offer_entity_type: Optional[str] = None
    offer_top: Optional[str] = None
    offer_proposed: Optional[str] = None
    error: Optional[str] = None

    @property
    def converged(self) -> bool:
        return self.stage == "resolved" and self.grounded_n > 0 \
            and self.refusal_kind is None


# ---------------------------------------------------------------------------
# Counterfactual variants — the minimal transformations whose replay outcome
# measures the distance from convergence. Bounded and deterministic.
# ---------------------------------------------------------------------------

# claim kinds whose facts (subject + field [+ value]) can be re-framed as an
# automation-effect — the kinds observed live carrying org-written fields
_KIND_SWAPPABLE = frozenset({"state-transition-claim", "value-claim"})


def _swap_ref(obj, old: str, new: str):
    """Deep copy of a descriptor with every EXACT string occurrence of the
    missed reference replaced by the offered one — covers field_name hints
    AND clause-embedded field references (acceptance/rejection conditions)."""
    if isinstance(obj, dict):
        return {k: _swap_ref(v, old, new) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_swap_ref(v, old, new) for v in obj]
    if isinstance(obj, str) and obj == old:
        return new
    return obj


def variants_for(snap: IntentSnapshot,
                 as_proposed: ReplayResult) -> list[tuple[str, dict]]:
    """The bounded counterfactual set for a FAILED proposal, in probe order.
    Each entry is ``(variant_name, descriptor)``. Rules:

    - ``offer`` — Layer-A subject miss (or a Field-candidates refusal) with
      a top offer: swap the missed reference for the substrate's own #1
      candidate. Its convergence rate IS recovery precision.
    - ``value_drop`` — an automation-effect with an expected_value: drop it
      (the org-defines-the-value class: transform/temporal/ladder).
    - ``kind_swap`` — state-transition/value-claim on a field: re-frame as
      automation-effect keeping subject/field/value.
    - ``kind_swap+value_drop`` — the composition (the live SLA shape:
      state-transition with an invented date on an org-computed field).

    Never more than 4 probes per failed intent; each is a pure dict
    transformation replayed through the SAME production entry points."""
    out: list[tuple[str, dict]] = []
    d = snap.descriptor

    def _clone(**hint_updates) -> dict:
        nd = {k: v for k, v in d.items() if k != "target_subject_hint"}
        h = dict(d.get("target_subject_hint") or {})
        for k, v in hint_updates.items():
            if v is None:
                h.pop(k, None)
            else:
                h[k] = v
        nd["target_subject_hint"] = h
        return nd

    # offer-follow (lexical recovery precision). For clause-carrying kinds
    # (acceptance/prohibition) the missed field lives INSIDE the condition
    # lists, so the swap is a deep exact-string replacement of the offer's
    # `proposed` name — the same reference the model would fix.
    if as_proposed.offer_top:
        if as_proposed.stage == "layer_a" \
                or as_proposed.offer_entity_type == "Object":
            out.append(("offer", _clone(sf_api_name=as_proposed.offer_top)))
        elif as_proposed.offer_entity_type == "Field":
            if as_proposed.offer_proposed:
                out.append(("offer", _swap_ref(
                    d, as_proposed.offer_proposed, as_proposed.offer_top)))
            else:
                out.append(("offer", _clone(field_name=as_proposed.offer_top)))
            if snap.has_expected_value \
                    and snap.claim_kind == "automation-effect-claim":
                base = (_swap_ref(d, as_proposed.offer_proposed,
                                  as_proposed.offer_top)
                        if as_proposed.offer_proposed
                        else _clone(field_name=as_proposed.offer_top))
                h = dict(base.get("target_subject_hint") or {})
                h.pop("expected_value", None)
                base = {k: v for k, v in base.items()
                        if k != "target_subject_hint"}
                base["target_subject_hint"] = h
                out.append(("offer+value_drop", base))

    if snap.claim_kind == "automation-effect-claim" and snap.has_expected_value:
        out.append(("value_drop", _clone(expected_value=None)))

    if snap.claim_kind in _KIND_SWAPPABLE and snap.field_name:
        base = {k: v for k, v in d.items() if k != "claim_kind_hint"}
        base["claim_kind_hint"] = "automation-effect-claim"
        nd = dict(base)
        out.append(("kind_swap", nd))
        if snap.has_expected_value:
            h = dict(nd.get("target_subject_hint") or {})
            h.pop("expected_value", None)
            nd2 = {k: v for k, v in nd.items() if k != "target_subject_hint"}
            nd2["target_subject_hint"] = h
            out.append(("kind_swap+value_drop", nd2))
    return out


# ---------------------------------------------------------------------------
# The classifier — exactly one category per failed path.
# ---------------------------------------------------------------------------

# structured-first bucketing of tail refusal details; substring matching is
# confined HERE, documented, and only refines within an already-structured
# branch (never crosses model/substrate/honest boundaries on strings alone)
_WITNESS_MARKERS = ("scale is not readable", "guard interval", "witness",
                    "synthesis grammar", "format pattern", "not stable "
                    "under the transform", "alternative active picklist",
                    # live req-320 test-run fixes (wrong-red generators
                    # closed): staging/derivation refusals, honestly named
                    "calculated/not writable",
                    "fires on UPDATE only")
_AMBIGUITY_MARKERS = ("cannot attribute", "— name the specific automation",
                      "produce the claimed effect (",
                      # the cross-object ambiguity now speaks FIELDS, not
                      # automation names (the D-318/B0 law — see the
                      # discriminator disclosure in governance_core): keep it
                      # classified as AMBIGUITY, not GROUNDING_OTHER
                      "they are told apart by",
                      "roll an aggregate up onto this field",
                      "conditionally write")
_EMISSION_GAP_MARKERS = ("is not yet built", "emission is not built",
                         "is not built")


def classify(snap: IntentSnapshot, as_proposed: ReplayResult,
             variant_results: dict) -> tuple[str, str]:
    """Assign exactly one taxonomy code to one replayed intent.

    Precedence (first match wins — smallest distance from convergence):
      self-refusal → converged → lexical (offer-follow measured) →
      value_shape → kind_misframe → structured tail buckets.
    Returns ``(code, note)`` — the note carries the audit trail (which
    variant converged / the refusal detail head)."""
    if snap.no_admissible_test:
        return ("MODEL_SELF_REFUSAL", "declared no_admissible_test")
    if as_proposed.converged:
        return ("CONVERGED", f"grounded_n={as_proposed.grounded_n}")

    def _v(name: str) -> Optional[ReplayResult]:
        return variant_results.get(name)

    # lexical classes — the offer-follow variant records recovery precision
    if as_proposed.stage == "layer_a":
        ofr = _v("offer")
        rec = "recovered" if (ofr is not None and ofr.converged) else \
              ("offer_present_not_convergent" if as_proposed.offer_top
               else "no_offer")
        return ("LEXICAL_SUBJECT", rec)
    if as_proposed.offer_entity_type == "Field":
        ofr = _v("offer")
        rec = "recovered" if (ofr is not None and ofr.converged) else \
              "offer_present_not_convergent"
        return ("LEXICAL_FIELD", rec)

    detail = (as_proposed.detail or "")
    kind = (as_proposed.refusal_kind or "")
    # EMISSION_GAP outranks variant rescues: a kind-swap may ground *a*
    # claim while altering the AC's MEANING (live case: a value-claim
    # asserting a user-chosen value persists, swapped to automation-effect,
    # grounds the DEFAULT-writing flow instead — true but not the AC). The
    # unbuilt emission is the honest class; the swap result rides the note.
    if any(m in detail for m in _EMISSION_GAP_MARKERS):
        swaps = [n for n in ("kind_swap", "kind_swap+value_drop")
                 if (_v(n) is not None and _v(n).converged)]
        note = f"{snap.claim_kind}: {detail[:80]}"
        if swaps:
            note += f" [meaning-altering {swaps[0]} would ground]"
        return ("EMISSION_GAP", note)

    # distance-one variants
    vd = _v("value_drop")
    if vd is not None and vd.converged:
        return ("VALUE_SHAPE",
                f"value_drop grounds {vd.grounded_n} "
                f"(dropped {snap.expected_value!r})")
    for name in ("kind_swap", "kind_swap+value_drop"):
        kv = _v(name)
        if kv is not None and kv.converged:
            return ("KIND_MISFRAME",
                    f"{name} grounds {kv.grounded_n} "
                    f"(was {snap.claim_kind})")
    if "cross-object effect" in detail or (
            snap.effect_object and "produces the claimed" in detail):
        return ("CAPABILITY_LIMIT_CROSS_OBJECT",
                f"effect_object={snap.effect_object!r}")
    if any(m in detail for m in _WITNESS_MARKERS):
        return ("GROUNDING_WITNESS", detail[:100])
    # Completion review: with the cross-object ambiguity now broken by the
    # effect field/value, intents reach the NEXT real problem — a lexical
    # miss on the effect ENDPOINT (the model guessing 'Order__c' for
    # 'PLS_FB_Order__c'). It is a model-side field miss like any other; it
    # simply carries no B0 offer yet (the offer-keyed branch above cannot
    # see it), so it must be named here rather than sink into
    # GROUNDING_OTHER. NOTE (follow-up, not folded in): attaching recovery
    # offers to this refusal would let these converge.
    if "cannot correlate the effect record" in detail \
            or "cannot link the trigger record" in detail:
        return ("LEXICAL_FIELD", f"effect_endpoint_no_offer: {detail[:70]}")
    if any(m in detail for m in _AMBIGUITY_MARKERS):
        if "no Flow on the subject produces" in detail \
                or "nor does any Flow" in detail:
            return ("NO_PRODUCER", detail[:100])
        return ("GROUNDING_AMBIGUITY", detail[:100])
    if "no Flow on the subject produces" in detail \
            or "no transform, relative-date, or classification producer" \
            in detail:
        return ("NO_PRODUCER", detail[:100])
    if kind == "no-admissible-negative-scenario-found":
        return ("NEGATIVE_UNDERIVABLE", detail[:100] or "negative boundary")
    if kind in ("ungrounded-claim", "UNGROUNDED_CLAIM"):
        return ("ADMISSION_DISMISSAL", detail[:100] or "layer-1 dismissal")
    return ("GROUNDING_OTHER", f"[{kind}] {detail[:100]}")


# ---------------------------------------------------------------------------
# Outcome-level funnel (from persisted attempted_interpretation)
# ---------------------------------------------------------------------------

def outcome_funnel(ai: dict, claims_written: list,
                   equivalent_existing: list) -> dict:
    """The per-outcome funnel summary from the PERSISTED record (as-run —
    no replay): declared ACs, covered/refused per reason class, recovery-hop
    effectiveness, abandonment. Pure over the stored JSONB shapes."""
    ai = ai or {}
    cov = ai.get("coverage_map") or {}
    rec = ai.get("coverage_recovery") or {}
    reasons: dict = {}
    for v in cov.values():
        key = (v.get("status"),
               v.get("reason") if v.get("status") == "refused" else None,
               v.get("reason_source"))
        reasons[key] = reasons.get(key, 0) + 1
    requested = set(rec.get("requested_refs") or [])
    newly = set(rec.get("recovery_newly_covered") or [])
    newly_refused = set(rec.get("recovery_newly_refused") or [])
    abandoned = sorted(requested - newly - newly_refused - {
        int(k) for k, v in cov.items()
        if v.get("status") == "covered" and k.isdigit()})
    return {
        "declared": len(cov),
        "covered": sum(1 for v in cov.values() if v.get("status") == "covered"),
        "refused_model": sum(
            1 for v in cov.values()
            if v.get("status") == "refused"
            and v.get("reason_source") == "model"),
        "refused_substrate": sum(
            1 for v in cov.values()
            if v.get("status") == "refused"
            and v.get("reason_source") != "model"),
        "claims_written": len(claims_written or []),
        "equivalent_existing": len(equivalent_existing or []),
        "recovery_requested": len(requested),
        "recovery_newly_covered": len(newly),
        "recovery_abandoned_acs": abandoned,
        "recovery_zero_progress": bool(rec.get("recovery_zero_progress")),
    }
