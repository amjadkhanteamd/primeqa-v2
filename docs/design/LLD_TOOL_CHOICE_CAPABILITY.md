# LLD — Forced tool choice as a model capability

**Status:** DESIGN (2026-10-08). Awaits AK's GO for the design commit; impl commit separate.
**Decision:** proposed D-504 (design + build), D-505 (merged and live).
**Scope:** `primeqa/intelligence/llm/{gateway,router,catalog}.py`, `primeqa/views.py` (picker + Models panel), `primeqa/templates/settings/llm_usage.html`, migration 075 (ADDITIVE, dumpless). **No change to S3 runtime, prompts, or tool schemas.**

---

## 1. The defect (verified on production, read-only, 2026-10-08)

| Evidence | Fact |
|---|---|
| `activity_log` 1255 / 1256 (09:06Z, 09:07Z, user 1) | Sonnet 5.5 and Opus 5.5 enabled in the model catalog (`llm_models`) |
| `activity_log` 1257 (09:07:46Z, user 1) | tenant 1 `llm_model_override`: `claude-sonnet-5` → `claude-sonnet-5-5` |
| `llm_usage_log` 1129–1135 (09:03–09:05Z) | 7 generation calls on `claude-sonnet-5`, all `ok` |
| `llm_usage_log` 1136 / 1137 (09:08Z) | 2 generation calls on `claude-sonnet-5-5`, both `content_error` (SQ-207, req-302, env 59) |
| API error | `tool_choice: type "tool" and "any" are not supported for this model.` |

**Mechanism.** The gateway forces one tool per turn: `generation/runtime.py::PhasePolicy.tool_choice` (D-085, substrate in control) and `gateway.py:189` for prompts with `force_tool_name` (`repair_proposal`; `test_plan_generation`, dormant). The provider passes `tool_choice` through unchanged. The Claude 5.5 models and Fable 5.1 reject `{"type": "tool"}` / `{"type": "any"}` with a 400; the provider maps a 400 to `content_error` (non-retryable); S3 fails the job loud, as designed (D-106.3). Nothing in the product knew that a model could lack this capability, so the picker offered it and the failure surfaced only at generation time.

**Root cause.** The product treats "forced tool choice" as a universal API feature. It is a per-model capability.

## 2. Facts proven by probe (free `count_tokens` calls, 2026-10-08, scratch script `probe_tool_choice.py`)

`count_tokens` validates the request shape exactly as `messages.create` does and costs nothing — it is a truthful, free capability probe.

| Model | forced `tool` | `auto` | `auto` + trailing `role:system` message | `auto` + `strict:true` | `disable_parallel_tool_use` |
|---|---|---|---|---|---|
| claude-sonnet-5 | 200 | 200 | 200 | 200 | 200 |
| claude-sonnet-5-5 | **400** | 200 | 200 | 200 | 200 |
| claude-opus-5 | 200 | 200 | 200 | 200 | — |
| claude-opus-5-5 | **400** | 200 | 200 | 200 | — |
| claude-fable-5 | 200 | 200 | 200 | 200 | — |
| claude-opus-4-7 | 200 | 200 | 400 (`role 'system' is not supported`) | 200 | — |
| claude-haiku-4-5-20251001 | 200 | 200 | 400 (same) | 200 | — |

**Live two-turn proof on `claude-sonnet-5-5`** (two real calls, 507+574 input / 50+47 output tokens, `probe_live_turns.py`):
turn 1 — `tool_choice {"type":"auto","disable_parallel_tool_use":true}` + a trailing `{"role":"system"}` message naming `propose_item` → `stop_reason tool_use`, content `[tool_use]`, the named tool called;
turn 2 — history replayed **exactly as the S3 runtime rebuilds it** (assistant turn = the `tool_use` block only, no thinking block; then the `tool_result`) + a trailing system message naming `emit_done` → 200, `[tool_use]`, the named tool called.
So: (a) the instruction shape works; (b) the runtime's thinking-free replay (`_assistant_tool_use_msg`) is accepted on a 5.5 model (no thinking blocks are ever sent, so the preserved-thinking check has nothing to compare).

