"""The login streak: days in a row, this week at a glance, and kind wording."""
import html
import re
from datetime import date, timedelta

from app import db, streak
from conftest import TODAY, Browser, signed_in  # TODAY is Monday 5 October 2026


def visits(person_id, *days):
    with db.SessionLocal() as s:
        for d in days:
            s.add(db.DailyVisit(person_id=person_id, day=d))
        s.commit()


def build(person_id, day=TODAY):
    with db.SessionLocal() as s:
        return streak.build(s, s.get(db.Person, person_id), day)


def days_ago(n, base=TODAY):
    return base - timedelta(days=n)


def test_signing_in_and_opening_a_page_records_today_once(people):
    c = signed_in("wife")
    c.get("/")
    c.get("/board")
    with db.SessionLocal() as s:
        rows = s.query(db.DailyVisit).filter_by(person_id=people["wife"]).all()
        assert [r.day for r in rows] == [TODAY]


def test_admins_are_not_tracked(people):
    signed_in("dad").get("/admin")
    with db.SessionLocal() as s:
        assert s.query(db.DailyVisit).count() == 0


def test_a_new_person_is_on_day_one(people):
    signed_in("wife").get("/")
    s = build(people["wife"])
    assert s["n"] == 1 and s["headline"] == "1-day login streak" and s["nudge"] == "Log in tomorrow for 2 days" and not s["fresh"] and not s["full"]
    assert [w["kind"] for w in s["week"]] == ["v", "f", "f", "f", "f", "f", "f"] and s["week"][0]["today"]  # Monday today


def test_days_in_a_row_count_and_carry_over_the_week(people):
    wife = people["wife"]
    visits(wife, days_ago(3), days_ago(2), days_ago(1), TODAY)  # Fri, Sat, Sun, Mon
    s = build(wife)
    assert s["n"] == 4 and s["headline"] == "4-day login streak" and s["nudge"] == "Log in tomorrow for 5 days"
    assert [w["kind"] for w in s["week"]] == ["v", "f", "f", "f", "f", "f", "f"]  # the pill shows this week only


def test_a_gap_starts_a_fresh_run_and_says_so_kindly(people):
    wife = people["wife"]
    visits(wife, days_ago(8), days_ago(7), days_ago(6), days_ago(5), TODAY)  # a 4-day run, a gap, today
    s = build(wife)
    assert s["n"] == 1 and s["fresh"] and s["previous"] == 4
    assert s["headline"] == "Login streak: fresh start" and s["nudge"] == "Last run: 4 days"
    assert "lost" not in (s["headline"] + s["nudge"]).lower() and "broke" not in (s["headline"] + s["nudge"]).lower()


def test_one_missed_day_in_the_week_shows_as_missed_and_the_past_as_missed_the_future_as_to_come(people):
    wife = people["wife"]
    thursday = TODAY + timedelta(days=3)
    visits(wife, TODAY, thursday - timedelta(days=1), thursday)  # Mon, Wed, Thu (Tuesday missed)
    s = build(wife, thursday)
    assert [w["kind"] for w in s["week"]] == ["v", "m", "v", "v", "f", "f", "f"] and s["week"][3]["today"] and s["n"] == 2


def test_a_full_week_earns_the_badge_and_drops_the_nudge(people):
    wife = people["wife"]
    sunday = TODAY + timedelta(days=6)
    visits(wife, *[TODAY + timedelta(days=i) for i in range(7)])
    s = build(wife, sunday)
    assert s["full"] and s["n"] == 7 and s["nudge"] == "" and s["headline"] == "7-day login streak"


def test_yesterday_still_counts_before_today_is_recorded(people):
    wife = people["wife"]
    visits(wife, days_ago(2), days_ago(1))
    assert build(wife)["n"] == 2


def test_recording_twice_or_concurrently_is_harmless(people):
    with db.SessionLocal() as s:
        person = s.get(db.Person, people["wife"])
        streak.record(s, person, TODAY)
        streak.record(s, person, TODAY)
        assert s.query(db.DailyVisit).count() == 1


def test_home_shows_the_pill_with_the_words_and_seven_days(people):
    wife = people["wife"]
    visits(wife, days_ago(1), days_ago(2))
    page = html.unescape(signed_in("wife").get("/").text)
    assert 'class="streak"' in page and "3-day login streak" in page and "Log in tomorrow for 4 days" in page
    assert len(re.findall(r'class="sday sday--', page)) == 7 and "sday--today" in page
    assert page.index("Good ") < page.index('class="streak"')  # under the greeting


def test_the_pill_is_for_users_only(people):
    assert 'class="streak"' not in signed_in("dad").get("/admin").text
    assert 'class="streak"' not in Browser().get("/").text


def test_each_persons_streak_is_their_own(people):
    visits(people["brother"], days_ago(1), days_ago(2), days_ago(3))
    signed_in("wife").get("/")
    assert build(people["wife"])["n"] == 1 and build(people["brother"])["n"] == 3  # yesterday back to three days ago


def test_removing_an_account_removes_its_visits(people):
    signed_in("wife").get("/")
    signed_in("dad").post(f"/admin/accounts/{people['wife']}/remove", {"confirm": "wife"})
    with db.SessionLocal() as s:
        assert s.query(db.DailyVisit).filter_by(person_id=people["wife"]).count() == 0
