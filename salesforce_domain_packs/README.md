# Domain Packs

Long-form prescriptive knowledge about specific Salesforce domains
(Case escalation, Lead conversion, Opportunity stages, etc.). When a
requirement matches a Domain Pack's keywords or referenced objects,
the pack content is injected into the test_plan_generation prompt so
the model has concrete patterns to follow instead of inferring.

Attribution for which packs fired on each LLM call is logged into
`llm_usage_log.context->>'domain_packs_applied'` (existing JSONB
column — no schema migration for logging; see migration 049 for the
tenant flag that gates the feature).

## File format

Markdown with YAML frontmatter. Required keys:

- `id` — unique, snake_case, used as the filename stem
- `title` — short human-readable label (shown in superadmin logs)
- `keywords` — list of distinctive strings matched word-boundary + inflection (-s/-es/-ed/-ing) against the requirement's `jira_summary` + `jira_description` + `acceptance_criteria`
- `objects` — list of SObject API names. **Dormant in v1**: scoring path
  exists but the generation caller always passes `referenced_objects=None`
  today. Will activate in v1.1 once the requirements pipeline extracts
  objects up front. Populate this field anyway so new packs are ready.
- `token_budget` — author-declared advisory (not enforced). The
  selector caps selection using a measured `len(content) // 4` estimate.
- `version` — bump whenever you edit the body; logged in attribution so
  generation quality can be correlated with pack version over time.

Body is plain markdown — author for the model, not for humans.
500-1500 tokens is the right size. Packs aren't cached in the prompt
prefix today; keeping them small keeps cache-miss overhead negligible.

## SECURITY — read this

Domain pack files are **trusted content**. They ship in git and reach
the LLM verbatim in the prompt. They MUST NOT be populated from:

- User uploads or form inputs
- Jira API responses
- Email bodies
- Any other channel outside source control

If someone opens a PR to add a pack sourced from user-supplied data,
reject it. Prompt-injection defence depends on this invariant. Treat
pack PRs with the same scrutiny as code PRs — a malicious pack could
redirect the model toward exfiltrating sensitive context or
generating deliberately broken tests.

## Adding a new pack

1. Create `<id>.md` in this directory with the required frontmatter.
2. Pick keywords that are distinctive to the domain (not generic
   nouns like `field`, `record`, `object` — those will match
   everything and waste prompt tokens on irrelevant packs).
3. Populate `objects` with the API names the pack is definitively
   about, even though v1 doesn't use them yet.
4. Keep body content under 1500 tokens. The selector enforces a
   char-count cap via `measured_tokens = len(content) // 4`.
5. Add a test case in `tests/test_domain_packs.py` that exercises the
   matcher against a representative requirement.
6. Ship via a normal commit to `main`. Railway auto-deploys.

## Operational notes

- Feature is per-tenant; superadmin toggles via `/settings/llm-usage`.
  Default off, so new packs don't silently change behaviour for
  tenants that haven't opted in.
- No schema migration when you add a pack — filesystem-only change.
- No cache-control on the packs block in v1; they ride the dynamic
  section of the prompt. Cost per matched call is ~$0.004 on Sonnet.
- The tenant flag check, provider instantiation, and selector call
  all live in `primeqa/intelligence/generation.py` immediately before
  `llm_call`. The prompt module (`test_plan_generation.build`)
  consumes `context["domain_packs"]` and handles placement.