Two observations not relied upon: Sonnet 5 accepted the trailing system message on `count_tokens` (the API reference says it does not support mid-conversation system messages — the shaping below never sends one to a model that supports forcing, so this is moot); the 5.5 responses carried no thinking block at all under the default display.

## 3. Design

### 3.1 The capability is a recorded FACT, not a code guess

- **Migration 075 (ADDITIVE, dumpless):** `llm_models.forced_tool_choice BOOLEAN NULL` (NULL = not yet probed) and `forced_tool_choice_probed_at TIMESTAMPTZ NULL`. Comment on the column names the probe and the error string it classifies.
- **Seed in the same migration (the lean — see fork F1):** the four catalog rows that exist today get the value proven above, `WHERE forced_tool_choice IS NULL` (idempotent): fable-5 TRUE, opus-5 TRUE, opus-5-5 FALSE, sonnet-5-5 FALSE; `probed_at = NOW()`. Reason: with the seed there is no window in which tenant 1 (already pinned to sonnet-5-5) fails loud between the deploy and a Refresh click.
- **Code set:** `router.FORCED_TOOL_CHOICE_CODE = {OPUS: True, SONNET: True, HAIKU: True, SONNET_5: True}` — the built-in models, proven 2026-10-08. A unit test pins `set(FORCED_TOOL_CHOICE_CODE) == SELECTABLE_MODELS` (every code-selectable model carries the fact, the SELECTABLE ⊆ MODEL_PRICING pattern).
- **Resolver:** `router.forced_tool_choice_supported(model_id) -> Optional[bool]`: a catalog row with a non-NULL value wins (it can also override a code model after a re-probe); else the code table; else `None` (unknown). Read with the selectable set — `selectable_model_ids()`'s one query grows to `(model_id, status, forced_tool_choice)` and the cache tuple carries both (same 60 s TTL, same `refresh=True`). Read failure → code table only (fail-open for the read, as today).

### 3.2 The probe (catalog, not gateway)

`catalog.probe_forced_tool_choice(api_key, model_id) -> bool`:
`client.messages.count_tokens(model, messages=[one user text], tools=[a one-field tool], tool_choice={"type":"tool","name":…})`.
200 → `True`. HTTP 400 whose message contains `tool_choice: type "tool" and "any" are not supported` → `False`. Anything else (network, auth, another 400, 404 unknown model) → `CatalogProbeError` (the fact stays unknown; the caller fails loud). Same class of call as `fetch_upstream_models` — platform metadata, outside the gateway by design (not a model call, no spend, no usage row).

Where it runs:
- **`enable_model`** — probe BEFORE the upsert; the row is written with the fact. A `CatalogProbeError` refuses the enable (nothing written, the flash names the error). "Selectable ⇒ correctly priced" (061) becomes "selectable ⇒ priced AND its driving shape known".
- **`refresh_model_catalog`** — every ACTIVE row with `forced_tool_choice IS NULL` is probed and stamped (this is the backfill path for any row the migration seed does not cover, and the recovery path after a refused enable). A per-row probe error is logged and the row stays NULL; the refresh itself does not fail.
- **Boot gate** `validate_tenant_model_overrides` — an override whose capability resolves to `None` is **logged as a warning** (fork F2), not raised; resolution at the chokepoint raises pre-spend (3.3).

### 3.3 Gateway shaping — ONE place, both entry points

In `gateway._invoke_and_record` (the internal `llm_call` and `tool_turn` share, D-095.2), before `provider.invoke`:

```
tool_choice, messages, shape = _shape_tool_choice(model, tool_choice, messages)
```

