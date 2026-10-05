"""Database tables. Reads DATABASE_URL.

Postgres in production, always: the shape of that database changes only through Alembic migrations.
SQLite (the default when DATABASE_URL is unset) is for tests and quick local work; its tables are created at startup.
"""
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import (JSON, Boolean, Column, Date, DateTime, ForeignKey, Integer, LargeBinary, String, Text,
                        UniqueConstraint, create_engine)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from . import skills

ROOT = Path(__file__).resolve().parent.parent
JOBS_FILE = ROOT / "data" / "jobs_tagged.json"  # seed data for tests and a fresh database

if not os.getenv("JOBBUDDY_NO_DOTENV"):
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

_url = os.getenv("DATABASE_URL") or f"sqlite:///{ROOT / 'jobbuddy.db'}"
if _url.startswith("postgres://"):  # some hosts still hand out the old spelling
    _url = _url.replace("postgres://", "postgresql+psycopg2://", 1)
elif _url.startswith("postgresql://"):
    _url = _url.replace("postgresql://", "postgresql+psycopg2://", 1)
IS_SQLITE = _url.startswith("sqlite")

engine = create_engine(
    _url,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if IS_SQLITE else {},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()

STATUSES = ["shortlisted", "applied", "interviewing", "offer", "rejected", "withdrawn"]
CV_STATUSES = ["draft", "in_review", "approved", "changes_requested"]
SOURCES = ["shortlisted", "assigned", "link", "manual"]


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Person(Base):
    """An account. Created only by an admin; the password is stored as a salted hash."""
    __tablename__ = "people"
    id = Column(Integer, primary_key=True)
    username = Column(String(40), unique=True, nullable=False)
    name = Column(String(80), nullable=False)
    role = Column(String(10), nullable=False, default="user")  # "user" or "admin"
    password_hash = Column(String(200), nullable=False)
    session_version = Column(Integer, nullable=False, default=1)  # raised to sign the person out everywhere
    failed_count = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime)
    created_at = Column(DateTime, default=utcnow)
    last_login = Column(DateTime)
    last_board_visit = Column(DateTime)

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def first_name(self):
        return (self.name or self.username).split()[0]

    @property
    def initial(self):
        return (self.name or self.username)[:1].upper()


JOB_FIELDS = ["id", "title", "company", "location", "description", "url", "salary_min", "salary_max",
              "date_posted", "required_skills", "optional_skills", "sponsorship", "sponsorship_quote",
              "work_mode", "work_mode_quote", "seniority", "industry", "tag_source", "deadline", "deadline_quote",
              "apply_url", "is_remote", "job_type", "salary_currency", "sources", "source_urls", "canonical_key",
              "content_hash", "needs_ai", "matched", "licensed_sponsor", "citizenship_required"]


class Job(Base):
    """The shared Job Board: the same rows for everyone. Written only by sync."""
    __tablename__ = "jobs"
    id = Column(String(32), primary_key=True)  # hash of the URL, used for de-duplication
    title = Column(String(300))
    company = Column(String(200))
    location = Column(String(200))
    description = Column(Text)
    url = Column(Text)
    salary_min = Column(Integer)
    salary_max = Column(Integer)
    date_posted = Column(String(30))
    required_skills = Column(JSON, default=list)
    optional_skills = Column(JSON, default=list)
    sponsorship = Column(String(20), default="unclear")
    sponsorship_quote = Column(Text, default="")
    work_mode = Column(String(20), default="unclear")
    work_mode_quote = Column(Text, default="")
    seniority = Column(String(20), default="junior")
    industry = Column(String(80), default="")
    tag_source = Column(String(20), default="")
    deadline = Column(Date)
    deadline_quote = Column(Text, default="")
    first_seen = Column(DateTime, default=utcnow)
    last_seen = Column(DateTime, default=utcnow)
    # Kept from the sources, no AI needed
    apply_url = Column(Text, default="")
    is_remote = Column(Boolean)
    job_type = Column(String(30), default="")
    salary_currency = Column(String(8), default="")
    sources = Column(JSON, default=list)        # which sites listed it
    source_urls = Column(JSON, default=list)    # one link per site
    canonical_key = Column(String(40), index=True)  # company + title + city: the same job on two sites shares it
    content_hash = Column(String(40), default="")   # changes when the advert changes: tells us what to re-tag
    needs_ai = Column(Boolean, default=False)       # tagged by keywords only; AI should look again when it can
    matched = Column(JSON, default=list)            # ids of the people whose searches found it: their "For you" view
    licensed_sponsor = Column(Boolean, default=False)       # the employer is on the Home Office sponsor register
    citizenship_required = Column(Boolean, default=False)   # the advert says British citizens / UK nationals / clearance only
    # Liveness
    last_checked = Column(DateTime)             # last time its page was checked directly
    closed_at = Column(DateTime)
    close_reason = Column(String(60), default="")


