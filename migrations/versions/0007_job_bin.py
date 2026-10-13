"""The bin: jobs hidden from the Job Board (expired, or found only by searches that were removed).

Revision ID: 0007
Revises: 0006
"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

COLUMNS = [sa.Column("binned_at", sa.DateTime()), sa.Column("bin_reason", sa.String(200)), sa.Column("bin_kept", sa.Boolean()),
           sa.Column("found_by", sa.JSON())]


def upgrade():
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("jobs")}
    for column in COLUMNS:
        if column.name not in have:
            op.add_column("jobs", column)


def downgrade():
    for column in COLUMNS:
        op.drop_column("jobs", column.name)
