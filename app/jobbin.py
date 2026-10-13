"""The bin: expired jobs, and jobs that no longer match anyone's searches, are hidden from the Job Board.

Nothing is deleted and no Tracker is ever touched: a binned job that someone is tracking stays in their Tracker exactly as it was.
Expired jobs are binned automatically after a scrape or a page check. Jobs found only by search terms that have since been removed
are binned when an admin presses the button on Admin > Bin, never on their own, and never if someone is tracking them.
"""
from sqlalchemy import func
from sqlalchemy.orm import defer

from . import db, deadlines


def _split(text):
    return [part.strip() for part in (text or "").replace("\n", ",").split(",") if part.strip()]


def merge_found_by(a, b):
    """Union of two {person id: [terms]} maps."""
    out = {k: list(v) for k, v in (a or {}).items()}
    for pid, terms in (b or {}).items():
        out[pid] = sorted(set(out.get(pid, [])) | set(terms))
    return out or None


def current_terms(database):
    """{person id: the search terms they have right now}, whether or not their search is switched on."""
    return {p.person_id: {t.lower() for t in _split(p.keywords)} for p in database.query(db.SearchProfile)}


def expired_reason(job, today):
    """Why a job counts as expired, or None. A page check or a passed closing date; "possibly closed" is only a guess, so it does not count."""
    if deadlines.listing_status(job, today) != "closed":
        return None
    if job.closed_at:
        return "Expired: " + (job.close_reason or "no longer listed")
    return f"Expired: the closing date ({deadlines.short(job.deadline)}) has passed"


def relevance_reason(job, terms):
    """Why a job no longer matches anyone's searches, or None. A job whose terms were never recorded (found before this
    feature) is never judged: it is simply not known."""
    found = job.found_by or {}
    if not found:
        return None
    for pid, searched in found.items():
        if any(t.lower() in terms.get(int(pid), ()) for t in searched):
            return None
    removed = sorted({t for searched in found.values() for t in searched})
    shown = ", ".join(f"“{t}”" for t in removed[:3]) + (f" and {len(removed) - 3} more" if len(removed) > 3 else "")
    return f"No longer relevant: found only by {shown}, which is no longer in anyone's search"


def tracked_ids(database):
    return {row[0] for row in database.query(db.TrackerEntry.job_id).filter(db.TrackerEntry.job_id.isnot(None)).distinct()}


def candidates(database, today):
    """What could go in the bin now: {"expired": [(job, reason)], "irrelevant": [(job, reason)], "unknown": how many were never judged}."""
    terms, tracked = current_terms(database), tracked_ids(database)
    out = {"expired": [], "irrelevant": [], "unknown": 0}
    query = (database.query(db.Job).options(defer(db.Job.description), defer(db.Job.source_urls))
             .filter(db.Job.binned_at.is_(None), db.Job.bin_kept.isnot(True)))
    for job in query:
        reason = expired_reason(job, today)
        if reason:
            out["expired"].append((job, reason))
        elif job.id in tracked:
            continue  # someone is tracking it: it is never binned for being off-topic
        else:
            reason = relevance_reason(job, terms)
            if reason:
                out["irrelevant"].append((job, reason))
            elif not job.found_by:
                out["unknown"] += 1
    return out


MANUAL_PREFIX = "Binned by hand"  # an advert an admin binned themselves: only an admin takes it out again


def is_manual(job):
    return (job.bin_reason or "").startswith(MANUAL_PREFIX)


def bin_by_hand(database, job, by, reason=""):
    """An admin bins one advert. It stays binned even if a scrape finds the advert again, until an admin restores it.
    Anyone tracking it keeps it in their Tracker."""
    note = (reason or "").strip()[:120]
    job.binned_at = db.utcnow()
    job.bin_reason = f"{MANUAL_PREFIX} by {by.first_name}" + (f": {note}" if note else "")
    job.bin_kept = False
    database.commit()


def bin_jobs(database, pairs, now=None):
    now = now or db.utcnow()
    for job, reason in pairs:
        job.binned_at, job.bin_reason = now, reason[:200]
    database.commit()
    return len(pairs)


def sweep_expired(database, today=None):
    """Bin every expired job. Run after a scrape or a page check. Returns how many."""
    from datetime import date
    return bin_jobs(database, candidates(database, today or date.today())["expired"])


def restore(database, ids):
    """Take jobs out of the bin. They are marked kept, so a later tidy-up leaves them alone, and an expired one is reopened."""
    n = 0
    for job in database.query(db.Job).filter(db.Job.id.in_(list(ids))):
        if job.binned_at is None:
            continue
        job.binned_at, job.bin_reason, job.bin_kept = None, "", True
        job.closed_at, job.close_reason = None, ""
        n += 1
    database.commit()
    return n


def count(database):
    return database.query(func.count(db.Job.id)).filter(db.Job.binned_at.isnot(None)).scalar()
