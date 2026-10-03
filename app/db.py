"""Database tables. Reads DATABASE_URL (SQLite if not set).

Jobs come from data/jobs_tagged.json and are loaded at startup when the jobs table is empty.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import JSON, Column, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from . import skills

ROOT = Path(__file__).resolve().parent.parent
JOBS_FILE = ROOT / "data" / "jobs_tagged.json"
VISITOR_DAYS = 7

if not os.getenv("CAREEROS_NO_DOTENV"):
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

_url = os.getenv("DATABASE_URL") or f"sqlite:///{ROOT / 'careeros.db'}"
if _url.startswith("postgres://"):  # some hosts still hand out the old spelling
    _url = _url.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    _url,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if _url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Job(Base):
    """Shared by every visitor."""
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


class Visitor(Base):
    """One row per anonymous session. Never holds the raw CV text or pasted job descriptions."""
    __tablename__ = "visitors"
    session_id = Column(String(64), primary_key=True)
    created_at = Column(DateTime, default=utcnow)
    skills = Column(JSON, default=list)
    level = Column(String(20), default="graduate")
    domains = Column(JSON, default=list)
    target_roles = Column(JSON, default=list)
    summary = Column(Text, default="")
    profile_source = Column(String(20), default="")  # "gemma" or "basic"
    preferences = Column(JSON, default=dict)
    dream_jobs = Column(JSON, default=list)           # [{title, company, url, skills}]
    analysis = Column(JSON, default=None)             # gap explanations + projects (Gemma call B)
    project_status = Column(JSON, default=dict)       # {project title: interested/started/completed}
    gemma_calls = Column(Integer, default=0)


class GemmaUsage(Base):
    """One row per day: total visitor-triggered Gemma calls (the overall limit)."""
    __tablename__ = "gemma_usage"
    day = Column(String(10), primary_key=True)
    calls = Column(Integer, default=0)


JOB_FIELDS = ["id", "title", "company", "location", "description", "url", "salary_min", "salary_max",
              "date_posted", "required_skills", "optional_skills", "sponsorship", "sponsorship_quote",
              "work_mode", "work_mode_quote", "seniority", "industry", "tag_source"]


def job_to_dict(job):
    return {f: getattr(job, f) for f in JOB_FIELDS}


def load_jobs_file(db):
    """Fill the jobs table from the committed snapshot. Returns how many jobs were added."""
    if not JOBS_FILE.exists():
        return 0
    added = 0
    for raw in json.loads(JOBS_FILE.read_text()):
        if db.get(Job, raw["id"]):
            continue
        row = {f: raw.get(f) for f in JOB_FIELDS}
        row["required_skills"] = skills.canonical_list(raw.get("required_skills"))
        row["optional_skills"] = skills.canonical_list(raw.get("optional_skills"))
        for f in ("salary_min", "salary_max"):
            row[f] = int(row[f]) if row[f] else None
        db.add(Job(**row))
        added += 1
    db.commit()
    return added


def remove_old_visitors(db):
    cutoff = utcnow() - timedelta(days=VISITOR_DAYS)
    db.query(Visitor).filter(Visitor.created_at < cutoff).delete()
    db.commit()


def init_db():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if db.query(Job).count() == 0:
            load_jobs_file(db)
        remove_old_visitors(db)
