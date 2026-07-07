# Substrate 8 — Evolution Engine — Open Questions

Questions specific to S8's design. Cross-cutting questions live in the top-level `OPEN_QUESTIONS.md`.

---

## Resolved

- ~~S8-Q-001 — What constitutes semantic continuity across org evolution?~~ → the keystone (D-112): identity is org-independent by construction, so evolution is a *grounding-axis* event, never an identity-axis event. S8 governs grounding continuity under identity preservation.
- ~~S8-Q-002 — The S6↔S8 boundary: does S8 generalize S6's drift check, or run parallel?~~ → **parallel**, on epistemic grounds (S6 post-run with evidence; S8 pre-run by derivation). Both stand on the neutral `formula.evaluate` primitive; never S6 → S8 (D-112 / D-113 / D-114).
- ~~S8-Q-003 — The recipe-grounding leg's exact verdict semantics?~~ → object-level, evaluate-based: `intact` (≥1 active VR fires) / `drifted` (none fired, ≥1 active evaluable VR exists) / `broken` (`no_active_vr` / `formula_non_evaluable`) (D-113).
- ~~S8-Q-004 — Who owns the synthesis→intent value-claim contract?~~ → resolved
  by D-115.1…D-115.4 (status audit 2026-07-07): S3 owns it, and the threading
  landed — `GroundedPositive` (`generation/emission.py`) is grounded + stashed
  during `resolve_intent` (`generation/governance_core.py`), with
  `("data_behavior", "value-claim")` in the emittable set; S8's recipe-grounding
  leg evaluates the stored payload (D-113). The production-reachability
  prerequisite this question named is met.

## Open

- **S8-Q-005 — VR-pin vs the NonEvaluable-symmetry fix: priority + ownership.** A probe showed the dominant object-level imprecision is `intact` masking drift (an unrelated required-field VR fires on the minimal payload), whose real fix is the generation-side VR-pin — not the held S8↔S6 `NonEvaluable`-symmetry pass (cheap-and-correct but marginal). Which lands first, and is the VR-pin an S3-emission change or an S8-read change? (`DEFERRED_ITEMS.md` §2/§3.)

  > **Status 2026-07-07:** adjacent progress, question stands. D-295 landed
  > generation-side deterministic field-overlap VR *selection* (which VR grounds
  > a claim, refuse-if-none/tie) — but S8's read side does not yet consume a
  > pinned VR (verdicts stay object-level), and the `NonEvaluable`-symmetry
  > pass remains deferred. Priority + ownership still to be decided.

- **S8-Q-006 — Does S8 act autonomously when the org changes?** The supersession *law* is recorded (identity-preserving re-grounding), but the autonomy boundary — when S8 may re-ground without a human gate vs flag-for-review — is part of the mechanics phase and the top-level S8-autonomy question. (Cross-ref top-level `OPEN_QUESTIONS` + the mechanics fence.)

  > **Status 2026-07-07:** mechanics phase still fenced — open. Adjacent
  > context: the fix-proposal agent is human-gated with flag-gated sandbox-only
  > auto-apply (D-236, `evolution/repair.py`), and per-org grounding refuses
  > the multi-org blend (D-265 guardrail).

- **S8-Q-007 — Re-interpretation temporality / contemporaneous grounding.** Grounding reads the *current* S1 by default. **Partially addressed (D-142/D-143):** the store records `evaluated_at_version_seq` — the S1 seq each verdict was computed against — so a verdict's freshness/staleness is queryable (it is a snapshot as-of that seq, refreshed by the D-143 trigger when S1 advances). **Still open:** snapshotting the run-time VR state (into evidence or a manifest) so a much-later *re-interpretation* reads the contemporaneous org rather than today's. (Cross-ref S6 `DEFERRED_ITEMS` item 1.)

  > **Status 2026-07-07:** stands as written. The ADR-001 / D-270 evidence
  > model keeps the evidence envelope methodology-agnostic (future evidence
  > kinds are admissible), but no run-time VR-state snapshot is captured yet.
