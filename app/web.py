"""Shared by every route file: templates, page rendering, flash messages, small formatters."""
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from . import auth, db, deadlines

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

STATUS_LABELS = {"shortlisted": "Shortlisted", "applied": "Applied", "interviewing": "Interviewing", "offer": "Offer",
                 "rejected": "Rejected", "withdrawn": "Withdrawn"}
CV_LABELS = {"draft": "Draft", "in_review": "In review", "approved": "Approved", "changes_requested": "Changes requested"}
MODE_LABELS = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "Onsite"}
SPONSOR_LABELS = {"sponsors": "Sponsors visas", "no_sponsorship": "No sponsorship", "unclear": "Sponsorship unclear"}


def today():
    return date.today()


def money(n):
    return f"£{int(n):,}"


def salary(job):
    lo, hi = job.salary_min, job.salary_max
    if not lo and not hi:
        return ""
    if lo and hi and lo != hi:
        return f"{money(lo)} to {money(hi)}"
    return money(lo or hi)


def dmy(value, year=False):
    """UK-style date: 5 Oct, or 5 Oct 2026."""
    if value is None:
        return ""
    text = f"{value.day} {value.strftime('%b')}"
    return f"{text} {value.year}" if year else text


def when(value):
    """A friendly 'today, 09:12' / 'yesterday' / '4 Oct' for a timestamp (stored in UTC)."""
    if value is None:
        return "never"
    days = (db.utcnow().date() - value.date()).days
    if days <= 0:
        return f"today, {value.strftime('%H:%M')}"
    return "yesterday" if days == 1 else dmy(value, year=value.year != date.today().year)


def added_by(entry, viewer):
    """'Added by you', 'Assigned by Dad', 'Added by you from a link'."""
    who = entry.added_by
    name = "you" if who is not None and who.id == viewer.id else (who.first_name if who else "someone who has left")
    if entry.source == "assigned":
        return f"Assigned by {name}"
    return f"Added by {name}" + (" from a link" if entry.source == "link" else " by hand" if entry.source == "manual" else "")


templates.env.globals.update(
    STATUS_LABELS=STATUS_LABELS, CV_LABELS=CV_LABELS, MODE_LABELS=MODE_LABELS, SPONSOR_LABELS=SPONSOR_LABELS,
    LISTING_LABELS=deadlines.LISTING_LABELS, STATUSES=db.STATUSES, salary=salary, added_by=added_by,
    chip_for=deadlines.chip, listing_status=deadlines.listing_status)
templates.env.filters.update(dmy=dmy, when=when, money=money)


def flash(request, message, kind="ok"):
    request.session["flash"] = [kind, message]


def page(request, name, person, active="", status_code=200, **ctx):
    """Render a template with everything the shell needs."""
    waiting = 0
    if person is not None and person.is_admin:  # the count beside "Review CVs" in the sidebar
        with db.SessionLocal() as s:
            waiting = s.query(db.TailoredCV).filter(db.TailoredCV.status == "in_review").count()
    ctx.pop("database", None)
    ctx.setdefault("viewing", None)
    has_session = "session" in request.scope  # missing only on the last-resort error page
    ctx.update(person=person, active=active, today=today(), waiting=waiting,
               csrf=auth.csrf_token(request) if has_session else "",
               flash=request.session.pop("flash", None) if has_session else None)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def redirect(url):
    return RedirectResponse(url, status_code=303)


def safe_next(url, default="/"):
    """Only ever redirect to a path on this site."""
    parts = urlparse(url or "")
    if url and url.startswith("/") and not url.startswith("//") and not parts.netloc and not parts.scheme:
        return url
    return default


def clean(text, limit):
    return (text or "").strip()[:limit]
