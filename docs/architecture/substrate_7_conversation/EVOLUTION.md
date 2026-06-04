# Substrate 7 — Conversation and Control — EVOLUTION

Append-only build history. One entry per session that makes substantive changes to
the substrate's docs or code, dated, with decision-entry refs.

## 2026-06-04 — Substrate 7 opened (Phase 8, D-163)

S7 opened — the **conversation layer**: the natural-language surface through which a
user asks the system about itself and gets an answer grounded in what the other
substrates recorded. Faculty-first (mirroring the S5/S6/S8 opens): the semantic
core is the **grounded-answering faculty** — a deterministic `classify → retrieve →
bounded-assemble → phrase` pipeline with a **grounded-or-refuse** keystone (refusal
is deterministic + substrate-authored, never the model's call; D-073). The
*Control* half (write-side commands) + the conversation *mechanics* (multi-turn,
persistence, proactive insights, broad retrieval, the open-ended router) are
explicitly fenced (SPEC §6).

Phase-1 scope (user-chosen): **three intents** — `failure_cause` (S6
interpretations + clustering), `grounding_drift` (S8 grounding-validity), `impact`
(S1 graph single-hop + S2 requirement links) — plus a thin `/ask` consumer page.
The first two phrase already-recorded deterministic verdicts (the safest debut
posture); `impact` is the one live S1 read-through. The intent set is explicitly
open (SPEC §3).

Dependency law: S7 is a **pure consumer** of every substrate's public read API and
**writes nothing** — it owns **no table** in phase 1 (it produces ephemeral answers
over others' durable artifacts; the one substrate that is primarily a consumer).
The `conversation/` package is **LLM-free** (the phrase step is an injected
callable; the real `llm_call` lives in v1) — the S6 `interpretation_phrasing`
boundary split.

Locked constraints honored: D-095.4 (stateless-per-question, explicit bounded
context — no hidden conversational state); D-073 (refusals are the
conversational-clarification surface); D-045 (Control deferred).

Slice 0 (D-163) lands the doc-set + the contract types (`QuestionContext`, `Intent`,
`EvidenceItem`, `Evidence`, `Citation`, `Answer` — frozen, behaviour-free) +
a contract/drift-guard test. On `phase-20-substrate-7-conversation`.
