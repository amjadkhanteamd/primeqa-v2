"""LearnedRulesProvider \u2014 wraps the existing tenant feedback-rule aggregator.

Zero-regression integration: `feedback_rules.build_rules_block(tenant_id)`
is unchanged and still callable directly. This provider adapts its string
output into the Rule-list shape the assembler expects, so the assembler
can merge it with system + future providers uniformly.

Output shape: a single synthetic Rule object wrapping the full rendered
block that build_rules_block already produces. Future work may split this
into per-signal Rules with stable ids (so dedup against system rules
works); for today, one aggregate rule is sufficient because the existing
aggregator already does per-signal dedup + ranking upstream.
"""
from __future__ import annotations

import logging
from typing import List

from primeqa.intelligence.knowledge.provider import (
    QueryContext, Rule, KnowledgeProvider,
)

log = logging.getLogger(__name__)


class LearnedRulesProvider:
    """Adapts feedback_rules.build_rules_block into the provider protocol.

    The existing module already renders "Common mistakes to avoid" from
    generation_quality_signals with severity + frequency ranking. That
    logic stays authoritative; this class just wraps the rendered
    string so KnowledgeAssembler can merge it with other providers.
    """

    def __init__(self, window_days: int = 30):
        self.window_days = window_days

    def get_rules(self, ctx: QueryContext) -> List[Rule]:
        if ctx.tenant_id is None:
            return []
        try:
            # Import lazily so unit tests that don't need DB don't drag
            # SQLAlchemy + all model modules into the import graph.
            from primeqa.intelligence.llm import feedback_rules
            block = feedback_rules.build_rules_block(
                ctx.tenant_id, window_days=self.window_days,
            )
        except Exception as e:
            log.warning("learned rules provider failed for tenant=%s: %s",
                        ctx.tenant_id, e)
            return []
        if not block or not block.strip():
            return []
        # Wrap the pre-rendered block as a single high-confidence learned rule.
        # The assembler renders each Rule as "- <rule_text>" under its
        # category heading. The pre-rendered block already has its own
        # internal structure, so we put it into a dedicated category
        # ("learned") which renders AFTER the canonical three.
        return [Rule(
            id="LEARNED_FEEDBACK_BLOCK",
            object_name=None,
            field_name=None,
            category="learned",
            rule_text=block.strip(),
            source="learned",
            confidence=0.95,  # high but below system 1.0 for cap-ranking
            scope="org",
        )]
