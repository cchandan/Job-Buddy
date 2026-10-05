"""Job Buddy tables.

Works on an empty database and on a database left by CareerOS: the shared `jobs` table is kept and gains the
deadline and first/last-seen columns; the anonymous-visitor tables are dropped (they held 7-day demo data only).

Revision ID: 0001
Revises:
"""
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for old in ("visitors", "gemma_usage"):
        if old in tables:
            op.drop_table(old)

    if "jobs" in tables:
        have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("jobs")}
        for column in (sa.Column("deadline", sa.Date()), sa.Column("deadline_quote", sa.Text()),
                       sa.Column("first_seen", sa.DateTime()), sa.Column("last_seen", sa.DateTime())):
            if column.name not in have:
                op.add_column("jobs", column)
        # The jobs already there count as first seen, and last found, now.
        jobs = sa.table("jobs", sa.column("first_seen"), sa.column("last_seen"), sa.column("deadline_quote"))
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        op.execute(jobs.update().where(jobs.c.first_seen.is_(None)).values(first_seen=now, last_seen=now, deadline_quote=""))
    else:
        op.create_table(
            "jobs",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("title", sa.String(300)), sa.Column("company", sa.String(200)), sa.Column("location", sa.String(200)),
            sa.Column("description", sa.Text()), sa.Column("url", sa.Text()),
            sa.Column("salary_min", sa.Integer()), sa.Column("salary_max", sa.Integer()), sa.Column("date_posted", sa.String(30)),
            sa.Column("required_skills", sa.JSON()), sa.Column("optional_skills", sa.JSON()),
            sa.Column("sponsorship", sa.String(20)), sa.Column("sponsorship_quote", sa.Text()),
            sa.Column("work_mode", sa.String(20)), sa.Column("work_mode_quote", sa.Text()),
            sa.Column("seniority", sa.String(20)), sa.Column("industry", sa.String(80)), sa.Column("tag_source", sa.String(20)),
            sa.Column("deadline", sa.Date()), sa.Column("deadline_quote", sa.Text()),
            sa.Column("first_seen", sa.DateTime()), sa.Column("last_seen", sa.DateTime()),
        )

    op.create_table(
        "people",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(40), nullable=False, unique=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("password_hash", sa.String(200), nullable=False),
        sa.Column("session_version", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime()),
        sa.Column("created_at", sa.DateTime()), sa.Column("last_login", sa.DateTime()), sa.Column("last_board_visit", sa.DateTime()),
    )
    op.create_table(
        "staged_jobs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("tagged", sa.Boolean(), nullable=False), sa.Column("failed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_table(
        "tracker_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("person_id", sa.Integer(), sa.ForeignKey("people.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.String(32), sa.ForeignKey("jobs.id")),
        sa.Column("details", sa.JSON()),
        sa.Column("source", sa.String(12), nullable=False),
        sa.Column("added_by_id", sa.Integer(), sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(14), nullable=False),
        sa.Column("status_history", sa.JSON()),
        sa.Column("deadline_override", sa.Date()),
        sa.Column("notes", sa.Text()),
        sa.Column("apply_clicked_at", sa.DateTime()), sa.Column("seen_at", sa.DateTime()), sa.Column("created_at", sa.DateTime()),
        sa.UniqueConstraint("person_id", "job_id", name="uq_tracker_person_job"),
    )
    op.create_index("ix_tracker_entries_person_id", "tracker_entries", ["person_id"])
    op.create_table(
        "cvs",
        sa.Column("person_id", sa.Integer(), sa.ForeignKey("people.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("filename", sa.String(200)), sa.Column("data", sa.LargeBinary()),
        sa.Column("text", sa.Text(), nullable=False), sa.Column("uploaded_at", sa.DateTime()),
    )
    op.create_table(
        "tailored_cvs",
        sa.Column("entry_id", sa.Integer(), sa.ForeignKey("tracker_entries.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("review_comment", sa.Text()), sa.Column("reviewed_at", sa.DateTime()),
        sa.Column("review_seen", sa.Boolean(), nullable=False),
        sa.Column("sent_at", sa.DateTime()), sa.Column("updated_at", sa.DateTime()),
    )
    op.create_table(
        "sync_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("person_id", sa.Integer(), sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("at", sa.DateTime()),
        sa.Column("new", sa.Integer()), sa.Column("updated", sa.Integer()), sa.Column("unchanged", sa.Integer()), sa.Column("failed", sa.Integer()),
    )
    op.create_table(
        "search_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("keywords", sa.Text()), sa.Column("locations", sa.Text()), sa.Column("per_search", sa.Integer()),
    )
    op.create_table("ai_usage", sa.Column("day", sa.String(10), primary_key=True), sa.Column("calls", sa.Integer()))


def downgrade():
    for table in ("ai_usage", "search_settings", "sync_runs", "tailored_cvs", "cvs", "tracker_entries", "staged_jobs", "people"):
        op.drop_table(table)
