"""The login streak: the days each person opened Job Buddy.

Revision ID: 0011
Revises: 0010
"""
import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    if "daily_visits" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "daily_visits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("person_id", sa.Integer(), sa.ForeignKey("people.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.UniqueConstraint("person_id", "day", name="uq_visit_person_day"),
    )


def downgrade():
    op.drop_table("daily_visits")
