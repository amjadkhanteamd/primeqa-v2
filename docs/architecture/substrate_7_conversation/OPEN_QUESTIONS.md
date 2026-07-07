# Substrate 7 — Conversation and Control — OPEN_QUESTIONS

Substrate-specific design questions. Resolved entries keep their closure
date/decision; open entries list the question + constraints/context.

## Open

- **S7-Q-001 — the citation back-check.** Phase 1 returns the *evidence's* citation
  ids and logs (does not yet act on) the case where the model's text cites no
  evidence. Should a no-citation answer be **downgraded to refused**? It is the
  strongest structural anti-hallucination measure, but has edge cases (a correct
  answer that legitimately summarizes without an inline id). *Constraint:* harden
  only once real Haiku outputs are observed (the story_view "verified periodically"
  posture). Lean: soft-log in phase 1, decide the downgrade later.

  > **Status 2026-07-07:** the phase-1 posture is implemented as designed and
  > live — `conversation/answerer.py` soft-logs a no-citation answer without
  > downgrading. The downgrade decision remains open pending observed outputs.

- **S7-Q-002 — intent classification: keyword vs LLM.** Phase 1 classifies by
  deterministic keyword (reusing `knowledge/_text.py`). When the intent vocabulary
  grows, does keyword matching scale, or is a (substrate-authored, constrained) LLM
  classifier needed? *Constraint:* whatever classifies, retrieval stays
  deterministic + substrate-authored — the classifier may pick *which* fixed recipe,
  never author an arbitrary query.

  > **Status 2026-07-07:** still keyword-only (`conversation/intent.py` via
  > `knowledge/_text.py`); no LLM classifier has been added. Open for when the
  > intent vocabulary grows.

- **S7-Q-003 — `impact` object resolution.** Phase 1 takes the target object /
  requirement from the bounded context (a picker), not free-text NL entity
  extraction. When does free-text entity resolution ("what's affected by **Account**?")
  become worth the entity-extraction machinery? *Constraint:* extraction must
  resolve against S1 (an entity that exists), not a model guess.

  > **Status 2026-07-07:** still picker-bound (`conversation/retrieval.py`
  > `retrieve_impact` resolves from the bounded context); no free-text entity
  > extraction exists. Open.

- **S7-Q-004 — the Control permission seam.** When Control opens, how does a
  conversational command resolve against the Permission Model + env run-policies?
  (A command is a write; the union-of-permission-sets + the env veto both apply.)
  Out of phase-1 scope; named so the Control phase designs it deliberately.

  > **Status 2026-07-07:** Control has not shipped; `/ask` remains read-only.
  > Open. (Note: the "union-of-permission-sets" wording predates D-245 — the
  > permission-set layer was deleted; a Control design would resolve against
  > the role ladder + environment scope + env run-policy instead.)

## Resolved

- ~~S7-Q-005 — answered-path demo data.~~ → resolved by reality (status audit
  2026-07-07): the "S6/S8 answer-stores are empty in prod until live runs land"
  premise no longer holds — env-59 runs live daily; `s6_interpretations` rows
  persist eagerly per run (D-111.2) and S8 grounding verdicts populate
  (D-142/D-143, D-265 era). The answered path is demonstrable with real data;
  no seeding needed.
