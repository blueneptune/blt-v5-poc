"""rubrik objects: every object RSC knows about, from inventory runs

Revision ID: c3f9a1e5b7d2
Revises: b8e3c5d17a42
Create Date: 2026-10-30 16:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "c3f9a1e5b7d2"
down_revision = "b8e3c5d17a42"
branch_labels = None
depends_on = None

SCHEMA = "rubrik"


def _ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "object",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("object_key", sa.String(), nullable=False),
        sa.Column("fid", sa.String(), nullable=True),
        sa.Column("rsc_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("object_type", sa.String(), nullable=True),
        sa.Column("location", sa.String(), nullable=True),
        sa.Column("object_state", sa.String(), nullable=True),
        sa.Column("protection_status", sa.String(), nullable=True),
        sa.Column("compliance_status", sa.String(), nullable=True),
        sa.Column("archival_compliance_status", sa.String(), nullable=True),
        sa.Column("replication_compliance_status", sa.String(), nullable=True),
        sa.Column("sla_domain_id", sa.String(), nullable=True),
        sa.Column("sla_domain_name", sa.String(), nullable=True),
        sa.Column("cluster_id", sa.String(), nullable=True),
        sa.Column("cluster_name", sa.String(), nullable=True),
        sa.Column("org_id", sa.String(), nullable=True),
        _ts("protected_on"),
        _ts("last_snapshot"),
        _ts("latest_archival_snapshot"),
        _ts("latest_replication_snapshot"),
        sa.Column("awaiting_first_full", sa.Boolean(), nullable=True),
        sa.Column("total_snapshots", sa.Integer(), nullable=True),
        sa.Column("missed_snapshots", sa.Integer(), nullable=True),
        sa.Column("local_snapshots", sa.Integer(), nullable=True),
        sa.Column("archive_snapshots", sa.Integer(), nullable=True),
        sa.Column("replica_snapshots", sa.Integer(), nullable=True),
        sa.Column("logical_bytes", sa.BigInteger(), nullable=True),
        sa.Column("physical_bytes", sa.BigInteger(), nullable=True),
        sa.Column("local_storage", sa.BigInteger(), nullable=True),
        sa.Column("archive_storage", sa.BigInteger(), nullable=True),
        sa.Column("replica_storage", sa.BigInteger(), nullable=True),
        sa.Column("transferred_bytes", sa.BigInteger(), nullable=True),
        _ts("pull_time"),
        sa.Column("present", sa.Boolean(), nullable=False),
        _ts("first_seen_at", nullable=False),
        _ts("last_seen_at", nullable=False),
        sa.Column("last_seen_run_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], [f"{SCHEMA}.account.id"]),
        sa.ForeignKeyConstraint(["last_seen_run_id"], [f"{SCHEMA}.collection_run.id"]),
        sa.PrimaryKeyConstraint("account_id", "object_key"),
        schema=SCHEMA,
    )
    op.create_index("ix_rubrik_object_name", "object", ["name"], schema=SCHEMA)
    op.create_index(
        "ix_rubrik_object_account_type", "object", ["account_id", "object_type"], schema=SCHEMA
    )
    op.create_index(
        "ix_rubrik_object_account_protection",
        "object",
        ["account_id", "protection_status"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("object", schema=SCHEMA)
