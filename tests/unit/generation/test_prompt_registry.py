"""Prompt registry slice 1 (D-089 / D-103): the content-hash freeze guard, the
CURRENT resolution, and that the runtime loads the frozen prompt into
``state.system``. No PG — the runtime check drives a Layer-A failure (bad
archetype), so it never reaches grounding (D-103.3: the prompt is a quality
input, never a correctness dependency)."""
from __future__ import annotations

from uuid import uuid4

import pytest

from primeqa.generation.prompts import registry


# ---------------------------------------------------------------------------
# Freeze guard + resolution (D-103.1)
# ---------------------------------------------------------------------------

def test_frozen_content_hash_matches_recorded():
    # Every frozen version's live content still hashes to its recorded SHA — an
    # edit to a frozen version (which would corrupt replay) fails here.
    for v in registry.versions():
        assert registry.content_hash(v) == registry.recorded_hash(v), (
            f"{v}: frozen content drifted from its recorded hash")


def test_working_source_composes_to_current():
    # The working source (base.md + fragments) must compose to EXACTLY the frozen
    # CURRENT version. This is the drift the frozen-hash guard CANNOT catch:
    # content_hash() only catches edits to an already-frozen file, so a passage
    # that silently drifts OUT of the working source — as the D-313 intent-mandatory
    # directive once did (present in frozen v17, absent from working base.md) — would
    # ship DROPPED at the next freeze. Asserting compose_working() == the CURRENT
    # frozen artifact makes that drift fail loudly here, and enforces the freeze
    # ritual (edit fragment + re-freeze are one change). D-318.
    import hashlib
    composed = registry.compose_working()
    assert composed == registry.get(), (
        "working source (base.md + fragments) no longer composes to the frozen "
        "CURRENT — either re-freeze (author the next version) or the working source "
        "has drifted from what shipped")
    assert hashlib.sha256(composed.encode("utf-8")).hexdigest() == registry.recorded_hash()


def test_current_resolves_to_v28():
    # v28 (B1 flow-grounding arc): the sixth division-of-responsibility
    # instance — a saved record SHOWING a value the user did not enter
    # (default / normalization / classification) is an automation effect,
    # never a state-transition or a stage-it-yourself value-claim; plus the
    # field-naming contract (the substrate resolves labels/bare names
    # uniquely and teaches the real vocabulary on a miss). v1..v27 stay
    # frozen + pinned-resolvable.
    assert registry.CURRENT == "generation@v28"
    assert registry.get() == registry.get("generation@v28")
    # v28's shows-value routing + naming contract
    assert "shows Standard once saved" in registry.get()
    assert "Field naming:" in registry.get()
    assert "never stage such a value as a create input" in registry.get()
    # v27: lean != minimal — retained
    assert "lean does NOT mean minimal" in registry.get()
    # v26: the numeric-threshold contract
    assert "numeric threshold has NO predicate" in registry.get()
    # v25: the lock-prohibition contract
    assert "names the LOCK STATE" in registry.get()
    sys = registry.get()
    # v24's feasibility-boundary clarification
    assert "Do not decide technical feasibility" in sys
    assert "threshold, derivability, testability, or executability" in sys
    # v23's proposal-contract clarification — retained
    assert "missing configured value is not a reason to withhold" in sys
    assert "naming the value is the substrate's" in sys
    # v22's approval-action arc (D-333) — retained
    assert "APPROVAL-ACTION ARC" in sys
    assert "approval_actions" in sys and "attempted_change" in sys
    # v20's implied-valid-case decomposition (D-329)
    assert "implied valid case" in sys
    assert "out-of-scope control" in sys
    assert "one happy path per scoped rule-set" in sys
    # v21's cross-field rejection clause (D-330)
    assert '"exceeds"' in sys
    assert "compared_to" in sys
    assert "Never flatten a" in sys
    assert len(sys) > 1000                                  # substantive, not a stub
    # v17's mandatory-intent_descriptors contract (D-313) — the fix directive
    assert "IS the proposal" in sys
    assert "never call this tool with just `acceptance_criteria`" in sys
    # the substance the schemas don't enforce (D-103.3) is present
    assert "bounded cognition provider" in sys
    assert "Transcribe admissibility" in sys
    # v2's value-claim guidance is carried forward
    assert "fully-qualified" in sys and "expected_value" in sys
    # v3's breadth guidance — the four kinds + their hint keys, carried forward
    assert "existence-claim" in sys and "property-claim" in sys
    assert "capability-claim" in sys and "granted_capability" in sys and "grant_type" in sys
    assert "layout-claim" in sys
    # v4's operation guidance (D-203)
    assert "modify_record" in sys and '"delete"' in sys
    assert "WHICH operation is prohibited" in sys
    # v5's multi-intent decomposition guidance (D-207)
    assert "intent_descriptors" in sys
    assert "Decompose for full coverage" in sys
    assert "one negative per prohibition" in sys
    # v6's automation-effect + state-transition hint contracts (D-210.1)
    assert "state-transition-claim" in sys and "trigger_object" in sys
    assert "effect_object" in sys and "effect_lookup_field" in sys
    # v9's config-first-class decomposition + per-AC coverage contract (D-247)
    assert "metadata structure" in sys
    assert "Scan every requirement" in sys
    assert "precision" in sys and "scale" in sys            # property atomicity
    assert "ac_ref" in sys and "no_admissible_test" in sys  # the coverage contract
    # v10's prohibition behaviour-instance contract (D-293)
    assert "rejection_conditions" in sys                    # the business-state hint
    assert "business STATE" in sys
    assert "is_not_null" in sys and "matches_pattern" in sys  # the predicate taxonomy
    assert "REFUSES that intent honestly" in sys            # refuse-not-degrade
    # v11's automation-effect entry-condition trigger contract (D-299)
    assert "trigger_fields" in sys                          # the entry-condition hint
    assert "automation_name" in sys                         # disambiguate WHICH Flow
    assert "entry-gate" in sys or "ENTRY CONDITION" in sys
    # v12's formula-primitive contract (D-304)
    assert "CALCULATED (formula) field is also an automation" in sys
    assert "on the named field being calculated" in sys
    # v13's acceptance-archetype contract (D-305)
    assert "acceptance-claim" in sys
    assert "acceptance_conditions" in sys
    assert "DISTINCT claims" in sys
    # v16's approval contract (D-308)
    assert "APPROVAL PROCESS is also an automation" in sys
    assert "ProcessInstance" in sys and "TargetObjectId" in sys
    # v15's automation-absence contract (D-307)
    assert "expected_absence" in sys
    assert "asserts NO correlated" in sys or "assert NO correlated" in sys
    # v14's update-then-observe contract (D-306)
    assert "update_trigger_fields" in sys                   # the recompute hint
    assert "update_conditions" in sys                       # the change-accepted hint
    assert "RE-computed" in sys or "re-computed" in sys
    assert "Never list the observed field" in sys           # k16, both phases
    # v18's automation_name-optional / bind-by-effect contract (D-318)
    assert "You do NOT need to name the internal Flow" in sys
    assert "actually PRODUCES" in sys                        # bind by the produced effect
    assert "ONLY to break a tie" in sys                      # name only disambiguates same-effect
    assert "FORMULA FIELD's" in sys                          # D-304 calc-field naming preserved
    # v19's approval-name-optional contract (D-320) — replaces v18's "MUST be named"
    assert "You do NOT need to name the approval process" in sys
    assert "MORE than one approval process" in sys           # name only to break the tie
    assert "Approval processes MUST be named" not in sys     # the v18 requirement is lifted
    assert "APPROVAL PROCESS is also an automation" in sys   # D-308 block otherwise carried
    # v18's object subject-key doc (closes the D-317 prompt-doc follow-up)
    assert "`target_subject_hint.object`" in sys
    # the honest-dismissals guard is preserved verbatim (not overturned)
    assert "honest" in sys and "forced breadth" in sys


