"""Public sign-up: anyone can ask, nothing exists until an admin approves, and it can't be used to flood the site."""
import html

import pytest

from app import db, notify
from app.routes import signup as signup_routes
from conftest import Browser, make_person, signed_in

NEW = {"name": "Priya Patel", "username": "Priya.P", "password": "a-good-password-1", "again": "a-good-password-1"}


@pytest.fixture(autouse=True)
def clean_throttle():
    signup_routes._recent.clear()
    yield
    signup_routes._recent.clear()


def text(response):
    return html.unescape(response.text)


def ask(browser, **over):
    return browser.post("/signup", {**NEW, **over})


def requests_waiting():
    with db.SessionLocal() as s:
        return [(r.name, r.username) for r in s.query(db.SignupRequest).order_by(db.SignupRequest.id)]


def people_named(username):
    with db.SessionLocal() as s:
        return s.query(db.Person).filter(db.Person.username == username).count()


def test_the_public_pages_offer_it(browser):
    assert 'href="/signup"' in browser.get("/").text
    assert 'href="/signup"' in browser.get("/login").text
    page = browser.get("/signup")
    assert page.status_code == 200 and "Send my request" in page.text


def test_a_request_creates_nothing_until_approved(people, browser):
    r = ask(browser)
    assert r.status_code == 303 and r.headers["location"] == "/signup/sent"
    assert "Your request has been sent" in browser.get("/signup/sent").text
    assert requests_waiting() == [("Priya Patel", "priya.p")]  # the username is lowercased
    assert people_named("priya.p") == 0
    other = Browser()
    wrong = other.login("priya.p", "not-the-password")
    assert wrong.status_code == 401 and "isn't right" in text(wrong)
    waiting = other.login("priya.p", NEW["password"])  # right password, but not approved yet
    assert waiting.status_code == 401 and "waiting for an admin to approve" in text(waiting)
    assert other.get("/tracker").status_code == 303  # still shut out


def test_the_password_is_only_ever_stored_as_a_hash(people, browser):
    ask(browser)
    with db.SessionLocal() as s:
        stored = s.query(db.SignupRequest).one().password_hash
    assert NEW["password"] not in stored and stored.startswith("scrypt$")


def test_admin_approves_and_the_person_signs_in_with_their_own_password(people, browser):
    ask(browser)
    dad = signed_in("dad")
    page = dad.get("/admin").text
    assert "Sign-up request" in page and "Priya Patel" in page and "priya.p" in page
    with db.SessionLocal() as s:
        request_id = s.query(db.SignupRequest).one().id
    r = dad.post(f"/admin/signups/{request_id}/approve")
    assert r.status_code == 303
    assert requests_waiting() == []
    with db.SessionLocal() as s:
        person = s.query(db.Person).filter_by(username="priya.p").one()
        assert person.role == "user" and person.name == "Priya Patel"  # never an admin
    priya = Browser()
    assert priya.login("priya.p", NEW["password"]).status_code == 303
    assert priya.get("/tracker").status_code == 200
    assert priya.get("/admin").status_code == 403
    assert "Sign-up request" not in dad.get("/admin").text


def test_admin_declines(people, browser):
    ask(browser)
    dad = signed_in("dad")
    with db.SessionLocal() as s:
        request_id = s.query(db.SignupRequest).one().id
    dad.post(f"/admin/signups/{request_id}/reject")
    assert requests_waiting() == [] and people_named("priya.p") == 0
    assert "isn't right" in text(Browser().login("priya.p", NEW["password"]))
    assert dad.post(f"/admin/signups/{request_id}/reject").status_code == 404  # already dealt with


def test_approving_after_the_username_was_taken_drops_the_request(people, browser):
    ask(browser)
    make_person("priya.p")  # an admin made that account by hand in the meantime
    dad = signed_in("dad")
    with db.SessionLocal() as s:
        request_id = s.query(db.SignupRequest).one().id
    dad.post(f"/admin/signups/{request_id}/approve")
    assert requests_waiting() == [] and people_named("priya.p") == 1


