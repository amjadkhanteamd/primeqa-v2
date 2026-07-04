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
CURRENT = "generation@v18"

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
    "generation@v9": _VERSIONS_DIR / "generation_v9.md",
    "generation@v10": _VERSIONS_DIR / "generation_v10.md",
    "generation@v11": _VERSIONS_DIR / "generation_v11.md",
    "generation@v12": _VERSIONS_DIR / "generation_v12.md",
    "generation@v13": _VERSIONS_DIR / "generation_v13.md",
    "generation@v14": _VERSIONS_DIR / "generation_v14.md",
    "generation@v15": _VERSIONS_DIR / "generation_v15.md",
    "generation@v16": _VERSIONS_DIR / "generation_v16.md",
    "generation@v17": _VERSIONS_DIR / "generation_v17.md",
    "generation@v18": _VERSIONS_DIR / "generation_v18.md",
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
    # v9 (D-247): config decomposition made first-class — existence / property /
    # metadata-relationship are scan targets, not behavior props; property
    # atomicity (precision+scale = two intents); value-claim persistence framing;
    # the per-AC coverage contract (acceptance_criteria + ac_ref / no_admissible_test).
    "generation@v9": "fdd5e2171ea9da1f8fef32c8ecd70e5b96b39c832a8bd6211fec8dd680487240",
    # v10 (D-293): prohibition behaviour-instance contract — the LLM names the
    # rejection business STATE (rejection_conditions: field/predicate/value
    # clauses) so distinct rules are distinct claims (no AC1/2/4 collapse); the
    # substrate REFUSES an incomplete behaviour instance (no derivable behavioural
    # reject recipe) rather than degrading to the caveated metadata inspection.
    "generation@v10": "0f43a120674001d1476698c274d3a939282bc0a7d725e5b564a9146d34d5d69e",
    # v11 (D-299): automation-effect entry-condition trigger — the LLM proposes
    # trigger_fields (the {field_name, value} pairs the create must SET so the
    # Flow's entry gate fires AND the record passes its validation rules) plus
    # automation_name (disambiguate WHICH Flow when several fire on the object).
    "generation@v11": "57677c7318cb053133f49db98421a16a60a7bf76ee15a8c6d35f8bc4f58cadd0",
    # v12 (D-304): the formula automation primitive — a calculated field's value
    # is an automation-effect (automation_name = the FIELD, trigger_fields = the
    # formula inputs + VR-survival staging, expected_value = the computed result).
    "generation@v12": "f36cd9609a5fb038be099e2a27228e3ee74520bbecae427ea8841681a608ba01",
    # v13 (D-305): the acceptance archetype — "this case SAVES" is a claim
    # (acceptance_conditions define the case; boundary values are identity).
    "generation@v13": "1eb5c8ec81c95288d77960e9a855b213b905bd1919a2a40f25955abf004e695a",
    # v14 (D-306): update-then-observe — "recalculates when X changes" →
    # update_trigger_fields (initial vs changed state, both phases k16); "the
    # CHANGE succeeds" → acceptance update_conditions (create-accepted and
    # update-accepted are distinct claims/intents).
    "generation@v14": "79b2ee9a703a8c6c2d8ca299ef9a7050e29f11b112939f974f7521aeeb503b6b",
    # v15 (D-307): the automation-ABSENCE contract — expected_absence on the
    # cross-object shape ("no correlated record for this case"); presence and
    # each absence band are distinct intents; field-conditional absence refused.
    "generation@v15": "3f2eab5cec8aee714cd63f06b7a17c8661aabd532a4dc646209ef5e89f56aa56",
    # v16 (D-308): approvals — an approval process is an automation
    # (automation_name + ProcessInstance/TargetObjectId; absence for the
    # no-approval cases; every boundary a distinct intent; name required).
    "generation@v16": "46cbd544f9dbe680ba28d00ed9088389bd4a030711ed49aeeb06038ce25ea9ab",
    # v17 (D-313): intent_descriptors is mandatory — acceptance_criteria alone is
    # NEVER a proposal; emit one intent per AC in the same call (fixes the req-302
    # "propose with only acceptance_criteria" persistent-Layer-A decline).
    "generation@v17": "4a2077ed46fea3110ee29d8d5d3001da7ed4ef98124f8e2deb3fde32656fe374",
    # v18 (D-318): automation_name is OPTIONAL for record-triggered Flow effects —
    # the substrate binds the Flow by the EFFECT it produces (field/value or
    # effect_object), so describe the effect and don't invent an internal Flow
    # name (name only to break a same-effect tie); approvals + calculated fields
    # stay named. Also documents the `target_subject_hint.object` subject-key the
    # behavioral kinds use (closes the D-317 prompt-doc follow-up), and restores
    # the D-313 intent-mandatory directive into working base.md — it had lived
    # only in the frozen v17 file, so composing from the working source would
    # otherwise have silently dropped it.
    "generation@v18": "51783404eec407ae48852ad6362bdebe26302cf0a92ec29f7d4b6b41a2d1b70a",
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
