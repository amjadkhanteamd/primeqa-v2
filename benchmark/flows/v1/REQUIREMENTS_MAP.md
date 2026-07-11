# FB-V1 — Requirements Map & Leak Review (internal)

> **INTERNAL TO EVALUATION** — like the gold standard, never an input to the
> system under test. Records which controls each acceptance criterion is
> expected to exercise, the adversarial leak review of the requirement texts,
> and the ambiguities that are deliberate because the benchmark depends on
> metadata reasoning.

## AC → control mapping

### REQ-A (Order Lifecycle Automation) → FL01–FL09

| AC statement (abbrev.) | Exercises | Notes |
|---|---|---|
| lifecycle progression (Draft…Cancelled) | staging vocabulary for all controls | mirrors VRB-V1's lifecycle sentence |
| no stated priority → standard; chosen priority respected | **FL01** (fire + suppress) | |
| any casing/spaces → canonical uppercase; wrong format not accepted | **FL02** + VR01 | the repair sentence *contradicts* what VR metadata alone predicts — the designed order-of-execution trap |
| tiers by value, higher value → higher tier, stays current | **FL03** | band count and boundaries entirely metadata-derived |
| newly raised duplicate of an existing open order → flagged | **FL06** | "newly raised" = create-only, stated as behaviour |
| submitted → service-level deadline from submission date | **FL08** | offset unstated |
| confirmed → **a** fulfilment task, appropriate due date | **FL04** | singular = the exactly-one cardinality |
| cancelling cancels outstanding work; completed stays | **FL05** | scope semantics stated, filter unstated |
| totals/count of line items always current | **FL07** | |
| leaves fulfilled state → reopened + audit trail | **FL09** | wording matches org truth exactly (incl. Fulfilled→Cancelled) |
| order accurately reflects work outstanding | *(none — designed underspecification probe)* | added on AK review 2026-07-11: no single control maps to it; honest handling = grounding it to specific observable derivations (task states, totals) or an explicit refusal. Overlaps FL05/FL07/FL12 vocabulary without naming any behaviour. |

### REQ-B (Order Operations) → FL10–FL15

| AC statement (abbrev.) | Exercises | Notes |
|---|---|---|
| stalled submitted order escalated later, only if still awaiting | **FL10** | delay unstated; the later-not-now timing is observable behaviour, so stating it is legitimate |
| large submitted orders need approval, locked while pending | **FL14** | threshold and boundary operator metadata-derived |
| confirmed → ledger posting; failure → audit trail, confirmation stands | **FL13** (both arms) | failure precondition unstated — must be derived from target-object metadata |
| enrichment recorded shortly after confirmation, not instantly | **FL11** | deferred-observable class |
| fulfilling completes outstanding work + records fulfilment date | **FL12** (+SF01) | composition invisible in text, as it must be |
| email on file → confirmation message; none on file → none | **FL15** | the evidence-limit control |

Cross-requirement note: the Confirmed transition is co-triggered
(FL04 in REQ-A; FL11/FL13/FL15 in REQ-B). The split is deliberate — each
requirement's tests must still tolerate (and not mis-attribute) the other's
effects on shared fixtures.

## Adversarial leak review (Part 5)

Method: term scan + per-AC reading with the question *"if I had only this
text and the org metadata, would I have to reason about implementation?"*

- **Prohibited-term scan** (flow, element, decision, assignment, loop,
  subflow, path, before-save, after-save, trigger, Create/Update/Get
  Records, flow names, field API names): **zero hits** in both AC blocks.