class StagedJob(Base):
    """Fetched jobs waiting for Sync. `data` has the same fields as a Job."""
    __tablename__ = "staged_jobs"
    id = Column(String(32), primary_key=True)
    data = Column(JSON, nullable=False)
    tagged = Column(Boolean, nullable=False, default=False)
    failed = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=utcnow)


class TrackerEntry(Base):
    """One job in one person's Tracker. Points at a Board job, or carries its own details (link / manual)."""
    __tablename__ = "tracker_entries"
    __table_args__ = (UniqueConstraint("person_id", "job_id", name="uq_tracker_person_job"),)
    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("people.id", ondelete="CASCADE"), nullable=False, index=True)
    job_id = Column(String(32), ForeignKey("jobs.id"))
    details = Column(JSON)                      # the job's own details when job_id is empty
    source = Column(String(12), nullable=False, default="shortlisted")
    added_by_id = Column(Integer, ForeignKey("people.id", ondelete="SET NULL"))
    status = Column(String(14), nullable=False, default="shortlisted")
    status_history = Column(JSON, default=list)  # [{"status", "at", "by"}]
    deadline_override = Column(Date)             # a hand-set deadline wins over the advert's
    notes = Column(Text, default="")
    apply_clicked_at = Column(DateTime)          # drives the "Did you apply?" prompt
    seen_at = Column(DateTime)                   # when the owner first saw an assigned job
    created_at = Column(DateTime, default=utcnow)

    job = relationship(Job)
    person = relationship(Person, foreign_keys=[person_id])
    added_by = relationship(Person, foreign_keys=[added_by_id])
    tailored = relationship("TailoredCV", uselist=False, back_populates="entry", cascade="all, delete-orphan")


class CV(Base):
    """A person's core CV: the base for every tailored CV. Lives only in the database."""
    __tablename__ = "cvs"
    person_id = Column(Integer, ForeignKey("people.id", ondelete="CASCADE"), primary_key=True)
    filename = Column(String(200), default="")
    data = Column(LargeBinary)  # the original PDF; empty when the text was pasted
    text = Column(Text, nullable=False)
    uploaded_at = Column(DateTime, default=utcnow)


class TailoredCV(Base):
    __tablename__ = "tailored_cvs"
    entry_id = Column(Integer, ForeignKey("tracker_entries.id", ondelete="CASCADE"), primary_key=True)
    text = Column(Text, nullable=False, default="")
    status = Column(String(20), nullable=False, default="draft")
    reviewer_id = Column(Integer, ForeignKey("people.id", ondelete="SET NULL"))
    review_comment = Column(Text, default="")
    reviewed_at = Column(DateTime)
    review_seen = Column(Boolean, nullable=False, default=True)  # False until the owner sees the outcome
    sent_at = Column(DateTime)
    updated_at = Column(DateTime, default=utcnow)

    entry = relationship(TrackerEntry, back_populates="tailored")
    reviewer = relationship(Person)


class SyncRun(Base):
    __tablename__ = "sync_runs"
    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("people.id", ondelete="SET NULL"))
    at = Column(DateTime, default=utcnow)
    new = Column(Integer, default=0)
    updated = Column(Integer, default=0)
    unchanged = Column(Integer, default=0)
    failed = Column(Integer, default=0)
    seen = Column(Integer, default=0)      # known jobs found again, nothing to change
    closed = Column(Integer, default=0)    # jobs confirmed closed by a page check

    person = relationship(Person)


class FetchState(Base):
    """When each (source, search) last succeeded, so the next fetch asks only for what is newer."""
    __tablename__ = "fetch_state"
    key = Column(String(300), primary_key=True)
    last_ok = Column(DateTime)


class RunLog(Base):
    """One refresh: its status and a summary. The events below tell the story."""
    __tablename__ = "run_logs"
    id = Column(Integer, primary_key=True)
    started = Column(DateTime, default=utcnow)
    finished = Column(DateTime)
    status = Column(String(12), default="running")  # running / complete / partial / failed
    summary = Column(JSON, default=dict)


class RunEvent(Base):
    """A line in a run's log. Never holds advert text or secrets."""
    __tablename__ = "run_events"
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("run_logs.id", ondelete="CASCADE"), index=True, nullable=False)
    at = Column(DateTime, default=utcnow)
    level = Column(String(6), default="info")   # info / warn / error
    stage = Column(String(12), default="")      # plan / fetch / tag / push / liveness
    source = Column(String(30), default="")
    message = Column(Text, default="")
    data = Column(JSON)


