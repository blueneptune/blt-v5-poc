"""commvault tables into a schema of their own

Revision ID: b8e3c5d17a42
Revises: a4d2f8c61e37
Create Date: 2026-10-29 20:00:00

A move, not a copy: SET SCHEMA changes only the catalog, so it takes a
moment whatever the size of `job`. Each table's indexes, constraints
and id sequence go with it, and foreign keys keep pointing at the same
tables. The indexes named after their column take the schema into their
name, as Rubrik's do (ix_commvault_job_client_name). Nothing is left
behind in `public` - no compatibility views, as nothing outside blt read
these tables when they moved.
"""

from alembic import op

revision = "b8e3c5d17a42"
down_revision = "a4d2f8c61e37"
branch_labels = None
depends_on = None

SCHEMA = "commvault"
TABLES = ["commcell", "collection_run", "job", "source_object", "instance_backup_report"]
# Named from table and column, so the models now name them with the schema.
INDEXES = [
    "commcell_name",
    "job_client_name",
    "source_object_name",
    "source_object_parent_id",
    "instance_backup_report_instance_id",
]


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    for table in TABLES:
        op.execute(f"ALTER TABLE public.{table} SET SCHEMA {SCHEMA}")
    for index in INDEXES:
        op.execute(f"ALTER INDEX {SCHEMA}.ix_{index} RENAME TO ix_{SCHEMA}_{index}")


def downgrade() -> None:
    for index in INDEXES:
        op.execute(f"ALTER INDEX {SCHEMA}.ix_{SCHEMA}_{index} RENAME TO ix_{index}")
    for table in TABLES:
        op.execute(f"ALTER TABLE {SCHEMA}.{table} SET SCHEMA public")
    op.execute(f"DROP SCHEMA {SCHEMA}")
