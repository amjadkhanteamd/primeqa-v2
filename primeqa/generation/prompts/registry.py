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
CURRENT = "generation@v8"

# version id -> frozen composed artifact (filesystem-safe slug).
_FILES = {
    "generation@v1": _VERSIONS_DIR / "generation_v1.md",
    "generation@v2": _VERSIONS_DIR / "generation_v2.md",
    "generation@v3": _VERSIONS_DIR / "generation_v3.md",
    "generation@v4": _VERSIONS_DIR / "generation_v4.md",
    "generation@v5": _VERSIONS_DIR / "generation_v5.md",
    "generation@v6": _VERSIONS_DIR / "generation_v6.md",
    "generation@v7": _VERSIONS_DIR / "generation_v7.md",
    "generation@v8": _VERSIONS_DIR / "generation_v8.md",
}

# Recorded SHA-256 of each frozen version's composed content (D-103.1 drift
# guard). A frozen version is immutable; if its file is edited, content_hash()
# diverges from this value and the guard test fails.
RECORDED_HASHES = {
    "generation@v1": "a486cebfa3d5a91e15dd3c20bebb39f287ed114881e4537563d2423f52fccdef",
    "generation@v2": "4618fc0c0e62ae5bf4e648c2c8d69c3a4458396826f14a528745b8381d1eb0e7",
    "generation@v3": "3820ffbfee52fed4c1594b495e993de4151a89751c59dd98d2db6cbbba33eaed",
    # v4 (D-203): prohibition guidance names WHICH operation is prohibited via
    # target_subject_hint.operation (update/delete-rejected dispatch).
    "generation@v4": "135bdd3f49a37869bb73c09298015b58ca5cfbf4c57879eabb2bbce3fe289225",
    # v5 (D-207): multi-intent coverage — propose EVERY distinct testable
    # intent as the intent_descriptors array (positive + one negative per
    # prohibition/condition + config checks), each on its own verbatim excerpt.
    "generation@v5": "b2b6c8f7519947c4c1af446d719430fb5c78408a6d659a69d7a0aceca9ae8117",
    # v6 (D-210.1): automation-effect + state-transition reach — the hint
    # contracts (field_name/expected_value to-state; trigger_object for
    # cross-object transitions; effect_object/effect_lookup_field for
    # Flow-created records).
    "generation@v6": "19835bff29e78b2467a3321c9a0c963171cb0644e6131f9d6cdf877b3ef3b76e",
    # v7 (D-222): state-transition staged-trigger pair (trigger_field +
    # trigger_value) so conditional transitions can be provoked.
    "generation@v7": "bf374f509e0b93f1f990d34e7090799b88d415bb53803c611de9c8b399627244",
    # D-227: cross-object trigger + parent-stamp hint contracts.
    "generation@v8": "c759d9c8f18bf037b7cf954af701b7b1ed36489672c33f6e93392467f1205d7f",
}

# Working composition order — authors the NEXT frozen version (NOT runtime).
_FRAGMENTS = ["data_behavior", "configuration", "permission", "ui"]


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
