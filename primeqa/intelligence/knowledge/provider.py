"""Knowledge Provider protocol + the Assembler.

Every rule the AI generator should know about SF flows through this layer.
One concept (Rule), one interface (KnowledgeProvider), one merger
(KnowledgeAssembler). The assembler handles:

  1. Collecting rules from every registered provider
  2. Deduplicating by rule id
  3. Applying source-precedence (learned > org-curated > global > system)
  4. Enforcing a token cap (rank by confidence, truncate lowest)
  5. Rendering to a compact string ready to drop into the prompt

The assembler is stateless + deterministic: same inputs \u2192 same output
bytes, so the rendered block is cache-stable (critical for Anthropic's
prompt cache).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Protocol, Tuple

log = logging.getLogger(__name__)


# ---- Public types ---------------------------------------------------------

@dataclass(frozen=True)
class Rule:
    """One piece of knowledge the AI should respect.

    Fields:
      id           stable identifier; dedup key. e.g. "NO_ID_IN_CREATE"
      object_name  SObject this rule applies to; None = applies everywhere
      field_name   Field this rule applies to; None = object-level rule
      category     grouping for rendered output: "field_behaviour" |
                   "operation" | "assertion"
      rule_text    compressed imperative. "Never include 'Id' in create payload."
                   Keep under ~140 chars to budget tokens.
      source       "system" | "curated" | "learned". Precedence: learned wins.
      confidence   0.0\u20131.0. System/curated rules are 1.0; learned rules
                   derive theirs from signal frequency \u00d7 severity.
      scope        "global" | "org". Org rules only apply when ctx.env matches.
    """
    id: str
    object_name: Optional[str]
    field_name: Optional[str]
    category: str
    rule_text: str
    source: str = "system"
    confidence: float = 1.0
    scope: str = "global"


@dataclass(frozen=True)
class QueryContext:
    """Passed to every provider when rules are requested.

    Providers use whichever fields they care about. System rules ignore
    everything. Learned rules key off tenant_id. Future org-curated rules
    key off environment_id. Objects / fields filter the rule set to only
    what's relevant to this generation.
    """
    tenant_id: Optional[int] = None
    environment_id: Optional[int] = None
    objects: Tuple[str, ...] = ()
    fields: Tuple[str, ...] = ()


class KnowledgeProvider(Protocol):
    """Duck-typed interface. Implementations return a list of Rule objects."""

    def get_rules(self, ctx: QueryContext) -> List[Rule]:
        ...


# ---- Precedence, cap, rendering constants ---------------------------------

# Lower number = wins on dedup when two rules share an id.
_SOURCE_RANK = {
    "learned": 0,   # org-specific signal-derived; freshest truth
    "curated": 1,   # org-curated by a human admin (future)
    "system":  2,   # baked-in SF-wide rules
}

# Category ordering in the rendered output. Field behaviour first because
# it's the highest-signal; operations next (concrete actions); assertions
# last (post-execution reasoning).
_CATEGORY_ORDER = ("field_behaviour", "operation", "assertion")

# Max tokens for the entire rendered block. Rough byte heuristic below.
_DEFAULT_TOKEN_CAP = 3000

# Rough tokens-per-char heuristic (Anthropic tokenization averages ~4
# chars/token on English prose; closer to 3.5 for rule text + punctuation).
_CHARS_PER_TOKEN = 3.5


# ---- Assembler ------------------------------------------------------------

class KnowledgeAssembler:
    """Merges rules from all providers into a single prompt-ready string.

    Construct with a list of providers. assemble() pulls rules from each,
    merges with precedence + dedup + cap, then renders.

    Stateless + deterministic: same (providers, ctx) inputs produce byte-
    identical output. Safe to call inside the prompt build path.
    """

    def __init__(self, providers: List[KnowledgeProvider],
                 token_cap: int = _DEFAULT_TOKEN_CAP):
        self.providers = list(providers)
        self.token_cap = int(token_cap)

    def assemble(self, ctx: QueryContext) -> str:
        """Return the rendered knowledge block (empty string if no rules)."""
        collected: List[Rule] = []
        for p in self.providers:
            try:
                collected.extend(p.get_rules(ctx))
            except Exception as e:  # never let a provider crash prompt build
                log.warning("knowledge provider %s failed: %s",
                            type(p).__name__, e)

        if not collected:
            return ""

        merged = self._merge(collected)
        capped = self._apply_cap(merged)
        return self._render(capped)

    # ---- Internals --------------------------------------------------------

    @staticmethod
    def _merge(rules: List[Rule]) -> List[Rule]:
        """Dedup by id with source-precedence. First-write-wins within the
        same source, so providers can't accidentally shadow each other by
        emitting the same rule twice.
        """
        by_id: dict = {}
        for r in rules:
            existing = by_id.get(r.id)
            if existing is None:
                by_id[r.id] = r
                continue
            # Precedence: keep the lower-ranked source (learned beats system).
            if _SOURCE_RANK.get(r.source, 99) < _SOURCE_RANK.get(existing.source, 99):
                by_id[r.id] = r
            # tie = first-write-wins (order-stable)
        return list(by_id.values())

    def _apply_cap(self, rules: List[Rule]) -> List[Rule]:
        """If the rendered block would exceed token_cap, drop lowest-
        confidence rules first. Stable: ties broken by source rank, then id.
        """
        ranked = sorted(
            rules,
            key=lambda r: (
                -r.confidence,                         # high confidence first
                _SOURCE_RANK.get(r.source, 99),        # learned before system on ties
                r.id,                                  # deterministic tiebreak
            ),
        )
        kept: List[Rule] = []
        for r in ranked:
            tentative_tokens = self._estimate_tokens(kept + [r])
            if tentative_tokens <= self.token_cap:
                kept.append(r)
            else:
                log.info("knowledge assembler token cap hit; dropping '%s' "
                         "and all lower-ranked rules", r.id)
                break
        # Restore category grouping for deterministic output
        kept.sort(key=lambda r: (
            _CATEGORY_ORDER.index(r.category) if r.category in _CATEGORY_ORDER else 99,
            r.id,
        ))
        return kept

    def _estimate_tokens(self, rules: List[Rule]) -> int:
        if not rules:
            return 0
        # Approximate: render and char-count / 3.5. Cheap enough that we
        # can re-compute per candidate without caching.
        text = self._render(rules)
        return int(len(text) / _CHARS_PER_TOKEN)

    @staticmethod
    def _render(rules: List[Rule]) -> str:
        if not rules:
            return ""
        by_cat: dict = {}
        for r in rules:
            by_cat.setdefault(r.category, []).append(r)

        lines = ["## Salesforce rules the generator MUST follow", ""]
        for cat in _CATEGORY_ORDER:
            if cat not in by_cat:
                continue
            heading = {
                "field_behaviour": "### Field behaviour",
                "operation":       "### Operations",
                "assertion":       "### Assertions",
            }.get(cat, f"### {cat}")
            lines.append(heading)
            # Sort for deterministic output; keep tag prefix short.
            for r in sorted(by_cat[cat], key=lambda x: x.id):
                tag = ""
                if r.object_name and r.field_name:
                    tag = f"[{r.object_name}.{r.field_name}] "
                elif r.object_name:
                    tag = f"[{r.object_name}] "
                lines.append(f"- {tag}{r.rule_text}")
            lines.append("")
        # Any category not in the canonical order, appended at the end.
        # The "learned" category is special: its rule_text is pre-rendered
        # by feedback_rules.build_rules_block (already includes a "###"
        # header + multi-line bullets), so we dump it verbatim without
        # any bullet / tag prefix.
        for cat, rs in by_cat.items():
            if cat in _CATEGORY_ORDER:
                continue
            if cat == "learned":
                for r in rs:
                    # Rule text already carries its own heading + bullets
                    lines.append(r.rule_text)
                    lines.append("")
                continue
            lines.append(f"### {cat}")
            for r in sorted(rs, key=lambda x: x.id):
                tag = ""
                if r.object_name:
                    tag = f"[{r.object_name}{'.' + r.field_name if r.field_name else ''}] "
                lines.append(f"- {tag}{r.rule_text}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
