"""Domain Packs — parallel knowledge channel for test-plan generation.

This is NOT a KnowledgeProvider implementation. The existing
KnowledgeAssembler serves SHORT PROSCRIPTIVE rules (e.g. "don't include
IsClosed in a create payload"); Domain Packs serve LONG PRESCRIPTIVE
patterns (e.g. "how Case escalation works in Service Cloud"). Squeezing
both through the same pipe is premature abstraction — they're composed
independently into the final prompt.

SAFETY: Pack files are trusted git-controlled content. They MUST NOT be
populated from user uploads, Jira fetches, or any other untrusted
source. Prompt-injection defence depends on this invariant.

File format — markdown with YAML frontmatter:

    ---
    id: case_escalation
    title: Case Escalation Patterns in Service Cloud
    keywords: [escalate, escalation, case, isescalated, sla]
    objects: [Case, Case_SLA__c, Escalation__c]
    token_budget: 1200
    version: v1
    ---

    # Body content in markdown — this is what reaches the model verbatim.

Frontmatter keys are all required. Malformed files are skipped with a
warning log; the library continues. See `salesforce_domain_packs/README.md`
for the full author guide and security contract.

Feature-gated per tenant via `tenant_agent_settings.llm_enable_domain_packs`
(migration 049). Selector integration happens in
`primeqa/intelligence/generation.py`; prompt wiring in
`primeqa/intelligence/llm/prompts/test_plan_generation.py`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from primeqa.intelligence.knowledge._text import matched_keywords

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class DomainPack:
    """A single loaded domain-pack file.

    `token_budget` is author-declared (advisory / audit only); the selector
    enforces a measured cap using `len(content) // 4` as the token
    estimate. Don't rely on `token_budget` for any runtime decision.
    """
    id: str
    title: str
    keywords: List[str]        # lowercased for case-insensitive matching
    objects: List[str]         # SObject names, original casing preserved
    token_budget: int          # advisory; selector measures content length
    version: str
    content: str               # markdown body (post-frontmatter)
    source_path: str           # absolute path, for cache invalidation + logs

    @property
    def measured_tokens(self) -> int:
        """Rough token estimate — ~4 chars per token for English prose.

        Used as the hard cap in selection. Preferred over the
        author-declared `token_budget` because authors drift / lie.
        """
        return max(1, len(self.content) // 4)


@dataclass
class DomainPackMatch:
    """A pack + why it matched + its relevance score."""
    pack: DomainPack
    score: int
    matched_keywords: List[str] = field(default_factory=list)
    matched_objects: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Library — filesystem IO
# ---------------------------------------------------------------------------

_REQUIRED_FRONTMATTER_KEYS = (
    "id", "title", "keywords", "objects", "token_budget", "version",
)


def _parse_pack_file(path: Path) -> Optional[DomainPack]:
    """Parse one markdown-with-frontmatter file into a DomainPack.

    Returns None (with a warning log) on any shape failure — missing
    frontmatter fence, malformed YAML, missing required keys, wrong
    types. Never raises: a single bad file shouldn't break library load.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("domain_pack: unreadable file %s: %s", path, exc)
        return None

    if not raw.startswith("---"):
        log.warning("domain_pack: %s missing leading '---' frontmatter fence", path)
        return None

    # Split "---\n<yaml>\n---\n<body>" — the first `---` at index 0,
    # next `---` somewhere in [1, end). yaml.safe_load rejects the
    # trivial case of empty frontmatter.
    parts = raw.split("---", 2)
    if len(parts) < 3:
        log.warning("domain_pack: %s has no closing '---' fence", path)
        return None

    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        log.warning("domain_pack: %s malformed YAML frontmatter: %s", path, exc)
        return None

    missing = [k for k in _REQUIRED_FRONTMATTER_KEYS if k not in meta]
    if missing:
        log.warning("domain_pack: %s missing required keys %s", path, missing)
        return None

    try:
        return DomainPack(
            id=str(meta["id"]).strip(),
            title=str(meta["title"]).strip(),
            keywords=[str(k).lower() for k in (meta.get("keywords") or [])],
            objects=[str(o) for o in (meta.get("objects") or [])],
            token_budget=int(meta.get("token_budget") or 1200),
            version=str(meta.get("version") or "v1"),
            content=parts[2].strip(),
            source_path=str(path.absolute()),
        )
    except (TypeError, ValueError) as exc:
        log.warning("domain_pack: %s frontmatter type error: %s", path, exc)
        return None


