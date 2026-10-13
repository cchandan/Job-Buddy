"""Triage: one job at a time, best first; shortlist or skip; a skip with a reason teaches a rule that lowers similar jobs."""
import html
import re
from datetime import date, datetime

from app import db, triage
from conftest import TODAY, make_job, signed_in


def text(response):
    return html.unescape(response.text)


def profile(person_id, **over):
    with db.SessionLocal() as s:
        row = dict(person_id=person_id, keywords="data analyst", enabled=True, needs_sponsorship=False)
        row.update(over)
        s.add(db.SearchProfile(**row))
        s.commit()


def job_for(person_id, job_id="j1", **fields):
    fields.setdefault("matched", [person_id])
    return make_job(job_id, **fields)


def deck_ids(person_id):
    with db.SessionLocal() as s:
        return [j.id for j in triage.deck(s, s.get(db.Person, person_id), TODAY)]


def decisions(person_id):
    with db.SessionLocal() as s:
        return {d.job_id: (d.decision, d.reason, d.rule) for d in s.query(db.JobDecision).filter_by(person_id=person_id)}


def tracked(person_id):
    with db.SessionLocal() as s:
        return {e.job_id for e in s.query(db.TrackerEntry).filter_by(person_id=person_id)}


# ---------- who is in the deck ----------

def test_the_deck_holds_only_matches_that_are_open_untracked_and_undecided(people):
    wife = people["wife"]
    profile(wife)
    job_for(wife, "ok", title="Open one")
    make_job("other", matched=[people["brother"]])                       # someone else's match
    job_for(wife, "closed", deadline=date(2026, 9, 1))                      # deadline passed
    job_for(wife, "binned", binned_at=datetime(2026, 10, 1), bin_reason="Expired: x")
    job_for(wife, "mine")
    job_for(wife, "done")
    with db.SessionLocal() as s:
        s.add(db.TrackerEntry(person_id=wife, job_id="mine", status="shortlisted", source="board", added_by_id=wife, status_history=[]))
        s.add(db.JobDecision(person_id=wife, job_id="done", decision="skip"))
        s.commit()
    assert deck_ids(wife) == ["ok"]


def test_someone_without_a_search_sees_every_open_job(people):
    make_job("a")
    make_job("b")
    assert sorted(deck_ids(people["wife"])) == ["a", "b"]


def test_citizenship_only_jobs_never_reach_someone_who_needs_sponsorship(people):
    wife = people["wife"]
    profile(wife, needs_sponsorship=True)
    job_for(wife, "fine")
    job_for(wife, "citizens", citizenship_required=True)
    assert deck_ids(wife) == ["fine"]


# ---------- ranking ----------

def test_sponsors_and_close_deadlines_rank_first_for_someone_who_needs_a_visa(people):
    wife = people["wife"]
    profile(wife, needs_sponsorship=True)
    job_for(wife, "plain", title="Plain")
    job_for(wife, "refuses", title="Refuses", sponsorship="no_sponsorship", sponsorship_quote="We cannot sponsor visas")
    job_for(wife, "sponsors", title="Sponsors", sponsorship="sponsors")
    job_for(wife, "soon", title="Soon", sponsorship="sponsors", deadline=date(2026, 10, 8))
    ids = deck_ids(wife)
    assert ids[:2] == ["soon", "sponsors"] and ids[-1] == "refuses"


def test_a_skip_teaches_a_rule_that_ranks_similar_jobs_lower_and_never_hides_them(people):
    wife = people["wife"]
    profile(wife)
    job_for(wife, "lon1", title="Analyst", location="London, UK")
    job_for(wife, "lon2", title="Analyst two", location="London", first_seen=datetime(2026, 10, 4))  # newer, so first among equals
    job_for(wife, "leeds", title="Analyst three", location="Leeds", first_seen=datetime(2026, 10, 3))
    c = signed_in("wife")
    c.post("/triage/lon1/skip", {"reason": "location"})
    assert decisions(wife)["lon1"] == ("skip", "location", "place:london")
    ids = deck_ids(wife)
    assert set(ids) == {"lon2", "leeds"} and ids[0] == "leeds"  # London is lower, not gone
    c.post("/triage/rules/forget", {"rule": "place:london"})
    assert decisions(wife)["lon1"][2] == ""
    ids = deck_ids(wife)
    assert set(ids) == {"lon2", "leeds"} and ids[0] == "lon2"  # London no longer ranks lower, so the earlier job is first again


