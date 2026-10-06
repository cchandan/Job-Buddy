"""One-off bulk copy of this laptop's jobs straight into the live (Supabase) database, bypassing the website.

    LIVE_DATABASE_URL='postgresql://...pooler.supabase.com:6543/postgres' python scripts/copy_jobs_to_live.py

Copies only jobs, and who each is for (matched by username, so the two databases' ids can differ). It never touches
accounts, CVs, Trackers or anything else, never deletes, and keeps a deadline the live site already has. Safe to run again.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app import db, ingest, push  # noqa: E402

CHUNK = 500


def main():
    url = os.getenv("LIVE_DATABASE_URL", "").strip()
    if not url:
        sys.exit("Set LIVE_DATABASE_URL to the Supabase pooled connection string for this one command.")
    if url.startswith(("postgres://", "postgresql://")):
        url = url.replace("postgres://", "postgresql+psycopg2://", 1).replace("postgresql://", "postgresql+psycopg2://", 1)
    live = sessionmaker(bind=create_engine(url, pool_pre_ping=True), expire_on_commit=False)()
    with db.SessionLocal() as local:
        names = {p.id: p.username for p in local.query(db.Person)}
        live_ids = {p.username: p.id for p in live.query(db.Person)}
        missing = sorted({n for n in names.values() if n not in live_ids and n != "admin"})
        if missing:
            print("Not on the live site yet (their matches will be skipped):", ", ".join(missing))
        total = local.query(db.Job).count()
        now, new, updated, done = db.utcnow(), 0, 0, 0
        jobs = local.query(db.Job).order_by(db.Job.id)
        for start in range(0, total, CHUNK):
            chunk = jobs.offset(start).limit(CHUNK).all()
            # one query for the whole chunk: the live rows land in the session, so there are no per-job lookups
            live.query(db.Job).filter(db.Job.id.in_([j.id for j in chunk])).all()
            for job in chunk:
                row = push.job_dict(job, names)
                row["matched"] = [live_ids[u] for u in row["matched"] if u in live_ids]
                outcome = ingest._upsert(live, row, {}, now)
                new, updated = new + (outcome == "new"), updated + (outcome == "updated")
            live.commit()  # hundreds of rows per round trip, not one
            done += len(chunk)
            print(f"  {done}/{total}", flush=True)
    print(f"Done: {new} new, {updated} updated on the live database.")


if __name__ == "__main__":
    main()
