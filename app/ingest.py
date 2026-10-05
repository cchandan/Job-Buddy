"""Fetch (JobSpy) -> tag -> stage -> sync.

Fetching only works where JobSpy is installed: an admin's laptop, never the server. Fetched jobs wait in
`staged_jobs`; Sync copies them into the shared `jobs` table.

Sync writes ONLY jobs, sync_runs and staged_jobs. It never deletes a job and never touches a Tracker entry,
a status, a hand-set deadline or a CV.
"""
import hashlib
import importlib.util
import os
import threading
import time
from datetime import date

from . import ai, db, tagging

COUNTRY = os.getenv("JOBSPY_COUNTRY", "UK")
COMPARE = ["title", "company", "location", "description", "salary_min", "salary_max", "sponsorship", "work_mode",
           "seniority", "deadline"]

# One fetch at a time, in a background thread of the admin's local app. Progress survives a stop: every job is
# saved as it is fetched and as it is tagged, so pressing the button again carries on.
STATE = {"running": False, "stop": False, "phase": "", "fetched": 0, "tagged": 0, "total": 0, "message": ""}
_lock = threading.Lock()


def jobspy_available():
    return importlib.util.find_spec("jobspy") is not None


def job_id(url):
    return hashlib.sha1(url.strip().lower().encode()).hexdigest()[:16]


def split_list(text):
    return [part.strip() for part in (text or "").replace("\n", ",").split(",") if part.strip()]


def get_settings(database):
    settings = database.get(db.SearchSettings, 1)
    if settings is None:
        settings = db.SearchSettings(id=1, keywords="", locations="United Kingdom", per_search=25)
        database.add(settings)
        database.commit()
    return settings


def _num(v):
    try:
        v = float(v)
        return None if v != v else v  # NaN -> None
    except (TypeError, ValueError):
        return None


def _scrape(term, location, per_search):
    """One JobSpy search -> list of raw job dicts. Tests replace this function."""
    from jobspy import scrape_jobs
    df = scrape_jobs(site_name=["indeed"], search_term=term, location=location, country_indeed=COUNTRY,
                     results_wanted=per_search, hours_old=24 * 30)
    out = []
    for row in df.to_dict("records"):
        url, desc = row.get("job_url") or "", row.get("description")
        if not url or not isinstance(desc, str) or len(desc) < 200:
            continue
        out.append({
            "id": job_id(url), "title": str(row.get("title") or "").strip(), "company": str(row.get("company") or "").strip(),
            "location": str(row.get("location") or "").strip(), "description": desc, "url": url,
            "salary_min": _num(row.get("min_amount")), "salary_max": _num(row.get("max_amount")),
            "salary_interval": str(row.get("interval") or ""), "date_posted": str(row.get("date_posted") or ""),
        })
    return out


def fetch(keywords, locations, per_search, state=STATE):
    """Run every keyword x location search and stage what comes back."""
    state.update(phase="Fetching", message="")
    for term in keywords:
        for location in locations or ["United Kingdom"]:
            if state["stop"]:
                return
            try:
                rows = _scrape(term, location, per_search)
            except Exception as e:  # a blocked search should not lose the others
                state["message"] = f"One search failed ({type(e).__name__}); carrying on."
                continue
            with db.SessionLocal() as s:
                seen = set()
                for raw in rows:
                    if raw["id"] in seen or s.get(db.StagedJob, raw["id"]) is not None:
                        continue
                    seen.add(raw["id"])
                    s.add(db.StagedJob(id=raw["id"], data=raw, tagged=False))
                s.commit()
                state["fetched"] = s.query(db.StagedJob).count()


