# Substrate 6 — Observation & Interpretation — Open Questions

Questions specific to S6's design. Cross-cutting questions live in the top-level `OPEN_QUESTIONS.md`.

---

## Resolved

- ~~S6-Q-001 — The S4/S6 boundary: does S6 re-judge the outcome?~~ → **no** (D-111). S4 captures truth and owns the outcome; S6 consumes `evidence.outcome` and explains it, carried verbatim, never flipped.
- ~~S6-Q-002 — Deterministic vs LLM attribution.~~ → **deterministic-first** (D-111). The interpreter attributes from the evidence with no LLM; LLMs phrase + cluster only, in later additive slices, never as the attribution source.
- ~~S6-Q-003 — How deep does attribution go for a non-enforcing VR?~~ → a structured `Cause` read from S1's VR metadata through S1's query interface, evaluate-based 3-way (`vr_inactive` / `vr_formula_drift` / `vr_formula_indeterminate` / `enforcement_gap` / `no_active_vr`), on the neutral `formula.evaluate` primitive (D-111.1 / D-114; parallel-siblings with S8).
- ~~S6-Q-004 — Where does the interpretation live; eager or lazy?~~ → persisted **eagerly** at run time in the S4 run-path, best-effort behind a savepoint (S6 failure never rolls back the S4 truth); a per-tenant `s6_interpretations` store mirroring the S4 result store (D-111.2).

## Open

- **S6-Q-005 — The reviewer edit/version lifecycle.** The `Interpretation` is designed reviewable / editable / versionable, but the edit + version mechanics (mirroring the S2-claim lifecycle) are not built. What is the version model for a human-corrected interpretation, and how does it relate to deterministic re-interpretation of the same evidence? (Deferred — `DEFERRED_ITEMS.md`.)
- **S6-Q-006 — LLM-phrasing grounding.** The phrasing layer turns the structured `Interpretation` into QA-readable prose, constrained to the deterministic facts (invent nothing). Where does it run (on-demand vs async pass), where is it stored, and what structurally prevents invention? (The grounding survey leaned on-demand+cache + a nullable field; held.)
- **S6-Q-007 — Clustering granularity.** Cross-run clustering (recurring non-enforcement, flapping outcomes, the same VR failing across runs) — at what grain (per-VR / per-claim / per-release), and does it consume S8's drift signals? (Deferred.)
- **S6-Q-008 — Does S6 consume S8's evolution signals?** S8 may emit a drift-trigger signal toward S6 ("keeps drifting across runs → re-evaluate"). Whether and how S6 ingests it is open — it is *not* part of the S6 interpret core (the predicate boundary keeps S8 ↛ S6 for the predicate), but evolution-signal → S6 is a separate channel. (Cross-ref top-level + S8 `OPEN_QUESTIONS`.)
