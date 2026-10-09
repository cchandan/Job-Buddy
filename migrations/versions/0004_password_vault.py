"""Encrypted copy of admin-issued passwords, for the admin "View passwords" page.

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("people", sa.Column("password_enc", sa.Text()))


def downgrade():
    op.drop_column("people", "password_enc")
