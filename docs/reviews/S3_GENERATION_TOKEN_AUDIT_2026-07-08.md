# S3 Generation Loop — Architecture & Token-Efficiency Audit

**Date:** 2026-07-08 · **Scope:** the S3 constrained-semantic generation loop
(`propose_semantic_intent` → correction → `emit_outcome`). **No code changed.**

**Method:** traced the production path in code, then reconstructed the exact
observed activity and 30 days of production activity from `llm_usage_log`
(public) and `tenant_1.llm_calls` / `tenant_1.generation_outcomes` (substrate
telemetry). Every number tagged **[M]=measured** or **[E]=estimated**.

The activity the user observed is real: it is `generation_outcome_id =
ab65fb0c-430a-408d-b0d9-4747988d3b00`, `llm_usage_log` rows **544–547**,
2026-07-07 13:37–13:39, model `claude-sonnet-5`, prompt `generation@v22`. Its
numbers reconcile to the stored `cost_usd` to the cent. **[M]**

---

## 0. TL;DR

1. The 2k→8.5k→16.5k→28k input growth is **real and almost entirely the
   accumulating assistant proposals being replayed**. It is **standard Anthropic
   tool-use transport behaviour** (you must resend prior turns), amplified by a
   PrimeQA choice: each `propose` output is 6–8k tokens, and correction/coverage
   turns **re-emit the whole proposal**. **[M]**
2. The **static prefix (system+tools = 13,364 tok) is already cached** — it does
   NOT grow. Caching is working. The growth is the **uncached dynamic message
   history**, which carries **no cache breakpoint**. **[M]**
3. `emit_outcome`'s **LLM input is discarded** — `finalize_outcome` authors the
   draft purely from substrate state and never reads the tool input. It fired on
   **70/70 drafts, 0/17 refusals**. It is **deterministic code wearing an LLM
   call** (28k in → 120 out). **[M]**
4. **33% of all propose output tokens over 30d were spent on rejected proposes
   that were then fully regenerated** (12.9% post-fix, 42% pre-fix). **[M]**
5. **Verified defect (out of scope, flagged):** the observed activity wrote **53
   claims from a 29-intent final proposal** — the coverage re-prompt path
   concatenates `presented_candidates` across turns with **no dedup**
   (`runtime.py:488`). Delta correction would fix this as a side effect. **[M]**
6. **Biggest low-risk win:** make `emit_outcome` deterministic (−16% cost, −1
   turn, −~14–28 s latency, verified-safe). **Biggest lever overall:** delta
   correction (attacks the 50%-of-cost output regeneration + the dedup bug).
7. **Optimise the loop before benchmarking Haiku** — a cheaper, more-error-prone
   model multiplies correction turns, and each miss currently costs a *full*
   regeneration.

---

## 1. Reconstructed conversation state

### Production path (traced)

```
run.py:build_batch → GenerationRuntime.run                       generation/runtime.py:364
  └ _run_requirement (one conversation per requirement)          runtime.py:403
      state.messages = [_initial_user_message(ctx)]              runtime.py:416,137
      loop:
        turn = tool_turn_fn(messages=state.messages, tools=TOOLS,
                            tool_choice=..., system=state.system) runtime.py:434
          → gateway_binding.build_tool_turn_fn.fn               gateway_binding.py:37
          → gateway.tool_turn(...)                              gateway.py:434
              _system_with_cache / _tools_with_cache            gateway.py:389,402
              provider.invoke → Anthropic Messages API          gateway.py:482
        tool_uses = extract_tool_uses(turn.content_blocks)      runtime.py:441,103
        Layer A validate + ref-existence                        runtime.py:453,465
        on success  → _respond  (append assistant + tool_result) runtime.py:574
        on failure  → _reject   (append assistant + error result) runtime.py:543
        coverage reprompt → _respond(reprompt_text)             runtime.py:499
        emit  → seam.finalize_outcome(outcome_input=tu.input)   runtime.py:534
```

### Who appends what to `state.messages` (the growth engine)

| Code | What it appends | Size |
|---|---|---|
| `_initial_user_message` (runtime.py:137) | `role=user`: requirement text + shared context | ~2.0k tok **[M]** |
| `_assistant_tool_use_msg` (runtime.py:116) | `role=assistant`: **the full `tool_use` block incl. the entire `intent_descriptors` array** | 6–8k tok/propose **[M]** |
| `_user_tool_result_msg` (runtime.py:122) | `role=user`: `tool_result` (feedback / presented candidates) | 0.2–3.7k tok **[M]** |

