"""Triage: deal each person their best-matching Board jobs one at a time, and remember what a skip taught.

Nothing here is machine learning. A skip with a reason becomes a small rule ("place:london", "company:monzo", "level:senior"),
and jobs that fit a rule are ranked lower, never hidden. Forgetting a rule or bringing a job back undoes it.
"""
from datetime import datetime, time

from sqlalchemy.orm import defer

from . import db, deadlines

DECK_SIZE = 20
REASONS = [("senior", "Too senior"), ("location", "Wrong location"), ("field", "Wrong field"),
           ("company", "Not this company"), ("pay", "Pay too low"), ("other", "Not for me")]
REASON_LABELS = dict(REASONS)
LEVEL_WORDS = ("senior", "lead", "principal", "head of", "manager", "director", "staff")
PENALTY = {"level": 3, "place": 3, "field": 2, "company": 4, "pay": 2}


def _norm(text):
    return " ".join((text or "").lower().replace("-", " ").split())


def _town(job):
    place = _norm((job.location or "").split(",")[0])
    return "" if place in ("", "remote", "uk", "united kingdom", "hybrid") else place


def _level(job):
    if (job.seniority or "") in ("mid", "senior"):
        return job.seniority
    title = _norm(job.title)
    return next((w for w in LEVEL_WORDS if w in title), "senior")


def _field(job):
    title = _norm(job.title)
    for w in LEVEL_WORDS + ("graduate", "junior", "trainee", "apprentice", "entry level"):
        title = title.replace(w, " ")
    return _norm(title)


def rule_for(reason, job):
    """What skipping this job for this reason teaches, as 'kind:value', or '' when it teaches nothing."""
    value = {"senior": _level(job), "location": _town(job), "field": _field(job), "company": _norm(job.company),
             "pay": str(job.salary_max or job.salary_min or "")}.get(reason, "")
    kind = {"senior": "level", "location": "place", "field": "field", "company": "company", "pay": "pay"}.get(reason)
    return f"{kind}:{value}" if kind and value else ""


def rule_label(rule):
    kind, _, value = rule.partition(":")
    if kind == "pay":
        return f"Pay under £{int(value):,} lower" if value.isdigit() else "Low pay lower"
    return {"level": f"“{value}” roles lower", "place": f"{value.title()} lower", "field": f"“{value}” lower",
            "company": f"{value.title()} lower"}.get(kind, rule)


def _penalty(job, rules):
    total = 0
    title, place, company = _norm(job.title), _norm(job.location), _norm(job.company)
    for rule in rules:
        kind, _, value = rule.partition(":")
        if kind == "level" and (job.seniority == value or value in title):
            total += PENALTY[kind]
        elif kind == "place" and value and value in place:
            total += PENALTY[kind]
        elif kind == "field" and len(value) >= 4 and value in title:
            total += PENALTY[kind]
        elif kind == "company" and value and value == company:
            total += PENALTY[kind]
        elif kind == "pay" and value.isdigit() and (job.salary_max or job.salary_min) and (job.salary_max or job.salary_min) <= int(value):
            total += PENALTY[kind]
    return total


def _refuses(job):
    return job.sponsorship == "no_sponsorship" and "sponsor" in (job.sponsorship_quote or "").lower()


def score(job, person, profile, today, rules):
    s = 2 if person.id in (job.matched or []) else 0
    if profile is not None and profile.needs_sponsorship:
        if job.sponsorship == "sponsors":
            s += 2
        elif job.licensed_sponsor:
            s += 1
        elif _refuses(job):
            s -= 3
    if job.deadline is not None and 0 <= (job.deadline - today).days <= 7:
        s += 1
    return s - _penalty(job, rules)


def rules_for(database, person):
    """The distinct rules this person's skips have taught, oldest first."""
    seen = []
    for row in (database.query(db.JobDecision).filter(db.JobDecision.person_id == person.id, db.JobDecision.decision == "skip",
                                                       db.JobDecision.rule != "").order_by(db.JobDecision.id)):
        if row.rule not in seen:
            seen.append(row.rule)
    return seen


