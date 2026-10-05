"""Deadline and listing-status rules. Pure code, no AI, no database.

Listing status is a fact about the advert, worked out from its deadline and when it was last fetched.
It is never stored.
"""
import re
from datetime import date, datetime, timedelta

CLOSING_SOON_DAYS = 7
STALE_DAYS = 30  # no deadline and not found by a fetch for this long -> "possibly closed"

LISTING_LABELS = {"open": "Open", "closing_soon": "Closing soon", "closed": "Closed", "possibly_closed": "Possibly closed"}


def listing_status(job, today):
    """open / closing_soon / closed / possibly_closed."""
    if job.deadline:
        if job.deadline < today:
            return "closed"
        return "closing_soon" if (job.deadline - today).days <= CLOSING_SOON_DAYS else "open"
    last = job.last_seen
    if isinstance(last, datetime):
        last = last.date()
    if last and (today - last).days >= STALE_DAYS:
        return "possibly_closed"
    return "open"


def effective_deadline(entry, job):
    """The hand-set deadline if there is one, else the advert's, else None."""
    return entry.deadline_override or job.deadline


def short(d):
    return f"{d.day} {d.strftime('%b')}"


def chip(deadline, today):
    """The deadline chip: {"text", "tone"} with tone neutral / warn / urgent / muted / none."""
    if deadline is None:
        return {"text": "No deadline", "tone": "none"}
    days = (deadline - today).days
    if days < 0:
        return {"text": f"Closed {short(deadline)}", "tone": "muted"}
    if days == 0:
        return {"text": "Closes today", "tone": "urgent"}
    if days == 1:
        return {"text": "Closes tomorrow", "tone": "warn"}
    if days <= CLOSING_SOON_DAYS:
        return {"text": f"Closes in {days} days", "tone": "warn"}
    return {"text": short(deadline) + (f" {deadline.year}" if deadline.year != today.year else ""), "tone": "neutral"}


_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"])}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})
_MONTHS["sept"] = 9
_MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))
# The look-arounds stop a match from starting or ending in the middle of a number ("October 20" in "October 2026").
DATE_RE = (rf"(?<!\d)(?:\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}[/.]\d{{1,2}}[/.]\d{{2,4}}|"
           rf"\d{{1,2}}(?:st|nd|rd|th)?(?:\s+of)?\s+(?:{_MONTH_RE})\.?,?(?:\s+\d{{4}})?|"
           rf"(?:{_MONTH_RE})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?(?:\s+\d{{4}})?)(?!\d)")


def parse_date(text, today):
    """A real date from '2026-10-12', '12/10/2026' (day first), '12th October 2026', 'October 12' ... or None.

    A date with no year means its next occurrence. Dates more than 60 days past or 2 years ahead are refused:
    a deadline like that is more likely a mis-read than a fact.
    """
    text = (text or "").strip().lower()
    found = None
    try:
        m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
        if m:
            found = date(int(m[1]), int(m[2]), int(m[3]))
        m = found or re.fullmatch(r"(\d{1,2})[/.](\d{1,2})[/.](\d{2,4})", text)
        if m and not found:
            year = int(m[3]) + (2000 if int(m[3]) < 100 else 0)
            found = date(year, int(m[2]), int(m[1]))
        if not found:
            m = (re.fullmatch(rf"(?P<d>\d{{1,2}})(?:st|nd|rd|th)?(?:\s+of)?\s+(?P<m>{_MONTH_RE})\.?,?(?:\s+(?P<y>\d{{4}}))?", text)
                 or re.fullmatch(rf"(?P<m>{_MONTH_RE})\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?,?(?:\s+(?P<y>\d{{4}}))?", text))
            if m:
                month, day = _MONTHS[m["m"]], int(m["d"])
                if m["y"]:
                    found = date(int(m["y"]), month, day)
                else:
                    found = date(today.year, month, day)
                    if found < today - timedelta(days=7):
                        found = date(today.year + 1, month, day)
    except ValueError:  # 31 February and friends
        return None
    if found is None or found < today - timedelta(days=60) or found > today + timedelta(days=730):
        return None
    return found