Both `_respond` (success) and `_reject` (failure) append the **full assistant
proposal**. Nothing is ever pruned. `tool_turn` re-sends `state.messages`
verbatim each turn (`gateway.py:474`, `safe_messages`). This is the mutation
logic responsible for the growth.

### The observed activity, turn by turn (all **[M]** from `llm_usage_log` + `llm_calls`)

| Turn | Tool | Op-outcome | Uncached in | Cache read | Cache write | Output | Cost |
|---|---|---|---|---|---|---|---|
| 1 | propose | success → coverage re-prompt | 2,084 | 0 | 13,364 | 5,981 | $0.1461 |
| 2 | propose | **rejected_for_correction** | 8,494 | 13,364 | 0 | 7,775 | $0.1461 |
| 3 | propose | success | 16,490 | 13,364 | 0 | 7,775 | $0.1701 |
| 4 | emit | success | 28,027 | 13,364 | 0 | 120 | $0.0899 |
| | | **totals** | **55,095** | | | **21,651** | **$0.5522** |

What sits inside each turn's uncached input:

| Turn | System+tools | Init user | Prior proposals replayed | Tool-results replayed | ≈ uncached total |
|---|---|---|---|---|---|
| 1 | (cache-write 13,364) | 2,084 | — | — | **2,084** |
| 2 | (cache-read) | 2,084 | 5,981 | ~430 (reprompt) | **~8,494** |
| 3 | (cache-read) | 2,084 | 5,981+7,775 | ~650 | **~16,490** |
| 4 | (cache-read) | 2,084 | 5,981+7,775+7,775 | ~3,760 (53 candidates) + ~650 | **~28,027** |

The system prompt (v22 = ~10.6k tok) + tool schemas (~2.7k tok) = the 13,364
cached prefix; **fixed, cached, does not grow**. **[M]** (The `gateway.py:378`
comment saying "~4.4k + ~2k" is stale for v22.)

---

## 2. Why the growth — cause attribution

| Category | Contribution to growth | Verdict |
|---|---|---|
| **Previous assistant outputs (proposals)** | **Dominant.** 5,981 + 7,775 + 7,775 = 21,531 tok re-billed as input by turn 4 | **[M]** the growth |
| Tool results (present-candidates at emit) | ~3.7k at turn 4 (53 candidates × {path_id, layer, summary}) | **[M]** secondary |
| Correction/coverage feedback | ~200–650 tok each | **[M]** minor |
| Requirement text (init user msg) | 2,084, constant every turn | **[M]** flat |
| System prompt + tool defs | 13,364, **cached** (write once, read after) | **[M]** flat, cheap |
| Rejected intents / accepted-intent repeats / schema / errors / dup content | folded into "previous assistant outputs" above | **[M]** |

- **Turn 1 = ~2k** because only the init user message is uncached; system+tools
  went to a one-time cache **write** billed on a separate meter. **[M]**
- **Turn 2 = ~8.5k** = init (2k) + **turn-1's 5,981-token proposal** now replayed
  + reprompt result. **[M]**
- **Turn 3 = ~16.5k** = + **turn-2's 7,775-token proposal** + reject feedback.
- **Turn 4 (emit) = ~28k** = + **turn-3's 7,775-token proposal** + the ~3.7k
  presented-candidates result. Then produces 120 tokens. **[M]**

**Classification:** the *mechanism* (resend history) is expected Anthropic
tool-use behaviour. The *magnitude* is a **PrimeQA implementation choice**:
(a) each proposal is 6–8k tokens and every correction/coverage turn regenerates
the whole thing; (b) the message history is not cache-broken; (c) an extra emit
turn replays the entire history for a discarded output. Not accidental
duplication of a bug kind — but see §5's dedup defect for genuine duplication in
the *output*.

---

## 3. The 6k–8k propose output — structure (all **[M]**)

Final successful propose (turn 3): **17,656 JSON bytes, 29 `intent_descriptors`,
21 `acceptance_criteria`, 7,775 output tokens (~2.27 bytes/tok, ~268
tok/intent).** One intent descriptor decomposes as:

