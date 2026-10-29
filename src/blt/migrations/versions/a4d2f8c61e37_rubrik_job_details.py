"""rubrik job details: failure reason and cause, last message, sizes,
attempt, on-demand, progress as a number; index for the activity type filter

Revision ID: a4d2f8c61e37
Revises: 7c1e4b2a9d60
Create Date: 2026-10-29 12:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "a4d2f8c61e37"
down_revision = "7c1e4b2a9d60"
branch_labels = None
depends_on = None

SCHEMA = "rubrik"

COLUMNS = [
    ("percent_complete", sa.Float()),
    ("last_activity_message", sa.String()),
    ("failure_reason", sa.String()),
    ("cause_error_code", sa.String()),
    ("cause_error_message", sa.String()),
    ("cause_error_reason", sa.String()),
    ("cause_error_remedy", sa.String()),
    ("attempt_number", sa.Integer()),
    ("is_on_demand", sa.Boolean()),
    ("data_transferred", sa.BigInteger()),
    ("logical_size", sa.BigInteger()),
    ("effective_throughput", sa.BigInteger()),
]


def upgrade() -> None:
    for name, type_ in COLUMNS:
        op.add_column("job", sa.Column(name, type_, nullable=True), schema=SCHEMA)
    op.create_index(
        "ix_rubrik_job_account_type_updated",
        "job",
        ["account_id", "activity_type", "last_updated"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_rubrik_job_account_type_updated", table_name="job", schema=SCHEMA)
    for name, _ in reversed(COLUMNS):
        op.drop_column("job", name, schema=SCHEMA)
