"""S1 picklist capture: record the per-field capture OUTCOME.

Revision ID: 20260727_0010
Revises: 20260708_0010
Create Date: 2026-07-27

D-403 / D-399.1. Today a NULL ``field_details.picklist_value_set_entity_id`` is
produced identically by three different facts, and no consumer can tell them
apart:

  * the field genuinely has no value set;
  * the D-118 standard-picklist CONTENT-MATCH found no exact set-equal
    StandardValueSet (``match_standard_value_set`` fail-closed: 0 matches);
  * the match was AMBIGUOUS (>=2 SVSes share an identical value set);
  * the PicklistValueSet phase was skipped on a resumed sync, so the SVS index
    was empty and NO standard field could match this run
    (``phases.py`` logs this at INFO and continues).

Measured on env-59 before this migration: **275 of 377** picklist/multipicklist
fields carry a NULL FK — 275 standard, 0 custom — so 73% of the picklist surface
is indistinguishable between "no values" and "not captured". That ambiguity is
what makes value-membership validation (D-399) unsafe: refusing a claim on an
uncaptured field would invert fail-loud into a SILENT false refusal (D-399.1's
binding constraint — refuse only where capture is known-complete, otherwise
report CANNOT VALIDATE, never INVALID).

  * ``field_details.picklist_capture VARCHAR`` (nullable; the capture outcome for
    THIS field's value set. Open text, NO CHECK — code owns the vocabulary, and a
    CHECK would force a migration to admit a future capture source, the D-285
    precedent. Vocabulary: ``matched_svs`` / ``gvs`` / ``inline`` / ``no_match`` /
    ``ambiguous`` / ``phase_skipped``.)

**NULL means PRE-MIGRATION — it does NOT mean "unknown and fine".** Every row
existing before this migration is NULL because nothing wrote the column, not
because capture was assessed and found inconclusive. A consumer MUST treat NULL
as "capture outcome unknown, do not draw a conclusion", exactly as it must treat
``no_match`` / ``ambiguous`` / ``phase_skipped`` as "not authoritative". Only
``matched_svs`` / ``gvs`` / ``inline`` assert a captured set.

Additive, nullable, NO default, NO backfill — a pure ADD COLUMN (non-locking on
Postgres for nullable-no-default; zero rows rewritten). Idempotent (ADD COLUMN IF
NOT EXISTS) per the migrations-016+ convention. No index (no reader filters on it
yet). Written by the sync at the point the value-set marker is decided
(``phases.py``); nothing reads it in this slice — the D-399 validator is its first
consumer and is NOT built here.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '20260727_0010'
down_revision = '20260708_0010'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE field_details "
        "ADD COLUMN IF NOT EXISTS picklist_capture VARCHAR")
    op.execute(
        "COMMENT ON COLUMN field_details.picklist_capture IS "
        "'D-403: how this field''s picklist value set was captured — "
        "matched_svs / gvs / inline / no_match / ambiguous / phase_skipped. "
        "Open text, no CHECK (code owns the vocabulary). NULL means "
        "PRE-MIGRATION (nothing wrote it), NOT ''unknown and fine'': a NULL FK "
        "alone cannot distinguish ''field has no values'' from ''values not "
        "retrieved''. Only matched_svs / gvs / inline assert a captured set; "
        "every other value (and NULL) means NOT AUTHORITATIVE, which the D-399 "
        "value-membership validator must read as CANNOT VALIDATE rather than "
        "INVALID (D-399.1).'")


def downgrade():
    op.execute("ALTER TABLE field_details DROP COLUMN IF EXISTS picklist_capture")
