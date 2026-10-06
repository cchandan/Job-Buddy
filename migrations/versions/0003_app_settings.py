"""Admin settings that survive restarts.

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("app_settings", sa.Column("key", sa.String(60), primary_key=True), sa.Column("value", sa.Text()))


def downgrade():
    op.drop_table("app_settings")