def tag_staged(state=STATE):
    """Tag every staged job that is not tagged yet, saving after each one."""
    state["phase"] = "Tagging"
    use_ai = ai.available()
    with db.SessionLocal() as s:
        state["total"] = s.query(db.StagedJob).count()
        state["tagged"] = s.query(db.StagedJob).filter(db.StagedJob.tagged.is_(True)).count()
        todo = [row.id for row in s.query(db.StagedJob).filter(db.StagedJob.tagged.is_(False))]
    for staged_id in todo:
        if state["stop"]:
            return
        with db.SessionLocal() as s:
            staged = s.get(db.StagedJob, staged_id)
            if staged is None:
                continue
            try:
                tags = None
                for attempt in range(3):  # a rate limit usually clears within a minute
                    try:
                        tags = tagging.tag_job(staged.data, use_ai=use_ai)
                        break
                    except ai.AIUnavailable:
                        if attempt == 2:
                            use_ai = False
                            state["message"] = "AI is not responding; using keyword tags for the rest."
                        else:
                            time.sleep(20)
                if tags is None:
                    tags = tagging.tag_job(staged.data, use_ai=False)
                staged.data = {**staged.data, **tags}
                staged.tagged, staged.failed = True, False
            except Exception:
                staged.failed = True
            s.commit()
        state["tagged"] += 1


def run(keywords, locations, per_search, state=STATE):
    try:
        fetch(keywords, locations, per_search, state)
        tag_staged(state)
        state["phase"] = "Stopped" if state["stop"] else "Done"
    except Exception as e:
        state.update(phase="Stopped", message=f"Something went wrong ({type(e).__name__}). Press Fetch and tag to carry on.")
    finally:
        state["running"] = False


def start(keywords, locations, per_search):
    """Start a fetch-and-tag run in the background. Returns False if one is already running."""
    with _lock:
        if STATE["running"]:
            return False
        STATE.update(running=True, stop=False, phase="Starting", fetched=0, tagged=0, total=0, message="")
    threading.Thread(target=run, args=(keywords, locations, per_search), daemon=True).start()
    return True


def stop():
    STATE["stop"] = True


def _same(job, row):
    return all((getattr(job, f) or None) == (row[f] or None) for f in COMPARE)


def staged_summary(database):
    """What Sync would do: counts, plus a sample of the new jobs."""
    out = {"new": 0, "updated": 0, "unchanged": 0, "failed": 0, "untagged": 0, "total": 0, "sample": []}
    for staged in database.query(db.StagedJob):
        out["total"] += 1
        if staged.failed:
            out["failed"] += 1
        elif not staged.tagged:
            out["untagged"] += 1
        else:
            row = db.job_row(staged.data)
            job = database.get(db.Job, staged.id)
            if job is None:
                out["new"] += 1
                if len(out["sample"]) < 8:
                    out["sample"].append(row)
            elif _same(job, _keep_deadline(job, row)):
                out["unchanged"] += 1
            else:
                out["updated"] += 1
    return out


def _keep_deadline(job, row):
    """A re-fetch that no longer finds the deadline does not erase one we already know."""
    if row["deadline"] is None and job.deadline is not None:
        row["deadline"], row["deadline_quote"] = job.deadline, job.deadline_quote
    return row


def sync(database, admin):
    """Publish staged jobs to the shared Job Board. Returns the SyncRun."""
    run_row = db.SyncRun(person_id=admin.id if admin else None, new=0, updated=0, unchanged=0, failed=0)
    now = db.utcnow()
    for staged in database.query(db.StagedJob).all():
        if staged.failed or not staged.tagged:
            run_row.failed += 1 if staged.failed else 0
            if staged.failed:
                database.delete(staged)
            continue
        row = db.job_row(staged.data)
        job = database.get(db.Job, staged.id)
        if job is None:
            database.add(db.Job(**row, first_seen=now, last_seen=now))
            run_row.new += 1
        else:
            row = _keep_deadline(job, row)
            if _same(job, row):
                run_row.unchanged += 1
            else:
                for field, value in row.items():
                    setattr(job, field, value)
                run_row.updated += 1
            job.last_seen = now
        database.delete(staged)
    database.add(run_row)
    database.commit()
    return run_row


def last_sync(database):
    return database.query(db.SyncRun).order_by(db.SyncRun.at.desc()).first()


def board_age_days(database, today=None):
    last = last_sync(database)
    return None if last is None else ((today or date.today()) - last.at.date()).days