def test_only_admins_can_decide(people, browser):
    ask(browser)
    with db.SessionLocal() as s:
        request_id = s.query(db.SignupRequest).one().id
    wife = signed_in("wife")
    assert wife.post(f"/admin/signups/{request_id}/approve").status_code == 403
    assert wife.post(f"/admin/signups/{request_id}/reject").status_code == 403
    assert wife.post("/admin/signups/toggle").status_code == 403
    assert Browser().post(f"/admin/signups/{request_id}/approve").status_code == 303  # sent to sign in
    assert requests_waiting() == [("Priya Patel", "priya.p")]


@pytest.mark.parametrize("over,message", [
    ({"name": "  "}, "give your name"),
    ({"username": "a b"}, "username of 3 to 40"),
    ({"username": "ab"}, "username of 3 to 40"),
    ({"password": "short", "again": "short"}, "at least 10"),
    ({"again": "something-different-1"}, "didn't match"),
])
def test_bad_details_are_explained_and_nothing_is_made(people, browser, over, message):
    r = ask(browser, **over)
    assert r.status_code == 400 and message in text(r)
    assert requests_waiting() == []


def test_usernames_already_used_are_refused_however_they_are_typed(people, browser):
    assert "already taken" in ask(browser, username="WIFE").text  # an existing account
    assert ask(browser).status_code == 303
    assert "already taken" in ask(browser, name="Someone Else").text  # a request already waiting
    assert requests_waiting() == [("Priya Patel", "priya.p")]


def test_the_form_remembers_what_was_typed_but_never_the_password(people, browser):
    r = ask(browser, again="different-password-9")
    assert 'value="Priya Patel"' in r.text and 'value="priya.p"' in r.text and NEW["password"] not in r.text


def test_a_filled_in_trap_field_creates_nothing(people, browser):
    r = browser.post("/signup", {**NEW, "website": "http://spam.example"})
    assert r.status_code == 303 and requests_waiting() == []


def test_one_address_cannot_flood_the_form(people, browser):
    for i in range(signup_routes.PER_IP):
        assert ask(browser, username=f"person{i}").status_code == 303
    r = ask(browser, username="onemore")
    assert r.status_code == 429 and "Too many attempts" in r.text
    assert len(requests_waiting()) == signup_routes.PER_IP


def test_the_waiting_list_has_a_ceiling(people, monkeypatch):
    monkeypatch.setattr(signup_routes, "MAX_PENDING", 2)
    for i in range(2):
        signup_routes._recent.clear()
        assert ask(Browser(), username=f"person{i}").status_code == 303
    signup_routes._recent.clear()
    r = ask(Browser(), username="person9")
    assert r.status_code == 400 and "Too many requests are waiting" in r.text and len(requests_waiting()) == 2


def test_an_admin_can_close_and_reopen_sign_up(people, browser):
    dad = signed_in("dad")
    assert "Close sign-up" in dad.get("/admin").text
    dad.post("/admin/signups/toggle")
    assert "Open sign-up" in dad.get("/admin").text
    assert "Sign-up is closed" in browser.get("/signup").text
    r = ask(browser)
    assert r.status_code == 403 and requests_waiting() == []
    dad.post("/admin/signups/toggle")
    assert ask(browser).status_code == 303 and len(requests_waiting()) == 1


def test_admins_are_told_about_a_request(people, browser, monkeypatch):
    public, private = notify.generate_keys()
    monkeypatch.setenv("VAPID_PUBLIC_KEY", public)
    monkeypatch.setenv("VAPID_PRIVATE_KEY", private)
    sent = []
    monkeypatch.setattr(notify, "_post", lambda sub, payload: sent.append((sub.person_id, payload)))
    monkeypatch.setattr(notify, "_spawn", lambda fn, *args: fn(*args))
    dad = signed_in("dad")
    dad.post("/push/subscribe", {"endpoint": "https://fcm.googleapis.com/fcm/send/dad1", "p256dh": "B" * 87, "auth": "a" * 22})
    sent.clear()
    ask(browser)
    assert [(pid, p["title"], p["body"]) for pid, p in sent] == [(people["dad"], "New sign-up request", "Priya Patel would like an account.")]
    assert "password" not in str(sent).lower()
