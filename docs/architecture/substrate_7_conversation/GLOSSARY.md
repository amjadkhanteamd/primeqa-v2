# Substrate 7 — Conversation and Control — GLOSSARY

- **Grounded-or-refuse** — the keystone: every answer is grounded in retrieved
  substrate evidence; when no evidence grounds the question, S7 refuses (rather than
  guessing). The refusal is deterministic and produced before any LLM call.

- **QuestionContext** — the explicit, bounded scope of a question (tenant + optional
  environment / requirement / recipe / test). Per D-095.4, the context is passed in
  full each time; there is no hidden accumulating conversational state.

- **Intent** — one of the fixed, deterministically-classified question kinds, each
  with a deterministic retrieval recipe. Phase 1: `failure_cause`, `grounding_drift`,
  `impact`. The set is explicitly open.

- **EvidenceItem** — a single flattened substrate read row (an S6 interpretation, an
  S8 verdict, an S1 entity/edge) carrying its source substrate + a stable
  `citation_id`.

- **Evidence** — the bounded collection of `EvidenceItem`s assembled for a question
  (item-cap + token budget). `Evidence.is_empty` drives the deterministic refusal.

- **Answer** — the result: `status` (`answered` / `refused`), `text` (the phrasing,
  or the clarify prompt), `citations` (the evidence's ids), `refusal_reason`.

- **phrase_fn** — the injected callable that turns bounded `Evidence` + the question
  into answer prose via the LLM gateway. Injected so the `conversation/` package
  stays LLM-free (the real `llm_call` lives in v1).

- **Deterministic-first** — classification, retrieval, assembly, and the refusal
  decision are pure + deterministic; the LLM only phrases handed evidence.

- **The fence** — the explicitly-deferred items (SPEC §6): the Control half,
  multi-turn + persistence, proactive insights, broad retrieval, the open-ended
  router, rich UI.
