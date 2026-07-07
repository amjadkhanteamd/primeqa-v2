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


def suggest_repairs(verdict, cause_kind=None, vr_name=None,
                    failure_category=None) -> list:
    """Deterministic repair suggestions for one interpreted run.

    Input is the S6 vocabulary (``Verdict`` + optional ``Cause``); output is an
    ordered list of ``{owner, title, detail, human_gated}`` suggestions —
    empty for passing verdicts (nothing to repair).

    ``failure_category`` (the run's typed error class, D-261) forks the
    ``not_evaluated`` copy: a deterministic setup rejection or an our-side
    build defect must NOT suggest a blind re-run. Default ``None`` keeps every
    existing caller on the ops/re-run copy.
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
        if cause_kind == "before_save_automation_overwrote":
            # D-241: a before-save Flow ran on insert and rewrote the posted
            # value. Org-owned (intended behavior or a real bug), not a recipe
            # defect — the auto-fix agent treats it as a finding (not recipe_edit).
            return [
                _s("org", "A before-save Flow overwrote the value",
                   "An active before-save Flow on the object runs before insert "
                   "and rewrote the posted value, so it never persisted as "
                   "asserted. If that is intended behavior, update the claim's "
                   "expected value to the Flow's result; otherwise fix the Flow."),
                _s("claim", "Or assert the Flow's result",
                   "If the before-save transformation is correct business "
                   "behavior, regenerate or edit the claim to assert the "
                   "transformed value."),
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
        # Primitive-agnostic wording: the grounding automation can be a Flow,
        # an approval process (D-308), a formula (D-304), … — never assert
        # "the Flow ran" for a mechanism the evidence can't see running.
        by_cause = {
            "automation_inactive": [
                _s("org", "Re-activate the grounding automation",
                   "The asserted effect did not materialize because the "
                   "grounding automation no longer fires for this record — "
                   "deactivated, removed, or retargeted. Re-activate it in "
                   "the org, or deprecate the claim if the automation is "
                   "intentionally gone."),
            ],
            "automation_effect_absent": [
                _s("recipe", "The automation's effect didn't land",
                   "The asserted effect was not observed — the test's payload "
                   "may not meet the automation's entry conditions, or its "
                   "logic changed since generation. Enrich the payload to "
                   "satisfy the entry conditions, or regenerate the test from "
                   "the automation's current behavior."),
            ],
        }
        return by_cause.get(cause_kind, [
            _s("org", "Asserted automation effect not observed — investigate",
               "The org did not produce the asserted automation/state effect "
               "and no deeper cause was attributed. Inspect the run evidence and "
               "the automations that fire on the record."),
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
        if failure_category == "setup_rejection":
            # A deterministic org business rejection of the SETUP data (a rule
            # that names no fields gated the staging/padding write) — a plain
            # re-run fails identically, so never suggest one.
            return [
                _s("recipe", "The org rejected the test's setup data — adjust it",
                   "A business rule that names no fields blocked the test's "
                   "staging create, so the value under test was never reached. "
                   "Adjust the test's staged values or padding (e.g. a "
                   "dispatch-time field override), or regenerate the test; a "
                   "plain re-run will fail the same way."),
                _s("org", "Make the rejecting rule name its fields",
                   "The rule's error carries no field attribution, which is why "
                   "the rejection cannot be ascribed to the value under test. "
                   "Assign the validation rule's error to a specific field so "
                   "rejections become attributable."),
            ]
        if failure_category == "normalization":
            # An our-side malformed / un-buildable test — needs the test fixed,
            # not a blind retry (D-272 §2.4 permanent class).
            return [
                _s("recipe", "The test could not be built or run — fix it",
                   "The run errored on an our-side defect (a malformed request "
                   "or an unbuildable world). Re-running as-is repeats it — "
                   "regenerate the test or repair its recipe."),
            ]
        return [
            _s("ops", "Run errored before evaluation — re-run",
               "The run could not be evaluated (credentials, connectivity, or "
               "an unfillable world). Check the run's error surface, fix the "
               "operational cause, and re-queue the claim."),
        ]

    return []
