"""The user side in the same style: grouped sidebar with counts, Home, Job Board, Tracker, Calendar and Profile."""
import html
import re
from datetime import date, datetime

from app import db
from conftest import TODAY, make_job, signed_in


def text(response):
    return html.unescape(response.text)


def sidebar(page):
    return re.search(r'<nav class="rail rail--grouped[^"]*".*?</nav>', page, re.S).group(0)


def track(person_id, job_id, status="shortlisted", **fields):
    with db.SessionLocal() as s:
        row = dict(person_id=person_id, job_id=job_id, status=status, source="board", added_by_id=person_id,
                   status_history=[{"status": status, "at": "2026-10-01T09:00:00", "by": person_id}])
        row.update(fields)
        s.add(db.TrackerEntry(**row))
        s.commit()


# ---------- the sidebar ----------

def test_the_user_sidebar_is_grouped_with_the_menu_button_on_the_left(people):
    nav = sidebar(signed_in("wife").get("/").text)
    for label in ("Home", "Job Board", "Tracker", "Calendar", "Profile"):
        assert f">{label}<" in nav
    assert nav.index('nav-label">Jobs') < nav.index('nav-label">My applications') < nav.index(">Profile<")
    assert nav.index('id="burger"') < nav.index('class="mark"') and 'aria-controls="rail-body"' in nav
    assert "Admin" not in nav and "Refresh" not in nav


def test_the_current_user_page_is_marked(people):
    wife = signed_in("wife")
    for url, label in (("/", "Home"), ("/board", "Job Board"), ("/tracker", "Tracker"), ("/calendar", "Calendar"), ("/profile", "Profile")):
        current = re.findall(r'<a [^>]*aria-current="page"[^>]*>.*?</a>', sidebar(wife.get(url).text), re.S)
        assert len(current) == 1 and f">{label}<" in current[0], url


def test_badges_count_things_to_do_new_jobs_and_assigned_jobs(people):
    wife = signed_in("wife")
    assert "to do" not in sidebar(wife.get("/").text) and "assigned to you" not in sidebar(wife.get("/").text)
    make_job("j1")
    make_job("j2", deadline=date(2026, 10, 7))
    track(people["wife"], "j2", added_by_id=people["dad"], source="assigned")  # a deadline in two days, assigned and not yet seen
    nav = sidebar(wife.get("/").text)
    assert 'aria-label="2 things to do"' in nav and 'aria-label="1 new job assigned to you"' in nav and "2 new since you last looked" in nav
    wife.get("/tracker")  # seeing the Tracker clears the assigned count
    assert "assigned to you" not in sidebar(wife.get("/").text)


# ---------- Home ----------

def test_home_leads_with_do_next_and_has_a_status_strip_and_the_new_jobs_panel(people):
    make_job("j1", deadline=date(2026, 10, 7))
    track(people["wife"], "j1")
    page = text(signed_in("wife").get("/"))
    assert "Do next" in page and "Deadline coming up" in page and ">Apply<" in page
    assert 'href="/tracker?status=shortlisted"' in page and 'href="/tracker?status=closed"' in page and "Closed out" in page
    assert "This week" in page and "New on the Job Board" in page


def test_a_new_user_sees_getting_started_not_the_strip(people):
    page = text(signed_in("wife").get("/"))
    assert "Getting started" in page and "Upload your CV" in page and "Do next" not in page


def test_an_all_caught_up_user_is_told_so(people):
    make_job("j1", deadline=date(2026, 12, 1))
    track(people["wife"], "j1")
    assert "You&#39;re all caught up" in signed_in("wife").get("/").text or "all caught up" in text(signed_in("wife").get("/"))


# ---------- Job Board ----------

def test_the_board_has_one_search_bar_a_filters_button_and_chips_that_remove_one_filter(people):
    make_job("j1")
    page = text(signed_in("wife").get("/board?work_mode=hybrid&location=Leeds"))
    assert 'name="q"' in page and "Filters" in page and 'class="filterpop-panel"' in page
    assert "Hybrid" in page and "Near Leeds" in page
    hybrid = re.search(r'href="([^"]*)" aria-label="Remove Hybrid"', page).group(1)
    assert "location=Leeds" in hybrid and "work_mode" not in hybrid  # taking Hybrid off keeps the rest
    plain = text(signed_in("wife").get("/board"))
    assert "fchip" not in plain.split("<main")[1]  # nothing to remove when no filter is on


def test_a_board_row_has_one_action(people):
    make_job("j1", title="Alpha role")
    make_job("j2", title="Beta role")
    track(people["wife"], "j2")
    page = text(signed_in("wife").get("/board"))
    assert page.count('action="/board/j1/shortlist"') == 1 and 'action="/board/j2/shortlist"' not in page and "In your Tracker" in page
    admin = text(signed_in("dad").get("/board"))
    assert "Move to bin" in admin and "Assign" in admin and 'action="/board/j1/shortlist"' not in admin


# ---------- Job page ----------

def test_the_job_page_puts_the_facts_beside_the_description(people):
    make_job("j1", description="Build dashboards in SQL.", sponsorship="sponsors", sponsorship_quote="We hold a licence", deadline=date(2026, 10, 20))
    page = text(signed_in("wife").get("/board/j1"))
    assert 'aria-label="Job description"' in page and 'aria-label="What the advert says"' in page
    assert "Build dashboards in SQL." in page and "We hold a licence" in page and "20 Oct 2026" in page
    assert page.index('aria-label="Job description"') < page.index('aria-label="What the advert says"')


# ---------- Tracker ----------

def test_the_tracker_is_rows_with_the_status_in_place_and_a_did_you_apply_prompt(people):
    make_job("j1", title="Alpha role")
    make_job("j2", title="Beta role")
    track(people["wife"], "j1", apply_clicked_at=datetime(2026, 10, 4, 9, 0))
    track(people["wife"], "j2", status="applied")
    page = text(signed_in("wife").get("/tracker"))
    assert 'aria-label="Did you apply?"' in page and "You opened the advert for Alpha role" in page and "Mark as Applied" in page
    assert page.count('class="jrow') >= 2 and 'class="selst selst--applied"' in page and 'data-autosubmit' in page
    assert "Closed out" in page and 'aria-label="Filter by status"' in page


def test_an_admin_viewing_a_tracker_sees_status_but_cannot_change_it(people):
    make_job("j1", title="Alpha role")
    track(people["wife"], "j1")
    page = text(signed_in("dad").get(f"/admin/users/{people['wife']}/tracker"))
    assert "Alpha role" in page and "selst" not in page and "Assign a job to Wife" in page


# ---------- Calendar and Profile ----------

def test_the_calendar_groups_upcoming_deadlines_beside_the_month(people):
    make_job("j1", title="Alpha role", deadline=date(2026, 10, 7))
    track(people["wife"], "j1")
    page = text(signed_in("wife").get("/calendar"))
    assert "Upcoming" in page and "Alpha role" in page and 'aria-label="Month view"' in page
    assert 'aria-label="Previous month"' in page and 'aria-label="Next month"' in page and "Still to apply" in page


def test_the_profile_has_the_cv_notifications_and_a_folded_password(people):
    page = text(signed_in("wife").get("/profile"))
    assert "Core CV" in page and "Choose a PDF" in page and "Your CV is stored in Job Buddy" in page
    assert 'id="notify"' in page and "Change password" in page
    assert re.search(r'<details class="more">\s*<summary class="foldhead">', page)  # not open until asked
