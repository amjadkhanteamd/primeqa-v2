# PrimeQA Architecture Documentation

This directory is the canonical source of truth for PrimeQA's platform architecture. It is the foundation we design against and return to when context is needed across sessions.

**If you are new to this repo:** start with `PLATFORM_VISION.md`. Read it in full. Then read the README in the substrate you're working on.

**If you are returning after time away:** read `DECISIONS_LOG.md` from your last touchpoint forward. Open questions in `OPEN_QUESTIONS.md`. Then the specific substrate file you need.

## Top-level documents

| File | Purpose | Mutability |
|---|---|---|
| `README.md` | This file — navigation and conventions | Mutable as conventions evolve |
| `PLATFORM_VISION.md` | The 8 substrates, what each is, how they relate | **Near-immutable** — changes are architectural events, logged explicitly |
| `DECISIONS_LOG.md` | Chronological record of architectural decisions | Append-only |
| `OPEN_QUESTIONS.md` | Open design questions by substrate | Mutable — items added and resolved continuously |
| `PARKING_LOT.md` | Deferred ideas with explicit "revisit when X" triggers | Mutable, but items must have triggers |

## Substrate directories

Each of the 8 substrates has its own directory: `substrate_N_<name>/`.

Inside each substrate directory:

| File | Purpose |
|---|---|
| `SPEC.md` | The current canonical design for this substrate |
| `BACKGROUND.md` | Why this substrate exists, what problem it solves, what it replaces |
| `GLOSSARY.md` | Terms specific to this substrate |
| `EVOLUTION.md` | Append-only log of how this substrate's design has changed over time |
| `OPEN_QUESTIONS.md` | Open questions specific to this substrate |
| `examples/` | Concrete examples: sample data, JSON schemas, mock responses |

Substrate directories are created as we begin serious work on each substrate. Do not pre-create skeletons for substrates we haven't started.

## Archive directory

`archive/` contains historical specs that have been superseded but are worth preserving for context on why we didn't take certain paths. When a spec becomes historical (e.g., supplanted by a new substrate design), move it to archive with a note explaining what replaced it.

## Update conventions — MANDATORY

Every working session on a substrate ends with doc updates committed to git. No exceptions. The discipline is what makes this system work.

**At the end of each session:**

1. **Substrate's `SPEC.md`** — update only if we decided something new in this session. If we debated but didn't decide, do not update SPEC.md. Update OPEN_QUESTIONS.md instead.
2. **Top-level `DECISIONS_LOG.md`** — append a new entry with:
   - Date (ISO format)
   - Decision ID (monotonic, format `D-001`, `D-002`, etc.)
   - Substrate(s) affected
   - One-sentence decision
   - Rationale (2-4 sentences)
   - Alternatives considered and why rejected
3. **Substrate's `OPEN_QUESTIONS.md`** — add new questions surfaced, remove questions answered.
4. **Substrate's `EVOLUTION.md`** — append a one-line summary of what changed.
5. **Substrate's `GLOSSARY.md`** — define any new terms introduced.

Commit these as one commit per session with message pattern:
```
docs(substrate-N): <session topic summary>
```

Example: `docs(substrate-1): agree on graph-based representation, defer storage backend`

**Do NOT batch doc updates across multiple sessions.** The whole point of this system is that every session produces a durable record. A session without a commit is a session whose context is at risk of being lost.

## Who produces what

| Document type | Author |
|---|---|
| Design docs (SPEC, BACKGROUND, EVOLUTION, GLOSSARY, top-level vision) | Claude (design sessions) |
| Implementation docs (how the code actually works) | Claude Code (post-implementation) |
| Decisions log | Claude with user confirmation |
| Open questions | Either, continuously |
| Parking lot | Either, when ideas arise |

Claude and Claude Code do not edit each other's docs without explicit handoff. Implementation docs reference design docs as source of truth; design docs reference implementation docs as reality check.

## Cross-substrate references

When Substrate A's decision depends on Substrate B, reference it explicitly:

> See `substrate_2_test_representation/SPEC.md#state-refs` for the state_ref model this assumes.

When a decision in DECISIONS_LOG affects multiple substrates, tag with all affected substrate numbers: `[S1, S3]`.

## Naming and formatting

- All files are Markdown (`.md`).
- Diagrams embedded as Mermaid where helpful.
- JSON schemas live as separate `.json` files under `examples/`.
- Headings: one `#` for document title, `##` for major sections, `###` for subsections.
- Do not use emoji in filenames or headings (makes grep harder).

## When to update PLATFORM_VISION.md

Near-immutable. Only update when:
- A substrate is being added or removed (major architectural event)
- The relationship between substrates fundamentally changes
- The product vision itself shifts

Any update to PLATFORM_VISION.md requires a corresponding DECISIONS_LOG entry explaining what changed and why. Updates are rare by design.
