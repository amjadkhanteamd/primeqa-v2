# FB-V1 — Benchmark Completeness Review (Wave 0)

An independent-evaluator pass over the built benchmark, answering four
questions: does every flow test something different; is anything redundant;
what are the gaps; and would a system under test have to reason about
observable behaviour rather than internal implementation. Recommendations
are **recorded, not implemented** (per the Wave-0 charter); none block
Wave 1.

## 1. Does every flow genuinely test something different?

Yes, with one designed near-overlap. The distinctness matrix, by the single
capability each control isolates:

| Control | The one thing it uniquely tests |
|---|---|
| FL01 | expected-value sourced from effect, not payload (baseline) |
| FL02 | order-of-execution composition (flow repairs before VR) |
| FL03 | N-way first-match decision + boundary derivation from flow metadata |
| FL04 | side-effect creation + transition entry semantics + cardinality (exactly one) |
| FL05 | set-scoped fan-out (a predicate over a record set, with spared records) |
| FL06 | outcome as a function of *other records* (data-presence differential) |
| FL07 | aggregate expected values + child-event/parent-assertion direction |
| FL08 | symbolic (replay-stable) *assertion* values |
| FL09 | prior-state logic inside the flow body ($Record__Prior) |
| FL10 | out-of-window execution → honesty (or genuine deferral) |
| FL11 | deferred-but-observable evidence (race-aware protocol) |
| FL12 | attribution across composition (subflow) |
| FL13 | externally-reachable fault + handled-fault verdict class |
| FL14 | evidence in another governance mechanism's objects |
| FL15 | per-effect evidence limits under co-triggered automations |

Near-overlap, accepted: FL01/FL08 are both before-save assignments — FL08's
distinct load is the symbolic computed value and the transition entry, and
having a trivial baseline (FL01) below it is deliberate instrument design
(if FL01 fails, FL08's failure is not diagnostic).

## 2. Redundant flows?

None removable without losing a capability. Two deliberate redundancies are
features, not waste: the FL10/FL11 pair exists to force the
"cannot-observe" vs "cannot-observe-*yet*" distinction (either alone lets a
system conflate them), and the four-way co-trigger on Confirmed
(FL04/FL11/FL13/FL15) is the attribution stressor — removing any member
weakens the smearing test.

## 3. Gaps (recorded for V2; none invalidate V1)

1. **SF01's fault connector is dormant.** No task-level constraint exists
   today, so the CloseoutFault path is unreachable from outside — it is
   structural (parser-visible) but not exercisable. FL13 carries fault
   reachability for V1. *Recommendation:* V2 adds a task-level validation
   rule armable by input, making the subflow-fault arm live.
2. **Bulk behaviour is untested.** All probes are single-record; flow
   bulkification (200-record chunks, loop behaviour at volume) is a
   different instrument category, consistent with the taxonomy's exclusion —
   but worth stating: FB-V1 proves nothing about volume semantics.
3. **No `__r` cross-object traversal in conditions.** FL07 reaches the
   parent via its FK and a Get; a decision condition reading
   `$Record.Parent__r.Field` is a distinct parser shape V1 doesn't contain.
4. **Single-tenant actor model.** Everything runs as one integration user;
   the USER/sharing axis (system vs user context, `$Permission`) is
   deliberately deferred to its own benchmark family.
5. **The whole excluded-trigger surface** per the taxonomy: delete triggers,
   standalone scheduled flows, platform events, screen flows, Transform
   element, Apex actions.
6. **FL15's org truth is itself unconfirmed end-to-end** — sandbox
   deliverability may suppress the actual send. Acceptable (the control
   scores honesty about unobservability, and the send *action* executing
   without fault is verified), but a V2 with an org-wide email log object
   would strengthen the instrument.
7. **FL10's two-day firing not yet witnessed.** Sentinel ORD-00023 is
   staged; verify ~2026-07-13 and record before freeze. The mechanism
   (path scheduling) deployed and validated; the firing is the open item.

## 4. Observable behaviour vs internal implementation?

The benchmark holds the line in the places that matter:

- **Every number lives only in metadata** (bands 10k/50k/250k, offsets
  +3/+5/2d, threshold 100k, the regex). The future requirement text stays
  qualitative; a system must derive values from the org's own flow
  definitions.
- **Evidence is API-observable state**: read-backs, records the probe never
  posted, set predicates, ProcessInstance rows. Nothing in the rubric asks
  "did element X execute" — only what the org's state proves.
- **Attribution must be earned by differentials** (fire/suppress pairs),
  since effects carry no signature — reasoning about internal element
  wiring alone cannot produce the required suppression arms.
- **Read-only FLS on flow-computed fields** removes the "the test staged it
  itself" escape hatch.

One acknowledged leak, consistent with VRB-V1: flow API names and
descriptions are self-documenting (`PLS_FB_FL03_Tier_Banding`, and the
`description` fields narrate behaviour). A metadata-synced system will see
them. This mirrors VRB-V1 (rule names + descriptions were equally legible)
and is realistic — production orgs have descriptive names — but evaluators
should note that name-reading alone cannot supply thresholds, arm structure,
or suppression semantics, which is where the scoring lives.

## Verdict

The instrument is complete for its declared V1 scope: 15 controls, each with
a distinct capability load, adversarial where designed, fully declarative,
live-verified at 46/46 on the synchronous surface, with both honesty
controls structurally incapable of being satisfied by fabrication under the
gold standard's scoring. Open pre-freeze items: the FL10 two-day drill and
the (already-designed, not yet authored) requirement text.
