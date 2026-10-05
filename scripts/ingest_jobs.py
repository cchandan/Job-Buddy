"""Refresh the Job Board from your laptop: fetch, tag only what is new, publish, check old jobs.

    python scripts/ingest_jobs.py refresh [--as chandan]

Normally you just double-click `Refresh Jobs.command`, which sets everything up and runs this. Progress is saved
as it goes, so stopping or a rate limit loses nothing: run it again to carry on.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, ingest, runlog, sources  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", nargs="?", default="refresh", choices=["refresh"])
    ap.add_argument("--as", dest="username", help="the admin username to record the refresh against (optional)")
    ap.add_argument("--no-checks", action="store_true", help="skip the old-job page checks")
    args = ap.parse_args()
    db.init_db()
    person_id = None
    with db.SessionLocal() as s:
        if args.username:
            admin = s.query(db.Person).filter(db.Person.username == args.username.lower(), db.Person.role == "admin").first()
            person_id = admin.id if admin else None
        if not sources.plan()[0]:
            sys.exit("No job source is available. Run 'Refresh Jobs.command' once, or: pip install -r requirements-ingest.txt")
        if not ingest._plan(ingest.profiles(s)):
            sys.exit("No job titles or keywords are set. Open Admin > Jobs sync, add some to someone's search, and run this again.")
    state = dict(ingest.STATE, running=True, stop=False)
    import threading
    worker = threading.Thread(target=ingest.refresh, args=(person_id, state, not args.no_checks))
    worker.start()
    last = ""
    while worker.is_alive():
        line = f"{state['phase']}… found {state['fetched']}" + (f", tagged {state['tagged']}/{state['total']}" if state["total"] else "")
        if line != last:
            print(line, flush=True)
            last = line
        time.sleep(2)
    worker.join()
    with db.SessionLocal() as s:
        run, events = runlog.recent(s, 1)[0]
        print()
        for e in events:
            print(f"[{e.stage}] {e.message}")
        print(f"\nFinished: {run.status}. {state['message']}")


if __name__ == "__main__":
    main()