| Field | Bytes | Note |
|---|---|---|
| `target_subject_hint` | 230 | **the heavy field** — S1 entity ref / selector object |
| `failure_mode_framing` | 52 | negatives only |
| `requirement_excerpt` | 27 | Guardrail-3 verbatim anchor |
| `claim_kind_hint` | 19 | enum |
| `archetype_hint` | 15 | enum |
| `polarity_hint` | 10 | enum |
| `ac_ref` | 1 | integer |

So the output is **structured, not prose** — but large because it is
**~29 near-identical 7-field records**, and `target_subject_hint` is ~40% of each.

Against the A–F menu:
- **B (structurally similar repetition): YES** — 29 records sharing one schema.
- **E (regenerating accepted intents during correction): YES, measured.** Turn 2
  (rejected, 17,657 B, 29 intents) and turn 3 (success, 17,656 B, 29 intents) are
  **byte-for-byte near-identical**. The "correction" reproduced the entire
  29-intent array to fix ~one field. **[M]**
- **D (fields deterministic code could derive): PARTIAL.** `ac_ref`,
  `archetype_hint`, `claim_kind_hint`, `polarity_hint` are enums the substrate
  re-derives / overrides during grounding anyway. `requirement_excerpt` and
  `target_subject_hint` are genuinely model-authored and needed.
- **A (genuinely necessary unique info): the excerpt + subject hint per intent** —
  yes. The rest is overhead.
- **C (detailed tests too early): NO** — this is the semantic-intent layer, not
  executable tests (those are substrate-authored later). Intent granularity is
  appropriate; the waste is *regenerating* it, not *producing* it once.
- **F (verbose prose): NO.**

Representative (redacted) shape of one intent:
```json
{ "requirement_excerpt": "<verbatim ≤27B span>",
  "archetype_hint": "data_behavior",
  "target_subject_hint": { "object": "‹Obj›", "trigger_fields": ["‹F›"],
                           "rejection_conditions": [ ... ] },   // ~230B
  "polarity_hint": "negative", "claim_kind_hint": "prohibition-claim",
  "failure_mode_framing": "‹mode›", "ac_ref": 7 }
```

---

## 4. Correction architecture

### Current flow (measured behaviour)

```
propose (full N intents)
  → Layer A (tools.validate_layer_a) + ref-existence (seam.check_refs_exist)   runtime.py:453,465
      FAIL → _reject: append(full assistant proposal) + append(error tool_result)   runtime.py:543
             model re-proposes the WHOLE N-intent array   ← regeneration
  → resolve_intent grounds candidates; ACCUMULATE:
      state.presented_candidates = presented + grounded    runtime.py:488   ← NO DEDUP
  → coverage under-covered? _respond(reprompt) → model re-proposes WHOLE array   runtime.py:499
  → emit → finalize_outcome authors from state.groundings (ignores emit input)   runtime.py:534
```

Answers to the precise questions:

- **Does the next call receive the entire previous proposal?** **Yes** — as
  replayed assistant history, and the model regenerates it in full. **[M]**
- **Accepted intents again? Rejected intents again?** **Yes to both** — the whole
  array comes back each turn (turn2≡turn3 byte-identical). **[M]**
- **Concise reasons or large payloads?** Rejection feedback is **concise** (typed
  Layer-A strings, ~200–650 tok, `runtime.py:559`). The bloat is the *proposal
  replay + regeneration*, not the feedback. **[M]**
- **Regenerate whole or only corrected?** **Whole.** No delta path exists. **[M]**
- **Does the substrate merge corrected + accepted?** It **concatenates**
  (`presented_candidates += grounded`) with **no dedup** → the §5 defect. **[M]**
- **Could accepted intents be removed from correction context?** Yes.
- **Could rejected intents be represented more compactly?** Yes (id + reason).
- **Could correction operate only on failed items?** Yes.
- **Could deterministic code merge the corrected subset with the accepted
  subset?** Yes — the runtime already holds `state.groundings` /
  `presented_candidates`; a delta merge is a code change, not a model change.

### Smallest safe alternative (delta correction)

```
propose (full N intents)  →  substrate grounds, keeps state.groundings[accepted]
  reject/coverage:
    send back ONLY: {failed_intent_ids + concise reason}  and/or  {uncovered AC labels}
    model returns ONLY the corrected/new intents (delta, ~1–4 intents)
  substrate grounds the delta, DEDUPES by (excerpt, subject, polarity), merges with accepted
  deterministic finalize (no emit LLM turn)
```

