"""Triage: each swipe a person makes on a Board job, and what a skip taught.

Revision ID: 0010
Revises: 0009
"""
import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    if "job_decisions" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "job_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("person_id", sa.Integer(), sa.ForeignKey("people.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("job_id", sa.String(32), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("decision", sa.String(10), nullable=False),
        sa.Column("reason", sa.String(12), server_default=""),
        sa.Column("rule", sa.String(200), server_default=""),
        sa.Column("created_at", sa.DateTime()),
        sa.UniqueConstraint("person_id", "job_id", name="uq_decision_person_job"),
    )


def downgrade():
    op.drop_table("job_decisions")
