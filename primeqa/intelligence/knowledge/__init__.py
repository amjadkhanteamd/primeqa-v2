"""Knowledge injection layer.

Extensibility foundation for what the AI generator "knows" about the target
Salesforce environment. Every piece of knowledge lives behind a uniform
provider interface:

    Rule               dataclass for a single piece of knowledge
    KnowledgeProvider  Protocol: .get_rules(ctx) -> List[Rule]
    KnowledgeAssembler merges providers with precedence, dedup, token cap

Today two providers exist:

    SystemPromptRulesProvider  static JSON (salesforce_knowledge/system_rules.json)
    LearnedRulesProvider       wraps existing feedback_rules.build_rules_block
                               (tenant signal-derived "common mistakes")

Future providers slot in by implementing the protocol and registering with
the assembler \u2014 precedence / dedup / token-cap logic is already tested.

Public API:
    from primeqa.intelligence.knowledge import Rule, QueryContext, KnowledgeAssembler
    from primeqa.intelligence.knowledge import SystemPromptRulesProvider, LearnedRulesProvider
"""
from primeqa.intelligence.knowledge.provider import (
    Rule,
    QueryContext,
    KnowledgeProvider,
    KnowledgeAssembler,
)
from primeqa.intelligence.knowledge.system_rules import SystemPromptRulesProvider
from primeqa.intelligence.knowledge.learned_rules import LearnedRulesProvider

__all__ = [
    "Rule",
    "QueryContext",
    "KnowledgeProvider",
    "KnowledgeAssembler",
    "SystemPromptRulesProvider",
    "LearnedRulesProvider",
]