This cuts correction output from ~7,775 → ~300–1,100 tok, removes the replay
growth, and **eliminates the double-emission** because the accepted set is held
by code, not re-proposed by the model.

---

## 5. `emit_outcome` audit → **Class D (deterministic code)**

- **What it does:** the model calls `emit_outcome({outcome_kind, payload})`;
  `finalize_outcome` (governance_core.py:3103) authors the S2 claim+recipe
  bodies **from `state.groundings`** via `author_emission(g)`, computes
  `admissibility_layer` from the bundles, and returns the outcome. **[M]**
- **Does it read the conversation / its own input?** **No.** `finalize_outcome`
  never references `outcome_input` in its body (3103–3183); the docstring
  concedes the input "owns only linguistic realization" — and even that is
  substrate-authored (readable phrasing is a *separate* task,
  `readable_body_phrasing_generation`). **[M]**
- **Is output deterministic?** Effectively yes. Draft-vs-refusal is already
  decided before EMIT (refusals return via `_route` earlier; **emit fired on
  70/70 drafts, 0/17 refusals over 30d**). `admissibility_layer` is
  recomputed from bundles regardless of what the model transcribes. **[M]**
- **Can it use fresh compact context / Haiku / be removed?** All three. It costs
  **avg 7,113 input → 134 output over 84 calls (30d)** to produce a discarded
  result. **[M]** The runtime can call `finalize_outcome` directly once
  `presented_candidates` is populated — no turn, no tokens.

**Evidence for Class D:** input provably unused (code), output provably a draft
(70/70), `admissibility_layer` recomputed. The only thing lost by removing it is
the *narrative* that "the model emits the outcome" (D-086) — functionally the
model authors nothing here.

---

## 6. Prompt caching — actual behaviour (all **[M]**)

`tool_turn` marks an ephemeral breakpoint on the **last tool** and on the
**system** (`gateway.py:389–411`). Because tools+system precede messages in the
request, the cached prefix = **tools + system = 13,364 tok**. The **message
history has no breakpoint**, so it is re-billed at full input rate every turn.

Observed activity, per meter:

| | Volume (tok) | Rate ($/M) | Cost | Share |
|---|---|---|---|---|
| Output | 21,651 | 15.00 | $0.3248 | **58.8%** |
| Uncached input (history replay) | 55,095 | 3.00 | $0.1653 | **29.9%** |
| Cache write (prefix, once) | 13,364 | 3.75 | $0.0501 | 9.1% |
| Cache read (prefix ×3) | 40,092 | 0.30 | $0.0120 | 2.2% |
| **Total** | | | **$0.5522** | |

Sonnet-5 production, 30 days (real `cost_usd`, $3.48 total):

| Component | Cost | Share |
|---|---|---|
| Output | $1.72 | **50%** |
| Uncached input replay | $1.01 | **29%** |
| Cache write | $0.65 | 19% |
| Cache read | $0.09 | 3% |

**Do correction turns benefit from cache?** Only for the static prefix (the
$0.09 line). The growing history — the thing the user is worried about — gets
**zero cache benefit today** because no message-level breakpoint exists.
**Caching did NOT solve the growth; it solved the flat 13k prefix.** The
dominant cost is **output (50–59%), which caching cannot touch at all.**

---

## 7. Alternative architectures

Anchored to the observed activity ($0.5522) and 30-day Sonnet production.

| Option | Quality risk | Coverage risk | Impl. complexity | Latency | Token↓ | Cost↓ | Observability | Rollback |
|---|---|---|---|---|---|---|---|---|
| **A. Current** | baseline | baseline | — | 4 turns / ~110 s | — | — | good | — |
| **A+ det. emit** | none (input unused) | none | **XS** (1 call-site) | −1 turn / −14–28 s | −7k in / −120 out per act. | **−16%** [E] | unchanged | trivial |
| **+ msg-prefix cache** | none | none | **XS** (1 breakpoint) | none | replay→10% | **−10–15%** [E] | unchanged | trivial |
| **B. Delta correction** | low* | **low if dedup correct** | **M** (delta protocol + merge) | −0–1 turn | −33% output waste + fixes dup | **−35–55%** [E] | better (delta logged) | medium |
| **C. Stateless turns** | med (loses in-context grounding memory) | med | **M–L** (rebuild compact state/turn) | similar | −input replay | −20–30% [E] | worse | medium |
| **D. Two-stage (plan→ground→expand)** | med | low | **L** (new phase) | +1 turn | reshuffles, not clearly less | ~0–20% [E] | better | high |
| **E. Hybrid Haiku-first + Sonnet escalate** | **med** (Haiku under-covers → more turns) | med | **M–L** | variable | model-price −67% *if turns hold* | **−30–60% only if loop already delta** [E] | same | medium |

