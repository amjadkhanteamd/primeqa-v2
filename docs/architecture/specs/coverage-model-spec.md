# Coverage Model Specification

**Status:** Accepted (design) · build deferred (follows the S6 `Verified` build)
**Date:** 2026-06-25
**Depends on:** [ADR-001 — Evidence Model & Evaluation Strategy](../ADRs/ADR-001-evidence-model-evaluation-strategy.md) (D-270). This spec **does not** redefine `Verified` or restate evaluation/verdict semantics — it cites ADR-001 for them and consumes the `Verified` boolean.

---

## 1. Purpose

Coverage answers one product question: **"is each requirement covered, and how well?"** It is a **derived view, not canonical** — a **read / aggregation layer** over the existing Requirement ↔ Claim graph. It **originates no truth**: every fact it surfaces already exists on a claim or a requirement; coverage only traverses and counts. Nothing in coverage is a source of record, so it can be recomputed at any time from the graph and never needs reconciliation.

## 2. The locked ladder (the core of this spec)

Coverage is a **three-rung ladder**, evaluated per requirement, in order:

| Rung | Definition | Source fact |
|---|---|---|
| **1. Has Claim** | the requirement has **≥1 claim** | a requirement→claim link to any current claim (see §5a) |
| **2. Approved** | the requirement has **≥1 approved claim** | claim `status = 'approved'` |
| **3. Verified** | the requirement's approved claim(s) are **`Verified`** | the `Verified` predicate **read off the claim** per ADR-001 |

**Headline coverage number = Verified %** (requirements at rung 3 ÷ total requirements in scope).

**Stated plainly:** `Verified` is **read from the claim** as defined in ADR-001. **The coverage layer NEVER inspects probes, recipes, evidence, runs, or roll-up rules** — it consumes the boolean and nothing else. The whole complexity of N probes and their aggregation was already collapsed behind that one bit by the Evaluation Strategy (ADR-001 §3b), upstream of coverage.

**There is no fourth rung.** Coverage does **not** add execution-count, run-recency, per-probe, or pass-rate notions. Those belong to evaluation/verdict (ADR-001), not coverage. The ladder is exactly three rungs.

## 3. The coverage graph

```
Requirement ──link──▶ Claim ──▶ (Recipe ──▶ Execution ──▶ Evidence)   ◀── downstream; coverage does NOT read here
                        │
                        └── status (approved?)   ┐
                        └── Verified (ADR-001)    ┘  ◀── coverage reads THESE two off the claim
```

- Coverage walks **Requirement → its claims** and reads two facts per claim: `status` (for rung 2) and `Verified` (for rung 3, per ADR-001).
- The downstream **Recipe → Execution → Evidence** chain exists, but **coverage reads `Verified` off the claim, not the runs.** It never queries the execution / interpretation stores directly — that indirection is exactly what keeps coverage stable as the evidence/verdict model grows.
- **Coverage = traverse the graph and surface the gaps:** for each rung, which requirements **fall off** (have no claim / no approved claim / no verified claim).

## 4. Coverage dimensions & reporting

- **Per-rung percentages:** Has-Claim %, Approved %, Verified % (the headline), over the requirements in scope.
- **Gap lists** (the actionable output — "what to do next"):
  - **Claimed-but-unapproved** — requirements at rung 1 but not rung 2 (claims exist, awaiting approval).
  - **Approved-but-not-Verified** — requirements at rung 2 but not rung 3 (approved, but evidence hasn't verified them).
  - **No-claim** — requirements below rung 1 (nothing generated yet).
- **Risk-weighting is a FUTURE input, not designed here.** A risk-weighted coverage number (weight each requirement by business risk) **depends on a risk attribute that is not yet decided**; it is **noted** as an extension point and explicitly out of scope for this spec.

## 5. Decisions

**(a) Requirement → claim link — SETTLED.** The **Requirement node is first-class** (its own `requirements` table, the `/requirements` list + detail UI, manual + Jira-import create, D-207 multi-claim). A claim links to its requirement via the **`test_requirement_links`** edge (`external_system`, `external_key=<requirement key>`, `link_kind`, `test_id`) — the same edge the requirement-plan reader and `count_claims_by_requirement` already traverse. **Coverage counts `generated_from` links now.** Manually-attached claims are a **future link kind** the ladder will include when that kind exists — so the traversal is specified as *"count claims linked to the requirement via an admitted link kind (today: `generated_from`)"*, **not** as "only `generated_from`, forever." No new node or link is needed; coverage reuses this graph.

**(b) Multi-claim roll-up — SETTLED: STRICT-AND.** When a requirement has several claims, it counts **Verified only if ALL of its approved (live, non-deprecated) claims are `Verified`.** *Rationale:* a requirement with **any** unverified approved claim is not honestly "covered" — strict-AND keeps the headline number **meaningful** and consistent with the platform's **don't-overstate** principle (never report coverage the evidence doesn't support). Deprecated claims are already excluded from the coverage views (D-269), so only live/active claims enter the ladder; rung 2 ("Approved") needs ≥1 approved claim, and rung 3 ("Verified") needs **every** approved claim Verified.

**(c) "Current org" scoping — PRINCIPLE CONFIRMED, MECHANISM DEFERRED.** Coverage **is org-scoped** so "covered" means "covered *against the org under test*," and it **inherits** that scoping by **reading a per-org `Verified`** — the **same `environment → connected_org_id` mechanism the rest of the product already uses** (per-org S1/grounding, D-255–D-260 / D-265), **not** a coverage-specific scoping. The **exact attachment point** of the org dimension onto `Verified` is **owned by the S6 `Verified` build** (ADR-001), not pinned here. Confirmed principle; deferred mechanism — this spec does not specify any S6 internal.

## 6. Non-goals

Coverage does **NOT**:
- **define or compute `Verified`** — that is ADR-001 / the S6 Evaluation Strategy; coverage **reads** the boolean,
- execute or run anything (S4),
- generate claims or recipes (S3 / §4a),
- prioritize risk or decide release GO/NO-GO (the decision engine),
- model **recipe adequacy / test-design quality** ("are these the *right* probes?") — a **separate concern that reads off the strategy**, noted here, not built.

Coverage's entire job is the three-rung traversal and the gap lists.

## 7. Decision & status

**Decision:** Adopt the **three-rung coverage ladder** (`Has Claim → Approved → Verified`, headline = Verified %) as a **derived read/aggregation view** over the Requirement ↔ Claim graph, whose **sole input** is the `Verified` predicate defined in ADR-001. The multi-claim roll-up is **strict-AND** (§5b); the link traversal counts **`generated_from`** today and is written to admit future link kinds (§5a); coverage is org-scoped by reading a **per-org `Verified`** (§5c).

**Status:** **Accepted (design).** The multi-claim (§5b) and link-kind (§5a) questions are **decided**; only the **S6-owned org-attachment mechanism** (§5c) remains deferred — **to the S6 build, by design** (this spec must not pin an S6 internal). **Build deferred** — it follows the S6 `Verified` build, since coverage reads that boolean.

**Pointers:**
- This file is the spec of record.
- A one-line **D-271** pointer in `docs/architecture/DECISIONS_LOG.md` records the ratification.
- Implementation (the read/aggregation queries + the coverage surface) lands **after** the S6 `Verified` build exposes the per-claim `Verified` predicate.
