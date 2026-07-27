"""Picklist value capture for STANDARD fields — inline-always (D-403).

Supersedes the D-118 StandardValueSet content-match, which this module replaces
outright (``standard_value_set_match.py`` is deleted, not deprecated).

**What D-118 did.** No Salesforce API exposes a *standard* picklist field's
StandardValueSet linkage (corrections-log §22), so S1 inferred it by
content-matching: a standard field's active describe values were linked to the
one synced StandardValueSet whose active values were *exactly equal*, and the
match was **fail-closed** — 0 matches, >1 match (identical-value SVSes), or an
empty set all produced no link. The intent was sound: a *false* link means wrong
accepted-values, which means a wrong test.

**Why it was wrong.** Fail-closed was applied to the wrong thing. Failing to
identify which SHARED value set a field uses is not the same as not knowing the
field's VALUES — and the values were never missing: they sit in the REST
describe payload ``phase_field`` already holds. D-118 discarded them on every
non-match. Measured on env-59: **275 of 377** picklist/multipicklist fields
carried a NULL ``field_details.picklist_value_set_entity_id`` (275 standard, 0
custom — the custom paths were already complete), i.e. 73% of the picklist
surface held no values at all. Worse, that NULL was *indistinguishable* from
"this field genuinely has no value set", so no consumer could tell a real
absence from a capture failure — which is what made value-membership validation
(D-399) unsafe to build: refusing a claim on an uncaptured field would invert
fail-loud into a silent FALSE REFUSAL.

Two representative losses, both from real approved claims: ``Case.Priority``
(``{High, Low, Medium}``) matched **10** SVSes with identical value sets →
ambiguous → dropped; ``Opportunity.ForecastCategory`` matched none at all.

**What replaces it.** Every standard picklist field's values are captured
INLINE, from the describe payload, onto a field-local ``INLINE:{Object}.{Field}``
anchor — the same mechanism D-334 already uses for custom inline picklists, and
no new Salesforce call. Cross-field value-set *identity* is given up
deliberately; the Phase 0 audit found nothing depends on it (every consumer of
``picklist_value_set_entity_id`` is a per-field forward lookup — there is no
reverse set→fields query anywhere in ``primeqa/``), sharing covered only 5 of 93
sets, and de-sharing costs ~95 extra value rows.

Dropping the shared anchor also removes two live defect classes:

  * **Silent link-drop.** D-118's own docstring called fail-closed a self-heal:
    edit a field's values and "the match fails and the edge correctly drops".
    Dropping the edge drops the accepted-values grounding — silently. There is
    no such transition here; capture cannot fail without saying so.
  * **Child-row orphaning.** An SVS anchor's payload embeds its values, so it
    re-versions whenever the org edits the set — and any child value whose own
    payload is byte-identical is not re-versioned, stranding it on the
    superseded parent where the current-version reads cannot see it. This is
    live on env-59: ``Opportunity.StageName`` reads **10** of the org's **12**
    stages (``Prospecting``/``Qualification`` orphaned at seq 99, when
    ``Credit Assessment`` and ``Approved`` were inserted mid-list and shifted
    every later value's sort order). The inline anchor payload is identity-only
    — it never churns on value edits — so parents do not re-version and children
    cannot be orphaned.
"""
from __future__ import annotations

from typing import Any


# Cap on values captured per standard picklist field.
#
# Salesforce ships platform ENUMERATIONS down the same picklist channel as
# business vocabularies, and every captured value becomes an embedded S1 entity.
# On env-59 the 275 uncaptured fields hold 19 724 values — but one field
# (``PromptVersion.TargetPageKey1Ref``, 10 862 page keys) is 55% of that, and the
# rest of the tail is timezone (424×8), locale (279×3) and sObject-name lists.
# Capturing it verbatim would add 19 724 entities + embeddings to a 6 355-entity
# org, essentially none of it assertable by a test.
#
# 200 captures the business surface whole: across every object the approved
# claim corpus exercises, the largest picklist other than a timezone list is 12
# values. 250 of the 267 value-carrying fields land under the cap; the 17 above
# it are all platform enumerations, and each is recorded as ``inline_truncated``
# so a consumer reads "subset, not authoritative" rather than guessing.
#
# Truncation is SAFE in both directions that matter: it only ever removes
# values, so S4's k16 padding can still only choose a value the org accepts, and
# the D-399 validator downgrades to CANNOT VALIDATE instead of INVALID.
MAX_INLINE_PICKLIST_VALUES = 200


def describe_values_as_metadata(field: dict[str, Any]) -> dict[str, Any]:
    """A REST-describe field's ``picklistValues`` re-keyed into the Metadata
    value shape, so the existing extractor + PicklistValue detail mapper consume
    it verbatim.

    Describe emits ``{value, label, active, defaultValue}``; the Metadata API
    (GVS ``customValue`` / SVS ``standardValue``) emits ``{valueName, label,
    isActive, default}`` — and ``_map_picklist_value_details`` reads the latter.
    Rather than teach the mapper a second dialect, translate at the boundary and
    keep one downstream shape.

    Emitted under the ``"value"`` list key so callers reuse the same
    ``value_list_key="value"`` the custom inline path already passes.

    INACTIVE values are kept, with ``isActive`` riding through — the mapper
    records ``is_active`` and consumers filter. Dropping them here would erase
    the difference between a value the org never had and one it retired, and
    that difference is exactly what lets an audit report "INACTIVE (org drift)"
    instead of the far more serious "ABSENT (hallucination)". Note this is a
    deliberate departure from D-118, which compared active-vs-active because it
    was establishing set *identity*; capture wants the whole set.
    """
    out: list[dict[str, Any]] = []
    for pv in (field.get("picklistValues") or []):
        if not isinstance(pv, dict) or not pv.get("value"):
            continue
        out.append({
            "valueName": pv["value"],
            "label": pv.get("label") or pv["value"],
            # ``is not False`` (the detail-mapper idiom), NOT ``.get(k, True)``:
            # Salesforce sends ``active: null`` for never-deactivated values and
            # a .get default fires only on a MISSING key. D-204.1 burned on
            # exactly this — the null made every value read as inactive.
            "isActive": pv.get("active") is not False,
            "default": bool(pv.get("defaultValue", False)),
        })
    return {"value": out}
