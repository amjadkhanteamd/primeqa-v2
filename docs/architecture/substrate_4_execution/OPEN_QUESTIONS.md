# Substrate 4 — Execution Engine — Open Questions

Questions specific to S4's design. Cross-cutting questions live in the top-level OPEN_QUESTIONS.md.

---

## Open

### S4-Q-001 — Does the inspection claim carry active-ness, and does `exists`-over-`APPLIES_TO` faithfully realize it? [S3-owned]

**Surfaced by:** S4 slice 2 (metadata-inspection executor design; D-108.1).
**Owner:** **S3** (emission / emittable-set work) — possible S1 dependency. **Not resolved in S4.**

The prohibition inspection recipe asserts plain `exists` over the `APPLIES_TO` edge (ValidationRule → Object); S4 slice 2 realizes exactly that — the relationship is *present*, with **no `Active` filter** (per the D-108.1 realization principle: the translator carries only what the recipe asserts, never a translator-injected predicate).

But the claim's *intent* may be **behavioral**: a ValidationRule enforces a prohibition only when it is **active**. An inactive VR still produces an `APPLIES_TO` edge (S1 builds the edge regardless of `Active`), so `exists`-over-`APPLIES_TO` would report "present" for a prohibition that does **not** actually enforce. Faithfully carrying active-ness needs a `VR.active` constraint **beyond edge-existence** — a representation choice in the recipe's assertion (and possibly an S1 edge-property dependency).

Whether the inspection claim *should* be about active-ness — and if so, how the recipe carries it — is an **S3/emission representation concern**, parked for the S3 emittable-set work. S4 does not resolve it: **S4 realizes what the recipe asserts.** Tagged here so the gap surfaced at S4 slice 2 is not lost.

---