\*Delta-B quality risk is low precisely because the substrate already verifies
every intent (Layer A + grounding); the model never needs to re-see accepted
intents to keep them.

**Read:** A+ and message-prefix caching are free wins that don't touch quality.
B is the real lever and *also fixes the dup defect*. E only pays off **after** B
(else every Haiku miss triggers a full regeneration — the current 33% waste
gets worse, not better).

---

## 8. The opportunity, quantified (30-day production; **[M]** unless noted)

**Current, per activity (Sonnet-5, `tenant_1.llm_calls`, 87 activities):**

| Metric | Value |
|---|---|
| avg / median / p90 turns | 2.40 / 2 / 4 |
| avg proposes / avg rejects | 1.44 / 0.54 |
| activities with ≥1 reject | 27/87 (31%) |
| activities multi-propose | 26/87 (30%) |
| avg / median / p90 input tok | 13,876 / 8,824 / 37,588 |
| avg / median / p90 output tok | 2,387 / 635 / 6,144 |
| avg / median / p90 cost (in+out) | $0.077 / $0.048 / $0.199 |
| avg latency/turn; worst observed activity | 14.4 s; ~110 s (4 turns) |
| **claims per draft (avg)** | **5.8** (70 drafts → 406 claims) |

The observed $0.55 activity is a **p90+ outlier**, not typical. Median is 2 turns
/ $0.048.

**Scenario estimates for the observed activity** (per-intent cost measured at
~268 out-tok/intent; **[E]** for non-current rows):

| Scenario | Turns | Output tok | Cost | Δ vs A |
|---|---|---|---|---|
| 1. Current (A) | 4 | 21,651 | $0.5522 | — |
| 2. Current + Haiku ($1/$5) | 4 | 21,651 | ~$0.19 | −66% *(price only; assumes turns hold — risky)* |
| 3. Sonnet + delta correction | 3 | ~7,400 | ~$0.22 | −60% |
| 4. Haiku + delta correction | 3 | ~7,400 | ~$0.09 | −84% |
| 5. Stateless compact Haiku | 3 | ~7,400 | ~$0.08 | −86% *(coverage risk)* |
| 6. Hybrid Haiku→Sonnet escalate | 3–4 | ~9–12k | ~$0.12 | −78% *(only with delta)* |

**Cost per accepted claim (the metric that matters):** observed activity
$0.5522 / 25 unique intents ≈ **$0.022/claim** today; with det-emit + delta
≈ **$0.008–0.010/claim [E]**. Median activity ≈ $0.048 / ~2–6 claims.

---

## 9. Is correction helping quality? (30-day, **[M]**)

Outcome × correction cross-tab (87 activities):

| Outcome | Clean (no reject) | After a reject | 
|---|---|---|
| **draft** | 52 (48 single + 4 multi) | **18 (13 multi + 5 single)** |
| **refusal** | 8 (single) | **9 (all multi)** |

- **% of reject-bearing activities that recover a usable draft:** **18/27 = 67%.**
  Correction is doing real work in the majority.
- **% that repeat failure and still refuse:** **9/27 = 33%** — **but all 9 are
  pre-2026-07-05**, i.e. the old 2048-token ceiling truncating the array
  mid-proposal (a death-spiral now fixed by D-313.1 → 8192). **Post-fix:
  zero reject→refusal.** **[M]**
- **Wasted (rejected, regenerated) share of propose output:** 33% all-time →
  **12.9% post-fix** (from 42% pre-fix). **[M]**
- **Post-fix multi-turn is now mostly the D-247 coverage re-prompt** (4/11
  activities) rather than Layer-A rejection (1/11). The coverage re-prompt is a
  *success* that adds real coverage (the missing ACs) — **it recovers useful
  tests** — but it regenerates the entire array to add a few, and (see below)
  currently **double-writes** the originals.

