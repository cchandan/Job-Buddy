"""Richer job fields, liveness columns, fetch cursors and the run log.

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

JOB_COLUMNS = [
    sa.Column("apply_url", sa.Text()), sa.Column("is_remote", sa.Boolean()), sa.Column("job_type", sa.String(30)),
    sa.Column("salary_currency", sa.String(8)), sa.Column("sources", sa.JSON()), sa.Column("source_urls", sa.JSON()),
    sa.Column("canonical_key", sa.String(40)), sa.Column("content_hash", sa.String(40)),
    sa.Column("needs_ai", sa.Boolean()), sa.Column("matched", sa.JSON()),
    sa.Column("licensed_sponsor", sa.Boolean()), sa.Column("citizenship_required", sa.Boolean()),
    sa.Column("last_checked", sa.DateTime()), sa.Column("closed_at", sa.DateTime()), sa.Column("close_reason", sa.String(60)),
]


def upgrade():
    inspector = sa.inspect(op.get_bind())
    have = {c["name"] for c in inspector.get_columns("jobs")}
    for column in JOB_COLUMNS:
        if column.name not in have:
            op.add_column("jobs", column)
    op.create_index("ix_jobs_canonical_key", "jobs", ["canonical_key"])
    have_runs = {c["name"] for c in inspector.get_columns("sync_runs")}
    for name in ("seen", "closed"):
        if name not in have_runs:
            op.add_column("sync_runs", sa.Column(name, sa.Integer(), server_default="0"))
    op.create_table("search_profiles",
                    sa.Column("person_id", sa.Integer(), sa.ForeignKey("people.id", ondelete="CASCADE"), primary_key=True),
                    sa.Column("keywords", sa.Text()), sa.Column("locations", sa.Text()), sa.Column("avoid", sa.Text()),
                    sa.Column("distance", sa.Integer()), sa.Column("per_search", sa.Integer()),
                    sa.Column("remote_uk", sa.Boolean()), sa.Column("uk_wide", sa.Boolean()),
                    sa.Column("needs_sponsorship", sa.Boolean()), sa.Column("enabled", sa.Boolean()))
    op.create_table("fetch_state", sa.Column("key", sa.String(300), primary_key=True), sa.Column("last_ok", sa.DateTime()))
    op.create_table("run_logs", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("started", sa.DateTime()),
                    sa.Column("finished", sa.DateTime()), sa.Column("status", sa.String(12)), sa.Column("summary", sa.JSON()))
    op.create_table("run_events", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("run_id", sa.Integer(), sa.ForeignKey("run_logs.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("at", sa.DateTime()), sa.Column("level", sa.String(6)), sa.Column("stage", sa.String(12)),
                    sa.Column("source", sa.String(30)), sa.Column("message", sa.Text()), sa.Column("data", sa.JSON()))
    op.create_index("ix_run_events_run_id", "run_events", ["run_id"])


def downgrade():
    op.drop_table("run_events")
    op.drop_table("run_logs")
    op.drop_table("fetch_state")
    op.drop_table("search_profiles")
    for name in ("seen", "closed"):
        op.drop_column("sync_runs", name)
    op.drop_index("ix_jobs_canonical_key", "jobs")
    for column in JOB_COLUMNS:
        op.drop_column("jobs", column.name)
