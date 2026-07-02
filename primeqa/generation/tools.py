"""Substrate-3 tool surface — the three thin semantic primitives (D-086).

The Anthropic tool schemas the LLM is offered: ``propose_semantic_intent`` /
``select_canonical`` / ``emit_outcome`` (D-086 locked parameter contracts).
Thin by design — they carry the LLM's *proposals*; the substrate holds
governance. Enum positions bind verbatim to the locked vocabularies:

  - Substrate-3 (Slice-0 ``enums.py``): ``OutcomeKind``, ``RefusalKind``,
    ``DismissalReason``, ``AdmissibilityLayer``.
  - Substrate-2: ``archetype`` (``data_behavior`` etc. — the underscore form
    is S2's actual ``ARCHETYPE_ENUM`` value, D-095.A) and ``claim_kind``
    (16 hyphenated values). Mirrored here verbatim; ``tests/unit/generation/
    test_tools.py`` guards against drift from S2's ``models_db`` enums.
  - D-086-defined: ``rationale_kind``, ``polarity_hint``.

These schemas are **Layer A's structural surface** (D-087): the model cannot
emit free-form values at vocabulary positions. ``validate_layer_a`` here is
the grounding-free half of Layer A (schema / enum / structure / Guardrail-3
excerpt presence). The grounding-dependent half (S1 entity-ref existence) is
the spine's ``GovernanceProvider.check_refs_exist`` operational seam (D-095.1),
NOT in this module.

``admissibility_layer`` appears in the ``emit_outcome`` draft payload but is
**substrate-authored** (D-086): Layer A requires its presence + valid value;
Layer B (governance) checks it matches the substrate-presented value. The LLM
transcribes; it never asserts admissibility.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from primeqa.generation.enums import (
    AdmissibilityLayer,
    DismissalReason,
    OutcomeKind,
    RefusalKind,
)


# ---------------------------------------------------------------------------
# Tool names (the three primitives)
# ---------------------------------------------------------------------------

TOOL_PROPOSE = "propose_semantic_intent"
TOOL_SELECT = "select_canonical"
TOOL_EMIT = "emit_outcome"

TOOL_NAMES = (TOOL_PROPOSE, TOOL_SELECT, TOOL_EMIT)


# ---------------------------------------------------------------------------
# Locked vocabularies bound to enum positions
# ---------------------------------------------------------------------------

# Substrate-3 vocabularies — derived from the Slice-0 enums so they cannot
# drift from the locked definitions.
_OUTCOME_KINDS = [e.value for e in OutcomeKind]          # draft | refusal
_REFUSAL_KINDS = [e.value for e in RefusalKind]          # 10 values (D-293 added behaviour-incomplete)
_DISMISSAL_REASONS = [e.value for e in DismissalReason]  # 8 values
_ADMISSIBILITY_LAYERS = [e.value for e in AdmissibilityLayer]  # layer_1 | layer_2

# Substrate-2 vocabularies — verbatim from S2's ARCHETYPE_ENUM / CLAIM_KIND_ENUM
# (test_representation/models_db.py). The archetype value is the underscore
# form `data_behavior` (S2's actual enum value; D-095.A). Drift-guarded by a
# test that imports S2's enums (kept out of this runtime module to avoid
# importing S2's SQLAlchemy models at tool-surface import time).
_ARCHETYPES = ["data_behavior", "configuration", "permission", "ui", "integration"]
_CLAIM_KINDS = [
    # data_behavior (5)
    "value-claim", "state-transition-claim", "automation-effect-claim",
    "prohibition-claim", "acceptance-claim",
    # configuration (3)
    "existence-claim", "property-claim", "metadata-relationship-claim",
    # permission (2)
    "capability-claim", "sharing-rule-claim",
    # ui (3)
    "element-state-claim", "navigation-claim", "layout-claim",
    # integration (4)
    "platform-event-claim", "outbound-message-claim", "callout-claim",
    "inbound-effect-claim",
]

# D-086-defined vocabularies (introduced by the tool contract itself).
_POLARITY = ["positive", "negative"]
_RATIONALE_KINDS = ["highest_specificity", "only_admissible", "other_substrate_authorized"]

# D-207: the multi-intent envelope cap. One propose call carries every distinct
# testable intent the requirement implies; the cap bounds token spend per turn
# (mirrors v1's 3-6 TCs-per-requirement envelope, with headroom).
MAX_INTENTS = 6


# ---------------------------------------------------------------------------
# Tool schemas (Anthropic format: name / description / input_schema)
# ---------------------------------------------------------------------------

# The shared per-intent descriptor fields (used nested in the legacy singular
# form and flat in each D-207 array item).
_DESCRIPTOR_FIELD_PROPS = {
    "archetype_hint": {"type": "string", "enum": _ARCHETYPES},
    "target_subject_hint": {
        "type": "object",
        "description": "An S1 entity reference or a descriptive selector.",
    },
    "polarity_hint": {"type": "string", "enum": _POLARITY},
    "failure_mode_framing": {
        "type": "string",
        "description": "Optional; for negatives, the distinct failure mode implied.",
    },
    "claim_kind_hint": {
        "type": "string",
        "enum": _CLAIM_KINDS,
        "description": "Optional; the substrate may select a different claim_kind if grounding is stronger.",
    },
    # D-247 per-AC coverage: tag the intent with the acceptance-criterion it
    # addresses (1-based index into the top-level acceptance_criteria list), or
    # mark an AC explicitly un-testable instead of inventing a claim for it.
    "ac_ref": {
        "type": "integer",
        "description": (
            "Optional; the 1-based index (into acceptance_criteria) of the AC "
            "this intent addresses, when the requirement is AC-structured."
        ),
    },
    "no_admissible_test": {
        "type": "boolean",
        "description": (
            "Optional; set true (with no_admissible_test_reason, and ac_ref) to "
            "explicitly refuse an AC the org cannot ground a test for — this "
            "ADDRESSES the AC honestly instead of inventing a claim."
        ),
    },
    "no_admissible_test_reason": {
        "type": "string",
        "description": "Required when no_admissible_test is true: why no admissible test exists.",
    },
}

PROPOSE_SEMANTIC_INTENT_SCHEMA = {
    "name": TOOL_PROPOSE,
    "description": (
        "Propose every distinct testable intent the requirement implies "
        "(intent_descriptors — preferred). The substrate derives candidates, "
        "computes admissibility per intent, and replies with admissibly-"
        "grounded candidates (or routes to refusal). You do not author "
        "admissibility."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        # D-207: exactly one of the two forms — enforced at Layer A (JSON
        # Schema oneOf is not expressible in the flat tool surface). The array
        # form is preferred; the singular form remains for pinned-prompt replay.
        "required": [],
        "properties": {
            # D-247: the AC list the model identified in the requirement (the
            # coverage denominator). Each AC must be addressed by >=1 intent
            # (via ac_ref) or an explicit no_admissible_test intent. Omitted for
            # freeform requirements with no enumerable criteria.
            "acceptance_criteria": {
                "type": "array",
                "description": (
                    "The acceptance criteria you identified in the requirement, "
                    "in order. Every AC must be addressed — by an intent tagged "
                    "with its ac_ref, or by a no_admissible_test intent."
                ),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["index", "label"],
                    "properties": {
                        "index": {"type": "integer", "description": "1-based position."},
                        "label": {"type": "string", "description": "Short paraphrase of the AC."},
                    },
                },
            },
            "intent_descriptors": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_INTENTS,
                "description": (
                    "One entry per distinct testable intent the requirement "
                    "implies — the positive behavior, one negative per "
                    "prohibition/condition, configuration checks. Each entry "
                    "carries its own verbatim requirement_excerpt "
                    "(Guardrail-3 anchor)."
                ),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["requirement_excerpt", "archetype_hint",
                                  "target_subject_hint", "polarity_hint"],
                    "properties": {
                        "requirement_excerpt": {
                            "type": "string",
                            "description": (
                                "Verbatim excerpt from the requirement text "
                                "supporting THIS intent (Guardrail-3 anchor; "
                                "mandatory per intent)."
                            ),
                        },
                        **_DESCRIPTOR_FIELD_PROPS,
                    },
                },
            },
            "requirement_excerpt": {
                "type": "string",
                "description": (
                    "Legacy single-intent form: verbatim excerpt from the "
                    "requirement text supporting the intent (Guardrail-3 "
                    "anchor; mandatory with intent_descriptor)."
                ),
            },
            "intent_descriptor": {
                "type": "object",
                "additionalProperties": False,
                "required": ["archetype_hint", "target_subject_hint", "polarity_hint"],
                "description": "Legacy single-intent form (one intent only).",
                "properties": _DESCRIPTOR_FIELD_PROPS,
            },
        },
    },
}

SELECT_CANONICAL_SCHEMA = {
    "name": TOOL_SELECT,
    "description": (
        "Select the canonical candidate when the substrate presented multiple "
        "admissibly-grounded candidates for one failure mode (highest-specificity "
        "discipline)."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["candidate_refs", "selection_rationale"],
        "properties": {
            "candidate_refs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "path_ids of the substrate-presented candidates.",
            },
            "selection_rationale": {
                "type": "object",
                "additionalProperties": False,
                "required": ["selected_path_id", "rationale_kind"],
                "properties": {
                    "selected_path_id": {"type": "string"},
                    "rationale_kind": {"type": "string", "enum": _RATIONALE_KINDS},
                    "dismissed_alternatives_with_reason": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["path_id", "dismissal_reason"],
                            "properties": {
                                "path_id": {"type": "string"},
                                "dismissal_reason": {"type": "string", "enum": _DISMISSAL_REASONS},
                            },
                        },
                    },
                },
            },
        },
    },
}

EMIT_OUTCOME_SCHEMA = {
    "name": TOOL_EMIT,
    "description": (
        "Emit the final outcome for this requirement: a draft (claim + recipe "
        "refs) or a refusal. admissibility_layer is substrate-authored — "
        "transcribe the value the substrate presented; do not assert it."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["outcome_kind", "payload"],
        "properties": {
            "outcome_kind": {"type": "string", "enum": _OUTCOME_KINDS},
            "payload": {
                "type": "object",
                "description": (
                    "draft -> {claim_ref, recipe_ref, admissibility_layer}; "
                    "refusal -> {refusal_kind, refusal_payload}."
                ),
            },
        },
    },
}

# The tool list passed to the model on every turn.
TOOLS = [
    PROPOSE_SEMANTIC_INTENT_SCHEMA,
    SELECT_CANONICAL_SCHEMA,
    EMIT_OUTCOME_SCHEMA,
]

_SCHEMAS_BY_NAME = {s["name"]: s for s in TOOLS}


# ---------------------------------------------------------------------------
# Layer A — grounding-free validation (D-087 / D-095.1)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LayerAResult:
    """Outcome of the grounding-free Layer-A check on one tool call.

    Operational by construction (D-095.1): a failure routes to
    ``rejected_for_correction``; it never becomes a semantic dismissal. The
    ``errors`` are typed-feedback strings serialized into the correction
    ``tool_result``.
    """

    ok: bool
    errors: list[str] = field(default_factory=list)

    @property
    def feedback(self) -> str:
        return "; ".join(self.errors)


def _is_str(v) -> bool:
    return isinstance(v, str)


def validate_layer_a(tool_name: str, tool_input: dict) -> LayerAResult:
    """Validate a tool call's structure + vocabulary (the grounding-free half
    of Layer A). Does NOT touch S1 (ref-existence is ``check_refs_exist``) or
    semantics (Layer B is the governance seam)."""
    if tool_name not in _SCHEMAS_BY_NAME:
        return LayerAResult(False, [f"unknown tool {tool_name!r}"])
    if not isinstance(tool_input, dict):
        return LayerAResult(False, [f"{tool_name}: input must be an object"])

    if tool_name == TOOL_PROPOSE:
        return _validate_propose(tool_input)
    if tool_name == TOOL_SELECT:
        return _validate_select(tool_input)
    return _validate_emit(tool_input)


def normalize_propose_input(inp: dict) -> list[dict]:
    """Normalize a (Layer-A-valid) propose input to a list of per-intent inputs
    in the legacy single shape ``{requirement_excerpt, intent_descriptor}`` —
    the shape every downstream resolver speaks (D-207). The singular legacy
    form normalizes to a one-element list."""
    arr = inp.get("intent_descriptors")
    if isinstance(arr, list) and arr:
        out: list[dict] = []
        for item in arr:
            item = dict(item or {})
            excerpt = item.pop("requirement_excerpt", None)
            out.append({"requirement_excerpt": excerpt, "intent_descriptor": item})
        return out
    return [inp]


def _validate_acceptance_criteria(inp: dict, errors: list[str]) -> None:
    """D-247: the optional model-declared AC list — type-only (``{index:int>0,
    label:str}``). Semantics (every AC addressed) are the runtime coverage
    loop's job, not Layer A's."""
    acs = inp.get("acceptance_criteria")
    if acs is None:
        return
    if not isinstance(acs, list):
        errors.append("acceptance_criteria, if present, must be an array of {index, label}")
        return
    for i, ac in enumerate(acs):
        idx = ac.get("index") if isinstance(ac, dict) else None
        label = ac.get("label") if isinstance(ac, dict) else None
        if not (isinstance(idx, int) and not isinstance(idx, bool) and idx > 0) or not _is_str(label):
            errors.append(f"acceptance_criteria[{i}] must be {{index:int>0, label:str}}")


def _validate_descriptor_fields(desc: dict, errors: list[str], prefix: str) -> None:
    """The shared per-intent descriptor checks (legacy nested form and D-207
    flat array items carry the same fields)."""
    arch = desc.get("archetype_hint")
    if arch not in _ARCHETYPES:
        errors.append(f"{prefix}archetype_hint must be one of {_ARCHETYPES}")
    if not isinstance(desc.get("target_subject_hint"), dict):
        errors.append(f"{prefix}target_subject_hint is required and must be an object")
    pol = desc.get("polarity_hint")
    if pol not in _POLARITY:
        errors.append(f"{prefix}polarity_hint must be one of {_POLARITY}")
    ck = desc.get("claim_kind_hint")
    if ck is not None and ck not in _CLAIM_KINDS:
        errors.append(f"{prefix}claim_kind_hint, if present, must be one of {_CLAIM_KINDS}")
    # D-247 per-AC coverage fields — all optional, type-only (semantics enforced
    # by the runtime coverage loop, not Layer A).
    ac_ref = desc.get("ac_ref")
    if ac_ref is not None and not (isinstance(ac_ref, int) and not isinstance(ac_ref, bool) and ac_ref > 0):
        errors.append(f"{prefix}ac_ref, if present, must be a positive integer")
    nat = desc.get("no_admissible_test")
    if nat is not None and not isinstance(nat, bool):
        errors.append(f"{prefix}no_admissible_test, if present, must be a boolean")
    natr = desc.get("no_admissible_test_reason")
    if natr is not None and not _is_str(natr):
        errors.append(f"{prefix}no_admissible_test_reason, if present, must be a string")


def _validate_propose(inp: dict) -> LayerAResult:
    errors: list[str] = []
    has_array = "intent_descriptors" in inp
    has_single = "intent_descriptor" in inp or "requirement_excerpt" in inp

    # D-207: exactly one of the two forms.
    if has_array and "intent_descriptor" in inp:
        return LayerAResult(False, [
            "provide either intent_descriptors (array) or the legacy "
            "intent_descriptor (single) — not both"])
    if not has_array and not has_single:
        return LayerAResult(False, [
            "intent_descriptors is required: one entry per distinct testable "
            "intent the requirement implies"])

    # D-247: the optional declared AC list (the coverage denominator). Type-only;
    # accumulated into `errors` so it surfaces alongside whichever form is used.
    _validate_acceptance_criteria(inp, errors)

    if has_array:
        arr = inp.get("intent_descriptors")
        if not isinstance(arr, list) or not (1 <= len(arr) <= MAX_INTENTS):
            return LayerAResult(False, [
                f"intent_descriptors must be an array of 1..{MAX_INTENTS} intent objects"])
        for i, item in enumerate(arr):
            prefix = f"intent_descriptors[{i}]."
            if not isinstance(item, dict):
                errors.append(f"intent_descriptors[{i}] must be an object")
                continue
            excerpt = item.get("requirement_excerpt")
            if not _is_str(excerpt) or not excerpt.strip():
                errors.append(f"{prefix}requirement_excerpt is required and must be a "
                              "non-empty string (Guardrail-3 anchor)")
            _validate_descriptor_fields(item, errors, prefix)
        return LayerAResult(not errors, errors)

    # Legacy singular form (pinned-prompt replay; D-207 keeps it accepted).
    excerpt = inp.get("requirement_excerpt")
    # Guardrail-3 syntactic precondition: excerpt present + non-empty.
    if not _is_str(excerpt) or not excerpt.strip():
        errors.append("requirement_excerpt is required and must be a non-empty string (Guardrail-3 anchor)")
    desc = inp.get("intent_descriptor")
    if not isinstance(desc, dict):
        errors.append("intent_descriptor is required and must be an object")
        return LayerAResult(not errors, errors)
    _validate_descriptor_fields(desc, errors, "intent_descriptor.")
    return LayerAResult(not errors, errors)


def _validate_select(inp: dict) -> LayerAResult:
    errors: list[str] = []
    if not isinstance(inp.get("candidate_refs"), list):
        errors.append("candidate_refs is required and must be an array")
    rat = inp.get("selection_rationale")
    if not isinstance(rat, dict):
        errors.append("selection_rationale is required and must be an object")
        return LayerAResult(not errors, errors)
    if not _is_str(rat.get("selected_path_id")):
        errors.append("selection_rationale.selected_path_id is required and must be a string")
    if rat.get("rationale_kind") not in _RATIONALE_KINDS:
        errors.append(f"selection_rationale.rationale_kind must be one of {_RATIONALE_KINDS}")
    for i, alt in enumerate(rat.get("dismissed_alternatives_with_reason") or []):
        if not isinstance(alt, dict) or alt.get("dismissal_reason") not in _DISMISSAL_REASONS:
            errors.append(f"dismissed_alternatives_with_reason[{i}].dismissal_reason must be one of {_DISMISSAL_REASONS}")
    return LayerAResult(not errors, errors)


def _validate_emit(inp: dict) -> LayerAResult:
    errors: list[str] = []
    kind = inp.get("outcome_kind")
    if kind not in _OUTCOME_KINDS:
        errors.append(f"outcome_kind must be one of {_OUTCOME_KINDS}")
    payload = inp.get("payload")
    if not isinstance(payload, dict):
        errors.append("payload is required and must be an object")
        return LayerAResult(not errors, errors)
    if kind == OutcomeKind.DRAFT.value:
        # admissibility_layer is substrate-authored; Layer A requires its
        # presence + a valid value (Layer B checks it matches).
        if payload.get("admissibility_layer") not in _ADMISSIBILITY_LAYERS:
            errors.append(f"draft payload.admissibility_layer must be one of {_ADMISSIBILITY_LAYERS}")
    elif kind == OutcomeKind.REFUSAL.value:
        if payload.get("refusal_kind") not in _REFUSAL_KINDS:
            errors.append(f"refusal payload.refusal_kind must be one of {_REFUSAL_KINDS}")
    return LayerAResult(not errors, errors)
