"""What Home and Admin home show. Pure code over the Tracker, CVs and jobs: these pages store nothing of
their own, so they can never disagree with the other pages.
"""
from datetime import datetime, timedelta

from . import auth, db, deadlines, ingest, runlog

SOON_DAYS = 7
ADMIN_URGENT_DAYS = 3


def entry_view(entry, today):
    """One Tracker entry with everything a template needs."""
    job = db.entry_job(entry)
    deadline = deadlines.effective_deadline(entry, job)
    return {
        "entry": entry, "job": job, "deadline": deadline, "chip": deadlines.chip(deadline, today),
        "days_left": (deadline - today).days if deadline else None,
        "passed": bool(deadline and deadline < today and entry.status == "shortlisted"),
    }


def entries_for(database, person, today):
    """A person's Tracker, soonest deadline first; no-deadline entries last; passed-and-not-applied at the bottom."""
    rows = [entry_view(e, today) for e in database.query(db.TrackerEntry).filter(db.TrackerEntry.person_id == person.id)]
    far = today + timedelta(days=36500)
    rows.sort(key=lambda v: (v["passed"], v["deadline"] or far, v["entry"].id))
    return rows


def status_counts(views):
    counts = {s: 0 for s in db.STATUSES}
    for v in views:
        counts[v["entry"].status] = counts.get(v["entry"].status, 0) + 1
    return counts


def user_home(database, person, today):
    views = entries_for(database, person, today)
    do_next = []
    for v in views:
        e = v["entry"]
        if e.status == "shortlisted" and v["days_left"] is not None and 0 <= v["days_left"] <= SOON_DAYS:
            do_next.append({"kind": "deadline", "view": v, "text": "Deadline coming up", "rank": (0, v["days_left"])})
        if e.source == "assigned" and e.seen_at is None and e.status == "shortlisted":
            who = e.added_by.first_name if e.added_by else "an admin"
            do_next.append({"kind": "assigned", "view": v, "text": f"New job from {who}", "rank": (1, 0)})
        if e.apply_clicked_at is not None and e.status == "shortlisted":
            do_next.append({"kind": "applied", "view": v, "text": "Did you apply?", "rank": (3, 0)})
    do_next.sort(key=lambda item: item["rank"])

    since = person.last_board_visit
    q = database.query(db.Job).filter(db.Job.binned_at.is_(None))
    new_jobs = q.filter(db.Job.first_seen > since).count() if since else q.count()
    open_jobs = [j for j in database.query(db.Job).filter(db.Job.binned_at.is_(None), db.Job.deadline >= today).order_by(db.Job.deadline).limit(3)]

    week = []
    for offset in range(7):
        day = today + timedelta(days=offset)
        week.append({"day": day, "count": sum(1 for v in views if v["deadline"] == day and v["entry"].status not in ("rejected", "withdrawn"))})

    deadlines_soon = sum(1 for item in do_next if item["kind"] == "deadline")
    assigned = sum(1 for item in do_next if item["kind"] == "assigned")
    has_cv = database.get(db.CV, person.id) is not None
    return {
        "views": views, "do_next": do_next[:5], "counts": status_counts(views), "new_jobs": new_jobs,
        "closing_soonest": open_jobs, "week": week, "has_cv": has_cv,
        "getting_started": not views and not has_cv,
        "line": _user_line(deadlines_soon, assigned, do_next),
    }


def _plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def _user_line(deadlines_soon, assigned, do_next):
    parts = []
    if deadlines_soon:
        parts.append(f"{_plural(deadlines_soon, 'deadline')} this week")
    if assigned:
        parts.append(f"{_plural(assigned, 'new job')} assigned to you")
    return (" and ".join(parts) + ".").capitalize() if parts else "Nothing urgent today."


def admin_home(database, today):
    users = database.query(db.Person).filter(db.Person.role == "user").order_by(db.Person.name).all()
    admins = database.query(db.Person).filter(db.Person.role == "admin").order_by(db.Person.name).all()
    cards, urgent = [], []
    for user in users:
        views = entries_for(database, user, today)
        upcoming = [v for v in views if v["deadline"] and v["deadline"] >= today
                    and v["entry"].status not in ("rejected", "withdrawn")]
        for v in views:
            if v["entry"].status == "shortlisted" and v["days_left"] is not None and 0 <= v["days_left"] <= ADMIN_URGENT_DAYS:
                urgent.append({"user": user, **v})
        cards.append({"user": user, "counts": status_counts(views), "total": len(views), "next": upcoming[:3]})
    age = ingest.board_age_days(database, today)
    parts = []
    if age is None:
        parts.append("the Job Board has not been synced yet")
    else:
        parts.append("the Job Board was updated today" if age == 0 else f"the Job Board is {_plural(age, 'day')} old")
    line = " and ".join(parts)
    return {
        "cards": cards, "admins": admins, "urgent": urgent, "signups": database.query(db.SignupRequest).order_by(db.SignupRequest.created_at).all(),
        "signups_open": auth.signups_open(database), "board_age": age, "last_sync": ingest.last_sync(database),
        "stale": age is None or age > 3, "notes": runlog.attention(database, today), "job_count": database.query(db.Job).count(),
        "line": line[:1].upper() + line[1:] + ".", "activity": activity(database),
    }


def activity(database, limit=8):
    """The last few events across the family, read from the rows that already record them."""
    events = []
    for run in database.query(db.SyncRun).order_by(db.SyncRun.at.desc()).limit(limit):
        who = run.person.first_name if run.person else "Someone"
        events.append((run.at, who, f"Synced jobs: {run.new} new, {run.updated} updated"))
    for entry in database.query(db.TrackerEntry).order_by(db.TrackerEntry.created_at.desc()).limit(limit * 3):
        title = db.entry_job(entry).title
        owner = entry.person.first_name if entry.person else "someone"
        if entry.source == "assigned" and entry.added_by:
            events.append((entry.created_at, entry.added_by.first_name, f"Assigned {title} to {owner}"))
        for change in (entry.status_history or [])[1:]:
            events.append((datetime.fromisoformat(change["at"]), owner, f"Marked {change['status'].capitalize()} · {title}"))
    events.sort(key=lambda e: e[0], reverse=True)
    return [{"at": at, "who": who, "what": what} for at, who, what in events[:limit]]
