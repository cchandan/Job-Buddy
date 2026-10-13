"""Refreshers: always-on computers that refresh the Job Board and report to the website.

Revision ID: 0009
Revises: 0008
"""
import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    if "refreshers" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "refreshers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("last_seen", sa.DateTime()),
        sa.Column("version", sa.String(20)),
        sa.Column("state", sa.String(12)),
        sa.Column("status", sa.JSON()),
        sa.Column("pending_command", sa.String(12)),
    )


def downgrade():
    op.drop_table("refreshers")
