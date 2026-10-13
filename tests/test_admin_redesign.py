"""The reorganised admin: Today, People, Refresh, Searches, Bin, Snapshots, Settings, and the sidebar that links them."""
import html
import re
from datetime import datetime

from app import db, ingest, jobbin, refreshers, sources, vault
from conftest import Browser, TODAY, make_job, make_person, signed_in


def text(response):
    return html.unescape(response.text)


def sidebar(page):
    return re.search(r'<nav class="rail rail--grouped[^"]*".*?</nav>', page, re.S).group(0)


# ---------- the sidebar ----------

def test_the_admin_sidebar_is_grouped_and_a_user_keeps_theirs(people):
    nav = sidebar(signed_in("dad").get("/admin").text)
    for label in ("Today", "People", "Job Board", "Refresh", "Searches", "Bin", "Snapshots", "Settings"):
        assert f">{label}<" in nav
    assert "Jobs sync" not in nav and "Admin home" not in nav
    assert nav.index('nav-label">People') < nav.index('nav-label">Jobs') and nav.index(">Snapshots<") < nav.index(">Settings<")
    wife = signed_in("wife").get("/").text
    assert "rail--admin" not in wife and 'href="/tracker"' in wife and "ADMIN" not in sidebar(wife)


def test_the_current_page_is_marked_in_the_sidebar(people):
    dad = signed_in("dad")
    for url, label in (("/admin", "Today"), ("/admin/people", "People"), ("/admin/refresh", "Refresh"), ("/admin/searches", "Searches"),
                       ("/admin/bin", "Bin"), ("/admin/snapshots", "Snapshots"), ("/admin/settings", "Settings"), ("/board", "Job Board")):
        current = re.findall(r'<a [^>]*aria-current="page"[^>]*>.*?</a>', sidebar(dad.get(url).text), re.S)
        assert len(current) == 1 and f">{label}<" in current[0], url
    viewing = sidebar(dad.get(f"/admin/users/{people['wife']}/tracker").text)
    assert ">People<" in re.search(r'<a [^>]*aria-current="page"[^>]*>.*?</a>', viewing, re.S).group(0)  # a user's pages sit under People


def test_the_menu_button_comes_before_the_name_so_it_sits_on_the_left(people):
    nav = sidebar(signed_in("dad").get("/admin").text)
    assert 'id="burger"' in nav and nav.index('id="burger"') < nav.index('class="mark"')
    assert 'aria-expanded="false"' in nav and 'aria-controls="rail-body"' in nav


def test_badges_count_what_needs_the_admin(people):
    dad = signed_in("dad")
    assert "need you" not in sidebar(dad.get("/admin").text)
    Browser().post("/signup", {"name": "Sam Taylor", "username": "sam.t", "password": "a-good-password-1", "again": "a-good-password-1"})
    nav = sidebar(dad.get("/admin").text)
    assert 'aria-label="1 need you"' in nav and 'aria-label="1 sign-up request"' in nav
    make_job("old", closed_at=datetime(2026, 10, 1), close_reason="page gone")
    assert 'aria-label="1 ready to tidy"' in sidebar(dad.get("/admin").text)


def test_the_sidebar_shows_whether_the_refresher_is_healthy(people, browser):
    dad = signed_in("dad")
    assert "rdot" not in sidebar(dad.get("/admin").text)  # no refresher, no dot
    with db.SessionLocal() as s:
        _, token = refreshers.create(s, "Optiplex")
    assert "rdot--warn" in sidebar(dad.get("/admin").text)  # added but never heard from
    browser.post("/api/refresher/checkin", {})  # (no secret: ignored)
    with db.SessionLocal() as s:
        row = s.query(db.Refresher).one()
        refreshers.check_in(s, row, {"version": refreshers.VERSION, "state": "idle"})
    nav = sidebar(dad.get("/admin").text)
    assert "rdot--ok" in nav and "07:30" in nav
    with db.SessionLocal() as s:
        refreshers.check_in(s, s.query(db.Refresher).one(), {"version": refreshers.VERSION, "state": "idle", "last_run": {"status": "failed", "at": "2026-10-05T07:30:00", "message": "boom"}})
    assert "rdot--bad" in sidebar(dad.get("/admin").text)


# ---------- Today ----------

