# Substrate 5 — Knowledge System — SPEC

**Status:** Opened + ratified (Phase 4, D-134); the realized surface is built + deployed. Since the opening, the package was relocated to its top-level home `primeqa/knowledge/` (cutover Step-1, D-148) — see §2. This SPEC formalizes S5's boundary, its provider-port contract, and the realized vs deferred line. (Its original v1 consumer, `intelligence/generation.py`, was retired with the v1 product layer in D-221; the S5→S3 consumption seam is design-tracked in §6 / DEFERRED_ITEMS.)

## Purpose

Per PLATFORM_VISION §"Substrate 5 — Knowledge System": *persist and improve the knowledge that shapes generation and execution.* S5 holds the prescriptive + proscriptive knowledge the generator consults — Domain Packs, system rules, learned per-tenant facts, cross-tenant patterns, user-feedback signals — and "gets smarter the more the system is used. Not static configuration."

S5 is **cross-cutting** (dependency graph: `S3 ←— S5`): it shapes generation, receives signals from execution (S4) + user feedback, and (future) feeds interpretation (S6). It is a **retrieval/curation layer, not an LLM layer** — all knowledge is human-authored (git files, signal aggregation, DB rows); the LLM lives in the *consumer* (generation), never in S5.

## 1. What S5 is (and is not)

- **Is:** the uniform boundary through which knowledge reaches a generator's prompt — one concept (`Rule`), one port (`KnowledgeProvider`), one merger (`KnowledgeAssembler`), plus parallel prescriptive channels (Domain Packs) and a per-tenant learned channel (feedback rules).
- **Is not:** a generator, an LLM caller, or a metadata source (that is S1). S5 never decides what a test *is*; it supplies what the generator *should know* while deciding.
- **Knowledge flows one way:** consumers read from S5; S5 reads signals *about* generations (to learn) but never writes a generation.

## 2. The realized surface (the three channels)

S5's code lives at `primeqa/knowledge/` (its top-level package since the cutover Step-1 relocation, D-148) + `primeqa/intelligence/llm/feedback_rules.py` (the feedback aggregator stays in the LLM layer, wrapped via `LearnedRulesProvider`). Its original consumer was **v1 test-case generation** (`intelligence/generation.py` → the LLM gateway → `prompts/test_plan_generation.py`); that v1 generator was retired with the v1 product layer (D-221), so the channel's v1 consumption path no longer exists. The S3 *substrate* generation carries a `domain_pack_refs` slot (`generation/protocol.py`) but the S5→S3 consumption seam was "settled as not wired" (D-156) — see §6 / DEFERRED.

### 2.1 The provider port — short proscriptive rules (`knowledge/provider.py`)
- **`Rule`** (frozen): `id` (dedup key), `object_name`/`field_name` (None = applies everywhere), `category` (`field_behaviour`/`operation`/`assertion`), `rule_text` (compressed imperative, ≤~140 chars), `source` (`system`/`curated`/`learned`), `confidence` (0–1), `scope` (`global`/`org`).
- **`QueryContext`** (frozen): `tenant_id`, `environment_id`, `objects`, `fields` — providers use whichever they care about.
- **`KnowledgeProvider`** (Protocol): `get_rules(ctx) -> List[Rule]`.
- **`KnowledgeAssembler`** — the contract that makes the port safe to call inside prompt build:
  1. collects rules from every provider (a provider that raises is logged, never crashes the build);
  2. **dedup by id** with **source-precedence `learned > curated > system`** (lower rank wins);
  3. **token cap** (default 3000) — rank by confidence (then source, then id), drop lowest first;
  4. **deterministic, cache-stable render** — same `(providers, ctx)` → byte-identical output (required for the Anthropic prompt cache).

Two providers ship: **`SystemPromptRulesProvider`** (loads `salesforce_knowledge/system_rules.json` — v1, **33 rules**) and **`LearnedRulesProvider`** (wraps the feedback channel, §2.3, as a single pre-rendered `learned`-category rule).

### 2.2 Domain Packs — long prescriptive patterns (`knowledge/domain_packs.py` + `domain_pack_provider.py`)
A **parallel** channel, deliberately *not* a `KnowledgeProvider` (packs are ~1200-token patterns, not ≤140-char rules — unifying them would be premature abstraction). `DomainPack` (markdown + YAML frontmatter: `id`/`title`/`keywords`/`objects`/`token_budget`/`version`), loaded by `DomainPackLibrary` (mtime-aware, skips README), selected by `DomainPackSelector` (score `= len(matched_keywords) + 2·len(matched_objects)`; word-boundary + inflection keyword match; **measured-token** budget cap `len(content)//4`; deterministic tie-break by id). `DomainPackProvider.get_packs(requirement_text, referenced_objects, max_tokens) -> (packs, attribution)`; attribution rides `llm_usage_log.context['domain_packs_applied']`. Trusted git content only — **never** populated from user uploads / Jira.

### 2.3 Feedback rules — the per-tenant learned channel (`llm/feedback_rules.py`)
Aggregates `generation_quality_signals` (validator criticals, user thumbs/edits, BA rejects, regeneration churn, execution failures) into a prompt-ready `### Common mistakes to avoid` block: `build_rules_block(tenant_id, window_days, max_rules, max_examples_per_rule)` (ranked severity×frequency, top-5, deduped). Also powers `top_recurring_issues` (dashboard) + `correction_rate` (the north-star quality metric). `LearnedRulesProvider` adapts it into the provider port.

## 3. The boundary (the substrate contract)

- **Consumers** read S5 at prompt-build time. Today: v1 generation injects three blocks — the cached assembler block (system rules, object-filtered), the dynamic feedback block, the uncached domain-pack block. Future consumers (the S3 substrate generation; S6 interpretation) read the *same* port (DEFERRED).
- **Scoping model:** system rules — **global, git-controlled** (deploy to change); domain packs — **global, git-controlled** + a per-tenant opt-in flag `tenant_agent_settings.llm_enable_domain_packs`; feedback rules — **per-tenant, DB-derived** (`generation_quality_signals`, keyed by `tenant_id`).
- **Determinism + caching:** the assembler block is cache-stable (byte-identical) so it can sit behind a `cache_control: ephemeral` prefix; the feedback + domain-pack blocks are uncached (dynamic/tenant- or requirement-specific).
- **No-crash guarantee:** a failing provider / a malformed pack / a DB blip degrades to *less* knowledge, never a failed generation (every channel is wrapped + tolerant).

## 4. Status

**Realized (ratified at D-134):** the provider port + assembler; the system-rules channel (33 rules); the learned/feedback channel; the Domain Packs channel; the v1-generation consumption. **Unified public API** + a **contract drift-guard** land in Phase 4 Slice 2.

**Relocation — LANDED (D-148):** the physical move to the top-level `primeqa/knowledge/` package (cutover Step 1). **Deferred** (see `DEFERRED_ITEMS.md`): the S3-substrate generation forward-seam (needs a semantic-fit design — substrate gen emits *claims*, not test cases); the unbuilt vision pieces ("learned facts specific to a tenant's org" + "cross-tenant patterns"); serving S6 interpretation (a failure-pattern channel); org-curated rules (`source="curated"` is defined but has no provider yet).
