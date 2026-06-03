"""Knowledge injection layer.

Extensibility foundation for what the AI generator "knows" about the target
Salesforce environment. Every piece of knowledge lives behind a uniform
provider interface:

    Rule               dataclass for a single piece of knowledge
    KnowledgeProvider  Protocol: .get_rules(ctx) -> List[Rule]
    KnowledgeAssembler merges providers with precedence, dedup, token cap

This package is **Substrate 5 (Knowledge System)** \u2014 see
`docs/architecture/substrate_5_knowledge/SPEC.md` (ratified D-134). It exposes
S5's three channels behind one public API:

  - the **provider port** (short proscriptive rules) \u2014 `KnowledgeProvider` +
    `KnowledgeAssembler`, with `SystemPromptRulesProvider` (static JSON,
    `salesforce_knowledge/system_rules.json`) + `LearnedRulesProvider` (wraps
    `feedback_rules.build_rules_block` \u2014 per-tenant signal-derived "common
    mistakes");
  - the **Domain Packs** channel (long prescriptive patterns) \u2014
    `DomainPackProvider` + `DomainPack` / `DomainPackLibrary` /
    `DomainPackSelector`. A parallel channel, deliberately NOT a
    `KnowledgeProvider` (packs are ~1200-token patterns, not \u2264140-char rules).

Future providers slot in by implementing the protocol and registering with the
assembler \u2014 precedence / dedup / token-cap logic is already tested.

Public API:
    from primeqa.knowledge import Rule, QueryContext, KnowledgeAssembler
    from primeqa.knowledge import SystemPromptRulesProvider, LearnedRulesProvider
    from primeqa.knowledge import DomainPackProvider, DomainPack
"""
from primeqa.knowledge.provider import (
    Rule,
    QueryContext,
    KnowledgeProvider,
    KnowledgeAssembler,
)
from primeqa.knowledge.system_rules import SystemPromptRulesProvider
from primeqa.knowledge.learned_rules import LearnedRulesProvider
from primeqa.knowledge.domain_packs import (
    DomainPack,
    DomainPackLibrary,
    DomainPackSelector,
)
from primeqa.knowledge.domain_pack_provider import DomainPackProvider

__all__ = [
    # The provider port (proscriptive rules)
    "Rule",
    "QueryContext",
    "KnowledgeProvider",
    "KnowledgeAssembler",
    "SystemPromptRulesProvider",
    "LearnedRulesProvider",
    # The Domain Packs channel (prescriptive patterns)
    "DomainPackProvider",
    "DomainPack",
    "DomainPackLibrary",
    "DomainPackSelector",
]