def test_today_lists_what_needs_the_admin_and_lets_them_act_in_place(people):
    Browser().post("/signup", {"name": "Sam Taylor", "username": "sam.t", "password": "a-good-password-1", "again": "a-good-password-1"})
    dad = signed_in("dad")
    page = text(dad.get("/admin"))
    assert "Needs you" in page and "Sam Taylor wants the username" in page and "Approve" in page
    with db.SessionLocal() as s:
        request_id = s.query(db.SignupRequest).one().id
    r = dad.post(f"/admin/signups/{request_id}/approve", {"next": "/admin"})
    assert r.status_code == 303 and r.headers["location"] == "/admin"  # back to Today, not off to another page
    assert "Nothing needs you" in text(dad.get("/admin"))


def test_a_next_that_leaves_the_site_is_ignored(people):
    Browser().post("/signup", {"name": "Sam Taylor", "username": "sam.t", "password": "a-good-password-1", "again": "a-good-password-1"})
    with db.SessionLocal() as s:
        request_id = s.query(db.SignupRequest).one().id
    r = signed_in("dad").post(f"/admin/signups/{request_id}/reject", {"next": "https://evil.example.com"})
    assert r.headers["location"] == "/admin/people"


def test_today_shows_a_failed_refresh_and_the_at_a_glance_tiles(people):
    make_job("a")
    with db.SessionLocal() as s:
        row, _ = refreshers.create(s, "Optiplex at home")
        refreshers.check_in(s, row, {"version": refreshers.VERSION, "state": "idle", "last_run": {"status": "failed", "at": "2026-10-05T07:30:00", "message": "no network"}})
    page = text(signed_in("dad").get("/admin"))
    assert "Optiplex at home: the last refresh failed" in page and "no network" in page
    assert "1 job" in page and "to tidy" in page and "Last snapshot" in page and "None yet" in page


# ---------- People ----------

def test_people_shows_accounts_and_sign_up_requests_and_opens_a_manage_panel(people):
    Browser().post("/signup", {"name": "Sam Taylor", "username": "sam.t", "password": "a-good-password-1", "again": "a-good-password-1"})
    dad = signed_in("dad")
    page = text(dad.get("/admin/people"))
    assert "1 sign-up request" in page and "Sam Taylor" in page and "Wife" in page and "Brother" in page
    assert "Manage Wife" not in page  # nothing is open until asked
    page = text(dad.get(f"/admin/people?manage={people['wife']}"))
    assert 'aria-label="Manage Wife"' in page and "Make a new password" in page and "Remove account" in page
    assert "Dad" in text(dad.get("/admin/people?tab=admins"))
    assert 'id="add"' in dad.get("/admin/people?add=1").text and 'id="add"' not in dad.get("/admin/people").text


def test_a_password_is_only_revealed_after_the_admin_types_their_own(people):
    dad = signed_in("dad")
    dad.post("/admin/accounts", {"name": "New Bie", "username": "newbie", "role": "user"})
    issued = re.search(r'id="pw">([^<]+)<', dad.get("/admin/people").text).group(1)
    with db.SessionLocal() as s:
        uid = s.query(db.Person).filter_by(username="newbie").one().id
    assert issued not in dad.get(f"/admin/people?manage={uid}").text  # masked until asked
    bad = dad.post(f"/admin/people/{uid}/reveal", {"password": "wrong"})
    assert bad.status_code == 401 and issued not in bad.text and "isn't your password" in text(bad)
    ok = dad.post(f"/admin/people/{uid}/reveal", {"password": "correct-horse-1"})
    assert ok.status_code == 200 and issued in ok.text and ok.headers["Cache-Control"] == "no-store"
    assert issued not in dad.get(f"/admin/people?manage={uid}").text  # nothing is remembered


def test_a_password_the_person_chose_is_never_offered(people):
    dad = signed_in("dad")  # make_person gives no vault copy, as for someone who chose their own
    page = text(dad.get(f"/admin/people?manage={people['wife']}"))
    assert "chose their own password" in page and "Type your own password to reveal it" not in page


def test_revealing_a_password_is_for_admins_only_and_wrong_guesses_lock(people):
    assert signed_in("wife").post(f"/admin/people/{people['brother']}/reveal", {"password": "correct-horse-1"}).status_code == 403
    assert signed_in("wife").get("/admin/people").status_code == 403
    dad = signed_in("dad")
    for _ in range(5):
        dad.post(f"/admin/people/{people['wife']}/reveal", {"password": "nope"})
    r = dad.post(f"/admin/people/{people['wife']}/reveal", {"password": "correct-horse-1"})
    assert r.status_code == 401 and "Too many" in r.text


def test_the_manage_panel_does_not_offer_to_remove_yourself(people):
    page = text(signed_in("dad").get(f"/admin/people?tab=admins&manage={people['dad']}"))
    assert 'aria-label="Manage Dad"' in page and "Remove account" not in page


# ---------- Refresh ----------

