# Substrate 7 — Conversation and Control — DEFERRED_ITEMS

Forward-looking list of deliberately deferred work, with rationale + revisit
triggers. Append corrections as dated notes; never silently rewrite.

## The Control half (write-side commands)
**Deferred to a later phase.** Issuing actions through conversation — trigger a run,
approve a release, apply an agent fix. The larger, riskier half of "Conversation
*and* Control"; gates on the Permission Model (the additive permission-set union) +
env run-policies (production blocks agent auto-apply). **Revisit:** once
read-answering is proven and the permission-resolution seam for conversational
commands is designed.

## Multi-turn conversation store
**Deferred.** A session/turn table letting a follow-up reference a prior answer.
Phase 1 is stateless-per-question (D-095.4 forbids implicit shared state). The
natural home of any answer-cache or audit-log too. **Revisit:** when follow-up
dialogue is needed; design the per-turn explicit-context law first.

## S7-owned persistence (answer cache / audit log)
**Deferred.** Phase 1 owns no table — S7 produces ephemeral answers over others'
durable artifacts. **Revisit:** with multi-turn (the session store is the home), or
if an audit trail of asked/answered is required for compliance.

## Proactive / push insights
**Deferred.** Surfacing "regression coverage is dropping" unprompted — needs a
standing trigger + a push consumer. **Revisit:** with the trigger machinery
(parallels the S8 change→impact reverse index deferral).

## Broad retrieval over all substrates
**Deferred.** Phase 1 is three intents over S6/S8/S1(+S2). Adding S3 generation
outcomes, S4 raw runs, S5 knowledge-as-answer, and `coverage`/`risk` intents grows
the set. **Revisit:** intent-by-intent as the vocabulary matures (SPEC §3, the
"the set grows" principle).

## Open-ended NL → router
**Deferred.** Letting the model pick which substrate to read. Phase 1 keeps
retrieval substrate-authored (a fixed intent set) — letting the model choose
retrieval is letting it author its own grounding (the discipline the platform
refuses). **Revisit:** only once the fixed-intent vocabulary is mature; even then,
retrieval stays substrate-authored.

## Clarify-on-ambiguity
**Deferred (minor).** When ≥2 intents match a question strongly, phase 1 resolves by
a fixed priority order. A future refinement refuses-to-clarify instead (the D-073
surface). **Revisit:** when real questions show priority-order guesses are wrong.

## Rich chat UI
**Deferred.** Phase 1 is a single server-rendered `/ask` page (one question, one
answered/refused card with citations). **Revisit:** with multi-turn (a transcript
view) or product design for a conversational surface.