def test_each_reason_teaches_the_right_kind_of_rule(people):
    j = lambda **f: type("J", (), dict(title="Senior Data Analyst", company="Monzo", location="London, UK", seniority="senior",
                                       salary_min=38000, salary_max=45000, **f))()
    job = j()
    assert triage.rule_for("senior", job) == "level:senior"
    assert triage.rule_for("location", job) == "place:london"
    assert triage.rule_for("company", job) == "company:monzo"
    assert triage.rule_for("pay", job) == "pay:45000"
    assert triage.rule_for("field", job) == "field:data analyst"
    assert triage.rule_for("other", job) == "" and triage.rule_for("nonsense", job) == ""
    assert "Monzo lower" == triage.rule_label("company:monzo") and "£45,000" in triage.rule_label("pay:45000")


def test_rules_lower_by_level_company_and_pay(people):
    wife = people["wife"]
    profile(wife)
    job_for(wife, "a", title="Senior Analyst", seniority="senior", company="Acme")
    job_for(wife, "b", title="Analyst", seniority="junior", company="Monzo", salary_max=30000)
    job_for(wife, "c", title="Analyst", seniority="junior", company="Other", salary_max=50000)
    make_job("x1", matched=[wife], title="Senior", seniority="senior")
    make_job("x2", matched=[wife], title="Other", company="Monzo")
    c = signed_in("wife")
    c.post("/triage/x1/skip", {"reason": "senior"})
    c.post("/triage/x2/skip", {"reason": "company"})
    ids = deck_ids(wife)
    assert ids[0] == "c" and ids.index("b") > ids.index("c") and ids.index("a") > ids.index("c")


# ---------- the page and the swipes ----------

def test_the_page_shows_the_best_card_with_a_reason_and_both_buttons(people):
    wife = people["wife"]
    profile(wife, needs_sponsorship=True)
    job_for(wife, "best", title="Graduate Data Analyst", company="Ocado", sponsorship="sponsors", seniority="graduate",
            found_by={str(wife): ["graduate data analyst"]}, deadline=date(2026, 10, 7))
    job_for(wife, "next", title="Second job")
    page = text(signed_in("wife").get("/triage"))
    assert "Graduate Data Analyst" in page and "Ocado" in page and "Matches “graduate data analyst”" in page
    assert "Sponsors visas, and you need sponsorship" in page and 'action="/triage/best/shortlist"' in page and 'action="/triage/best/skip"' in page
    assert "Second job" in page and "2 to look at" in page and "Why skip it?" in page


def test_shortlisting_adds_to_the_tracker_and_moves_on(people):
    wife = people["wife"]
    job_for(wife, "a", title="Alpha")
    job_for(wife, "b", title="Beta", sponsorship="sponsors")
    c = signed_in("wife")
    assert c.post("/triage/a/shortlist").headers["location"] == "/triage"
    assert tracked(wife) == {"a"} and decisions(wife)["a"][0] == "shortlist"
    page = text(c.get("/triage"))
    assert "Added to your Tracker: Alpha" in page and 'action="/triage/undo"' in page
    assert "a" not in deck_ids(wife)


def test_skipping_without_a_reason_teaches_nothing(people):
    wife = people["wife"]
    job_for(wife, "a")
    c = signed_in("wife")
    c.post("/triage/a/skip", {})
    assert decisions(wife)["a"] == ("skip", "", "")
    c2 = signed_in("wife")
    c2.post("/triage/a/skip", {"reason": "bogus"})
    assert decisions(wife)["a"] == ("skip", "", "")  # an unknown reason is ignored, the skip still counts


def test_a_swipe_on_an_unknown_job_is_a_404(people):
    c = signed_in("wife")
    assert c.post("/triage/nope/skip").status_code == 404 and c.post("/triage/nope/shortlist").status_code == 404


def test_undo_takes_back_a_skip_and_a_untouched_shortlist(people):
    wife = people["wife"]
    job_for(wife, "a", title="Alpha")
    job_for(wife, "b", title="Beta")
    c = signed_in("wife")
    c.post("/triage/a/skip", {"reason": "other"})
    c.post("/triage/undo")
    assert "a" in deck_ids(wife) and "a" not in decisions(wife)
    c.post("/triage/b/shortlist")
    c.post("/triage/undo")
    assert tracked(wife) == set() and "b" in deck_ids(wife)
    assert "Undone: Beta" in text(c.get("/triage"))


def test_undo_leaves_a_shortlisted_job_alone_once_it_has_been_worked_on(people):
    wife = people["wife"]
    job_for(wife, "a")
    c = signed_in("wife")
    c.post("/triage/a/shortlist")
    with db.SessionLocal() as s:
        s.query(db.TrackerEntry).filter_by(person_id=wife).one().notes = "phoned them"
        s.commit()
    c.post("/triage/undo")
    assert tracked(wife) == {"a"}  # their notes are never thrown away


