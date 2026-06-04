# Substrate 7 — Conversation and Control — SPEC

**Status:** Opening (Phase 8, program). The **grounded-answering faculty** is the semantic core — a deterministic retrieve → bounded-assemble → phrase pipeline with a **grounded-or-refuse** keystone. Phase 1 opens three intents (`failure_cause` / `grounding_drift` / `impact`) over the substrate read-spine + a thin `/ask` surface; the **Control half + the conversation mechanics stay fenced** (§6).

**Last substantive update:** 2026-06-04 (Phase 8 — the open: keystone + dependency law + the intent set; D-163)

---

## Purpose

Substrate 7 is PrimeQA's **conversation layer**: the natural-language surface through which a user *asks the system about itself* — "did yesterday's failures share a cause?", "what's drifted since the org changed?", "what's affected if we touch this object?" — and gets an answer **grounded in what the other substrates actually recorded**. It "sits on top and touches every other substrate as a user-facing surface" (PLATFORM_VISION §S7).

It is **not** an open-ended chatbot, and it is **not** a free-form NL→arbitrary-query engine. Letting the model choose what to retrieve is letting it author what grounds the answer — the exact inversion the rest of the platform refuses (S3's substrate-authored admissibility; S6/S8's deterministic-first, "the LLM phrases, never invents"). S7 keeps **retrieval deterministic and substrate-authored**; the model only *phrases over evidence it was handed*. And it is **not** the *Control* half yet — issuing commands (trigger a run, approve, apply a fix) is write-side, gates on the Permission Model + env run-policies, and is explicitly deferred (§6).

## 1. The keystone — grounded-or-refuse

> **Every answer is grounded in substrate evidence retrieved deterministically; when nothing grounds it, S7 refuses.**

This is the platform's spine-wide grounding discipline raised to the user surface. The answer is not the model's recollection — it is a phrasing of concrete substrate rows (an S6 interpretation's recorded cause, an S8 `drifted` verdict, an S1 entity + its edges). When the deterministic retrieval yields **no evidence** for the question's intent, the system does not guess — it **refuses and asks to clarify** (D-073: "refusals are the substrate's surface for conversational clarification flows").

The refusal decision is **deterministic and substrate-authored, never the model's call.** Empty evidence ⇒ a refusal produced *before the model is invoked*. The LLM is never given the option to "answer anyway" — it only ever sees a non-empty, bounded evidence block. This is the structural anti-hallucination guarantee S6 achieves by "only deterministic facts reach the model": S7's model can only restate evidence it was handed.

## 2. The semantic core — the grounded-answering faculty

A deterministic pipeline; the LLM is a fenced phrasing edge at the end:

```
(question_text, QuestionContext)
  → classify_intent        deterministic keyword → Intent | None
  → retrieve_<intent>      deterministic recipe over the substrate read-APIs → EvidenceItems
  → assemble_evidence      bounded: item-cap + token budget + stable citation ids → Evidence
  → build_answer           empty ⇒ REFUSE (no LLM); else phrase over ONLY that evidence
  → Answer{status, text, citations, refusal_reason}
```

**Deterministic-first (the S6/S8 invariant, restated for S7):** classification, retrieval, assembly, and the refusal decision are **pure + deterministic**. The model phrases — it does not classify, retrieve, decide to refuse, or cite beyond the handed evidence. The `conversation/` package is **LLM-free**; the phrase step is injected as a callable, and the real `llm_call` lives in v1 (the S6 `interpretation_phrasing` boundary split — the LLM stays out of the substrate package).

**Bounded evidence (D-095.4).** `assemble_evidence` caps the evidence to an item count + a token/char budget and assigns each item a stable `citation_id`. The bound is what makes the context "explicit and bounded" in the D-095.4 sense — flat prompt cost, and every phrased claim is tied back to a concrete substrate row (the S6 `evidence_refs` discipline, one substrate up). `Answer.citations` are the assembler's ids — S7 returns the *evidence's* citations, not citations the model claims.

## 3. The intent set — three deterministic retrieval recipes (open)

Phase 1 supports a **small fixed set of intents**, each a deterministic retrieval recipe over a substrate's read API. The set is **explicitly open** (mirroring S8's "the leg set grows") — later phases add `coverage`, `risk`, single-run interpretation, and eventually an open-ended router, but only once the fixed-intent vocabulary has matured (the D-096 "single-hop now, traverse later" discipline applied to intents).

| Intent | The question | Reads (the recipe) |
|---|---|---|
| **`failure_cause`** | "did these failures share a cause?" | S6 `list_interpretations` + `cluster_recurring_causes` + `cluster_by_vr` — already-recorded deterministic attribution; S7 phrases the recorded verdict, invents nothing |
| **`grounding_drift`** | "what's drifted / at risk since the org changed?" | S8 `list_grounding_validity(overall="drifted"/"broken")` — the recorded grounding-validity verdict S8 already computed |
| **`impact`** | "what's affected by this object / requirement?" | S1 `get_entities` → `get_related` (single-hop) + S2 `list_tests_by_requirement` — the one *live-compute* intent (a graph walk), keeping the boundary honest (S7 reaches S1, not only the two answer-stores) |

