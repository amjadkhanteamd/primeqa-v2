"""Widen summary_prompt_version and summary_model columns on
flow_details and validation_rule_details.

The original phase-2 summaries migration (20260430_0040) sized
these columns at VARCHAR(20) and VARCHAR(50). The 20-char
ceiling fails for prompt-version identifiers like
'entity_summary_flow@v1' (22 chars).

This is a latent bug that predates §23 — but §23's enrichment
worker is the first code path that actually exercises the
columns. Detection happened during the §23 live integration
test (T2 attempt 2, 2026-05-14).

Widened to match LLMUsageLog conventions:
- prompt_version: VARCHAR(60) (was VARCHAR(20))
- model: VARCHAR(100) (was VARCHAR(50))
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260515_0010"
down_revision: Union[str, Sequence[str], None] = "20260514_0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for tbl in ("flow_details", "validation_rule_details"):
        op.execute(
            f"ALTER TABLE {tbl} ALTER COLUMN summary_prompt_version "
            f"TYPE VARCHAR(60)"
        )
        op.execute(
            f"ALTER TABLE {tbl} ALTER COLUMN summary_model "
            f"TYPE VARCHAR(100)"
        )


def downgrade() -> None:
    # Reverse to original widths. Note: any rows containing
    # values longer than the original widths would fail; this
    # downgrade assumes the table is empty or contains only
    # original-width values.
    for tbl in ("flow_details", "validation_rule_details"):
        op.execute(
            f"ALTER TABLE {tbl} ALTER COLUMN summary_prompt_version "
            f"TYPE VARCHAR(20)"
        )
        op.execute(
            f"ALTER TABLE {tbl} ALTER COLUMN summary_model "
            f"TYPE VARCHAR(50)"
        )
