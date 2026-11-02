"""common.job: every product's jobs in one shape

Step 2 of docs/common-jobs.md. The table only: blt-migrate fills it from
the product tables after this upgrade (blt.api.reproject), with the
mappings as they are then - a migration that ran them would be stuck
with this release's.

Revision ID: f1c5a8e2d3b6
Revises: e7b3c9d1f2a4
Create Date: 2026-11-02 16:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "f1c5a8e2d3b6"
down_revision = "e7b3c9d1f2a4"
branch_labels = None
depends_on = None

SCHEMA = "common"


def _ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "job",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("product_job_key", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("product_status", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        _ts("started_at"),
        _ts("ended_at"),
        _ts("updated_at"),
        sa.Column("asset_name", sa.String(), nullable=True),
        sa.Column("asset_type", sa.String(), nullable=True),
        sa.Column("protection", sa.String(), nullable=True),
        sa.Column("bytes_transferred", sa.BigInteger(), nullable=True),
        sa.Column("bytes_protected", sa.BigInteger(), nullable=True),
        sa.Column("message", sa.String(), nullable=True),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("source_object_id", sa.Uuid(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=True),
        _ts("first_seen_at", nullable=False),
        _ts("last_collected_at", nullable=False),
        sa.ForeignKeyConstraint(["source_id"], [f"{SCHEMA}.source.id"]),
        sa.ForeignKeyConstraint(["parent_id"], [f"{SCHEMA}.job.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "product_job_key", name="uq_common_job_product_key"),
        schema=SCHEMA,
    )
    op.create_index("ix_common_job_started", "job", ["started_at"], schema=SCHEMA)
    op.create_index(
        "ix_common_job_source_started", "job", ["source_id", "started_at"], schema=SCHEMA
    )
    op.create_index("ix_common_job_state_started", "job", ["state", "started_at"], schema=SCHEMA)
    op.create_index("ix_common_job_active", "job", ["is_active"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("job", schema=SCHEMA)
