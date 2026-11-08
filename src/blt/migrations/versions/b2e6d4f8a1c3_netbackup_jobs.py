"""netbackup jobs, in a schema of their own

NetBackup primaries, their job collection runs, and their jobs (the
activity monitor), as rubrik has for RSC accounts. Storage stays in
common.storage.

Revision ID: b2e6d4f8a1c3
Revises: 9a7d3e5f1b28
Create Date: 2026-11-08 18:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b2e6d4f8a1c3"
down_revision = "9a7d3e5f1b28"
branch_labels = None
depends_on = None

SCHEMA = "netbackup"


def _ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.create_table(
        "primary_server",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        _ts("created_at", nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["common.source.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_netbackup_primary_server_name", "primary_server", ["name"], unique=True, schema=SCHEMA
    )
    op.create_table(
        "collection_run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("primary_id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        _ts("started_at", nullable=False),
        _ts("finished_at"),
        sa.Column("lookup_seconds", sa.BigInteger(), nullable=False),
        sa.Column("jobs_collected", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["primary_id"], [f"{SCHEMA}.primary_server.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_netbackup_run_primary_status_started",
        "collection_run",
        ["primary_id", "status", "started_at"],
        schema=SCHEMA,
    )
    op.create_table(
        "job",
        sa.Column("primary_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("parent_job_id", sa.BigInteger(), nullable=True),
        sa.Column("job_type", sa.String(), nullable=True),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("policy_name", sa.String(), nullable=True),
        sa.Column("policy_type", sa.String(), nullable=True),
        sa.Column("schedule_name", sa.String(), nullable=True),
        sa.Column("schedule_type", sa.String(), nullable=True),
        sa.Column("client_name", sa.String(), nullable=True),
        sa.Column("media_server", sa.String(), nullable=True),
        sa.Column("storage_unit", sa.String(), nullable=True),
        sa.Column("source_media_server", sa.String(), nullable=True),
        sa.Column("source_storage_unit", sa.String(), nullable=True),
        sa.Column("backup_id", sa.String(), nullable=True),
        sa.Column("try_number", sa.Integer(), nullable=True),
        _ts("start_time"),
        _ts("end_time"),
        _ts("last_update_time"),
        sa.Column("kilobytes_transferred", sa.BigInteger(), nullable=True),
        sa.Column("kilobytes_data_transferred", sa.BigInteger(), nullable=True),
        sa.Column("kilobytes_to_transfer", sa.BigInteger(), nullable=True),
        sa.Column("number_of_files", sa.BigInteger(), nullable=True),
        sa.Column("percent_complete", sa.Integer(), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        _ts("first_seen_at", nullable=False),
        _ts("last_collected_at", nullable=False),
        sa.Column("last_seen_run_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["primary_id"], [f"{SCHEMA}.primary_server.id"]),
        sa.ForeignKeyConstraint(["last_seen_run_id"], [f"{SCHEMA}.collection_run.id"]),
        sa.PrimaryKeyConstraint("primary_id", "job_id"),
        schema=SCHEMA,
    )
    for name, columns in (
        ("ix_netbackup_job_primary_active", ["primary_id", "is_active"]),
        ("ix_netbackup_job_primary_updated", ["primary_id", "last_update_time"]),
        ("ix_netbackup_job_primary_start", ["primary_id", "start_time"]),
        ("ix_netbackup_job_primary_type_updated", ["primary_id", "job_type", "last_update_time"]),
        ("ix_netbackup_job_backup_id", ["backup_id"]),
        ("ix_netbackup_job_client_name", ["client_name"]),
    ):
        op.create_index(name, "job", columns, schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("job", schema=SCHEMA)
    op.drop_table("collection_run", schema=SCHEMA)
    op.drop_table("primary_server", schema=SCHEMA)
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}")
