# S3 Generation — Token & Cost Review

**Date:** 2026-07-25
**Scope:** the S3 claim/recipe generation path end to end (`primeqa/generation/*`), the shared
LLM gateway (`primeqa/intelligence/llm/*`), and the cost/usage recording layer.
Explicitly out of scope: S4 execution, S6 attribution, readable-body rendering.
**Mode:** read-only diagnostic. No code changed. Tracked working tree clean before and after;
the only file written is this one.

Every claim below is marked **[V]** (verified — file:line I opened this session, or output of a
command I actually ran) or **[A]** (assumed / inferred — stated as such). Live measurements come
from the Railway production database (`tenant_1` schema + public `llm_usage_log`) and from
Anthropic's `count_tokens` endpoint (a free, non-generating call).

---

## A. Instrumentation baseline — what is measurable today

### A.1 Where token usage is recorded — two tables, no join

There are exactly two recording sites on this path.

**1. `public.llm_usage_log`** — written by `usage.record()`
([usage.py:20](../../primeqa/intelligence/llm/usage.py#L20)), called from the single gateway
chokepoint `_invoke_and_record()`
([gateway.py:362](../../primeqa/intelligence/llm/gateway.py#L362)). One row per provider call,
success or failure. **[V]**

Live schema (`information_schema.columns`, run this session) **[V]**:

| column | type | populated for `task='generation'`? |
|---|---|---|
| `id`, `ts`, `tenant_id`, `task`, `model`, `prompt_version` | — | yes |
| `input_tokens`, `output_tokens` | integer | yes |
| `cached_input_tokens`, `cache_write_tokens` | integer | yes |
| `cost_usd` | numeric | yes |
| `latency_ms`, `status`, `complexity`, `escalated` | — | yes |
| `request_id` (provider request id) | varchar | **391/391** |
| `run_id` | integer | **0 / 391** |
| `requirement_id` | integer | **0 / 391** |
| `test_case_id` | integer | **0 / 391** |
| `generation_batch_id` | bigint | **0 / 391** |
| `sync_run_id` | uuid | **0 / 391** |
| `user_id` | integer | **0 / 391** |
| `context` (jsonb) | jsonb | **0 / 391 non-empty** |

**2. `tenant_1.llm_calls`** — the S3-local operational-telemetry table
([models_db.py:302](../../primeqa/generation/models_db.py#L302)), written by
`LedgerPersister._insert_llm_calls`
([persistence.py:263](../../primeqa/generation/persistence.py#L263)). It has a **real FK to
`generation_outcomes.outcome_id`** ([models_db.py:316-324](../../primeqa/generation/models_db.py#L316)).
Captured fields: `tool_name`, `operational_outcome`, `attempt_index`, `model_identifier`,
`prompt_version`, `timing_duration_ms`, `token_count_input`, `token_count_output`,
`raw_parameters`, `raw_response`. **[V]**

It does **not** have: `cost_usd`, cache-read tokens, cache-write tokens, or the provider
`request_id`. **[V]** — the full column list is at
[models_db.py:314-355](../../primeqa/generation/models_db.py#L314) and the in-memory record
`LlmCallRecord` at [runtime.py:72-89](../../primeqa/generation/runtime.py#L72) carries the same
seven value fields and nothing more.

### A.2 What attribution is possible today

| Attribute cost/tokens to… | Tokens | Cost | Cache activity |
|---|---|---|---|
| a single generation **run** (request_id) | **YES** via `llm_calls → generation_outcomes.request_id` **[V]** | **NO** | **NO** |
| a single **claim** | **YES** via `generation_outcomes.claims_written` **[V]** | **NO** | **NO** |
| a single **recipe** | **NO** — recipes are minted inside the same emission bundle; no per-recipe token record exists **[V]** | NO | NO |
| a single **org / environment** | **NO** — `environment_id` is a `run_generation` argument ([run.py:44](../../primeqa/generation/run.py#L44)) and is never persisted onto a usage or call row **[V]** | NO | NO |
| a **tenant** | YES (`llm_usage_log.tenant_id`) **[V]** | YES **[V]** | YES **[V]** |

**Stated plainly: cost records cannot be joined to generation outcomes.** The missing key is a
shared identifier. `llm_usage_log` carries the provider `request_id` (391/391 populated) and
`llm_calls` carries none; `llm_calls` carries `generation_outcome_id` and `llm_usage_log` carries
none. There is no third table bridging them. **[V]**

The mechanism is one call site: `build_tool_turn_fn`
([gateway_binding.py:38-42](../../primeqa/generation/gateway_binding.py#L38)) forwards only
`task`, `tenant_id`, `api_key`, `max_tokens`, `model_override`, `messages`, `tools`,
`tool_choice`, `system`. Meanwhile `gateway.tool_turn` **already accepts** `run_id`,
`requirement_id`, `test_case_id`, `generation_batch_id`, `user_id` and `context_for_log`
([gateway.py:441-443](../../primeqa/intelligence/llm/gateway.py#L441)) and forwards every one of
them to `usage.record` ([gateway.py:482-490](../../primeqa/intelligence/llm/gateway.py#L482)).
The parameters exist and are simply never passed. **[V]**

### A.3 Consequence for this review

Because cost is not attributable per run, every per-run cost figure in section B was derived by
**correlating on wall-clock timestamp** between `llm_usage_log` and `tenant_1.llm_calls` for a
run whose token totals match exactly. That correlation is sound for the specific runs cited
(fresh in/out match to the token), but it is not a durable mechanism — see §E Gap 1.

---

## B. Measured baseline

### B.1 Share of total LLM spend

```
SELECT task, count(*), sum(input_tokens), sum(output_tokens),
       sum(cached_input_tokens), sum(cache_write_tokens), round(sum(cost_usd)::numeric,2)
FROM llm_usage_log GROUP BY task ORDER BY sum(cost_usd) DESC;
```

Output (all time) **[V]**:

| task | n | input | output | cache read | cache write | recorded USD |
|---|---:|---:|---:|---:|---:|---:|
| **generation** | 391 | 2,999,036 | 836,859 | 3,483,032 | 1,001,197 | **$42.86** |
| generation_live_eval | 34 | 19,847 | 6,426 | 254,562 | 7,714 | $0.26 |
| readable_body_phrasing_generation | 47 | 26,844 | 6,737 | 0 | 0 | $0.09 |
| readable_run_phrasing_generation | 16 | 10,065 | 2,520 | 0 | 0 | $0.07 |
| entity_summary_validation_rule | 65 | 18,871 | 3,620 | 0 | 0 | $0.04 |
| entity_summary_flow | 33 | 9,450 | 1,970 | 0 | 0 | $0.02 |
| embedding_generation | 170 | 252,898 | 0 | 0 | 0 | $0.02 |
| grounded_answer_generation | 1 | 0 | 0 | 0 | 0 | $0.00 |

**S3 generation is 98.9% of all recorded LLM spend** ($42.86 of $43.36). **[V]**

Status breakdown: 388 `ok`, 1 `quota_exceeded`, 1 `content_error`, 1 `auth_error` — all three
error rows are zero-token. **[V]**

### B.2 Per-run call count and fan-out shape

Generation is a **staged multi-pass tool-use conversation, one conversation per requirement**
(`_run_requirement`, [runtime.py:429](../../primeqa/generation/runtime.py#L429)); it is **not**
one call per claim. All intents for a requirement ride a single `propose_semantic_intent` call as
the `intent_descriptors` array (cap `MAX_INTENTS = 30`,
[tools.py:90](../../primeqa/generation/tools.py#L90)). **[V]**

Measured distribution over `tenant_1.llm_calls` (449 rows, 165 outcomes) **[V]**:

| calls per outcome | outcomes |
|---:|---:|
| 1 | 10 |
| 2 | 58 |
| **3** | **72** |
| 4 | 18 |
| 5 | 7 |

Tool × outcome mix **[V]**:

| tool | operational_outcome | n | avg fresh-in | avg out |
|---|---|---:|---:|---:|
| propose_semantic_intent | success | 243 | 4,513 | 2,617 |
| emit_outcome | success | 139 | 8,485 | 118 |
| propose_semantic_intent | rejected_for_correction | 44 | 4,891 | 2,317 |
| emit_outcome | rejected_for_correction | 23 | 10,704 | 164 |

**`select_canonical` has fired zero times in 449 recorded calls.** **[V]** Its code path is
nevertheless live-reachable ([runtime.py:565](../../primeqa/generation/runtime.py#L565), taken
when `res.next_action == NextAction.AWAIT_SELECTION`).

**The smallest real full run** is 3 calls. Concrete instance — request
`f248794f-0026-4960-a798-86448f7f9edb`, requirement **req-320**, prompt `generation@v32`, model
`claude-sonnet-5`, 2026-07-24 10:05:55–10:06:06 UTC. Row-level `llm_usage_log` **[V]**:

| turn | fresh input | cache read | cache write | output | cost | latency |
|---:|---:|---:|---:|---:|---:|---:|
| 1 (propose) | 2,089 | 0 | **15,810** | 3,906 | $0.1241 | 26.3 s |
| 2 | 6,628 | 15,810 | 0 | 702 | $0.0352 | 5.5 s |
| 3 (emit) | 8,327 | 15,810 | 0 | 99 | $0.0312 | 1.4 s |
| **total** | **17,044** | **31,620** | **15,810** | **4,707** | **$0.1905** | 33.2 s |

Cross-check: the `tenant_1.llm_calls` roll-up for that same `request_id` reads
`calls=3 in=17044 out=4707` — an exact match on fresh in/out, confirming the correlation. **[V]**

### B.3 Cost decomposition of that run

Rates from [pricing.py:36](../../primeqa/intelligence/llm/pricing.py#L36) +
[pricing.py:25-26](../../primeqa/intelligence/llm/pricing.py#L25): `claude-sonnet-5` =
$3.00/MTok input, $15.00/MTok output, cache read = 0.10× input = $0.30/MTok, cache write =
1.25× input = $3.75/MTok. **[V]** I independently solved the same two rates from the three
observed `cost_usd` values (three equations, two unknowns) and got $3.00/$15.00 — the recorded
costs are internally consistent with the table. **[V]**

| line | tokens | cost | share |
|---|---:|---:|---:|
| Static prefix — **cache write** (grammar + tool schemas) | 15,810 | $0.0593 | **31.1%** |
| Static prefix — cache read (2 turns) | 31,620 | $0.0095 | 5.0% |
| **↳ static prefix subtotal** | | **$0.0688** | **36.1%** |
| Re-sent conversation history (turns 2–3, uncached) | 14,955 | $0.0449 | **23.6%** |
| Genuinely new per-requirement input (turn 1) | 2,089 | $0.0063 | 3.3% |
| Output (all turns) | 4,707 | $0.0706 | **37.1%** |
| **total** | | **$0.1905** | 100% |

**96.7% of the input spend on this run is content the model has seen before** — either the frozen
static prefix (36.1%) or the conversation's own earlier turns replayed (23.6%), against 3.3% that
is new.

### B.4 Prompt composition — measured

**Method:** I reconstructed the *real* turn-1 prompt for the run above — the pinned frozen prompt
`generation@v32` read via `prompts_registry.get()`, the live `TOOLS` list, the real req-320
requirement text read from `tenant_1.generation_requests.semantic_context`, and the ORG FIELD
VOCABULARY block rebuilt from the live env-59 S1 symbol table via
`build_field_vocabulary(...)` — then assembled it byte-for-byte as
`_initial_user_message` does ([runtime.py:142-154](../../primeqa/generation/runtime.py#L142)) and
counted it with Anthropic `messages.count_tokens(model="claude-sonnet-5")`. This is measured, not
estimated from the template. **[V]**

**Validation of the reconstruction:** reconstructed cached prefix = **15,877** tokens vs live
observed `cache_write_tokens` = **15,810** — a 0.4% delta (block-boundary accounting). The
reconstruction is faithful. **[V]**

| component | tokens | share of the 17,768-token turn-1 request |
|---|---:|---:|
| **Static instructions** — frozen system prompt `generation@v32` | **12,767** | **71.9%** |
| **Output contract** — the 3 tool JSON schemas | **3,110** | **17.5%** |
| &nbsp;&nbsp;↳ `propose_semantic_intent` | 2,386 | 13.4% |
| &nbsp;&nbsp;↳ `select_canonical` (never fires — §B.2) | 842 | 4.7% |
| &nbsp;&nbsp;↳ `emit_outcome` | 590 | 3.3% |
| **Injected org context** — ORG FIELD VOCABULARY block | **1,201** | **6.8%** |
| Requirement text | 587 | 3.3% |
| Shared interpretation context | 74 | 0.4% |
| Call instruction | 16 | 0.1% |
| **total** | **17,768** | 100% |

**89.4% of the turn-1 prompt is static and requirement-independent.**

Answers to the remaining §3 sub-questions:

- **Few-shot examples: none.** The frozen prompt is instructional; there is no exemplar block.
  Structural enforcement is done by the tool JSON schema + `validate_layer_a`, not by examples. **[V]**
- **Retrieved chunks (pgvector): none on this path.** `grep -rn "vector|embedding|similarity_search|pgvector" primeqa/generation/*.py primeqa/generation/prompts/*.py` returns only three unrelated prose comments. **[V]** The one retrieval-like component is the ORG FIELD VOCABULARY block, which is **deterministic, not embedding-based**: `_rank_objects` scores org objects by verbatim label presence and token-coverage ratio ([vocabulary.py:45-77](../../primeqa/generation/vocabulary.py#L45)) against a version-pinned S1 symbol table. So there is no retrieval *k* and no chunk size in the usual sense; the caps are `MAX_OBJECTS = 3`, `MAX_ADJACENT = 4`, `MAX_FIELDS_PER_OBJECT = 40`, `MAX_PICKLIST_VALUES = 6`
  ([vocabulary.py:30-33](../../primeqa/generation/vocabulary.py#L30)). Dedup: objects already
  ranked are excluded from the adjacency pass via `exclude={o.api_name for o in objects}`
  ([vocabulary.py:135](../../primeqa/generation/vocabulary.py#L135)); universal audit fields are
  dropped ([vocabulary.py:40-42](../../primeqa/generation/vocabulary.py#L40)). **[V]**
- **Prior-turn history:** yes, and it is the whole of the turn-2/3 growth — see §C-2.

### B.5 Cache effectiveness over time

```
SELECT date_trunc('day', ts)::date, count(*), sum(input_tokens), sum(cached_input_tokens),
       sum(cache_write_tokens)
FROM llm_usage_log WHERE task='generation' AND status='ok' GROUP BY 1 ORDER BY 1 DESC;
```

**[V]**

| day | turns | fresh in | cache read | cache write | read:write |
|---|---:|---:|---:|---:|---:|
| 2026-07-24 | 3 | 17,044 | 31,620 | 15,810 | **2.00** |
| 2026-07-22 | 21 | 127,223 | 219,568 | 109,784 | **2.00** |
| 2026-07-21 | 9 | 53,093 | 92,398 | 46,199 | **2.00** |
| 2026-07-16 | 6 | 30,900 | 52,612 | 26,306 | **2.00** |
| 2026-07-13 | 63 | 424,007 | 842,105 | 122,488 | 6.87 |
| 2026-07-12 | 126 | 789,890 | 1,639,244 | 248,680 | 6.59 |
| 2026-07-08 | 27 | 178,842 | 187,096 | 173,732 | 1.08 |
| 2026-07-07 | 16 | 152,927 | 104,572 | 103,636 | 1.01 |

A read:write ratio of **exactly 2.00** means the prefix is written once per requirement and read
exactly twice — i.e. **the cache is never reused across runs on those days**. On the high-volume
days (07-12, 07-13) the ratio is ~6.6, i.e. the cache *is* shared across back-to-back
requirements. The discriminator is elapsed time between runs versus the 5-minute TTL.

---

## C. Findings

Each finding: **mechanism → evidence → estimated share of total spend.** Shares are of the
$0.1905 canonical run (§B.3), which is representative of the 3-call shape (72 of 165 outcomes).

### C-1. The 15,810-token static prefix is re-written from scratch on most runs — 31.1%

**Mechanism.** Caching *is* wired: `tool_turn` marks the system prompt and the last tool schema
with an ephemeral breakpoint ([gateway.py:484-485](../../primeqa/intelligence/llm/gateway.py#L484),
via `_system_with_cache` / `_tools_with_cache` at
[gateway.py:389-411](../../primeqa/intelligence/llm/gateway.py#L389)). But the breakpoint is
`_EPHEMERAL = {"type": "ephemeral"}` ([gateway.py:386](../../primeqa/intelligence/llm/gateway.py#L386))
with **no `ttl` key**, i.e. the 5-minute default. Generation jobs on ordinary days are 12–90
minutes apart, so each run misses and re-pays the 1.25× write.

**Evidence.** §B.5 read:write = exactly 2.00 on 07-16/21/22/24 **[V]**; the observed inter-run
gaps on 2026-07-22 were 26 m, 38 m, 29 m, 12 m, 4 h 44 m, 1 h 29 m (from the row-level
timestamps in §B.2's query) **[V]**; `_EPHEMERAL` has no `ttl` **[V]**.

**Share: 31.1%** of the canonical run is the cache-write line for content that has not changed
since the v32 freeze.

### C-2. Turns 2–3 re-send the entire conversation history uncached — 23.6%

**Mechanism.** The runtime passes the accumulating `state.messages` to every turn
([runtime.py:462-465](../../primeqa/generation/runtime.py#L462)); each turn appends the assistant
`tool_use` block plus the substrate's `tool_result`
([runtime.py:630-634](../../primeqa/generation/runtime.py#L630)). The gateway marks **only**
`system` and `tools` for caching — the `messages` array carries no `cache_control` breakpoint
anywhere ([gateway.py:482-490](../../primeqa/intelligence/llm/gateway.py#L482)). So every turn
re-pays full input rate for every earlier turn.

**Evidence.** Fresh input per turn on the canonical run: 2,089 → 6,628 → 8,327 **[V]**. The
growth reconciles arithmetically: turn 1's 3,906 output tokens reappear as turn-2 input
(2,089 + 3,906 = 5,995, + ~633 tool_result = 6,628), and again inside turn 3's 8,327. Turn 1's
proposal is therefore paid for **three times**: once as output, twice as input. Only 2 of
Anthropic's 4 permitted breakpoints are in use. **[V]**

**Share: 23.6%.**

### C-3. Cost is unattributable to any generation run, claim, or org — 0% direct, blocks all measurement

**Mechanism.** §A.2. `build_tool_turn_fn` drops six cross-reference parameters that
`gateway.tool_turn` already accepts.

**Evidence.** `run_id / requirement_id / test_case_id / generation_batch_id / sync_run_id /
user_id` = 0 populated across all 391 generation rows; `context` empty on all 391 **[V]**.
[gateway_binding.py:38-42](../../primeqa/generation/gateway_binding.py#L38) vs
[gateway.py:441-443](../../primeqa/intelligence/llm/gateway.py#L441). **[V]**

**Share: 0% of spend, but it is the precondition for verifying any other item here.**

### C-4. Every regeneration is a full from-scratch run; the incremental path is defined but unimplemented — dominates aggregate spend

**Mechanism.** `GenerationRequest` carries `prior_request_id`, `regeneration_kind` and `deltas`
for exactly this purpose ([protocol.py:191-199](../../primeqa/generation/protocol.py#L191),
D-071), and `generation_requests` persists all three
([persistence.py:57-67](../../primeqa/generation/persistence.py#L57)). The production intake
path hard-codes all three to `None`: *"A fresh request carries no lineage (`prior_request_id` /
`deltas` / `regeneration_kind` all `None`)"*
([intake.py:72-76](../../primeqa/generation/intake.py#L72)) — `build_generation_request` never
sets them ([intake.py:80-90](../../primeqa/generation/intake.py#L80)). **[V]**

**Evidence.** `SELECT count(prior_request_id) FROM tenant_1.generation_requests` → **0 of 223**. **[V]**
Repeat-generation counts per requirement key **[V]**:

| requirement key | generations | window |
|---|---:|---|
| req-320 | **71** | 2026-07-11 → 07-24 |
| 315 | 33 | 2026-07-09 → 07-10 |
| req-315 | 17 | 2026-07-08 → 07-22 |
| req-302 | 14 | 2026-07-01 → 07-08 |

Top 4 keys = 135 of 223 requests (61%). Each is a full ~$0.19 run that re-derives every AC,
including the ones that already grounded on the previous pass.

**Share: not a per-run line — a run-count multiplier.** It is the largest single driver of the
$42.86 aggregate.

### C-5. 64% of derived candidate paths produce no claim — bounded by grounding, not waste per se

**Mechanism.** Three discard paths, all counted:

| discard path | counted where | measured |
|---|---|---|
| Layer-A structural rejection → `rejected_for_correction` + corrective `tool_result` ([runtime.py:599-615](../../primeqa/generation/runtime.py#L599)) | `llm_calls` row **and** an `llm_usage_log` row (`_invoke_and_record` always records) **[V]** | 67 of 449 calls (14.9%); 230 of 2,866 intents (8.0%) discarded and re-proposed **[V]** |
| Grounding refusal (governance) → `partial_refusals` on `attempted_interpretation` ([runtime.py:908-930](../../primeqa/generation/runtime.py#L908)) | `generation_outcomes` JSONB **[V]** | 1,817 partial refusals **[V]** |
| Identity dedup — D-339 collapse at `finalize_outcome` ([governance_core.py:6752-6789](../../primeqa/generation/governance_core.py#L6752)) + persister same-hash no-op ([persistence.py:117-138](../../primeqa/generation/persistence.py#L117)) | logged, not token-accounted **[V]** | not separable from the above |

**Evidence.** 2,257 candidate paths → 812 claims written across 165 outcomes = **64.0% produced
no claim** **[V]**. Per-AC verdicts: 480 `covered` vs 499 `ungrounded_after_reprompt` plus ~50
model-authored `no_admissible_test` reasons **[V]**. The D-247/D-340 recovery re-prompt — a full
extra propose turn, avg 2,617 output tokens — fires on **73 of 165 outcomes (44%)** **[V]**.

**Share:** the recovery hop alone is roughly one extra propose turn on 44% of runs. On the
canonical shape that is ≈ +$0.055 amortised (~29% of a 3-call run's cost, ASSUMED — I did not
isolate a 4-call run's marginal cost). **[A]**

This is *not* straightforwardly waste: the refusals are the grounding contract working as
designed. It is reported here because it is where the output tokens go.

### C-6. `select_canonical`'s schema rides the cached prefix on every turn but has never fired — 1.9%

**Mechanism.** `TOOLS` is a module constant sent whole on every turn
([tools.py:306-310](../../primeqa/generation/tools.py#L306); passed at
[runtime.py:463](../../primeqa/generation/runtime.py#L463)).

**Evidence.** 842 measured tokens (§B.4) of the 15,810-token prefix = 5.3% of the prefix,
= **1.9%** of run cost. Zero `select_canonical` rows in 449 calls **[V]**. Its only consumer is
[governance_core.py:6698-6701](../../primeqa/generation/governance_core.py#L6698). The code path
is reachable ([runtime.py:565](../../primeqa/generation/runtime.py#L565)), so it is *unused*, not
*dead*.

### C-7. `failure_mode_framing` is written by the model and read by nothing — 0.25%

**Mechanism.** Declared in the shared descriptor schema
([tools.py:110-113](../../primeqa/generation/tools.py#L110)).

**Evidence.** `grep -rn "failure_mode_framing" primeqa/ --include="*.py"` returns **only**
`tools.py` — no reader anywhere in the codebase **[V]**. Populated on **395 of 2,866 intents
(13.8%)**, 28,381 characters ≈ 7,095 output tokens over the whole corpus ≈ $0.11 at $15/MTok **[V]**.

By contrast `claim_kind_hint` (94.0% populated) and `no_admissible_test` (12.3%) both have real
consumers ([tools.py:438-451](../../primeqa/generation/tools.py#L438),
[runtime.py:674-676](../../primeqa/generation/runtime.py#L674)). **[V]**

### C-8. Recorded cost may overstate actual billing — accounting integrity

**Mechanism.** `pricing.py` bills `claude-sonnet-5` at list $3.00/$15.00
([pricing.py:36](../../primeqa/intelligence/llm/pricing.py#L36)).

**Evidence.** Anthropic's current pricing reference (loaded via the `claude-api` skill this
session, not from this repo) lists Claude Sonnet 5 at **$3.00 ($2.00 introductory through
2026-08-31) / $15.00 ($10.00 introductory)**. **[V]** that the reference says this; **[A]** that
the tenant's account is actually billed at the introductory rate — I did not inspect an invoice.
If it is, the recorded $42.86 overstates real spend by roughly one third. The catalog overlay
(`llm_models.input_usd_per_mtok`, [pricing.py:66-92](../../primeqa/intelligence/llm/pricing.py#L66))
is the intended place to correct this without a code change. **[V]**

---

## D. Recommendations

Ordered by leverage within each risk band, NONE-risk band first. "Semantic risk" = any
possibility of degrading grounding fidelity, weakening the grounding validator's inputs, or
changing what a generated claim means.

### Risk: NONE — the model sees byte-identical content, or only accounting changes

**D-1. Add a cache breakpoint to the `messages` array.**
**Leverage: HIGH — ~9% of run cost.**
Basis: modelled from the measured per-turn token counts in §B.2 at the §B.3 rates. Marking the
last message block each turn makes turn 2 write the 4,539-token increment (1.25×) and turn 3 read
the 6,628-token prefix (0.1×) instead of both re-paying full input rate: $0.0449 → $0.0276 on the
canonical run, **−$0.0173 = −9.1%**. Saving grows with turn count (18 of 165 outcomes are 4-call,
7 are 5-call **[V]**). Only 2 of Anthropic's 4 permitted breakpoints are currently used
([gateway.py:484-485](../../primeqa/intelligence/llm/gateway.py#L484)), and S3 adds 2 blocks per
turn — far inside the 20-block lookback window. The model receives exactly the same bytes; this
is purely how the provider bills the prefix. **[A]** on the precise saving (a modelled projection,
not an observed A/B); **[V]** on every input to the model.

**D-2. Thread the cross-reference ids through `build_tool_turn_fn`.**
**Leverage: HIGH — 0% direct saving, but it is the gate on measuring D-1, D-3 and everything in §C.**
Basis: `gateway.tool_turn` already accepts and forwards all six parameters
([gateway.py:441-443](../../primeqa/intelligence/llm/gateway.py#L441),
[gateway.py:482-490](../../primeqa/intelligence/llm/gateway.py#L482)) — the closure simply drops
them ([gateway_binding.py:38-42](../../primeqa/generation/gateway_binding.py#L38)). Passing the
S3 `request_id` in `context_for_log` (a JSONB column that is empty on all 391 rows **[V]**)
creates the join key to `generation_outcomes` with no schema change. Nothing about the request
sent to Anthropic changes.

**D-3. Set `ttl: "1h"` on the static-prefix breakpoint.**
**Leverage: MEDIUM — bounded by run spacing; ~5% on the observed 2026-07-22 pattern, more under bursty load.**
Basis: `_EPHEMERAL` currently has no `ttl` ([gateway.py:386](../../primeqa/intelligence/llm/gateway.py#L386)).
1h TTL costs 2× to write (vs 1.25×) and 0.1× to read, so it needs ≥3 requests inside the window
to pay off. Modelling the seven 2026-07-22 runs (gaps 26/38/29/12 m, 4 h 44 m, 1 h 29 m **[V]**):
4 writes + 3 reads = $0.394 vs 7 writes = $0.415, a 5% saving on that day. On the 07-12/07-13
back-to-back pattern the 5-minute TTL already works (read:write 6.6) and 1h adds little. **[A]**
on the projection. I am deliberately not overselling this: measured run spacing is the binding
constraint, and D-1 is the larger and more reliable win.

**D-4. Correct the `claude-sonnet-5` rate via the `llm_models` catalog overlay if the account is on introductory pricing.**
**Leverage: LOW as a saving (zero — it changes no token), HIGH as accounting integrity.**
Basis: C-8. This is a data change in `llm_models`, not code
([pricing.py:66-99](../../primeqa/intelligence/llm/pricing.py#L66)). Confirm the invoice rate
first — see §E Gap 3.

### Risk: LOW — no change to grounding inputs, but a real behavioural or contract change

**D-5. Drop `failure_mode_framing` from the descriptor schema.**
**Leverage: LOW — ~0.25% of spend ($0.11 over the whole corpus).**
Basis: C-7 (zero consumers verified by grep; 13.8% population measured). Risk is LOW rather than
NONE because removing a field the model currently fills changes the shape of what it produces on
the propose turn, and no A/B exists showing proposal quality is unaffected.

**D-6. Consider deferring the `select_canonical` schema.**
**Leverage: LOW — 1.9% of run cost (842 measured tokens of the cached prefix).**
Basis: C-6. Risk is LOW, not NONE, precisely because the SELECT phase is *reachable*
([runtime.py:565](../../primeqa/generation/runtime.py#L565)) even though it has never fired in
449 calls — removing the tool outright would turn a live path into a hard failure. Any change
here must keep the path intact. Note also that altering `tools` invalidates the tools+system cache
prefix, so this is a one-time re-write cost, not a recurring one.

### Risk: HIGH — trades grounding fidelity for tokens

**D-7. Implement incremental regeneration (`prior_request_id` / `deltas`).**
**Leverage: HIGH on aggregate spend — the top 4 requirement keys account for 61% of all 223 requests, every one a full re-derivation.**
Basis: C-4. **This is the structural root cause of the aggregate bill, and I am naming it as such
even though the fix is large.** The protocol and the persisted schema were designed for it
(D-071, [protocol.py:191-199](../../primeqa/generation/protocol.py#L191)); only the intake path
is missing. Marked HIGH risk without qualification: skipping re-derivation of an AC means the
grounding validator does not re-run against the current S1 snapshot, so a claim could silently
survive an org change that should have invalidated it — exactly the failure mode S8 grounding-
validity exists to catch. Any design here needs the S1-version-delta test to be the gate, and
that is a substrate design task, not a cost optimisation.

**D-8. Tune the D-247/D-340 recovery re-prompt.**
**Leverage: MEDIUM-HIGH — fires on 44% of outcomes, one extra propose turn each.**
Basis: C-5. Marked HIGH risk: the hop exists specifically to recover ACs whose first-pass intent
failed to ground ([runtime.py:541-556](../../primeqa/generation/runtime.py#L541)), and D-340 made
"covered" mean *grounded* rather than merely tagged. Suppressing or narrowing it directly reduces
grounded-per-AC coverage. Not recommended as a cost measure; listed because it is where a large
share of output tokens goes, and because the 499 `ungrounded_after_reprompt` verdicts suggest the
hop frequently spends a full turn for no yield — that is a **quality** question worth its own
investigation, with cost as a side effect.

---

## E. Gaps — what I could not answer read-only

**Gap 1 — Per-run cost cannot be verified going forward.** Every per-run cost figure in §B is a
timestamp correlation between two tables with no shared key (§A.3). It is sound for the runs I
cited (token totals match exactly) but cannot be reproduced systematically or for concurrent runs.
*Needed:* D-2 (thread `request_id` into `context_for_log`). This is a one-line change I did not
make, per the read-only constraint.

**Gap 2 — D-1's and D-3's savings are modelled, not observed.** I derived them from measured
token counts and verified rates, but no A/B run exists. *Needed:* a live generation run with the
breakpoint added, comparing `cache_read_input_tokens` / `cache_creation_input_tokens` before and
after. That requires a code change and a live LLM call.

**Gap 3 — Whether the account bills at Sonnet 5 introductory rates.** Determines whether the
recorded $42.86 is accurate or ~33% high (C-8). *Needed:* the Anthropic console invoice or usage
export — outside this repo.

**Gap 4 — Marginal cost of the recovery hop.** I measured that it fires on 44% of outcomes and
that propose turns average 2,617 output tokens, but did not isolate the cost delta between a
3-call and 4-call run. *Needed:* a per-outcome cost roll-up, which needs Gap 1 closed first.

**Gap 5 — Whether generation ever runs unattended.** Answering §6 of the brief: generation is
**never triggered by org re-sync**. `grep -rn "generation" primeqa/sync/*.py` returns only
docstring cross-references, no enqueue **[V]**. The only callers of `enqueue_s3_generation` are
`views.py:4155` (manual UI action), `repair_agent.py:499` (S8 repair proposal) and
`s3_generation_console.py:72` **[V]**. Jobs are idempotent on `(requirement_key, s1_version_seq)`
([intake.py:103](../../primeqa/generation/intake.py#L103)), so a *re-sync alone* mints no work —
but an explicit "Regenerate" click passes `force_rerun=True` and re-runs a terminal job in place
([intake.py:105-107](../../primeqa/generation/intake.py#L105)) **[V]**. So generation runs over
neither the full org surface nor changed entities: it runs **per requirement, on demand**.
Previously-generated claims are reused only in the narrow sense that a same-identity-hash
regeneration re-versions the existing test instead of minting a duplicate
([persistence.py:117-138](../../primeqa/generation/persistence.py#L117)) — the LLM work is
re-paid in full regardless **[V]**. What I could *not* determine read-only is how many of the 71
req-320 runs were development iteration versus a workflow that would recur in production use.

**Gap 6 — Per-recipe attribution is structurally impossible.** Claims and recipes are written
together in one emission bundle; no token or cost record is scoped below the outcome. Answering
"what did this recipe cost" would need a new record, not a new query.

---

## Verification

Commands run this session (all read-only):

```bash
git branch --show-current                      # -> main
git status --porcelain --untracked-files=no    # -> (empty; tracked tree clean)
./venv/bin/python <scratchpad>/q_usage.py      # §B.1, B.5, A.1 population counts
./venv/bin/python <scratchpad>/q_rows.py       # §B.2 row-level turns + llm_calls roll-up
./venv/bin/python <scratchpad>/q_compose5.py   # §B.4 count_tokens against claude-sonnet-5
./venv/bin/python <scratchpad>/q_waste.py      # §C-5 candidate paths, coverage verdicts
grep -rn "failure_mode_framing" primeqa/ --include="*.py"      # -> tools.py only
grep -rn "vector|embedding|pgvector" primeqa/generation/*.py   # -> 3 unrelated comments
grep -rn "enqueue_s3_generation" primeqa/ --include="*.py"     # -> 3 callers, none in sync/
```

All query scripts live in the session scratchpad, outside the repository. No file inside the
repository other than `docs/reviews/S3_TOKEN_COST_REVIEW.md` was created or modified; the tracked
working tree is clean.
