"""Never trust AI output: quotes must be in the advert and deadlines must be real dates."""
from datetime import date

import pytest

from app import ai, tagging

TODAY = date(2026, 10, 5)
ADVERT = ("Junior developer wanted. You will use Python and AWS every day. "
          "Applications close on 20 October 2026. Unfortunately we cannot offer visa sponsorship for this role.")


def tags(**fields):
    return tagging.JobTags(**fields)


def test_real_quotes_are_kept():
    out = tagging.finalize({"description": ADVERT}, tags(
        sponsorship="no_sponsorship", sponsorship_quote="we cannot offer visa sponsorship",
        deadline="2026-10-20", deadline_quote="Applications close on 20 October 2026"), "ai", TODAY)
    assert out["sponsorship"] == "no_sponsorship" and out["deadline"] == "2026-10-20"
    assert out["deadline_quote"] == "Applications close on 20 October 2026"


def test_made_up_quote_is_dropped():
    advert = "Junior developer wanted. You will use Python and AWS every day in a friendly team."
    out = tagging.finalize({"description": advert}, tags(
        sponsorship="sponsors", sponsorship_quote="We happily sponsor visas",
        deadline="2026-10-20", deadline_quote="Closing date 20 October"), "ai", TODAY)
    assert out["sponsorship"] == "unclear" and out["sponsorship_quote"] == ""
    assert out["deadline"] is None and out["deadline_quote"] == ""


def test_impossible_deadline_becomes_unknown():
    advert = "Developer role. Apply by 31 February 2026. Posted 1 January 2020."
    for bad in ("2026-02-31", "not a date", "2020-01-01"):
        out = tagging.finalize({"description": advert}, tags(deadline=bad, deadline_quote="Apply by 31 February 2026"), "ai", TODAY)
        assert out["deadline"] is None, bad


def test_keyword_deadline_reads_the_whole_date():
    for advert, want in (("Applications must be received by **Friday 23****rd** **October 2026** at noon.", date(2026, 10, 23)),
                         ("Closing date: Wednesday 14th October 2026", date(2026, 10, 14)),
                         ("Apply by October 20, 2026 please.", date(2026, 10, 20)),
                         ("The deadline is 2026-11-02.", date(2026, 11, 2)),
                         ("We were founded on 3 March 2019 and have no closing date.", None)):
        assert tagging.keyword_deadline(advert, TODAY)[0] == want, advert


def test_keyword_safety_net_when_ai_is_unsure_or_down():
    out = tagging.finalize({"description": ADVERT}, tags(), "ai", TODAY)  # AI said "unclear" to everything
    assert out["sponsorship"] == "no_sponsorship" and out["deadline"] == "2026-10-20"
    assert not ai.available()
    out = tagging.tag_job({"title": "Junior developer", "company": "Acme", "description": ADVERT}, use_ai=False, today=TODAY)
    assert out["tag_source"] == "keywords" and "Python" in out["required_skills"]


