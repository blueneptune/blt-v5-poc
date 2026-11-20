"""commvault source_object: plan and protection

Each inventoried thing's plan and the source's own view of whether it is
protected (protected / unprotected / excluded), so "discovered but not
backed up" can be asked of every kind. Rows inventoried before this have
both null until the next inventory run.

Revision ID: c7d2e9a4b6f3
Revises: a9c4e2f7d1b5
Create Date: 2026-11-20 10:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "c7d2e9a4b6f3"
down_revision = "a9c4e2f7d1b5"
branch_labels = None
depends_on = None

SCHEMA = "commvault"


def upgrade() -> None:
    op.add_column("source_object", sa.Column("plan", sa.String(), nullable=True), schema=SCHEMA)
    op.add_column(
        "source_object", sa.Column("protection", sa.String(), nullable=True), schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_column("source_object", "protection", schema=SCHEMA)
    op.drop_column("source_object", "plan", schema=SCHEMA)
