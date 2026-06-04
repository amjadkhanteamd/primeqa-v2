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

- **S7-Q-002 — intent classification: keyword vs LLM.** Phase 1 classifies by
  deterministic keyword (reusing `knowledge/_text.py`). When the intent vocabulary
  grows, does keyword matching scale, or is a (substrate-authored, constrained) LLM
  classifier needed? *Constraint:* whatever classifies, retrieval stays
  deterministic + substrate-authored — the classifier may pick *which* fixed recipe,
  never author an arbitrary query.

- **S7-Q-003 — `impact` object resolution.** Phase 1 takes the target object /
  requirement from the bounded context (a picker), not free-text NL entity
  extraction. When does free-text entity resolution ("what's affected by **Account**?")
  become worth the entity-extraction machinery? *Constraint:* extraction must
  resolve against S1 (an entity that exists), not a model guess.

- **S7-Q-004 — the Control permission seam.** When Control opens, how does a
  conversational command resolve against the Permission Model + env run-policies?
  (A command is a write; the union-of-permission-sets + the env veto both apply.)
  Out of phase-1 scope; named so the Control phase designs it deliberately.

- **S7-Q-005 — answered-path demo data.** The substrate answer-stores (S6/S8) are
  empty in prod until live runs land (the `/substrate-insights` reality), so the
  common phase-1 result is *refused / no evidence* (correct behaviour). The
  *answered* path demo needs seeded S6/S8 rows. Tracked as a phase-close demo note,
  not a code question.

## Resolved

*(none yet)*
