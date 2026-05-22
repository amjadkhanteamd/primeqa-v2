"""Prompt registry — frozen per-version composed prompts (D-089 / D-103).

Two surfaces, deliberately separated:

  - **Working source** (``base.md`` + ``fragments/*.md``): human-editable. It
    authors the *next* version (:func:`compose_working`). The runtime never
    reads it.
  - **Frozen versions** (``versions/<slug>.md``): each shipped version's
    composed content, frozen and immutable. The runtime reads these via
    :func:`get`, so a later edit to the working source cannot change an
    already-shipped version (replay determinism, D-103.1).

Immutability is convention (never edit a shipped ``versions/`` file; author a
new version instead) made mechanical by a content-hash guard: :data:`RECORDED_HASHES`
records each frozen version's SHA-256, and a unit test asserts the live frozen
content still hashes to it — catching an accidental edit to a frozen version
before it corrupts replay.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).parent
_FRAGMENTS_DIR = _DIR / "fragments"
_VERSIONS_DIR = _DIR / "versions"

# The default version new generations use (replay pins its own version on the
# request's operational_context.prompt_template_version, per D-071).
CURRENT = "generation@v1"

# version id -> frozen composed artifact (filesystem-safe slug).
_FILES = {
    "generation@v1": _VERSIONS_DIR / "generation_v1.md",
}

# Recorded SHA-256 of each frozen version's composed content (D-103.1 drift
# guard). A frozen version is immutable; if its file is edited, content_hash()
# diverges from this value and the guard test fails.
RECORDED_HASHES = {
    "generation@v1": "a486cebfa3d5a91e15dd3c20bebb39f287ed114881e4537563d2423f52fccdef",
}

# Working composition order — authors the NEXT frozen version (NOT runtime).
_FRAGMENTS = ["data_behavior", "configuration", "permission"]


@lru_cache(maxsize=None)
def get(version: str | None = None) -> str:
    """The frozen composed system prompt for ``version`` (default
    :data:`CURRENT`). Reads the frozen artifact — never recomposes from the
    working source (D-103.1)."""
    version = version or CURRENT
    path = _FILES.get(version)
    if path is None:
        raise KeyError(f"unknown prompt version: {version!r}; known: {sorted(_FILES)}")
    return path.read_text()


def content_hash(version: str | None = None) -> str:
    """SHA-256 of the frozen content actually on disk for ``version``."""
    return hashlib.sha256(get(version).encode("utf-8")).hexdigest()


def recorded_hash(version: str | None = None) -> str:
    """The SHA-256 recorded when ``version`` was frozen."""
    return RECORDED_HASHES[version or CURRENT]


def versions() -> list[str]:
    return sorted(_FILES)


def compose_working() -> str:
    """Compose ``base.md`` + all archetype fragments from the WORKING source —
    the freeze step that authors the NEXT version (D-103.2 all-fragments). A dev
    tool: the runtime never calls this (it reads a frozen version via
    :func:`get`). Composition is deterministic so a freeze is reproducible."""
    parts = [(_DIR / "base.md").read_text().strip()]
    for name in _FRAGMENTS:
        frag = (_FRAGMENTS_DIR / f"{name}.md").read_text().strip()
        parts.append(f"## Archetype guidance — {name}\n\n{frag}")
    return "\n\n".join(parts) + "\n"
