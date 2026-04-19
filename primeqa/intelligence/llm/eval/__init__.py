"""Offline prompt-quality eval harness.

Fixture loader + helpers. The runner + CLI live in sibling modules.
Each fixture file under `fixtures/<task>/` is JSON with:
  {
    "id": "unique-slug",
    "description": "one line",
    "input": { ... task-specific context ... },
    "expected": { ... assertions + optional dry-mode output ... },
    "rubric": { ... optional per-fixture scoring overrides ... }
  }
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


FIXTURES_ROOT = Path(__file__).parent / "fixtures"


@dataclass
class Fixture:
    id: str
    task: str
    description: str
    input: Dict[str, Any]
    expected: Dict[str, Any]
    rubric: Dict[str, Any]
    path: str


def load_suite(task: str) -> List[Fixture]:
    """Return all fixtures for a task, sorted by id."""
    folder = FIXTURES_ROOT / task
    if not folder.exists():
        return []
    out: List[Fixture] = []
    for f in sorted(folder.glob("*.json")):
        data = json.loads(f.read_text())
        out.append(Fixture(
            id=data.get("id") or f.stem,
            task=task,
            description=data.get("description", ""),
            input=data.get("input", {}),
            expected=data.get("expected", {}),
            rubric=data.get("rubric", {}),
            path=str(f),
        ))
    return out


def available_suites() -> List[str]:
    """Every subfolder under fixtures/ with at least one .json fixture."""
    if not FIXTURES_ROOT.exists():
        return []
    return sorted([
        p.name for p in FIXTURES_ROOT.iterdir()
        if p.is_dir() and any(p.glob("*.json"))
    ])
