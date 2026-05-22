"""Golden-case corpus — data model + loader (D-102.3).

A golden case is ``(scripted tool-turns, S1 fixture, expected governed
outcome)`` authored as JSON so the corpus is extensible by non-engineers (the
D-090(d) curated corpus, v1-scoped to a spectrum-representative set). Cases live
in ``corpus/*.json``; each file is a JSON list of case objects.

Case object shape::

    {
      "id": "config-verified-draft",
      "description": "...",
      "category": "draft" | "caveated_draft" | "refusal" | "dedup",
      "fixture": {
        "entities": [{"entity_type": "Object", "sf_api_name": "Case"}, ...],
        "edges": [{"source": "Case.RequireReason", "target": "Case",
                   "edge_type": "APPLIES_TO", "edge_category": "BEHAVIOR"}, ...]
      },
      "turns": [{"tool": "propose_semantic_intent", "input": {...}}, ...],
      "expect": {"outcome_kind": "draft", "admissibility_layer": "layer_1",
                 "caveat_required": false, "caveat_kind": null,
                 "claim": {"archetype": "configuration",
                           "claim_kind": "metadata-relationship-claim"},
                 "recipe": {"trigger_kind": "inspection-trigger",
                            "recipe_kind": "metadata-recipe"}},
      "emit_twice": false,              # dedup: run the turns twice, score the 2nd
      "repeat_last_on_exhaust": false   # structural-validation-failure: loop a bad turn
    }
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

_CORPUS_DIR = Path(__file__).parent / "corpus"


@dataclass(frozen=True)
class GoldenCase:
    """One curated golden case. ``turns`` / ``expect`` / ``fixture`` stay raw
    dicts — they are couriered to the engine and compared structurally; modeling
    them deeper would couple the corpus loader to the tool + outcome schemas."""

    id: str
    description: str
    category: str
    fixture: dict[str, Any]
    turns: list[dict[str, Any]]
    expect: dict[str, Any]
    emit_twice: bool = False
    repeat_last_on_exhaust: bool = False
    source_file: Optional[str] = field(default=None, compare=False)


def load_corpus(corpus_dir: Optional[Path] = None) -> list[GoldenCase]:
    """Load every golden case from ``corpus_dir`` (default: the packaged
    ``corpus/``). Each ``*.json`` file is a list of case objects. Cases are
    returned sorted by ``id`` for deterministic ordering."""
    corpus_dir = corpus_dir or _CORPUS_DIR
    cases: list[GoldenCase] = []
    for path in sorted(corpus_dir.glob("*.json")):
        raw = json.loads(path.read_text())
        for obj in raw:
            cases.append(GoldenCase(
                id=obj["id"],
                description=obj.get("description", ""),
                category=obj["category"],
                fixture=obj.get("fixture", {}),
                turns=obj["turns"],
                expect=obj["expect"],
                emit_twice=obj.get("emit_twice", False),
                repeat_last_on_exhaust=obj.get("repeat_last_on_exhaust", False),
                source_file=path.name,
            ))
    cases.sort(key=lambda c: c.id)
    return cases
