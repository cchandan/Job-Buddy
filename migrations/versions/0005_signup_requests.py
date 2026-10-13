"""Public sign-up requests waiting for an admin.

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    if "signup_requests" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "signup_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("username", sa.String(40), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime()),
    )


def downgrade():
    op.drop_table("signup_requests")
