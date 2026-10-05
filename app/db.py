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
    _url = _url.replace("postgres://", "postgresql://", 1)
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
              "work_mode", "work_mode_quote", "seniority", "industry", "tag_source", "deadline", "deadline_quote"]


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

    person = relationship(Person)


class SearchSettings(Base):
    """One row (id 1): what the daily fetch looks for."""
    __tablename__ = "search_settings"
    id = Column(Integer, primary_key=True)
    keywords = Column(Text, default="")
    locations = Column(Text, default="")
    per_search = Column(Integer, default=25)


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
    for f in ("sponsorship_quote", "work_mode_quote", "deadline_quote", "industry", "tag_source", "date_posted"):
        row[f] = row[f] or ""
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


def init_db():
    if IS_SQLITE:
        Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_jobs_if_empty(db)
