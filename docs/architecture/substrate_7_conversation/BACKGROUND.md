# Substrate 7 — Conversation and Control — BACKGROUND

## Why S7 is last

PLATFORM_VISION's design order puts S7 last: "Depends on every other substrate
having queryable APIs." S7 answers questions *about the system*, so it can only be
as good as the substrates it reads. By Phase 8 they are all built and queryable:

- **S1** semantic org model — `SemanticOrgModel` query interface (entities, edges, details, picklists).
- **S2** test representation — the coordinator read methods (claims, recipes, requirement links).
- **S3** generation — generation-outcome rows (what was generated / refused).
- **S4** execution — recorded run evidence (`s4_execution_runs`).
- **S5** knowledge — the assembler + domain packs.
- **S6** interpretation — recorded interpretations + cross-run clustering (cause / VR / flapping).
- **S8** evolution — recorded grounding-validity verdicts (`intact` / `drifted` / `broken`).

S7 is the surface that turns those queryable faculties into answers a release owner,
BA, or tester can read.

## The discipline S7 inherits

The platform refuses to let an LLM author what is true. S3 keeps admissibility
substrate-authored; S6 and S8 are deterministic-first ("the LLM phrases + clusters,
never invents the attribution"). S7 raises the same line to the user surface: the
**retrieval** (what evidence grounds an answer) is deterministic and
substrate-authored; the **model** only phrases over evidence it was handed. Letting
the model choose retrieval would be letting it choose its own grounding — the exact
inversion the rest of the platform was built to avoid.

This is why phase 1 is a **fixed intent set with deterministic retrieval recipes**,
not an open-ended NL→arbitrary-query engine. The intent vocabulary grows
deliberately (the S8 "the leg set grows" / D-096 "single-hop before traverse"
pattern), and even a future open-ended router keeps retrieval substrate-authored.

## Prior art in the codebase

- **`primeqa/intelligence/substrate_insights.py`** (cutover Step 2, D-155) — the
  first v1→substrate read consumer: a best-effort bridge over a tenant connection
  that reads S6 + S8 and flattens the DTOs to a page payload. S7's bridge is the
  same shape, with an LLM phrasing edge added.
- **`primeqa/intelligence/llm/prompts/story_view.py`** (migration 048) — a
  single-shot, no-cache, no-escalation Haiku task. The skeleton for S7's
  `grounded_answer` task.
- **`primeqa/evolution/recompute.py`** — the dual-derivation: one
  `get_tenant_connection` serves both `SemanticOrgModel(conn)` (S1) and
  `Session(bind=conn)` (the ORM-read substrates). S7's bridge reuses it.

## The Control half (deferred)

The substrate's full name is "Conversation **and** Control". Control — issuing
write actions through conversation — is the larger, riskier half: it gates on the
Permission Model (the additive permission-set union) and env run-policies
(production blocks agent auto-apply). It is a deliberate later phase. Phase 1 is
Conversation, read-only.