def test_unknown_version_raises():
    with pytest.raises(KeyError):
        registry.get("generation@v999")


def test_compose_working_has_all_fragments():
    # All-fragments composition (D-103.2): base + the four archetypes (ui added D-125).
    composed = registry.compose_working()
    assert "Archetype guidance — data_behavior" in composed
    assert "Archetype guidance — configuration" in composed
    assert "Archetype guidance — permission" in composed
    assert "Archetype guidance — ui" in composed


# ---------------------------------------------------------------------------
# Runtime wiring: the frozen prompt is what reaches the LLM as `system`
# ---------------------------------------------------------------------------

class _FakeTurn:
    def __init__(self, tool, inp):
        self.content_blocks = [{"type": "tool_use", "id": "tu_x", "name": tool, "input": inp}]
        self.input_tokens = 1
        self.output_tokens = 1
        self.model = "test"
        self.stop_reason = "tool_use"
        self.latency_ms = 1


class _CapturingToolTurn:
    """Captures the `system` the runtime passes, then returns a malformed
    propose (bad archetype) so Layer A rejects it every correction loop — the
    run resolves to structural-validation-failure WITHOUT touching S1/PG."""

    def __init__(self):
        self.system = None

    def __call__(self, *, messages, tools, tool_choice, system):
        self.system = system
        return _FakeTurn("propose_semantic_intent", {
            "requirement_excerpt": "x",
            "intent_descriptor": {
                "archetype_hint": "bogus_archetype", "polarity_hint": "negative",
                "target_subject_hint": {"entity_type": "Object", "sf_api_name": "X"},
            },
        })


def test_runtime_loads_frozen_prompt_into_system():
    from primeqa.generation.governance_core import GovernanceCore
    from primeqa.generation.protocol import (
        GenerationRequest, GovernanceContext, OperationalContext, SemanticContext,
    )
    from primeqa.generation.runtime import GenerationRuntime

    req = GenerationRequest(
        request_id=uuid4(),
        semantic_context=SemanticContext(
            requirement_refs=[{"key": "R0", "text": "x"}], s1_version_seq=1),
        governance_context=GovernanceContext(),
        operational_context=OperationalContext(),   # no prompt_template_version -> CURRENT
    )
    cap = _CapturingToolTurn()
    # GovernanceCore(None): the bad-archetype turn fails Layer A before any S1
    # access, so the S1 model is never touched (no PG).
    GenerationRuntime().run(request=req, seam=GovernanceCore(None),
                            tool_turn_fn=cap, persister=None)

    assert cap.system == registry.get()              # the frozen CURRENT prompt, verbatim
    assert "bounded cognition provider" in cap.system


def test_runtime_honors_pinned_prompt_version():
    # A request that pins CURRENT explicitly resolves the same frozen content
    # (replay determinism path; an unknown pin would raise).
    from primeqa.generation.governance_core import GovernanceCore
    from primeqa.generation.protocol import (
        GenerationRequest, GovernanceContext, OperationalContext, SemanticContext,
    )
    from primeqa.generation.runtime import GenerationRuntime

    req = GenerationRequest(
        request_id=uuid4(),
        semantic_context=SemanticContext(
            requirement_refs=[{"key": "R0", "text": "x"}], s1_version_seq=1),
        governance_context=GovernanceContext(),
        operational_context=OperationalContext(prompt_template_version="generation@v1"),
    )
    cap = _CapturingToolTurn()
    GenerationRuntime().run(request=req, seam=GovernanceCore(None),
                            tool_turn_fn=cap, persister=None)
    assert cap.system == registry.get("generation@v1")
