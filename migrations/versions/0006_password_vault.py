"""Encrypted copy of admin-issued passwords, for the admin "View passwords" page.

Revision ID: 0006
Revises: 0005
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("people", sa.Column("password_enc", sa.Text()))


def downgrade():
    op.drop_column("people", "password_enc")