def deck(database, person, today, limit=DECK_SIZE):
    """The jobs to look at, best first. Closed, binned, tracked and already-decided jobs never appear, and neither do jobs
    a person who needs sponsorship cannot take."""
    profile = database.get(db.SearchProfile, person.id)
    has_profile = bool(profile and profile.enabled and (profile.keywords or "").strip())
    decided = {r[0] for r in database.query(db.JobDecision.job_id).filter(db.JobDecision.person_id == person.id)}
    tracked = {r[0] for r in database.query(db.TrackerEntry.job_id).filter(db.TrackerEntry.person_id == person.id,
                                                                            db.TrackerEntry.job_id.isnot(None))}
    rules = rules_for(database, person)
    rows = []
    query = (database.query(db.Job).options(defer(db.Job.description), defer(db.Job.source_urls))
             .filter(db.Job.binned_at.is_(None)))
    for job in query:
        if job.id in decided or job.id in tracked or deadlines.listing_status(job, today) == "closed":
            continue
        if has_profile and person.id not in (job.matched or []):
            continue
        if profile is not None and profile.needs_sponsorship and job.citizenship_required:
            continue
        rows.append((score(job, person, profile, today, rules), job))
    rows.sort(key=lambda r: (-r[0], -(r[1].first_seen or datetime.min).timestamp()))
    return [job for _, job in rows[:limit]]


def why(job, person, profile, today):
    """One honest line on why this job was picked, from what is actually recorded about it."""
    parts = []
    terms = (job.found_by or {}).get(str(person.id)) or (job.found_by or {}).get(person.id) or []
    if terms:
        parts.append("Matches " + " and ".join(f"“{t}”" for t in terms[:2]))
    elif person.id in (job.matched or []):
        parts.append("Found by your search")
    if (job.seniority or "") in ("graduate", "junior"):
        parts.append(f"{job.seniority.capitalize()} level")
    if profile is not None and profile.needs_sponsorship:
        if job.sponsorship == "sponsors":
            parts.append("Sponsors visas, and you need sponsorship")
        elif job.licensed_sponsor:
            parts.append("The employer is a licensed sponsor")
        elif job.sponsorship == "unclear":
            parts.append("Sponsorship is not stated, so check before applying")
    if job.deadline is not None and 0 <= (job.deadline - today).days <= 3:
        parts.append("The deadline is close")
    return ". ".join(parts) + "." if parts else "A recent job on the Board."


def record(database, person, job, decision, reason=""):
    """Save a swipe, replacing an earlier one on the same job. Returns the decision row."""
    reason = reason if reason in REASON_LABELS else ""
    row = (database.query(db.JobDecision).filter(db.JobDecision.person_id == person.id, db.JobDecision.job_id == job.id).first())
    if row is None:
        row = db.JobDecision(person_id=person.id, job_id=job.id)
        database.add(row)
    row.decision, row.reason, row.created_at = decision, reason if decision == "skip" else "", db.utcnow()
    row.rule = rule_for(reason, job) if decision == "skip" else ""
    database.commit()
    return row


def today_counts(database, person, today):
    start = datetime.combine(today, time.min)
    rows = database.query(db.JobDecision.decision).filter(db.JobDecision.person_id == person.id, db.JobDecision.created_at >= start).all()
    return {"shortlist": sum(1 for r in rows if r[0] == "shortlist"), "skip": sum(1 for r in rows if r[0] == "skip")}


def skipped(database, person):
    """[(decision, job)] newest first, for the Skipped list."""
    out = []
    for row in (database.query(db.JobDecision).filter(db.JobDecision.person_id == person.id, db.JobDecision.decision == "skip")
                .order_by(db.JobDecision.id.desc())):
        job = database.get(db.Job, row.job_id)
        if job is not None:
            out.append((row, job))
    return out
