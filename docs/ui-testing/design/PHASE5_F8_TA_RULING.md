# TA ruling — Phase 5 F8 vocabulary ceiling (2026-09-01)

> Recorded verbatim as supplied by AK, 2026-09-01. The decision this
> ratifies is logged as D-471; the LLD amendments it required are at
> `LLD_PHASE5_AUTHORING.md` §e.3 / §e.4 / §e.6 / §e.8.

---

TA ruling — Phase 5 F8 vocabulary ceiling

Decision: APPROVED / RATIFIED.

I agree with the architect's recommendation: do not add AND/OR/NOT
composition, and do not expand the evaluator model for v1.

The ceiling is appropriately tight because it preserves the central
contract established in the requirements: custom rules must become
deterministic executable rules after human approval, and AI must never
become the runtime decision-maker.

Why I approve the ceiling
The key architectural question is not: "Can we express every conceivable
customer rule?" It is: "Can we express useful deterministic rules without
turning Plimsol into a general-purpose rule language?"
Your four stop rules answer that well:
WITNESS — every result must be grounded in an observed value.
NO NEW EVALUATOR — don't keep adding bespoke engines for individual
customer requests.
ONE SENTENCE — rules remain understandable and auditable.
NO SECOND ENGINE — don't create a competing execution/evaluation
subsystem.
That is an excellent product boundary.

On the specific challenges
AND / OR / NOT — Reject. I would not add them to F8. Once you permit
A AND B, A OR B, NOT A, (A AND B) OR C, you have started building a logic
language. Then the next request becomes: nested expressions → grouping →
precedence → functions → variables → collections → predicates... The
ceiling disappears. The current philosophy explicitly prohibits becoming
a general-purpose logic language, so I would protect that boundary
aggressively.

idref_resolves_to_role — This is the one concession I would permit. It
is fundamentally different from arbitrary composition. It is a single
deterministic DOM relationship assertion: IDREF target resolves to an
element with the required role. That fits your existing five fact
families and can be witnessed directly from the captured
accessibility/DOM census. So my ruling would be: Not required for the
first implementation, but approved as the named first extension to the
vocabulary if the criterion catalogue proves it materially increases
useful WCAG coverage. Do not add it merely because it is technically
possible.

Geometry — Keep geometry. I would not cut it. The concern would be
whether layout geometry becomes an accidental visual-regression engine.
But that only happens if you allow arbitrary geometric comparison. A
constrained geometry predicate such as "element width ≥ X" or "target
does not overlap another target" is still a deterministic fact
assertion. The important distinction is: Geometry predicate ≠ pixel
comparison. So I approve geometry provided the vocabulary remains closed
and the geometry is measured against the stabilised, manifest-defined
viewport/runtime. That fits your existing requirement that viewport is
part of execution context and that visual/pixel comparison is explicitly
excluded.

surface_contains / surface_lacks — Approve, but with one important
constraint. These are acceptable as applicability gates, not as general
test predicates. And surface_lacks(X) must never mean "X wasn't found,
therefore the rule passed." It means "The rule is not applicable because
the stabilised census establishes that X is absent." If the census is
incomplete or uncertain: NOT-DETERMINED. That is consistent with the
core fail-loud principle. The existing architecture already establishes
that applicability belongs in the processor, not the worker, and that
unresolved conditions cannot become a PASS.

One TA addition I would require
Add this sentence to the F8 LLD as a hard invariant:
An applicability predicate may suppress execution of a rule only when
the census from which applicability is derived is itself COMPLETE and
TRUSTWORTHY; an incomplete or ambiguous census produces NOT-DETERMINED,
never NOT-APPLICABLE.
That closes an important loophole. Otherwise a failed/partial DOM census
could accidentally make a rule disappear.

My final ceiling
Allowed: 11 current predicates, plus idref_resolves_to_role — reserved
first extension.
Explicitly prohibited: AND; OR; NOT composition; nested expressions;
arbitrary selectors; arbitrary JavaScript; user-defined functions;
second evaluator; second accessibility engine; free-form rule language.
And the existing conditional-logic ceiling elsewhere in the platform
remains separate: that is test orchestration logic, not rule-language
composition. Your requirements deliberately close that ceiling as
IF/ELSE, optional steps, conditional assertions/components,
stop/continue, with loops and embedded code prohibited. Those two
boundaries should never be allowed to merge.

TA decision
Eleven-predicate ceiling — APPROVE
AND / OR / NOT — REJECT
Geometry — KEEP
surface_contains/lacks — KEEP as applicability only
idref_resolves_to_role — Reserve as first extension
CSS selectors — REJECT
Free-form expressions — REJECT
New evaluator per customer request — REJECT
AI runtime verdicts — REJECT
WITNESS / NO NEW EVALUATOR / ONE SENTENCE / NO SECOND ENGINE — RATIFY

Phase 5 F8: APPROVED. Authoring implementation may proceed.
The one requirement I would add before the LLD is considered closed is
the complete-census rule for applicability above. Everything else should
remain deliberately constrained.
This is one of those cases where being able to say "no" is part of the
architecture.

---

*Recording note (not TA text): the "final ceiling" line "Allowed: 11
current predicates, plus idref_resolves_to_role — reserved first
extension" uses the LLD table's headline count, which had INCLUDED
idref among its eleven. The operative rulings — "Not required for the
first implementation" and "Reserve as first extension" — fix v1 at TEN
forms with idref reserved, which is how D-471 and LLD §e.4 record it.*
