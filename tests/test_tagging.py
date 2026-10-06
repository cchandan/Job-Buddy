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




import pytest  # noqa: E402


@pytest.mark.parametrize("title, description, expected", [
    ("Senior Business Analyst", "", "senior"), ("Business Analyst", "", ""), ("Business Analyst", "Founded 25 years ago, we are growing.", ""),
    ("Platform Engineer", "You will have 3+ years of experience with AWS.", "mid"),
    ("Platform Engineer", "Experience: 8 years in platform work.", "senior"),
    ("Data Analyst", "1-2 years experience preferred.", "junior"),
    ("Project Manager", "", "mid"), ("Head of Data", "", "senior"), ("Associate Software Developer", "", "junior"),
    ("Graduate Software Engineer", "", "graduate"), ("2027 Software Engineer Programme", "", "graduate"),
    ("Programme Manager", "", "mid"), ("Software Engineer - Entry Level", "", "graduate"),
])
def test_seniority_comes_from_the_title_or_the_experience_asked_for(title, description, expected):
    assert tagging.keyword_seniority(title, description) == expected


def test_a_refusal_to_sponsor_is_never_read_as_an_offer():
    text = ("The salary for this role does not meet the minimum threshold required under the Immigration Rules for "
            "Skilled Worker visa sponsorship.")
    assert tagging.keyword_sponsorship(text)[0] == "no_sponsorship"
    assert tagging.keyword_sponsorship("We offer Skilled Worker visa sponsorship for this role.")[0] == "sponsors"


@pytest.mark.parametrize("text, expected", [
    ("Benefits: on-site parking and a gym.", "unclear"), ("On site training is provided.", "unclear"),
    ("Free on-site healthcare for staff.", "unclear"), ("Fully on-site role in Leeds.", "onsite"),
    ("Experience with hybrid cloud environments.", "unclear"), ("This role is hybrid, 3 days in the office.", "hybrid"),
])
def test_work_mode_ignores_perks_and_technology_words(text, expected):
    assert tagging.keyword_work_mode(text)[0] == expected


def test_improving_the_rules_makes_old_tags_get_redone():
    from app import sources
    raw = {"title": "Role", "company": "Co", "location": "Leeds", "description": "x " * 100}
    before = sources.content_hash(raw)
    old = sources.TAGGER_VERSION
    try:
        sources.TAGGER_VERSION = "999"
        assert sources.content_hash(raw) != before
    finally:
        sources.TAGGER_VERSION = old
