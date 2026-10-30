"""rubrik source_object: rubrik.object renamed and keyed as commvault.source_object is

Renamed in place, so objects already collected keep their first_seen_at.
object_key -> source_key, object_type -> kind, and a UUID id as the
primary key with (account_id, source_key) unique, as in Commvault's table.

Revision ID: d4a2b6c8e9f1
Revises: c3f9a1e5b7d2
Create Date: 2026-10-30 20:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "d4a2b6c8e9f1"
down_revision = "c3f9a1e5b7d2"
branch_labels = None
depends_on = None

SCHEMA = "rubrik"

# (old, new) - in upgrade order; downgrade runs them backwards.
_INDEXES = [
    ("ix_rubrik_object_name", "ix_rubrik_source_object_name"),
    ("ix_rubrik_object_account_type", "ix_rubrik_source_object_account_kind"),
    ("ix_rubrik_object_account_protection", "ix_rubrik_source_object_account_protection"),
]
_FOREIGN_KEYS = [
    ("object_account_id_fkey", "source_object_account_id_fkey"),
    ("object_last_seen_run_id_fkey", "source_object_last_seen_run_id_fkey"),
]


def upgrade() -> None:
    op.rename_table("object", "source_object", schema=SCHEMA)
    op.alter_column("source_object", "object_key", new_column_name="source_key", schema=SCHEMA)
    op.alter_column("source_object", "object_type", new_column_name="kind", schema=SCHEMA)
    for old, new in _INDEXES:
        op.execute(f"ALTER INDEX {SCHEMA}.{old} RENAME TO {new}")
    for old, new in _FOREIGN_KEYS:
        op.execute(f"ALTER TABLE {SCHEMA}.source_object RENAME CONSTRAINT {old} TO {new}")

    op.add_column("source_object", sa.Column("id", sa.Uuid(), nullable=True), schema=SCHEMA)
    op.execute(f"UPDATE {SCHEMA}.source_object SET id = gen_random_uuid()")
    op.alter_column("source_object", "id", nullable=False, schema=SCHEMA)
    op.drop_constraint("object_pkey", "source_object", type_="primary", schema=SCHEMA)
    op.create_primary_key("source_object_pkey", "source_object", ["id"], schema=SCHEMA)
    op.create_unique_constraint(
        "uq_rubrik_source_object_key",
        "source_object",
        ["account_id", "source_key"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_rubrik_source_object_key", "source_object", type_="unique", schema=SCHEMA
    )
    op.drop_constraint("source_object_pkey", "source_object", type_="primary", schema=SCHEMA)
    op.create_primary_key(
        "object_pkey", "source_object", ["account_id", "source_key"], schema=SCHEMA
    )
    op.drop_column("source_object", "id", schema=SCHEMA)

    for old, new in reversed(_FOREIGN_KEYS):
        op.execute(f"ALTER TABLE {SCHEMA}.source_object RENAME CONSTRAINT {new} TO {old}")
    for old, new in reversed(_INDEXES):
        op.execute(f"ALTER INDEX {SCHEMA}.{new} RENAME TO {old}")
    op.alter_column("source_object", "kind", new_column_name="object_type", schema=SCHEMA)
    op.alter_column("source_object", "source_key", new_column_name="object_key", schema=SCHEMA)
    op.rename_table("source_object", "object", schema=SCHEMA)
