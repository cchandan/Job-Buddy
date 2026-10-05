"""The Calendar: the deadlines of the jobs in the Tracker, and nothing else."""
import calendar as cal
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request

from .. import auth, summary, web
from .tracker import users_only

router = APIRouter()
DONE = ("applied", "interviewing", "offer")
HIDDEN = ("rejected", "withdrawn")


def build(database, owner, today, m=""):
    """Everything the Calendar page shows for one person."""
    try:
        first = date(int(m[:4]), int(m[5:7]), 1)
    except (ValueError, IndexError):
        first = today.replace(day=1)
    views = [v for v in summary.entries_for(database, owner, today) if v["entry"].status not in HIDDEN]
    dated = [v for v in views if v["deadline"]]
    by_day = {}
    for v in dated:
        by_day.setdefault(v["deadline"], []).append(v)

    weeks = [[{"day": d, "in_month": d.month == first.month, "jobs": by_day.get(d, [])} for d in week]
             for week in cal.Calendar(firstweekday=0).monthdatescalendar(first.year, first.month)]

    week_end = today + timedelta(days=6 - today.weekday())
    groups = {"This week": [], "Next week": [], "Later": []}
    for v in sorted(dated, key=lambda v: v["deadline"]):
        if v["deadline"] < today:
            continue
        key = "This week" if v["deadline"] <= week_end else "Next week" if v["deadline"] <= week_end + timedelta(days=7) else "Later"
        groups[key].append(v)
    prev_month = (first - timedelta(days=1)).replace(day=1)
    next_month = (first + timedelta(days=32)).replace(day=1)
    return {"weeks": weeks, "groups": groups, "month": first, "prev": prev_month.strftime("%Y-%m"),
            "next": next_month.strftime("%Y-%m"), "undated": [v for v in views if not v["deadline"]],
            "has_any": bool(views), "DONE": DONE}


@router.get("/calendar")
def calendar_page(request: Request, database=Depends(auth.get_db), person=Depends(users_only), m: str = ""):
    return web.page(request, "calendar.html", person, "calendar", owner=person, readonly=False, base="/calendar",
                    entry_base="/tracker", **build(database, person, web.today(), m))