The first two intents phrase **already-deterministic recorded verdicts** (the safest debut posture — the substrate already decided; S7 adds zero judgment). The third is the deliberate org-model read-through. **`impact` takes its target object/requirement from the bounded context** (a picker), not free-text NL entity extraction — deterministic, and it dodges entity-extraction. A question that matches no intent's keywords ⇒ `classify_intent` returns `None` ⇒ a deterministic clarify-refusal ("I can answer about failure causes, grounding drift, or impact — could you rephrase?"). Ambiguity (≥2 intents match) resolves by a **fixed priority order** (deterministic); clarify-on-ambiguity is a deferred refinement.

## 4. The dependency law — a pure consumer of every substrate's read API

```
S7 → { S1 query, S2 coordinator-read, S3 outcome-read, S4 run-read,
        S5 knowledge, S6 interpretation-read + clustering, S8 grounding-validity-read }
S7 writes NOTHING to any substrate.
```

S7 is the **one substrate that is primarily a consumer, not a producer** — it reads others' durable artifacts and produces an *ephemeral* answer. It therefore **owns no table in phase 1** (the keystone simplicity; S4/S6/S8 each own a result table because they *produce* a durable artifact — S7 does not). It reads through each substrate's **public read API** (never raw tables, never another substrate's internals): S1 through `SemanticOrgModel`, S6/S8/S2 through their `__init__` read functions on a tenant-scoped `Session`. The v1→substrate bridge (the allowed direction) opens one tenant connection and derives both an S1 reader and an ORM session from it (the verified `evolution/recompute.py` dual-derivation), best-effort (any failure → `available=False`, never breaks the page — the `substrate_insights` precedent).

## 5. Stateless + bounded — the conversation granularity law (D-095.4)

Phase-1 answering is **stateless per question.** There is **no hidden shared conversational state** — the scope is an **explicit, bounded `QuestionContext`** (a release / environment / requirement), passed in full each time, never an implicit accumulating session. This is D-095.4 ("the shared context is explicit and bounded, not an implicit shared conversation") applied at the user surface. Multi-turn dialogue — where a follow-up refers back to a prior answer — needs a session store and is **deferred** (§6); when it lands, the bounded-context law governs it (each turn's context is explicit, not implicitly inherited).

## 6. Deferred — the Control half + the conversation mechanics (the fence)

Explicitly **out of the phase-1 semantic core**, named so they are not later litigated as in-scope:

- **Control / commands (the write-side "and Control").** Issuing actions through conversation — trigger a run, approve a release, apply an agent fix. The bulk of "Conversation *and* Control"; gates hard on the Permission Model + env run-policies. The **next phase**, not this one.
- **Multi-turn conversation store.** A session/turn table enabling follow-ups that reference prior answers. D-095.4 forbids implicit shared state; phase 1 is stateless. Deferred — the natural home of any answer-cache / audit-log too.
- **Proactive / push insights.** Surfacing "regression coverage is dropping" unprompted — needs a standing trigger + consumer; deferred with the rest of the trigger machinery.
- **Broad retrieval over all substrates.** S3 generation-outcome reads, S4 raw-run reads, S5 knowledge-as-answer, coverage/risk intents. Phase 1 is three intents over S6/S8/S1(+S2). The intent set is open (§3).
- **Open-ended NL → router.** Letting the model pick which substrate to read. Deferred until the fixed-intent vocabulary matures — and even then, retrieval stays substrate-authored, not model-authored.
- **Rich chat UI.** Phase 1 is a single server-rendered page (one question, one answered/refused card with citations), reusing the `/substrate-insights` precedent.
- **S7-owned persistence.** No table in phase 1 (§4). Any cache/audit/session store arrives with multi-turn.

These are the **conversation-infrastructure local maximum** deliberately not built at the opening. The semantic core is the grounded-answering faculty — classify, retrieve, bound, phrase-or-refuse — and nothing more.

---

## Status

**Opening — Phase 8 (2026-06-04).** The keystone (grounded-or-refuse), the deterministic-first pipeline, the dependency law (pure consumer, no table), and the bounded-stateless law are locked (D-163). The build arc (see `EVOLUTION.md`): the open + the contract types (D-163) → intent classification (D-163.1) → deterministic retrieval + bounded assembly (D-163.2) → the LLM phrasing edge + grounded-or-refuse (D-163.3) → the thin `/ask` consumer surface + phase close (D-163.4). **Deferred:** the Control half (write-side, permission-gated), multi-turn + any S7 persistence, proactive insights, broad retrieval, the open-ended router, rich UI. See `DEFERRED_ITEMS.md`.
