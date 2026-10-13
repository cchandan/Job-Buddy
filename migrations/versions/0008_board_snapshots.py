"""Saved copies (snapshots) of the Job Board, so it can be restored.

Revision ID: 0008
Revises: 0007
"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    if "board_snapshots" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "board_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("note", sa.String(300)),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("job_count", sa.Integer()),
        sa.Column("size_bytes", sa.Integer()),
        sa.Column("data", sa.LargeBinary(), nullable=False),
    )


def downgrade():
    op.drop_table("board_snapshots")
