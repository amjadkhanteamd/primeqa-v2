# Substrate 8 — Evolution Engine — Open Questions

Questions specific to S8's design. Cross-cutting questions live in the top-level `OPEN_QUESTIONS.md`.

---

## Resolved

- ~~S8-Q-001 — What constitutes semantic continuity across org evolution?~~ → the keystone (D-112): identity is org-independent by construction, so evolution is a *grounding-axis* event, never an identity-axis event. S8 governs grounding continuity under identity preservation.
- ~~S8-Q-002 — The S6↔S8 boundary: does S8 generalize S6's drift check, or run parallel?~~ → **parallel**, on epistemic grounds (S6 post-run with evidence; S8 pre-run by derivation). Both stand on the neutral `formula.evaluate` primitive; never S6 → S8 (D-112 / D-113 / D-114).
- ~~S8-Q-003 — The recipe-grounding leg's exact verdict semantics?~~ → object-level, evaluate-based: `intact` (≥1 active VR fires) / `drifted` (none fired, ≥1 active evaluable VR exists) / `broken` (`no_active_vr` / `formula_non_evaluable`) (D-113).

## Open

- **S8-Q-004 — Who owns the synthesis→intent value-claim contract?** The recipe-grounding leg evaluates a stored payload; the *positive* (value-claim) production path (S3) is blocked on threading `{field, expected_value}` from synthesis to grounding. That contract is S3-grounding work (D-115.1), but it is the production-reachability prerequisite the evolution faculty will eventually evaluate against. (Cross-ref S3.)
- **S8-Q-005 — VR-pin vs the NonEvaluable-symmetry fix: priority + ownership.** A probe showed the dominant object-level imprecision is `intact` masking drift (an unrelated required-field VR fires on the minimal payload), whose real fix is the generation-side VR-pin — not the held S8↔S6 `NonEvaluable`-symmetry pass (cheap-and-correct but marginal). Which lands first, and is the VR-pin an S3-emission change or an S8-read change? (`DEFERRED_ITEMS.md` §2/§3.)
- **S8-Q-006 — Does S8 act autonomously when the org changes?** The supersession *law* is recorded (identity-preserving re-grounding), but the autonomy boundary — when S8 may re-ground without a human gate vs flag-for-review — is part of the mechanics phase and the top-level S8-autonomy question. (Cross-ref top-level `OPEN_QUESTIONS` + the mechanics fence.)
- **S8-Q-007 — Re-interpretation temporality / contemporaneous grounding.** Grounding reads the *current* S1 by default. **Partially addressed (D-142/D-143):** the store records `evaluated_at_version_seq` — the S1 seq each verdict was computed against — so a verdict's freshness/staleness is queryable (it is a snapshot as-of that seq, refreshed by the D-143 trigger when S1 advances). **Still open:** snapshotting the run-time VR state (into evidence or a manifest) so a much-later *re-interpretation* reads the contemporaneous org rather than today's. (Cross-ref S6 `DEFERRED_ITEMS` item 1.)
