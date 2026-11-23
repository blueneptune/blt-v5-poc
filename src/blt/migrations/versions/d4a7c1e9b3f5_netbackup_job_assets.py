"""netbackup job assets: what each job worked on, not who ran it

No schema change. blt.common.mapping.netbackup_asset now names a
NetBackup job's asset from the workload's own name for it, a backup's
client, or a copy's image (its backup id) - an SLP copy's clientName is
the primary or media server running it. REPROJECT has blt-migrate
project the NetBackup jobs already held into common.job again, once.

Revision ID: d4a7c1e9b3f5
Revises: e3b8f1a6c2d9
Create Date: 2026-11-23 12:00:00
"""

revision = "d4a7c1e9b3f5"
down_revision = "e3b8f1a6c2d9"
branch_labels = None
depends_on = None

REPROJECT = ("netbackup",)


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
