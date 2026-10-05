"""Listing status and deadline rules, including the 7-day and 30-day edges."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from app import deadlines

TODAY = date(2026, 10, 5)


def job(deadline=None, last_seen=None):
    return SimpleNamespace(deadline=deadline, last_seen=last_seen or datetime(2026, 10, 5))


def test_listing_status_from_the_deadline():
    assert deadlines.listing_status(job(TODAY + timedelta(days=8)), TODAY) == "open"
    assert deadlines.listing_status(job(TODAY + timedelta(days=7)), TODAY) == "closing_soon"
    assert deadlines.listing_status(job(TODAY), TODAY) == "closing_soon"
    assert deadlines.listing_status(job(TODAY - timedelta(days=1)), TODAY) == "closed"


def test_no_deadline_goes_stale_after_30_days():
    assert deadlines.listing_status(job(last_seen=datetime(2026, 9, 6)), TODAY) == "open"            # 29 days
    assert deadlines.listing_status(job(last_seen=datetime(2026, 9, 5)), TODAY) == "possibly_closed"  # 30 days
    # a known deadline wins over staleness
    assert deadlines.listing_status(job(TODAY + timedelta(days=20), datetime(2026, 8, 1)), TODAY) == "open"


def test_hand_set_deadline_wins():
    entry = SimpleNamespace(deadline_override=date(2026, 11, 1))
    assert deadlines.effective_deadline(entry, job(date(2026, 10, 20))) == date(2026, 11, 1)
    entry.deadline_override = None
    assert deadlines.effective_deadline(entry, job(date(2026, 10, 20))) == date(2026, 10, 20)
    assert deadlines.effective_deadline(entry, job()) is None


def test_chip_wording():
    def text(days):
        return deadlines.chip(TODAY + timedelta(days=days), TODAY)

    assert text(38) == {"text": "12 Nov", "tone": "neutral"}
    assert text(3) == {"text": "Closes in 3 days", "tone": "warn"}
    assert text(1)["text"] == "Closes tomorrow"
    assert text(0) == {"text": "Closes today", "tone": "urgent"}
    assert text(-3) == {"text": "Closed 2 Oct", "tone": "muted"}
    assert deadlines.chip(None, TODAY) == {"text": "No deadline", "tone": "none"}


def test_parse_date():
    assert deadlines.parse_date("2026-10-12", TODAY) == date(2026, 10, 12)
    assert deadlines.parse_date("12/10/2026", TODAY) == date(2026, 10, 12)       # day first
    assert deadlines.parse_date("12th October 2026", TODAY) == date(2026, 10, 12)
    assert deadlines.parse_date("3 January", TODAY) == date(2027, 1, 3)           # no year: the next one
    assert deadlines.parse_date("31 February 2026", TODAY) is None                # not a real date
    assert deadlines.parse_date("1 January 2020", TODAY) is None                  # far in the past
    assert deadlines.parse_date("soon", TODAY) is None
