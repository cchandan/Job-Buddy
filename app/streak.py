"""The login streak: how many days in a row someone has opened Job Buddy, and this week at a glance.

A day counts when the person opened the app while signed in. The week runs Monday to Sunday in UK time. Missing a day just starts
a fresh run; nothing is taken away and the wording stays kind.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError

from . import db

LOOKBACK_DAYS = 120  # far enough for any run that matters, and keeps the query small


def today():
    """Today in UK time. Read through `web` so tests (and anything else) can fix the date in one place."""
    from . import web
    return web.today()


def record(database, person, day):
    """Note that this person was here on `day`. Safe to call again."""
    exists = database.query(db.DailyVisit.id).filter(db.DailyVisit.person_id == person.id, db.DailyVisit.day == day).first()
    if exists is None:
        database.add(db.DailyVisit(person_id=person.id, day=day))
        try:
            database.commit()
        except IntegrityError:  # two requests at once: the other one wrote it
            database.rollback()


def _run_ending(days, end):
    n, d = 0, end
    while d in days:
        n, d = n + 1, d - timedelta(days=1)
    return n, d


def build(database, person, day):
    """Everything the Home pill shows: the run, this week's seven days, and the words."""
    since = day - timedelta(days=LOOKBACK_DAYS)
    visited = {r[0] for r in database.query(db.DailyVisit.day).filter(db.DailyVisit.person_id == person.id, db.DailyVisit.day >= since)}
    n, before = _run_ending(visited, day if day in visited else day - timedelta(days=1))
    previous = 0
    if n:  # the run before the gap, to say "last run: 6 days"
        gap = before
        while gap not in visited and gap > since:
            gap -= timedelta(days=1)
        previous, _ = _run_ending(visited, gap)
    monday = day - timedelta(days=day.weekday())
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    week = []
    for i in range(7):
        d = monday + timedelta(days=i)
        kind = "v" if d in visited else ("m" if d < day else "f")
        week.append({"kind": kind, "today": d == day, "name": names[i],
                     "label": names[i] + ": " + {"v": "logged in", "m": "no login", "f": "still to come"}[kind] + (" (today)" if d == day else "")})
    full = all(w["kind"] == "v" for w in week)
    fresh = n == 1 and previous >= 2
    return {"n": n, "week": week, "full": full, "fresh": fresh, "previous": previous,
            "headline": "Login streak: fresh start" if fresh else f"{n}-day login streak",
            "nudge": "" if full else (f"Last run: {previous} days" if fresh else f"Log in tomorrow for {n + 1} days")}