Rules of `_shape_tool_choice` (pure; never mutates the caller's lists/dicts):

| `tool_choice` in | capability | out |
|---|---|---|
| `None`, `{"type":"auto"}`, `{"type":"none"}` | any | unchanged, `shape=None` |
| `{"type":"tool","name":N}` or `{"type":"any"}` | `True` | unchanged, `shape="forced"` |
| same | `False` | `tool_choice={"type":"auto","disable_parallel_tool_use":True}`; `messages + [{"role":"system","content":INSTRUCTION}]`; `shape="auto+instruction"` |
| same | `None` | raise `router.ModelConfigError` **pre-spend** (no provider call, no usage row): "model X: forced tool choice capability unknown — refresh the Models panel on /settings/llm-usage" |

`INSTRUCTION` (a gateway constant, transport-level, NOT part of any frozen prompt version):
- for `tool`: `For this turn you must call the tool {name}. Open your response with that call and make no other tool call.`
- for `any`: `For this turn you must call exactly one of the provided tools. Open your response with that call.`

Preconditions for the appended system message (the API's placement rule): `messages` non-empty and the last message's role is `user`. Every current caller satisfies it (the S3 spine always ends on the opening user turn or a user `tool_result`; both `llm_call` prompts are a single user turn). If violated → `LLMError("content_error", …)` naming the shape, pre-spend. Pinned by a unit test; unreachable today.

Ordering with the cache markers: `tool_turn` applies `_messages_with_cache` BEFORE `_invoke_and_record`, so the moving breakpoint sits on the last completed turn and the appended system message is the volatile tail after it — the cached prefix is untouched (this is the documented purpose of mid-conversation system messages).

`disable_parallel_tool_use: true` restores the "at most one call per turn" the forced shape gave; the runtime's `tool_uses[0]` assumption ("forced-tool flow: exactly one call per turn") stays true by API guarantee, not by luck.

**Usage attribution.** The usage row's `context` (a COPY of `context_for_log`) gains `"tool_choice": shape` when `shape` is not `None` — so `llm_usage_log` answers "which calls were driven by instruction" and the deployed proof reads it.

**Error classification unchanged:** `consumer._classify_error` already maps `ModelConfigError` → `model_config_error` (D-106.3), so an unknown capability fails the S3 job with a NAMED code, pre-spend, instead of a `content_error` after a 400.

### 3.4 S3 runtime — NO change

- Layer A already rejects a missing call (`You must call {expected}; no tool call was made.`) and a wrong tool (`expected tool X, got Y`), each costing one correction turn up to `_max_corrections`; with `auto` these paths become reachable on 5.5 models and are the designed recovery (the live proof hit neither).
- `state.messages` never contains the appended system message (the gateway appends to a copy per call); prompt versions (D-089/D-103, frozen) are untouched; the eval runners inject fakes and are unaffected.
- `extract_tool_uses` reads only `tool_use` blocks; `rehydrate_content_blocks` passes unknown block types through — thinking blocks, when present, are ignored.

### 3.5 The picker and the Models panel (option 2)

- **Save handler** (`/settings/tenant-tier/<id>`): after the selectable check, `forced_tool_choice_supported(new_model) is None` → flash `Model X: its tool-call driving shape is unknown. Click "Refresh models" on this page, then pick it again.` and redirect; NO write, NO audit row. (`True`/`False` are both savable — a `False` model is drivable by instruction.)
- **Models panel:** a new column **"Forced tool call"** — `yes` / `no — driven by instruction` (title: "the gateway sends tool_choice auto plus a per-turn instruction naming the tool") / `unknown — refresh`. The existing "Action needed" band gains a second list: tenants pinned to a model whose capability is unknown (same shape as `retired_pinned`; `catalog_models[*]` gains `forced_tool_choice`).
- **Picker options:** unchanged list (selectable set); the save-time refusal is the gate. (Hiding unknown models from the list would silently drop a tenant's CURRENT value; the retired-value pattern already shows the current value flagged.)
- No new routes; the authority table (`test_route_authority_table.py`) is unchanged.
- **Standing rule (D-487/D-501):** the changed page is shown to AK as a fixture screenshot and a production-data render before merge.

### 3.6 Out of scope, ledgered

- `strict: true` on tool schemas: orthogonal (forced tool choice never guaranteed schema-valid input; Layer A validates). `generation/tools.py` has `additionalProperties:false` on 8 of 10 objects; `test_plan_generation`'s schema has none → strict would 400. Separate slice if wanted.
- Cost of adaptive thinking on the 5.5 models: on by default, billed; the probe turns spent 50 / 47 output tokens for one-field calls. Watch `llm_usage_log` output tokens per S3 turn after the switch; a per-tenant effort setting is a separate lever (D-387..D-396 territory).
- `test_plan_generation` has no live caller (v1 path, D-191…); it is shaped the same way if ever called; its missing `additionalProperties` is noted above.

## 4. Build plan (one impl commit after the design commit)

1. `migrations/075_llm_models_forced_tool_choice.sql` — columns + seed (idempotent).
2. `router.py` — `FORCED_TOOL_CHOICE_CODE`, resolver, cache widening, boot-gate warning.
3. `catalog.py` — `probe_forced_tool_choice`, `CatalogProbeError`, wired into `enable_model` and `refresh_model_catalog`.
4. `gateway.py` — `_shape_tool_choice`, call site in `_invoke_and_record`, context key.
5. `views.py` + `llm_usage.html` — save-time refusal, column, pinned-unknown band.
6. `core/models.py` — the two columns on `LlmModel`.

## 5. Gates

**Unit (no DB), each proven RED on main first:**
- `tests/unit/llm/test_tool_choice_shaping.py` — the rule table of 3.3 (unchanged / forced / auto+instruction with the message LAST and `disable_parallel_tool_use` / unknown → `ModelConfigError` and the fake provider NOT called / last-role-not-user refusal / caller's lists and dicts unmutated / context key present only when shaped).
- `tests/unit/llm/test_gateway_shared.py` (extend) — `tool_turn` and `llm_call` on a `False`-capability model deliver `{"type":"auto",…}` + a trailing system message to the fake provider; on a `True` model the forced choice unchanged.
- `tests/unit/test_router_forced_tool_choice.py` — resolver precedence (catalog non-NULL > code > None), the code-set pin.
- `tests/unit/test_catalog_probe.py` — classification with a fake client: 200 → True; the exact 400 text → False; other errors → `CatalogProbeError`.

**Integration (scratch DB), one process per file:**
- picker: an override to a model with NULL capability is refused (flash text, no row change, no `tenant_llm_model_override` audit row); to a `False` model is saved and audited as today.
- enable: with the probe faked True/False/raising — the fact lands on the row; a raising probe writes nothing.
- refresh: NULL active rows are probed and stamped; a per-row error leaves NULL and does not fail the refresh.
- Models panel renders the column and the pinned-unknown band (planted shapes, removed after; residue 0).
- boot gate: an unknown-capability override warns and does not raise.

**Deployed proof (AK's GO; a real spend):** migration 075 applied (ADDITIVE, dumpless); tenant 1 left on `claude-sonnet-5-5`; re-run generation for SQ-207 and req-302 on env 59; read back `llm_usage_log` rows `status='ok'`, `model='claude-sonnet-5-5'`, `context->>'tool_choice'='auto+instruction'`; the S3 `llm_calls` for the request show SUCCESS with ≤ the usual correction count; the Models panel on production renders the four facts. The free probe script is re-run against the deployed key as the capability census.

## 6. Forks for AK (the lean first)

- **F1 — seed the four facts in migration 075** (lean) vs. leave NULL and have AK click "Refresh models" after the deploy (one window in which tenant 1 fails loud as `model_config_error`).
- **F2 — boot gate WARNS on an unknown-capability override** (lean; the chokepoint already refuses pre-spend) vs. RAISES (a deploy blocks until re-pointed, the retired-model precedent).
- **F3 — `disable_parallel_tool_use: true` on the shaped `auto`** (lean; restores the one-call-per-turn guarantee the runtime assumes) vs. a Layer A "more than one call" rejection in the runtime (touches S3; rejected as wider).

## 7. Proposed ledger entry (append on GO — not written yet)

**D-504 — Forced tool choice is a per-model capability, recorded as a fact and shaped at the gateway chokepoint.** The Claude 5.5 models and Fable 5.1 reject `tool_choice` `tool`/`any`; tenant 1's 2026-10-08 switch to Sonnet 5.5 failed every generation at the API (usage rows 1136/1137). The fact lives on `llm_models.forced_tool_choice` (migration 075, probed by a free `count_tokens` call at enable and refresh, seeded for the four catalog rows proven by probe) with a code table for the built-in set; `_invoke_and_record` shapes a forced choice into `auto` + `disable_parallel_tool_use` + a trailing `role:system` instruction naming the tool on models that reject forcing, and refuses pre-spend (`ModelConfigError`) when the capability is unknown; the picker refuses an unknown-capability override at save time and the Models panel shows the fact. D-085's intent (the substrate names the tool each turn) is kept; its realisation becomes model-aware. The S3 runtime, prompt versions and tool schemas are unchanged; `strict` is ledgered. Live proof: two turns on Sonnet 5.5 with the runtime's thinking-free replay, both answered by the named tool.

---

## 8. Build record (2026-10-08, after AK's GO on the design and the leans)

**Design commit** 79026223 (the LLD + D-504 appended). **Implementation:** the six parts of section 4, plus one fallback the build found necessary:

- **The deploy window.** With the code deployed BEFORE migration 075, the widened catalog read (`model_id, status, forced_tool_choice`) fails on the missing column; the old fail-open path would then return the CODE set only, and the boot gate would RAISE for tenant 1 (pinned to the catalog model `claude-sonnet-5-5`) — every service down. Observed, not assumed: the unit file `tests/unit/test_api_request_log.py` boots the app against the `.env` database (production, read-only) and went red exactly so. Root fix in `selectable_model_ids`: when the capability column is unreadable, re-read `(model_id, status)` on the same session (facts empty) and warn naming migration 075. The selectable set stays the catalog's; every catalog model is UNKNOWN (refused pre-spend, boot gate warns). Proven on the same boot: the warning line, the boot-gate warning for tenant 1, no raise. The deploy order stays migration-first regardless.

**Gates run:**
- RED on main first, both new unit files on the design-only tree in a temporary worktree: `test_tool_choice_shaping.py` 15 errors, `test_forced_tool_choice_capability.py` a collection error — the new names do not exist there.
- Unit: the two new files + `test_model_control.py` + `test_gateway_shared.py` + `test_routing.py` + `test_api_request_log.py` green (62 in the last targeted run); the whole `tests/unit` tree — see the HOLD message for the count.
- The Models panel fragment rendered with planted facts (no DB): `yes` / `no` / `unknown` per row and the pinned-unknown band present; the template compiles.
- Integration (`tests/integration/test_tool_choice_capability_pages.py`, 7 tests, DB-real on scratch through the real routes): WRITTEN, NOT YET RUN — the scratch database address (`S3A3_TEST_DATABASE_URL`) is not available to this session (not in `.env`, the login shell, a Railway service, Docker, or memory). It runs, with migration 075 applied to scratch first, once AK provides the address.
- Screens for AK (standing rule): the Models panel fixture screenshot needs the app on scratch (same dependency); the production-data render follows the deploy.

**Observed live, read-only, during the build:** production's `llm_models` has no capability column yet (migration 075 unapplied), and the app boots with the warning path described above.