class DomainPackLibrary:
    """Loads all `.md` files from a directory into a list of DomainPacks.

    Re-reads the directory when any file's mtime is newer than the last
    scan — useful in dev, irrelevant in production (Railway images are
    immutable post-deploy).
    """

    def __init__(self, packs_dir: str):
        self.packs_dir = Path(packs_dir)
        self._packs: List[DomainPack] = []
        self._last_scan: float = 0.0

    def load(self, force: bool = False) -> List[DomainPack]:
        """Return all valid packs, sorted by id for deterministic order.

        Missing directory returns `[]` — the feature stays gracefully
        off rather than raising on a broken deploy layout.
        """
        if not self.packs_dir.exists() or not self.packs_dir.is_dir():
            return []

        # README and any other obvious non-pack docs live alongside the
        # packs. Skip them silently — otherwise every load logs a warn.
        md_paths = sorted(
            p for p in self.packs_dir.glob("*.md")
            if p.stem.lower() not in {"readme", "index"}
        )
        current = max((p.stat().st_mtime for p in md_paths), default=0.0)

        if force or current > self._last_scan or not self._packs:
            reloaded: List[DomainPack] = []
            for path in md_paths:
                pack = _parse_pack_file(path)
                if pack is not None:
                    reloaded.append(pack)
            # Stable sort so "same score" ties resolve predictably in the
            # selector (matches behaviour with id asc).
            reloaded.sort(key=lambda p: p.id)
            self._packs = reloaded
            self._last_scan = current

        return list(self._packs)


# ---------------------------------------------------------------------------
# Selector — pure function
# ---------------------------------------------------------------------------

class DomainPackSelector:
    """Matches packs against a requirement and returns ranked hits."""

    def __init__(self, library: DomainPackLibrary):
        self.library = library

    def select(
        self,
        requirement_text: str,
        referenced_objects: Optional[List[str]] = None,
        max_tokens: int = 4000,
    ) -> List[DomainPackMatch]:
        """Return packs ranked by relevance, total cost ≤ `max_tokens`.

        Scoring: ``len(matched_keywords) + 2 * len(matched_objects)``.
        Packs that don't match anything are excluded.

        **Object-match path is dormant in v1**: pass `referenced_objects=None`
        (the generation caller always does so today) and every pack's
        object-score contribution is zero regardless of declared objects.
        The path is kept alive so v1.1 can activate it once the
        requirements pipeline extracts objects up-front.

        Token-budget: enforced via `pack.measured_tokens` (char-count
        estimate), NOT the author-declared `token_budget`. Cap is the
        running total across selected packs — once adding a pack would
        blow the cap, skip it and try smaller remaining packs.
        """
        use_objects = referenced_objects is not None
        objects_lower = {o.lower() for o in (referenced_objects or [])}

        matches: List[DomainPackMatch] = []
        for pack in self.library.load():
            kw_hits = matched_keywords(requirement_text, pack.keywords)

            if use_objects:
                obj_hits = [o for o in pack.objects if o.lower() in objects_lower]
            else:
                obj_hits = []

            score = len(kw_hits) + 2 * len(obj_hits)
            if score == 0:
                continue

            matches.append(DomainPackMatch(
                pack=pack,
                score=score,
                matched_keywords=kw_hits,
                matched_objects=obj_hits,
            ))

        # Sort by score desc, id asc — deterministic across reloads.
        matches.sort(key=lambda m: (-m.score, m.pack.id))

        # Token-budget cap. Iterate through ranked matches; skip any
        # whose measured size would push the running total over the cap,
        # but keep considering smaller packs. Deterministic given the
        # sort above.
        selected: List[DomainPackMatch] = []
        running = 0
        for match in matches:
            cost = match.pack.measured_tokens
            if running + cost > max_tokens:
                continue
            selected.append(match)
            running += cost
        return selected


__all__ = [
    "DomainPack",
    "DomainPackMatch",
    "DomainPackLibrary",
    "DomainPackSelector",
]
