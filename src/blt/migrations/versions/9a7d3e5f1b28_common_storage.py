"""common.storage and common.storage_sample: storage monitoring

Step 2 of docs/roadmap-2026-11.md (docs/storage.md): each source's
storage targets, and their capacity and use every hour.

Revision ID: 9a7d3e5f1b28
Revises: f1c5a8e2d3b6
Create Date: 2026-11-05 14:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "9a7d3e5f1b28"
down_revision = "f1c5a8e2d3b6"
branch_labels = None
depends_on = None

SCHEMA = "common"


def _ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "storage",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("product_key", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("product_type", sa.String(), nullable=True),
        sa.Column("hosts", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("server", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("present", sa.Boolean(), nullable=False),
        _ts("first_seen_at", nullable=False),
        _ts("last_seen_at", nullable=False),
        sa.ForeignKeyConstraint(["source_id"], [f"{SCHEMA}.source.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "product_key", name="uq_common_storage_product_key"),
        schema=SCHEMA,
    )
    op.create_table(
        "storage_sample",
        sa.Column("storage_id", sa.Uuid(), nullable=False),
        _ts("collected_at", nullable=False),
        sa.Column("capacity_bytes", sa.BigInteger(), nullable=True),
        sa.Column("used_bytes", sa.BigInteger(), nullable=True),
        sa.Column("free_bytes", sa.BigInteger(), nullable=True),
        sa.Column("logical_bytes", sa.BigInteger(), nullable=True),
        sa.Column("physical_bytes", sa.BigInteger(), nullable=True),
        sa.Column("dedupe_ratio", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["storage_id"], [f"{SCHEMA}.storage.id"]),
        sa.PrimaryKeyConstraint("storage_id", "collected_at"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("storage_sample", schema=SCHEMA)
    op.drop_table("storage", schema=SCHEMA)