class SearchProfile(Base):
    """What one person's refresh looks for. Jobs found by it are tagged with this person's id (their "For you" view)."""
    __tablename__ = "search_profiles"
    person_id = Column(Integer, ForeignKey("people.id", ondelete="CASCADE"), primary_key=True)
    keywords = Column(Text, default="")
    locations = Column(Text, default="")
    avoid = Column(Text, default="")            # title words that rule a job out for this person
    distance = Column(Integer, default=25)      # miles around each location
    per_search = Column(Integer, default=100)
    remote_uk = Column(Boolean, default=False)  # also search UK-wide remote roles
    uk_wide = Column(Boolean, default=False)    # also search the big UK cities
    needs_sponsorship = Column(Boolean, default=False)  # rank sponsors first, hide citizenship-only jobs
    enabled = Column(Boolean, default=True)

    person = relationship(Person)


class AIUsage(Base):
    """One row per day: total AI calls made by the live app (the daily limit)."""
    __tablename__ = "ai_usage"
    day = Column(String(10), primary_key=True)
    calls = Column(Integer, default=0)


def entry_job(entry):
    """The job for a Tracker entry, in one shape, whether it came from the Board or was added by link or by hand."""
    if entry.job is not None:
        return entry.job
    d = dict(entry.details or {})
    deadline = d.get("deadline")
    return SimpleNamespace(
        id=None, title=d.get("title") or "Untitled job", company=d.get("company") or "", location=d.get("location") or "",
        description=d.get("description") or "", url=d.get("url") or "", salary_min=d.get("salary_min"),
        salary_max=d.get("salary_max"), sponsorship=d.get("sponsorship") or "unclear",
        sponsorship_quote=d.get("sponsorship_quote") or "", work_mode=d.get("work_mode") or "unclear",
        work_mode_quote=d.get("work_mode_quote") or "", seniority=d.get("seniority") or "", industry=d.get("industry") or "",
        required_skills=d.get("required_skills") or [], optional_skills=d.get("optional_skills") or [],
        deadline=date.fromisoformat(deadline) if deadline else None, deadline_quote=d.get("deadline_quote") or "",
        first_seen=entry.created_at, last_seen=entry.created_at)


def job_row(raw):
    """Clean a dict of job fields (from the seed file or from tagging) into Job column values."""
    row = {f: raw.get(f) for f in JOB_FIELDS}
    row["required_skills"] = skills.canonical_list(raw.get("required_skills"))
    row["optional_skills"] = skills.canonical_list(raw.get("optional_skills"))
    for f in ("salary_min", "salary_max"):
        row[f] = int(row[f]) if row[f] else None
    if isinstance(row["deadline"], str):
        row["deadline"] = date.fromisoformat(row["deadline"]) if row["deadline"] else None
    for f in ("sponsorship_quote", "work_mode_quote", "deadline_quote", "industry", "tag_source", "date_posted",
              "apply_url", "job_type", "salary_currency", "content_hash"):
        row[f] = row[f] or ""
    row["sources"], row["source_urls"] = list(raw.get("sources") or []), list(raw.get("source_urls") or [])
    row["needs_ai"] = bool(raw.get("needs_ai"))
    row["matched"] = sorted(set(raw.get("matched") or []))
    row["licensed_sponsor"], row["citizenship_required"] = bool(raw.get("licensed_sponsor")), bool(raw.get("citizenship_required"))
    if row["is_remote"] is not None:
        row["is_remote"] = bool(row["is_remote"])
    for f, default in (("sponsorship", "unclear"), ("work_mode", "unclear"), ("seniority", "junior")):
        row[f] = row[f] or default
    return row


def seed_jobs_if_empty(db):
    """A fresh database starts with the jobs in data/jobs_tagged.json. Returns how many were added."""
    if db.query(Job.id).first() is not None or not JOBS_FILE.exists():
        return 0
    from . import tagging  # here, not at the top: tagging needs this module
    rows = json.loads(JOBS_FILE.read_text())
    today = date.today()
    for raw in rows:
        row = job_row(raw)
        if row["deadline"] is None:  # the seed file predates deadlines: take any the advert plainly states
            row["deadline"], row["deadline_quote"] = tagging.keyword_deadline(row["description"], today)
        db.add(Job(**row))
    db.commit()
    return len(rows)


def _add_missing_sqlite_columns():
    """SQLite has no Alembic here: add any column a newer version of the app expects, so an old local file keeps working."""
    from sqlalchemy import inspect, text
    have = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not have.has_table(table.name):
                continue
            known = {c["name"] for c in have.get_columns(table.name)}
            for col in table.columns:
                if col.name not in known:
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(engine.dialect)}'))


def init_db():
    if IS_SQLITE:
        Base.metadata.create_all(engine)
        _add_missing_sqlite_columns()
    with SessionLocal() as db:
        seed_jobs_if_empty(db)
