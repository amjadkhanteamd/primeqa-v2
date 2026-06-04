"""Substrate-7 contract types (D-163, the open).

Frozen, behaviour-free dataclasses — the contract the grounded-answering pipeline
is built over. The pipeline stages (classify / retrieve / assemble / answer) land
in later slices (D-163.1–D-163.4); this module is produce-only.

Design notes:
  - ``QuestionContext`` is the **explicit, bounded scope** D-095.4 mandates — passed
    in full each question, never an implicit accumulating session.
  - ``Evidence`` is what the deterministic retrieval+assembly produce; ``is_empty``
    drives the deterministic refusal (the keystone — empty evidence ⇒ refuse, no LLM).
  - ``Answer.citations`` are the assembler-assigned ids tying the answer back to
    concrete substrate rows (the S6 ``evidence_refs`` discipline, one substrate up).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

# The fixed phase-1 intent kinds. The set is explicitly OPEN (SPEC §3) — later
# phases add coverage / risk / single-run / the open-ended router.
IntentKind = Literal["failure_cause", "grounding_drift", "impact"]

# An answer is either grounded (answered) or grounded-or-refuse's refusal.
AnswerStatus = Literal["answered", "refused"]


@dataclass(frozen=True)
class QuestionContext:
    """The explicit, bounded scope of a question (D-095.4 — no hidden state).

    ``object_api_name`` / ``requirement_key`` carry the ``impact`` target from the
    UI picker (a bounded-context selection), NOT free-text NL entity extraction
    (SPEC §3). ``recipe_id`` / ``test_id`` optionally narrow failure_cause /
    grounding_drift to one artifact.
    """
    tenant_id: int
    environment_id: Optional[int] = None
    requirement_key: Optional[str] = None      # a Jira key (impact / requirement scope)
    recipe_id: Optional[str] = None            # narrow failure_cause to one recipe
    test_id: Optional[str] = None              # narrow grounding_drift to one test
    object_api_name: Optional[str] = None      # impact target (from the picker)


@dataclass(frozen=True)
class Intent:
    """A classified question kind + the keywords that classified it. The retrieval
    parameters are drawn from the ``QuestionContext`` at retrieval time."""
    kind: IntentKind
    matched_keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceItem:
    """One flattened substrate read row, with its source substrate + a stable
    citation id. ``data`` is JSON-safe flattened fields (the substrate_insights
    flattener shape)."""
    citation_id: str                   # assembler-assigned, stable (e.g. "E1")
    source: str                        # the substrate: "S1" / "S2" / "S6" / "S8"
    kind: str                          # row kind: "interpretation" / "cause_cluster"
                                       # / "grounding_validity" / "entity" / "edge" / ...
    data: dict                         # JSON-safe flattened fields


@dataclass(frozen=True)
class Evidence:
    """The bounded collection of evidence assembled for a question (item-cap +
    token budget applied by the assembler). ``is_empty`` is the keystone gate."""
    intent: IntentKind
    items: tuple[EvidenceItem, ...] = ()

    @property
    def is_empty(self) -> bool:
        return len(self.items) == 0


@dataclass(frozen=True)
class Citation:
    """A returned citation tying an answer back to a concrete substrate row —
    auditable, not opaque (the S6 evidence_refs discipline)."""
    citation_id: str
    source: str
    kind: str
    ref: str                           # audit-readable, e.g. "run_id=..." / "Account"


@dataclass(frozen=True)
class Answer:
    """The result of a question — answered (with citations) or refused.

    ``status="refused"`` is the grounded-or-refuse keystone: produced
    deterministically (empty evidence / no intent) BEFORE any LLM call. The LLM
    only ever phrases a non-empty, bounded evidence block.
    """
    status: AnswerStatus
    text: str
    citations: tuple[Citation, ...] = ()
    refusal_reason: Optional[str] = None
