"""Putting jobs into a Tracker and changing them. Used by the Tracker routes and by admin "assign".

Three routes in: shortlisted from the Board, assigned by an admin, added by hand.
"""
import re
from datetime import date

from . import db, tagging

MAX_ENTRIES = 300


class TrackerFull(Exception):
    pass


def _check_room(database, person):
    if database.query(db.TrackerEntry).filter(db.TrackerEntry.person_id == person.id).count() >= MAX_ENTRIES:
        raise TrackerFull()


def _new_entry(person, added_by, source, **fields):
    by_other = added_by.id != person.id
    now = db.utcnow()
    return db.TrackerEntry(
        person_id=person.id, added_by_id=added_by.id, source="assigned" if by_other else source, status="shortlisted",
        status_history=[{"status": "shortlisted", "at": now.isoformat(timespec="seconds"), "by": added_by.id}],
        seen_at=None if by_other else now, created_at=now, **fields)


def add_from_board(database, person, job, added_by):
    """Shortlist (or assign) a Board job. Doing it twice does nothing. Returns (entry, created)."""
    existing = (database.query(db.TrackerEntry)
                .filter(db.TrackerEntry.person_id == person.id, db.TrackerEntry.job_id == job.id).first())
    if existing is not None:
        return existing, False
    _check_room(database, person)
    entry = _new_entry(person, added_by, "shortlisted", job_id=job.id)
    database.add(entry)
    database.commit()
    return entry, True


def prefill(text, today=None):
    """Fill the add-by-hand boxes from a job description's text. Plain code, no AI: whatever is not found is left blank."""
    lines = [l.strip(" \t•*-#") for l in text.splitlines() if l.strip()]

    def labelled(*names):
        for l in lines:
            m = re.match(rf"(?:{'|'.join(names)})\s*[:\-–]\s*(.+)", l, re.I)
            if m:
                return m.group(1).strip()[:200]
        return ""

    first = next((l for l in lines if 3 <= len(l) <= 120 and not re.match(r"job (description|spec|profile)", l, re.I)), "")
    about = re.search(r"\b(?:About|Join)\s+([A-Z][\w&.' -]{1,40}?)(?:[\n.,:!]|\s+(?:is|are|we|as)\b)", text)
    url = re.search(r"https?://[^\s)>\]]+", text)
    lo, hi = tagging.salary_range({"description": text})
    deadline = tagging.keyword_deadline(text, today or date.today())[0]
    d = {"title": labelled("job title", "position", "role", "vacancy") or first,
         "company": labelled("company", "employer", "organisation", "organization") or (about.group(1).strip() if about else ""),
         "location": labelled("location", "based in", "work location", "office"), "url": url.group(0).rstrip(".,") if url else "",
         "salary_min": lo, "salary_max": hi, "work_mode": tagging.keyword_work_mode(text)[0],
         "sponsorship": tagging.keyword_sponsorship(text)[0], "description": text[:20000]}
    return d, deadline.isoformat() if deadline else ""


def manual_details(form):
    """Clean the add-by-hand form into the same fields a Board job has. Returns (details, deadline)."""
    def text(key, limit):
        return str(form.get(key) or "").strip()[:limit]

    def number(key):
        digits = "".join(c for c in str(form.get(key) or "") if c.isdigit())
        return int(digits) if digits and 0 < int(digits) < 10_000_000 else None

    lo, hi = number("salary_min"), number("salary_max")
    work_mode = text("work_mode", 20) if text("work_mode", 20) in tagging.WORK_MODES else "unclear"
    sponsorship = text("sponsorship", 20) if text("sponsorship", 20) in tagging.SPONSORSHIP else "unclear"
    details = {
        "title": text("title", 300), "company": text("company", 200), "url": text("url", 2000),
        "location": text("location", 200), "description": text("description", 20000),
        "salary_min": lo or hi, "salary_max": hi or lo, "work_mode": work_mode, "sponsorship": sponsorship,
        "sponsorship_quote": "", "work_mode_quote": "", "seniority": "", "industry": "",
        "required_skills": [], "optional_skills": [], "deadline": None, "deadline_quote": "",
    }
    if details["url"] and not details["url"].lower().startswith(("http://", "https://")):
        details["url"] = ""
    try:
        deadline = date.fromisoformat(text("deadline", 10)) if text("deadline", 10) else None
    except ValueError:
        deadline = None
    return details, deadline


def add_manual(database, person, form, added_by):
    """Add a job by hand. Raises ValueError when title or company is missing."""
    details, deadline = manual_details(form)
    if not details["title"] or not details["company"]:
        raise ValueError("Please give at least a job title and a company.")
    _check_room(database, person)
    entry = _new_entry(person, added_by, "manual", details=details, deadline_override=deadline,
                       notes=str(form.get("notes") or "").strip()[:4000])
    database.add(entry)
    database.commit()
    return entry


def set_status(database, entry, status, by):
    """Any status at any time (people correct mistakes); each change is kept with its date."""
    if status not in db.STATUSES or status == entry.status:
        return False
    entry.status = status
    entry.status_history = list(entry.status_history or []) + [
        {"status": status, "at": db.utcnow().isoformat(timespec="seconds"), "by": by.id}]
    entry.apply_clicked_at = None
    database.commit()
    return True