**Could some rejection categories be dropped rather than sent back?** The
truncation category already was (via the ceiling bump). The remaining Layer-A
rejections (bad enum, missing excerpt, unresolved ref) are cheap to fix and
worth correcting — but as a **delta**, not a full regeneration.

### ⚠ Verified defect — coverage re-prompt double-emits claims

The observed activity's final proposal had **29 intents** but the outcome wrote
**53 claims** (`claims_written` length **[M]**). Mechanism: turn-1's 25 grounded
candidates persist in `state.presented_candidates`; the coverage re-prompt makes
the model re-send the full ~29-intent array in turn 3; the runtime
**concatenates** turn-3's grounded candidates onto turn-1's
(`runtime.py:488`) with **no dedup** (no dedup logic exists in that path), so the
overlapping ~25 intents are grounded and emitted **twice**. This is a
correctness issue (duplicate claims) as well as a token issue, and it is exactly
what **delta correction removes for free**. Recommend a separate fix.

---

## 10. Final recommendation

1. **Is 2k→8.5k→16.5k→28k necessary?** *Partly.* Resending prior turns is
   mandatory in Anthropic tool-use. Resending them **uncached**, **regenerating
   the full proposal per correction**, and adding a **4th emit turn** are all
   PrimeQA choices and are **not** necessary.
2. **What causes most of the growth?** Replayed **assistant proposals**
   (5,981 + 7,775 + 7,775 = 21.5k tok by turn 4), uncached. The 13k system+tools
   prefix is cached and flat.
3. **% removable without losing coverage?** Of the observed activity: emit turn
   (~5–10% of tokens, 16% of cost) is pure removal; delta correction removes
   ~one full 7.8k-token regeneration; message-prefix caching drops the replayed
   input to ~10%. **≈40–55% of tokens/cost removable at equal coverage [E]**,
   plus the duplicate-claim halving on coverage-reprompt activities.
4. **Full-regeneration or delta correction?** **Delta.** Full regeneration is the
   single largest avoidable cost and the cause of the dup defect.
5. **Should `emit_outcome` stay an LLM call?** **No — make it deterministic**
   (Class D). Its input is provably unused and it is always a draft when reached.
   If you want a staged rollout, run emit on **Haiku** first, then remove it.
6. **Optimise before benchmarking Haiku?** **Yes.** Haiku changes the
   *per-token* price but interacts badly with a full-regeneration correction
   loop (more misses × full regenerations). Land det-emit + delta first, then the
   Haiku A/B measures the architecture you'll actually ship.
7. **Smallest change, highest saving:** **make `emit_outcome` deterministic**
   (one call-site: replace the forced EMIT turn with a direct
   `finalize_outcome` call). Verified-safe, −16% cost, −1 turn, −~14–28 s
   latency. Runner-up (also XS, stackable): add a **message-prefix cache
   breakpoint** on the last history message each turn.
8. **Target architecture:**
   ```
   requirement
     → propose (full, once)                       [Sonnet, unchanged]
     → substrate grounds + holds accepted set
     → delta correction: send {failed ids+reason, uncovered AC labels};
       model returns ONLY the delta; substrate dedupes + merges     [fixes dup]
     → deterministic finalize_outcome              [no LLM turn]
     → (later) Haiku-first + Sonnet escalation A/B  [measured on the above]
   ```

---

## Verification appendix (reproducible)

- Observed activity: `SELECT * FROM tenant_1.llm_calls WHERE generation_outcome_id='ab65fb0c-430a-408d-b0d9-4747988d3b00'` — 4 rows: propose/success 2084·5981, propose/rejected 8494·7775, propose/success 16490·7775, emit/success 28027·120. **[M]**
- `llm_usage_log` rows 544–547: cache_write=13,364 (turn1), cache_read=13,364 (turns 2–4); `cost_usd` reconciles at $3/$15/$0.30/$3.75 per M. **[M]**
- `finalize_outcome` body (governance_core.py:3103–3183) contains no reference to `outcome_input`. **[M]**
- emit fired 70/70 drafts, 0/17 refusals (30d). **[M]**
- 53 claims written for a 29-intent proposal; `runtime.py:488` concatenates with no dedup. **[M]**
- Prompt `generation@v22`: system ~28,667 chars (~10.6k tok billed), tools ~7,274 chars (~2.7k tok billed). **[M]**
