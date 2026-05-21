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
_REFUSAL_KINDS = [e.value for e in RefusalKind]          # 8 values
_DISMISSAL_REASONS = [e.value for e in DismissalReason]  # 8 values
_ADMISSIBILITY_LAYERS = [e.value for e in AdmissibilityLayer]  # layer_1 | layer_2

# Substrate-2 vocabularies — verbatim from S2's ARCHETYPE_ENUM / CLAIM_KIND_ENUM
# (test_representation/models_db.py). The archetype value is the underscore
# form `data_behavior` (S2's actual enum value; D-095.A). Drift-guarded by a
# test that imports S2's enums (kept out of this runtime module to avoid
# importing S2's SQLAlchemy models at tool-surface import time).
_ARCHETYPES = ["data_behavior", "configuration", "permission", "ui", "integration"]
_CLAIM_KINDS = [
    # data_behavior (4)
    "value-claim", "state-transition-claim", "automation-effect-claim",
    "prohibition-claim",
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


# ---------------------------------------------------------------------------
# Tool schemas (Anthropic format: name / description / input_schema)
# ---------------------------------------------------------------------------

PROPOSE_SEMANTIC_INTENT_SCHEMA = {
    "name": TOOL_PROPOSE,
    "description": (
        "Propose what the requirement implies semantically. The substrate "
        "derives candidates, computes admissibility, and replies with "
        "admissibly-grounded candidates (or routes to refusal). You do not "
        "author admissibility."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["requirement_excerpt", "intent_descriptor"],
        "properties": {
            "requirement_excerpt": {
                "type": "string",
                "description": (
                    "Verbatim excerpt from the requirement text supporting "
                    "this intent (Guardrail-3 anchor; mandatory)."
                ),
            },
            "intent_descriptor": {
                "type": "object",
                "additionalProperties": False,
                "required": ["archetype_hint", "target_subject_hint", "polarity_hint"],
                "properties": {
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
                },
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


def _validate_propose(inp: dict) -> LayerAResult:
    errors: list[str] = []
    excerpt = inp.get("requirement_excerpt")
    # Guardrail-3 syntactic precondition: excerpt present + non-empty.
    if not _is_str(excerpt) or not excerpt.strip():
        errors.append("requirement_excerpt is required and must be a non-empty string (Guardrail-3 anchor)")
    desc = inp.get("intent_descriptor")
    if not isinstance(desc, dict):
        errors.append("intent_descriptor is required and must be an object")
        return LayerAResult(not errors, errors)
    arch = desc.get("archetype_hint")
    if arch not in _ARCHETYPES:
        errors.append(f"intent_descriptor.archetype_hint must be one of {_ARCHETYPES}")
    if not isinstance(desc.get("target_subject_hint"), dict):
        errors.append("intent_descriptor.target_subject_hint is required and must be an object")
    pol = desc.get("polarity_hint")
    if pol not in _POLARITY:
        errors.append(f"intent_descriptor.polarity_hint must be one of {_POLARITY}")
    ck = desc.get("claim_kind_hint")
    if ck is not None and ck not in _CLAIM_KINDS:
        errors.append(f"intent_descriptor.claim_kind_hint, if present, must be one of {_CLAIM_KINDS}")
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
