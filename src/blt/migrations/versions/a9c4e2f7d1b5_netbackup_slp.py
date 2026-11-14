"""netbackup storage lifecycle policies

Each primary's SLPs as last collected: their steps, with the storage
each writes to and the step it copies from, to trace a copy job back to
the storage it read (the Storage page's Sankey).

Revision ID: a9c4e2f7d1b5
Revises: b2e6d4f8a1c3
Create Date: 2026-11-14 10:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a9c4e2f7d1b5"
down_revision = "b2e6d4f8a1c3"
branch_labels = None
depends_on = None

SCHEMA = "netbackup"


def upgrade() -> None:
    op.create_table(
        "slp",
        sa.Column("primary_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=True),
        sa.Column("operations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["primary_id"], [f"{SCHEMA}.primary_server.id"]),
        sa.PrimaryKeyConstraint("primary_id", "name"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("slp", schema=SCHEMA)
