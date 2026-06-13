"""S8 repair suggestions — the substrate's agent opening (product theme #6, D-201).

v1's fix-and-rerun agent proposes LLM fixes and auto-applies on sandbox. The
substrate opens its repair loop **evidence-first**: deterministic, human-gated
repair suggestions derived from the S6 interpretation's closed verdict + cause
vocabulary — no LLM, no auto-apply. Every suggestion names WHO owns the fix
(``org`` — the Salesforce config; ``claim`` — the asserted truth; ``recipe`` —
the execution shape; ``ops`` — credentials/infra) so the human routes it in one
glance. The LLM-proposed recipe rewrite (via the existing ``recipe_s8_rewrite``
provenance path) is the deferred next increment; auto-apply stays out by design
(evidence-first, D-196-era discipline).

Pure module: no DB, no I/O — directly unit-testable over the vocabulary.
"""
from __future__ import annotations


def _s(owner: str, title: str, detail: str) -> dict:
    return {"owner": owner, "title": title, "detail": detail,
            "human_gated": True}


def suggest_repairs(verdict, cause_kind=None, vr_name=None) -> list:
    """Deterministic repair suggestions for one interpreted run.

    Input is the S6 vocabulary (``Verdict`` + optional ``Cause``); output is an
    ordered list of ``{owner, title, detail, human_gated}`` suggestions —
    empty for passing verdicts (nothing to repair).
    """
    vr = vr_name or "the grounding validation rule"

    if verdict in ("prohibition_enforced", "value_persisted",
                   "asserted_metadata_present", "asserted_value_matches",
                   None):
        return []

    if verdict == "prohibition_not_enforced":
        by_cause = {
            "vr_inactive": [
                _s("org", f"Re-activate {vr}",
                   f"The prohibition did not enforce because {vr} is inactive. "
                   "Re-activate it in the org — or if disabling was intentional, "
                   "deprecate this claim so it stops gating releases."),
            ],
            "vr_formula_drift": [
                _s("claim", "Re-align the claim with the edited rule",
                   f"{vr} is active but its current formula no longer rejects "
                   "the asserted violation — the rule was edited. Regenerate "
                   "the claim from the requirement, or update the asserted "
                   "prohibition to match the rule's new intent."),
            ],
            "vr_formula_indeterminate": [
                _s("recipe", "Review the violating payload",
                   f"{vr}'s current formula could not be evaluated against the "
                   "recipe's payload (org-state / unset fields). Enrich the "
                   "payload so the rule is genuinely exercised, or regenerate "
                   "the recipe."),
            ],
            "no_active_vr": [
                _s("org", "Implement or restore the enforcing rule",
                   "No active validation rule enforces this prohibition — it "
                   "was removed or deactivated. Restore/implement the rule, or "
                   "deprecate the claim if the requirement no longer stands."),
            ],
            "enforcement_gap": [
                _s("org", "Org defect — the rule did not block a violation",
                   f"{vr} is active and its formula IS violated by the payload, "
                   "yet the create succeeded. This is a genuine enforcement "
                   "defect in the org (rule order, bypass, or platform issue) — "
                   "not a test problem. Raise it with the org owner."),
            ],
        }
        return by_cause.get(cause_kind, [
            _s("org", "Prohibition not enforced — investigate",
               "The violating create succeeded but no deeper cause was "
               "attributed. Inspect the run evidence and the org's rules."),
        ])

    if verdict == "rejected_unasserted_reason":
        by_cause = {
            "other_vr_fired": [
                _s("claim", f"Point the claim at {vr}",
                   f"A different rule ({vr}) rejected the violation before the "
                   "asserted one. If that rule is the real enforcement, update "
                   "the claim's asserted rejection; otherwise adjust the "
                   "payload so the asserted rule fires first."),
            ],
            "platform_constraint": [
                _s("recipe", "Avoid the platform constraint",
                   "A platform rule (not a validation rule) rejected the create "
                   "before the asserted rule could fire. Adjust the recipe's "
                   "payload so the platform constraint is satisfied and the "
                   "asserted rule is the one actually tested."),
            ],
        }
        return by_cause.get(cause_kind, [
            _s("recipe", "Rejected for an unasserted reason — review",
               "The org rejected the violation, but not with the asserted "
               "error. Review which rule fired and re-point the claim or "
               "payload accordingly."),
        ])

    if verdict == "value_not_persisted":
        if cause_kind == "field_not_createable":
            # D-229: SF silently dropped a non-createable field on insert — not
            # an automation overwrite. Distinct owner + remedy.
            return [
                _s("recipe", "The asserted field is read-only on create",
                   "The field is not createable, so Salesforce dropped the "
                   "posted value on insert and it never persisted. A value-claim "
                   "cannot set a read-only/calculated field — regenerate the "
                   "recipe to set it via the supported path (a writable field or "
                   "an automation), or re-point the claim at a createable field."),
                _s("claim", "Or re-scope the claim",
                   "If the field is computed by the org, assert the computed "
                   "value via the inspection path instead of posting it."),
            ]
        return [
            _s("org", "Check automations that overwrite the field",
               "The record was created but the read-back value differs from "
               "the assertion — a flow/trigger/assignment rule likely rewrote "
               "it. If that automation is intended behavior, update the "
               "claim's expected value; otherwise fix the automation."),
            _s("claim", "Or update the asserted value",
               "If the org's transformed value is correct business behavior, "
               "regenerate or edit the claim to assert it."),
        ]

    if verdict in ("automation_not_triggered", "state_not_transitioned"):
        # D-229: the positive automation/state failures. automation_inactive is
        # the org-owned high-value cause (the grounding Flow was deactivated);
        # automation_effect_absent is recipe/claim-owned (the Flow exists but its
        # effect didn't land — entry condition or edited logic).
        by_cause = {
            "automation_inactive": [
                _s("org", "Re-activate the grounding Flow",
                   "The asserted effect did not materialize because no active "
                   "Flow triggers on the record — the grounding automation was "
                   "deactivated or removed. Re-activate it in the org, or "
                   "deprecate the claim if the automation is intentionally gone."),
            ],
            "automation_effect_absent": [
                _s("recipe", "The Flow ran but the effect didn't land",
                   "An active Flow triggers on the record, yet the asserted "
                   "effect was not observed — an entry condition the recipe's "
                   "payload doesn't meet, or the Flow's logic changed. Enrich "
                   "the payload to satisfy the entry condition, or regenerate "
                   "the claim from the Flow's current behavior."),
            ],
        }
        return by_cause.get(cause_kind, [
            _s("org", "Asserted automation effect not observed — investigate",
               "The org did not produce the asserted automation/state effect "
               "and no deeper cause was attributed. Inspect the run evidence and "
               "the Flows that trigger on the record."),
        ])

    if verdict in ("asserted_metadata_absent", "asserted_value_differs"):
        return [
            _s("org", "Restore the drifted metadata",
               "The asserted metadata is missing or its value differs — the "
               "org drifted under the claim. Restore/deploy the expected "
               "configuration, or accept the new state by updating the claim. "
               "Check the S8 grounding board for the drift timeline."),
        ]

    if verdict == "not_evaluated":
        return [
            _s("ops", "Run errored before evaluation — re-run",
               "The run could not be evaluated (credentials, connectivity, or "
               "an unfillable world). Check the run's error surface, fix the "
               "operational cause, and re-queue the claim."),
        ]

    return []