def test_the_old_pages_lead_to_the_new_ones(people):
    dad = signed_in("dad")
    for old, new in (("/admin/sync", "/admin/refresh"), ("/admin/refresher", "/admin/refresh")):
        r = dad.get(old)
        assert r.status_code == 303 and r.headers["location"] == new


def test_refresh_has_status_schedule_sources_history_and_computers_in_one_place(people):
    with db.SessionLocal() as s:
        refreshers.create(s, "Optiplex at home")
    page = text(signed_in("dad").get("/admin/refresh"))
    for part in ('aria-label="Schedule"', 'aria-label="Sources"', 'aria-label="History"', 'aria-label="Computers"', "Optiplex at home"):
        assert part in page
    assert "Who we are searching for" not in page  # searches have their own page now


def test_sources_can_be_switched_on_the_live_site_for_the_refresher_to_follow(people, monkeypatch):
    monkeypatch.setattr(sources, "jobspy_available", lambda: False)  # the live site has no fetching tools
    dad = signed_in("dad")
    page = dad.get("/admin/refresh").text
    assert 'value="indeed" checked' in page and "disabled" not in re.search(r'<input[^>]*value="indeed"[^>]*>', page).group(0)
    dad.post("/admin/sync/sources", {"source": ["indeed"]})
    with db.SessionLocal() as s:
        off = ingest.disabled_sources(s)
        assert "linkedin" in off and "indeed" not in off
        assert "linkedin" in refreshers.config_for(s)["sources_off"]  # and that is what the refresher is told


# ---------- Searches ----------

def test_searches_has_a_card_per_person_and_saves_from_the_page(people):
    with db.SessionLocal() as s:
        s.add(db.SearchProfile(person_id=people["wife"], keywords="tester, developer", locations="Leeds", enabled=True))
        s.commit()
    dad = signed_in("dad")
    page = text(dad.get("/admin/searches"))
    assert "Wife" in page and "data-chips" in page and "tester, developer" in page  # the text box stays, so it works without script
    r = dad.post(f"/admin/sync/profile/{people['wife']}", {"keywords": "tester", "locations": "Leeds", "enabled": "1"})
    assert r.status_code == 303 and r.headers["location"] == f"/admin/searches?who={people['wife']}"
    with db.SessionLocal() as s:
        assert s.get(db.SearchProfile, people["wife"]).keywords == "tester"
    assert signed_in("wife").get("/admin/searches").status_code == 403


# ---------- Bin, Snapshots, Settings ----------

def test_the_bin_page_counts_each_filter_and_names_who_binned_by_hand(people):
    make_job("e", closed_at=datetime(2026, 10, 1), close_reason="page gone (404)")
    make_job("m")
    dad = signed_in("dad")
    with db.SessionLocal() as s:
        jobbin.bin_by_hand(s, s.get(db.Job, "m"), s.get(db.Person, people["dad"]), "too senior")
    page = text(dad.get("/admin/bin"))
    assert "1 job is ready to bin" in page and "Binned by hand" in page and "by Dad: too senior" in page
    assert re.search(r"Binned by hand <span class=\"by\">1</span>", page) and re.search(r"Expired <span class=\"by\">0</span>", page)


def test_snapshots_keep_their_actions_in_a_menu(people):
    dad = signed_in("dad")
    dad.post("/admin/snapshots", {"name": "Before keyword change"})
    page = text(dad.get("/admin/snapshots"))
    assert "Before keyword change" in page and 'class="menu"' in page and 'role="menuitem"' in page and "Restore" in page


def test_settings_holds_notifications_sign_up_and_the_admins_password(people):
    dad = signed_in("dad")
    page = text(dad.get("/admin/settings"))
    assert 'id="notify"' in page and "Public sign-up" in page and "Change my password" in page
    r = dad.post("/profile/password", {"current": "correct-horse-1", "new": "a-new-password-1", "again": "a-new-password-1"})
    assert r.status_code == 303 and r.headers["location"] == "/admin/settings"
    assert dad.post("/admin/signups/toggle", {"next": "/admin/settings"}).headers["location"] == "/admin/settings"
    assert "Open sign-up" in text(dad.get("/admin/settings"))
    assert signed_in("wife").get("/admin/settings").status_code == 403


def test_every_admin_page_needs_an_admin(people):
    wife = signed_in("wife")
    for url in ("/admin", "/admin/people", "/admin/refresh", "/admin/searches", "/admin/bin", "/admin/snapshots", "/admin/settings"):
        assert wife.get(url).status_code == 403, url
    assert Browser().get("/admin/people", follow_redirects=False).status_code != 200  # signed out: sent to sign in
