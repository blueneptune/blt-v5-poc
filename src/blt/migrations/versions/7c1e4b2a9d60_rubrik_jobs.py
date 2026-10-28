"""rubrik jobs, in a schema of their own

Revision ID: 7c1e4b2a9d60
Revises: 31250984815e
Create Date: 2026-10-28 15:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "7c1e4b2a9d60"
down_revision = "31250984815e"
branch_labels = None
depends_on = None

SCHEMA = "rubrik"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.create_table(
        "account",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index("ix_rubrik_account_name", "account", ["name"], unique=True, schema=SCHEMA)
    op.create_table(
        "collection_run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lookup_seconds", sa.BigInteger(), nullable=False),
        sa.Column("jobs_collected", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], [f"{SCHEMA}.account.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_rubrik_run_account_status_started",
        "collection_run",
        ["account_id", "status", "started_at"],
        schema=SCHEMA,
    )
    op.create_table(
        "job",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("series_key", sa.String(), nullable=False),
        sa.Column("fid", sa.String(), nullable=True),
        sa.Column("activity_series_id", sa.String(), nullable=True),
        sa.Column("cluster_id", sa.String(), nullable=True),
        sa.Column("cluster_name", sa.String(), nullable=True),
        sa.Column("object_id", sa.String(), nullable=True),
        sa.Column("object_name", sa.String(), nullable=True),
        sa.Column("object_type", sa.String(), nullable=True),
        sa.Column("location", sa.String(), nullable=True),
        sa.Column("activity_type", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("severity", sa.String(), nullable=True),
        sa.Column("progress", sa.String(), nullable=True),
        sa.Column("sla_domain_name", sa.String(), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_run_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], [f"{SCHEMA}.account.id"]),
        sa.ForeignKeyConstraint(["last_seen_run_id"], [f"{SCHEMA}.collection_run.id"]),
        sa.PrimaryKeyConstraint("account_id", "series_key"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_rubrik_job_account_active", "job", ["account_id", "is_active"], schema=SCHEMA
    )
    op.create_index(
        "ix_rubrik_job_account_updated", "job", ["account_id", "last_updated"], schema=SCHEMA
    )
    op.create_index(
        "ix_rubrik_job_account_start", "job", ["account_id", "start_time"], schema=SCHEMA
    )
    op.create_index("ix_rubrik_job_object_name", "job", ["object_name"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("job", schema=SCHEMA)
    op.drop_table("collection_run", schema=SCHEMA)
    op.drop_table("account", schema=SCHEMA)
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}")