def test_undo_with_nothing_to_undo_is_harmless(people):
    assert signed_in("wife").post("/triage/undo").status_code == 303


# ---------- the skipped list ----------

def test_skipped_jobs_can_be_reviewed_and_brought_back(people):
    wife = people["wife"]
    job_for(wife, "a", title="Alpha", location="London")
    c = signed_in("wife")
    c.post("/triage/a/skip", {"reason": "location"})
    page = text(c.get("/triage/skipped"))
    assert "Alpha" in page and "Wrong location" in page and "London lower" in page and 'action="/triage/skipped/a/restore"' in page
    c.post("/triage/skipped/a/restore")
    assert "a" in deck_ids(wife) and "Nothing skipped" in text(c.get("/triage/skipped"))


def test_the_rules_list_shows_on_the_triage_page_and_can_be_forgotten_there(people):
    wife = people["wife"]
    job_for(wife, "a", location="London")
    job_for(wife, "b", location="Leeds")
    c = signed_in("wife")
    c.post("/triage/a/skip", {"reason": "location"})
    page = text(c.get("/triage"))
    assert "London lower" in page and 'name="rule" value="place:london"' in page
    r = c.post("/triage/rules/forget", {"rule": "place:london", "next": "/triage"})
    assert r.headers["location"] == "/triage" and "Nothing yet" in text(c.get("/triage"))
    assert c.post("/triage/rules/forget", {"rule": "x", "next": "https://evil.example.com"}).headers["location"] == "/triage/skipped"


def test_the_all_caught_up_page(people):
    page = text(signed_in("wife").get("/triage"))
    assert "all caught up" in page and 'href="/tracker"' in page


# ---------- people and access ----------

def test_one_persons_swipes_never_touch_another(people):
    wife, brother = people["wife"], people["brother"]
    make_job("a", matched=[wife, brother])
    signed_in("wife").post("/triage/a/skip", {"reason": "other"})
    assert "a" in deck_ids(brother) and "a" not in deck_ids(wife)
    assert decisions(brother) == {}


def test_triage_is_for_users_not_admins(people):
    dad = signed_in("dad")
    for url in ("/triage", "/triage/skipped"):
        assert dad.get(url).status_code == 403
    assert dad.post("/triage/a/skip").status_code == 403


def test_the_sidebar_has_triage_with_the_number_waiting(people):
    wife = people["wife"]
    c = signed_in("wife")
    nav = re.search(r'<nav class="rail rail--grouped.*?</nav>', c.get("/").text, re.S).group(0)
    assert ">Triage<" in nav and "to look at" not in nav  # always there, but no count while nothing waits
    job_for(wife, "a")
    job_for(wife, "b")
    nav = re.search(r'<nav class="rail rail--grouped.*?</nav>', c.get("/").text, re.S).group(0)
    assert ">Triage<" in nav and 'aria-label="2 jobs to look at"' in nav and nav.index(">Triage<") < nav.index(">Job Board<")


def test_removing_an_account_removes_their_swipes(people):
    wife = people["wife"]
    job_for(wife, "a")
    signed_in("wife").post("/triage/a/skip", {"reason": "other"})
    r = signed_in("dad").post(f"/admin/accounts/{wife}/remove", {"confirm": "wife"})
    assert r.status_code == 303
    with db.SessionLocal() as s:
        assert s.query(db.JobDecision).count() == 0


def test_the_why_line_only_says_what_is_recorded(people):
    wife = people["wife"]
    with db.SessionLocal() as s:
        person = s.get(db.Person, wife)
        bare = type("J", (), dict(found_by=None, matched=[], seniority="mid", sponsorship="unclear", licensed_sponsor=False, deadline=None))()
        assert triage.why(bare, person, None, TODAY) == "A recent job on the Board."
        sponsor = db.SearchProfile(person_id=wife, needs_sponsorship=True)
        unclear = type("J", (), dict(found_by=None, matched=[wife], seniority="graduate", sponsorship="unclear", licensed_sponsor=False, deadline=date(2026, 10, 6)))()
        line = triage.why(unclear, person, sponsor, TODAY)
        assert "Found by your search" in line and "Graduate level" in line and "not stated, so check" in line and "deadline is close" in line
        assert "Sponsors visas" not in line


def test_the_deadline_chip_shows_on_the_card(people):
    wife = people["wife"]
    job_for(wife, "a", deadline=date(2026, 10, 7))
    assert "Closes in 2 days" in text(signed_in("wife").get("/triage"))
