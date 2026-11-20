"""netbackup source_object: what each primary says exists

Hosts, VMs, SQL Server instances and databases, as a primary's
inventory lists them (blt.netbackup.assets), keyed and tracked as
commvault.source_object and rubrik.source_object are, so the Assets view
reads all three side by side.

Revision ID: e3b8f1a6c2d9
Revises: c7d2e9a4b6f3
Create Date: 2026-11-20 14:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e3b8f1a6c2d9"
down_revision = "c7d2e9a4b6f3"
branch_labels = None
depends_on = None

SCHEMA = "netbackup"


def _ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "source_object",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("primary_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("source_key", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("app_type", sa.String(), nullable=True),
        sa.Column("location", sa.String(), nullable=True),
        sa.Column("plan", sa.String(), nullable=True),
        sa.Column("protection", sa.String(), nullable=True),
        _ts("last_backup_at"),
        sa.Column("last_backup_job_id", sa.BigInteger(), nullable=True),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("present", sa.Boolean(), nullable=False),
        _ts("first_seen_at", nullable=False),
        _ts("last_seen_at", nullable=False),
        sa.Column("last_seen_run_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["primary_id"], [f"{SCHEMA}.primary_server.id"]),
        sa.ForeignKeyConstraint(["parent_id"], [f"{SCHEMA}.source_object.id"]),
        sa.ForeignKeyConstraint(["last_seen_run_id"], [f"{SCHEMA}.collection_run.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "primary_id", "kind", "source_key", name="uq_netbackup_source_object_key"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_netbackup_source_object_kind_present",
        "source_object",
        ["primary_id", "kind", "present"],
        schema=SCHEMA,
    )
    op.create_index("ix_netbackup_source_object_name", "source_object", ["name"], schema=SCHEMA)
    op.create_index(
        "ix_netbackup_source_object_parent_id", "source_object", ["parent_id"], schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_table("source_object", schema=SCHEMA)
