"""common.source: every source of any product, with a UUID

Step 1 of docs/common-jobs.md. One common.source row is made for each
CommCell and each RSC account already collected, keeping its created_at,
and each product row points up to it through a new source_id.

Revision ID: e7b3c9d1f2a4
Revises: d4a2b6c8e9f1
Create Date: 2026-11-02 12:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "e7b3c9d1f2a4"
down_revision = "d4a2b6c8e9f1"
branch_labels = None
depends_on = None

SCHEMA = "common"
# (schema, table, the source type its rows are)
_PRODUCTS = [("commvault", "commcell", "commvault"), ("rubrik", "account", "rubrik")]


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.create_table(
        "source",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("type", "name", name="uq_common_source_type_name"),
        schema=SCHEMA,
    )
    for schema, table, type_ in _PRODUCTS:
        op.add_column(table, sa.Column("source_id", sa.Uuid(), nullable=True), schema=schema)
        op.execute(
            f"INSERT INTO {SCHEMA}.source (id, type, name, created_at) "
            f"SELECT gen_random_uuid(), '{type_}', name, created_at FROM {schema}.{table}"
        )
        op.execute(
            f"UPDATE {schema}.{table} AS p SET source_id = s.id FROM {SCHEMA}.source AS s "
            f"WHERE s.type = '{type_}' AND s.name = p.name"
        )
        op.alter_column(table, "source_id", nullable=False, schema=schema)
        op.create_unique_constraint(f"{table}_source_id_key", table, ["source_id"], schema=schema)
        op.create_foreign_key(
            f"{table}_source_id_fkey",
            table,
            "source",
            ["source_id"],
            ["id"],
            source_schema=schema,
            referent_schema=SCHEMA,
        )


def downgrade() -> None:
    for schema, table, _ in _PRODUCTS:
        op.drop_constraint(f"{table}_source_id_fkey", table, type_="foreignkey", schema=schema)
        op.drop_column(table, "source_id", schema=schema)
    op.drop_table("source", schema=SCHEMA)
    op.execute(f"DROP SCHEMA {SCHEMA}")
