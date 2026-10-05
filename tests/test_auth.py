"""Sign-in: admin-created accounts, generated passwords, lockout, and no way in without signing in."""
import re

from app import auth, db
from conftest import Browser, make_person, signed_in


def test_every_page_needs_sign_in(browser):
    for url in ("/board", "/tracker", "/calendar", "/profile", "/admin", "/admin/review", "/admin/sync"):
        r = browser.get(url)
        assert r.status_code == 303 and r.headers["location"].startswith("/login"), url
    assert browser.get("/health").status_code == 200
    assert browser.get("/login").status_code == 200


def test_sign_in_and_out(people):
    b = Browser()
    r = b.login("wife")
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert b.get("/").status_code == 200
    assert b.login("dad").status_code == 303
    assert b.get("/").headers["location"] == "/admin"  # admins land on Admin home
    b.post("/logout")
    assert b.get("/").status_code == 200


def test_public_landing_page_contains_no_private_data(people, browser):
    page = browser.get("/")
    assert page.status_code == 200
    assert 'href="/login"' in page.text
    assert "Example workspace" in page.text
    assert "Good morning, Wife" not in page.text
    assert 'href="/admin"' not in page.text
    assert page.headers["Cache-Control"] == "no-store"


def test_wrong_details_give_the_same_message(people):
    b = Browser()
    wrong_password = b.login("wife", "nope")
    no_such_user = b.login("nobody", "nope")
    assert wrong_password.status_code == no_such_user.status_code == 401
    message = "That username or password isn&#39;t right."
    assert message in wrong_password.text and message in no_such_user.text


def test_repeated_failures_lock_the_account(people):
    b = Browser()
    for _ in range(auth.MAX_FAILURES):
        b.login("wife", "nope")
    r = b.login("wife")  # even the right password is refused while locked
    assert r.status_code == 401 and "Too many attempts" in r.text


def test_password_is_stored_as_a_hash(people):
    with db.SessionLocal() as s:
        stored = s.get(db.Person, people["wife"]).password_hash
    assert "correct-horse-1" not in stored and stored.startswith("scrypt$")
    assert auth.check_password("correct-horse-1", stored) and not auth.check_password("other", stored)


def test_admin_creates_an_account_and_the_password_works(people):
    admin = signed_in("dad")
    assert admin.post("/admin/accounts", {"name": "New Person", "username": "newbie", "role": "user"}).status_code == 303
    page = admin.get("/admin").text
    password = re.search(r'<code id="pw">([^<]+)</code>', page).group(1)
    assert password not in admin.get("/admin").text  # shown once only
    assert Browser().login("newbie", password).status_code == 303


def test_new_password_stops_the_old_one_and_signs_them_out(people):
    wife = signed_in("wife")
    admin = signed_in("dad")
    admin.post(f"/admin/accounts/{people['wife']}/password")
    new = re.search(r'<code id="pw">([^<]+)</code>', admin.get("/admin").text).group(1)
    assert wife.get("/tracker").status_code == 303           # signed out everywhere
    assert Browser().login("wife").status_code == 401        # old password refused
    assert Browser().login("wife", new).status_code == 303


def test_users_cannot_create_accounts(people):
    wife = signed_in("wife")
    assert wife.post("/admin/accounts", {"name": "X", "username": "sneaky", "role": "admin"}).status_code == 403
    with db.SessionLocal() as s:
        assert s.query(db.Person).filter(db.Person.username == "sneaky").first() is None


def test_change_own_password(people):
    wife = signed_in("wife")
    wife.post("/profile/password", {"current": "correct-horse-1", "new": "a-brand-new-one", "again": "a-brand-new-one"})
    assert wife.get("/profile").status_code == 200           # this browser stays signed in
    assert Browser().login("wife", "a-brand-new-one").status_code == 303
    assert Browser().login("wife").status_code == 401


def test_forms_need_the_csrf_token(people):
    wife = signed_in("wife")
    assert wife.client.post("/tracker/new", data={"title": "T", "company": "C"}).status_code == 403


def test_remove_account_deletes_their_data(people):
    make_person("temp")
    temp = signed_in("temp")
    temp.post("/tracker/new", {"title": "Job", "company": "Co"})
    admin = signed_in("dad")
    with db.SessionLocal() as s:
        temp_id = s.query(db.Person).filter(db.Person.username == "temp").one().id
    admin.post(f"/admin/accounts/{temp_id}/remove", {"confirm": "wrong"})
    with db.SessionLocal() as s:
        assert s.get(db.Person, temp_id) is not None       # needs the username typed exactly
    admin.post(f"/admin/accounts/{temp_id}/remove", {"confirm": "temp"})
    with db.SessionLocal() as s:
        assert s.get(db.Person, temp_id) is None
        assert s.query(db.TrackerEntry).filter(db.TrackerEntry.person_id == temp_id).count() == 0
