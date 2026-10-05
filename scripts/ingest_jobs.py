"""A command-line backup to Admin > Jobs sync. Run on YOUR LAPTOP, never on the server.

    python scripts/ingest_jobs.py fetch                 # fetch and tag, using the saved search settings
    python scripts/ingest_jobs.py sync --as chandan     # publish what was fetched to the Job Board

Fetched jobs wait in the database until they are synced, and tagging is saved job by job, so a rate-limit
error or a closed laptop loses nothing: run `fetch` again to carry on.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, ingest  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["fetch", "sync"])
    ap.add_argument("--as", dest="username", help="the admin username to record against the sync")
    args = ap.parse_args()
    db.init_db()
    with db.SessionLocal() as s:
        if args.step == "fetch":
            if not ingest.jobspy_available():
                sys.exit("JobSpy is not installed. Run: pip install -r requirements-ingest.txt")
            settings = ingest.get_settings(s)
            keywords = ingest.split_list(settings.keywords)
            if not keywords:
                sys.exit("No keywords are set. Add some on Admin > Jobs sync first.")
            state = dict(ingest.STATE, running=True, stop=False)
            ingest.run(keywords, ingest.split_list(settings.locations), settings.per_search or 25, state)
            print(f"{state['phase']}. {state['message']}")
            summary = ingest.staged_summary(s)
            print(f"Waiting to sync: {summary['new']} new, {summary['updated']} updated, {summary['unchanged']} unchanged, "
                  f"{summary['failed']} failed, {summary['untagged']} untagged.")
        else:
            admin = s.query(db.Person).filter(db.Person.username == (args.username or "").lower(), db.Person.role == "admin").first()
            if admin is None:
                sys.exit("Give an admin's username with --as, so the sync is recorded against them.")
            run = ingest.sync(s, admin)
            print(f"Synced: {run.new} new, {run.updated} updated, {run.unchanged} unchanged, {run.failed} failed.")


if __name__ == "__main__":
    main()