- **AK review 2026-07-11 revisions:** object API names REMOVED from both
  texts (grounding must now resolve the business label "PLS FB Order" plus
  the sync-appended projection tail — one notch harder than VRB-V1, which
  named its object); four ACs rewritten to observable-outcome phrasing
  (FL01 "shows a priority of Standard once saved", FL04 "a fulfilment task
  appears… linked to the order", FL10 "shows as escalated… a new escalation
  task appears", FL13 "a ledger entry appears / no ledger entry appears");
  one deliberately underspecified statement added to REQ-A (see mapping).
- **Mechanism-neutrality**: every behaviour in the texts is implementable as
  workflow rules, Apex triggers, or flows — nothing in the wording selects
  an implementation. A reader cannot tell FL05 (filtered update) from a
  loop, or FL12's subflow from inline logic.
- **Numbers**: no thresholds, offsets, delays, formats, or band boundaries
  anywhere. Every number in the gold standard is reachable only through
  metadata.
- **Two sentences were checked hardest**:
  1. REQ-A's casing-tolerance sentence *states* the repaired outcome. Not a
     leak — it is the observable behaviour, and it is precisely what makes
     the control adversarial: VR metadata alone predicts rejection; the
     requirement asserts acceptance; only order-of-execution reasoning
     reconciles them.
  2. REQ-B's "later, not at the moment of submission" states deferredness.
     Not a leak — timing of observability is externally observable
     behaviour, and without it the honest outcome (FL10) would be
     indistinguishable from a missed behaviour.

**Confidence: high** that no implementation detail is leaked. Residual,
accepted exposure (recorded in the completeness review): the org's own flow
API names/descriptions are legible to any metadata-synced system — a
property of the org, not of these texts, and consistent with VRB-V1.

## Deliberately ambiguous ACs (metadata reasoning required)

1. **Tier structure** — REQ-A doesn't even say how many tiers exist, let
   alone boundaries or which side each boundary falls (all ≥, first-match).
2. **Reference format** — "the company's required reference format", never
   spelled out.
3. **Duplicate criterion** — which attribute matches, and what "open" means
   (org truth: anything non-Cancelled, *including Fulfilled*).
4. **All offsets** — SLA (+5d), task due (+3d), escalation delay (+2d):
   "based on the submission date", "appropriate", "after too long".
5. **"Large"** — the approval threshold (100,000) and its inclusive
   boundary.
6. **State vs transition** — "when an order is submitted/confirmed" reads
   naturally either way; the org's updated-to-meet semantics (created-
   already-Confirmed ⇒ nothing) are discoverable only from metadata.
7. **"Leaves its fulfilled state"** — includes Fulfilled→Cancelled, which a
   casual reader might not expect; the metadata decides.
8. **Ledger-posting failure** — *when* posting "cannot be completed" is
   never stated; deriving the blank-accounting-details precondition requires
   composing the flow's data mapping with the target object's
   required-field metadata.
9. **Cardinality** — "a fulfilment task" (exactly one) is the only quantity
   hint anywhere; set sizes for FL05/FL07 are unstated.

## Fresh customer-read (independent reviewer, no flow-design context, 2026-07-11)

A reviewer given ONLY the two AC texts (no repository access, no design
knowledge) returned **LEAK-FREE: yes** — "no sentence forces one specific
Salesforce mechanism or names automation internals; every behaviour admits
at least two implementations" — with two borderline flags, both fixed in
place before commit:

1. The FL11 sentence's only content was timing (async structure with no
   business cover) → rewritten with a business purpose ("prepared for
   downstream processing") while keeping the observable not-instantly fact.
2. The FL10 timing sentence read tester-voiced → rewritten in grace-period
   business voice; the delay value stays unstated and the
   still-awaiting-processing re-check stays observable.

Accepted-as-is from that review: the "PLS FB" object prefix (it IS the org's
object label; VRB-V1's "Plimsol Benchmark Deal" had the same character); the
closing generate-test-cases instruction (VRB-V1 precedent — it is the
pipeline's required trailer, not BA prose); "audit trail" ambiguity (which
artifact realises it is metadata reasoning). The reviewer's testability list
independently reproduced the gold standard's arm structure from the text
alone — strong evidence the texts describe behaviour completely without
describing implementation.

## Customer-read verdict

Read cold, both texts are believable BA requirements: they describe what the
business observes and needs, in lifecycle vocabulary, with the vagueness real
requirements have exactly where real requirements have it (thresholds,
delays, formats — "the company's required format", "too long", "large").
Answering "could I guess how the flows are implemented?" from the text
alone: no — only what the org should *do*.
