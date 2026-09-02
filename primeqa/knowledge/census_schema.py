"""The census pin — what the worker CAPTURES for custom-rule evaluation
(LLD_PHASE5_AUTHORING §h; D-471 hard invariant).

The census is an OBSERVATION: a bounded, normalised property bag per
semantic node, captured by the browser worker without ever being told
what any rule says. Evaluation, applicability and verdicts stay in S6
(D-460 — that boundary is about who decides, not what is recorded).

Everything here is DATA handed to the worker through the manifest
payload, exactly as ``engine_run_set`` is: the worker never imports this
module (it cannot read S5/knowledge); the manifest builder resolves the
pin block here and the worker receives it as JSON. Two runs therefore
agree on WHAT was captured, not only on what was found.

``CENSUS_SCHEMA_VERSION`` moves ONLY when the capture shape changes
(properties added, scope changed, normalisation redefined). A rule
declares the schema version it requires; an older census decides
NOT_DETERMINED(census_unattested), never a guess (LLD §h).
"""
from __future__ import annotations

CENSUS_SCHEMA_VERSION = 1

# The pinned CLOSED property list (§e.3: the element's OWN resolved value
# for a manifest-pinned closed property list; no cascade origin, no
# compositing, no :focus-* state). Values are stored RAW as the browser
# returns them; normalisation is the evaluator's versioned, specified
# concern (cust_evaluation) so the census stays evidence.
PROPERTY_ALLOWLIST = [
    "color", "background-color",
    "font-size", "font-family", "font-weight", "line-height",
    "letter-spacing", "text-decoration-line",
    "outline-width", "outline-style",
    "border-radius", "display", "visibility", "opacity",
]

# The closed attribute allowlist (§e.2 has_attribute / §e.4 present-absent
# and equals forms). Presence always; the RAW value string (capped) so
# equals/not_equals/member_of have a witness. Never id, never class.
ATTRIBUTE_ALLOWLIST = [
    "alt", "href", "title", "lang", "tabindex", "type", "for",
    "placeholder", "required", "disabled", "autocomplete", "target",
    "role", "aria-label", "aria-labelledby", "aria-describedby",
    "aria-hidden", "aria-expanded", "aria-controls", "aria-live",
    "aria-required", "aria-current", "aria-haspopup",
]

# Bounded capture: the cap is RECORDED when hit, and a hit census can
# suppress nothing (the D-471 hard invariant reads this).
NODE_CAP = 1500

# Length comparison epsilon in px (browsers return 13.9993px; exact
# match would manufacture reds). Part of the schema: changing it is a
# schema-version change.
LENGTH_EPSILON_PX = 0.5


def census_pins() -> dict:
    """The manifest pin block (ui_manifest copies this into
    payload.pins.census; the worker passes it to the scan as data)."""
    return {
        "schema_version": CENSUS_SCHEMA_VERSION,
        "property_allowlist": list(PROPERTY_ALLOWLIST),
        "attribute_allowlist": list(ATTRIBUTE_ALLOWLIST),
        "node_cap": NODE_CAP,
        "length_epsilon_px": LENGTH_EPSILON_PX,
    }
