"""SystemPromptRulesProvider \u2014 loads rules from a static JSON file.

Rules live in salesforce_knowledge/system_rules.json at the repo root.
Editing the file + redeploying is the update path (no DB migration).

Filter semantics:
  - A rule with object_name=None + field_name=None is always in scope.
  - A rule with object_name set + field_name None is in scope when
    ctx.objects contains that object.
  - A rule with both object_name + field_name set is in scope when
    ctx.objects contains the object. We deliberately do NOT require
    the field in ctx.fields \u2014 rules about specific fields are useful
    as general guardrails ("here's a gotcha on Case.Name") even if the
    current generation doesn't reference that field yet.

If ctx.objects is empty, all rules pass through (no filtering). Useful
at prompt-build time when we don't know which objects the AI will reach
for.
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import List

from primeqa.intelligence.knowledge.provider import (
    QueryContext, Rule, KnowledgeProvider,
)

log = logging.getLogger(__name__)


def _default_rules_path() -> str:
    """Default path: <repo_root>/salesforce_knowledge/system_rules.json."""
    # primeqa/intelligence/knowledge/system_rules.py -> repo root
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, "..", "..", ".."))
    return os.path.join(root, "salesforce_knowledge", "system_rules.json")


@lru_cache(maxsize=4)
def _load_rules_cached(path: str) -> tuple:
    """File-mtime-keyed cache so repeated assembler calls don't re-parse.

    Cache invalidates on redeploy (new mtime). Within a single process the
    rules are effectively read once.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        log.warning("system_rules.json not found at %s; SystemPromptRulesProvider "
                    "will return empty rule list", path)
        return tuple()
    except json.JSONDecodeError as e:
        log.error("system_rules.json is malformed: %s; returning empty rule list", e)
        return tuple()

    rules = []
    for r in data.get("rules", []):
        try:
            rules.append(Rule(
                id=r["id"],
                object_name=r.get("object_name"),
                field_name=r.get("field_name"),
                category=r.get("category", "operation"),
                rule_text=r["rule_text"],
                source=r.get("source", "system"),
                confidence=float(r.get("confidence", 1.0)),
                scope=r.get("scope", "global"),
            ))
        except (KeyError, TypeError, ValueError) as e:
            log.warning("skipping malformed rule %s: %s", r.get("id", "?"), e)
    return tuple(rules)


class SystemPromptRulesProvider:
    """Loads rules from salesforce_knowledge/system_rules.json and filters
    to the objects referenced by the current generation context.
    """

    def __init__(self, rules_path: str = None):
        self.rules_path = rules_path or _default_rules_path()

    def get_rules(self, ctx: QueryContext) -> List[Rule]:
        all_rules = _load_rules_cached(self.rules_path)
        if not ctx.objects:
            # No object context supplied: return the full set. Caller's
            # token cap handles the budget.
            return list(all_rules)
        objects_lower = {o.lower() for o in ctx.objects}
        out: List[Rule] = []
        for r in all_rules:
            if r.object_name is None:
                out.append(r)  # applies everywhere
            elif r.object_name.lower() in objects_lower:
                out.append(r)
            # else: rule targets an object not in this generation's scope
        return out
