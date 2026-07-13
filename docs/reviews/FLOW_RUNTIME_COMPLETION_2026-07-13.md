# Flow Runtime — Completion Program report (2026-07-13)

Mission: continue autonomously until the Flow Runtime reaches natural
architectural completion for Record-Triggered Flows. Branch
`phase-7-substrate-3-b01-label-affinity`; no merge, no push; benchmark
(FB-V1 / req-320) validation-only and frozen throughout. Decision of
record: **D-371** (this report is the narrative companion).

## 1. Capabilities added

Six commits, each suite-green and replay-gated:

| Commit | Capability | Flow class proven |
|---|---|---|
| ed07ed3 | **E1 — cross-object CREATE evidence.** Single typed create producer → substrate-derived correlation (the op's subject_ref-Id assignment), asserted values (op literal / RelativeDate), attribution, and the Update-trigger create→update transition from the op's EqualTo guard. | FL04 (confirmation task) |
| 40ca9f6 | **E2 — SET-UPDATE evidence with a count differential.** Template/correlation/updated-value from the typed update op; a third-state distractor child; ONE correlated read + `count_equals(N)` — under-update and over-update both fail. `AssertionPredicate` + S4 gain `count_equals`; the bridge binds updates by target object. | FL05 (cancellation sync) |
| 48130fe | **E3 — ROLL-UP evidence.** Parent-framed claims (`Order.Line_Count`) ground via bounded org-wide attribution — the producer triggers on the CHILD. Typed triple match (parent-correlated update op ← var ← Count/Sum aggregate ← sibling premise on the same lookup); `staging_plan`/`aggregate_expectation` derive k siblings, a second-parent correlation distractor, and the deterministic expected value. | FL07 (order rollup) |
| ba03a1b | **Composition to the evidence layer.** The COLLECTION-UPDATE idiom (loop + per-item literal assign + update-by-collection) types as a filtered update op; composed subflow effect ops carry the call-site guard + full provenance (`via_subflow`, `via_collection`, `on_fault_of`); the binder offers a caller's composed ops (lazy org-wide callee registry); attribution stays on the CALLER. | FL12→SF01 (fulfilment orchestrator) |
| be9b820 | **Premise-conditioned same-record effects.** `NotEqualTo` joins the shared filter grammar (and the drifted duplicate parser is gone); the FL06 flag arm grounds WITH its `premise_guard`; sibling-first evidence with a format-rule-aware correlation witness (regex-derived under active VRs, refuse on unreadable rules, uppercase-stable constant otherwise); VR-conflict gates on both staged rows. | FL06 (duplicate flag) |
| d8282c4 | **Review fix:** fault-path (`on_fault_of`) and temporal-path ops excluded from every producer discovery — a fault-handler create must never ground a claim whose recipe cannot deterministically provoke the fault. | FL13 honesty |

## 2. Architectural changes

- **The honesty partition for conditional arms** (the load-bearing change):
  `_walk_linear` now forwards `premise_guard` into every behaviour, and ALL
  plain projections (same-record / guarded / transform / temporal /
  transition / approval) exclude premise-guarded behaviours — a bare create
  can never fire a conditional arm, so exposing one unconditionally is a
  structural wrong-green. `flow_grounded_premise_conditioned_effects` is
  the partitioned surface. This was latent before the NotEqualTo widening
  and became mandatory with it.
- **Attribution scope generalized**: producers are no longer confined to the
  subject's TRIGGERS_ON neighborhood — roll-ups attribute via a bounded
  org-wide scan; composed subflow effects attribute to the caller. Both are
  lazy (they run only when the cheaper paths found nothing / the caller
  actually calls a subflow).
- **One bounded filter grammar** (`_fb_parse_bounded_filters`) for premises
  and filtered updates; the inline twin in the premise parser had already
  drifted and is deleted (DEBT D6 partially reduced; walker unification
  remains a dedicated-slice item — not an obviously-safe extraction).
- **Producer-discovery hygiene**: every discovery path (`_xo_create/`
  `_xo_update/_flows_producing_effect`) excludes fault-path and
  temporal-path ops.
- Admission/tail alignment maintained: `_rollup_admits` and the
  premise-conditioned producer joined `_field_has_verifiable_producer`, so
  admission admits exactly what the tail can ground.

## 3. Benchmark status (FB-V1 corpus, offline replay — live quota-blocked to 2026-08-01)

- **Ground end-to-end (direct):** FL01, FL02, FL03, FL06, FL08, FL09, FL14-IR.
- **Ground end-to-end (evidence branches):** FL04 (E1), FL05 (E2), FL07
  (E3), FL12+SF01 (composition→E2). FL13's main-path ledger create grounds
  via E1 with its fault arm honestly excluded.
- **Named refusals by design:** FL10 (scheduled-only — human execution-model
  decision pending), FL11 (async — needs the C9 bounded-eventual read, the
  one unbuilt evidence class), FL15 (email delivery outside the evidence
  model).
- **Replay regression** (the standing instrument, run after every commit):
  req-320 CONVERGED **217 → 221 / 1864** — the four gains are exactly the
  FL07 benchmark ACs (ac11 order total, ac12 line count) proposed by real
  historical runs with placeholder values; **zero losses**; req-315
  byte-identical (54/89) throughout. Recovery yield rose 19%→29% (FL07
  value-drop variants now converge).

## 4. Production readiness

- Full offline tree **4338 green** at close; 23 new resolver/emission tests
  in `tests/unit/generation/test_xo_evidence.py` (all against the real
  benchmark fixture Metadata — no synthetic grammar-shaped fixtures), plus
  evolved corpus pins in the semantic suite.
- Everything is deterministic and identity-stable (fixed staging constants
  137 / `PQAW137X`; substrate-derived values everywhere; repeat-grounding
  determinism pinned by test).
- NOT yet done (blocked): the live req-320 generation gate
  (`scratch_flow_gate.py 3`) and the req-315 live regression
  (`scratch_conv_live315.py 2`) — Anthropic quota resets 2026-08-01. The
  deterministic replay over the persisted live corpus substitutes, per the
  Wave-3 policy. Do not merge before the live gate.

## 5. Remaining unsupported Flow features (all named, none silent)

- Async paths (FL11): representation complete (`temporal_paths`,
  `bounded_eventual` observability); evidence needs the C9 S4 retry-until
  read — the one remaining evidence class.
- Scheduled paths (FL10): `deferred_reobservation_required` — gated on a
  human execution-model decision.
- Email/callout actions (FL15): outside the evidence model by design.
- Cross-object premise staging for premise-conditioned effects (FL06 v1 is
  same-object sibling only); premise `IsNull False` templates without a
  derivable witness; non-picklist NotEqualTo templates; multi-condition
  premise guards; post-decision loops; formula guards beyond the bare
  passthrough; subflow depth > 1 and output-consuming calls.
- FL14 emission: gated on REQ-B being loaded (frozen-benchmark constraint).

## 6. Recommendation — next PrimeQA subsystem

The Flow Runtime is at natural completion for immediate-path
record-triggered flows: every typed effect class now has an evidence shape,
and the remaining items are either a single named evidence class (C9), a
human decision (FL10), or by-design limits. The highest-leverage next
subsystem is **the S4 execution-evidence layer (C9 + live closure)**:
1. the bounded-eventual read (retry-until with a deadline) — it unlocks
   FL11 AND hardens every async-adjacent assertion;
2. on quota reset (2026-08-01), the deferred live gates + a full FB-V1
   scoreboard rebaseline;
after which the decision-loop wiring (theme 3 — substrate run results →
risk score → GO/NO-GO on the NEW engine) is the vision-critical successor.


---

## Addendum — C9 shipped (same session, D-372, commit eba4ed9)

The recommendation's first item landed immediately after the report:
**bounded-eventual observation**. `ReadStep`/`PlannedDataRead` carry an
optional `eventual` spec; the executor retries an empty read until a
deadline (hard-clamped 300s/2s — advisory spec, executor-owned bounds);
grading unchanged; async producers (`bounded_eventual` observability)
take the E1 rails with the eventual read stamped and an identity-bearing
async narration; absence against an async producer refuses by name (a
bounded window can never prove non-appearance — closing a latent
wrong-green). FL11 grounds end-to-end offline. Full tree 4346 green;
replay byte-identical. The only non-evidenced flows are now FL10
(human-gated execution-model decision) and FL15 (by design).
