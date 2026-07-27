"""Widen picklist_value_details.value_api_name 40 -> 255.

Revision ID: 20260727_0011
Revises: 20260727_0010
Create Date: 2026-07-27

D-403 follow-on, found by RUNNING the resync rather than by reading: the Field
phase aborted with

    (psycopg2.errors.StringDataRightTruncation)
    value too long for type character varying(40)

on the first batch of standard-picklist values. The phase transaction rolled
back cleanly (zero partial rows), so this is a blocked capture, not a corruption.

`value_api_name` was sized varchar(40) when the only values S1 ever captured came
from GlobalValueSets, StandardValueSets and custom inline definitions — business
vocabularies, all comfortably short. D-403 starts capturing STANDARD picklist
fields from the REST describe, and those include platform enumerations whose
"values" are namespaced sObject API names. Measured on env-59: **31 values across
7 fields exceed 40 characters**, longest 59 —

    ApexTrigger.TableEnumOrId  CHANNEL_ORDERS__Customer_Order_Product_History__ChangeEvent  (59)
    WebLink.PageOrSobjectType  CHANNEL_ORDERS__Customer_Order_Product_History__c            (49)

255 matches the sibling `value_label` column (already varchar(255), and wide
enough — the longest label captured is 68 chars, a timezone display name). Widening
is the only correct fix: truncating an api-name would silently mint a value that
does not exist in the org, which is precisely the class of defect D-403 exists to
remove.

Widening a varchar in Postgres is a catalog-only change — no table rewrite, no
row scan, no lock beyond a brief ACCESS EXCLUSIVE — so this is safe on a live
table. Idempotent via a guarded DO block (plain ALTER TYPE is not conditional,
and re-running it on an already-255 column would be a no-op rewrite attempt
rather than an error; the guard keeps the migrations-016+ convention honest).

NOT reversible in general: downgrade re-narrows to 40, which would fail if any
row already holds a longer value. The downgrade therefore refuses loudly rather
than silently truncating real org data.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '20260727_0011'
down_revision = '20260727_0010'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'picklist_value_details'
                  AND column_name = 'value_api_name'
                  AND character_maximum_length < 255
            ) THEN
                ALTER TABLE picklist_value_details
                    ALTER COLUMN value_api_name TYPE VARCHAR(255);
            END IF;
        END $$;
    """)


def downgrade():
    # Refuse rather than truncate: a value_api_name IS the org's identifier for
    # a picklist value. Silently shortening one would fabricate a value that
    # does not exist in Salesforce.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM picklist_value_details
                WHERE LENGTH(value_api_name) > 40
            ) THEN
                RAISE EXCEPTION
                    'Cannot downgrade: % picklist value api-names exceed 40 chars; '
                    'narrowing would truncate real org identifiers.',
                    (SELECT COUNT(*) FROM picklist_value_details
                     WHERE LENGTH(value_api_name) > 40);
            END IF;
            ALTER TABLE picklist_value_details
                ALTER COLUMN value_api_name TYPE VARCHAR(40);
        END $$;
    """)
