"""Admin "View passwords": needs the admin's own password again, only shows admin-issued passwords."""
import re

from app import db
from conftest import make_person, signed_in


def _create(admin, username="newbie"):
    admin.post("/admin/accounts", {"name": "New Bie", "username": username, "role": "user"})
    return re.search(r'id="pw">([^<]+)<', admin.get("/admin").text).group(1)


def test_needs_admin_password_then_shows_issued_password(people):
    admin = signed_in("dad")
    issued = _create(admin)
    assert issued not in admin.get("/admin/passwords").text  # the page alone shows nothing
    bad = admin.post("/admin/passwords", {"password": "wrong"})
    assert bad.status_code == 401 and issued not in bad.text
    ok = admin.post("/admin/passwords", {"password": "correct-horse-1"})
    assert ok.status_code == 200 and issued in ok.text and ok.headers["Cache-Control"] == "no-store"
    assert issued not in admin.get("/admin/passwords").text  # nothing is remembered


def test_users_cannot_reach_it(people):
    wife = signed_in("wife")
    assert wife.get("/admin/passwords").status_code == 403
    assert wife.post("/admin/passwords", {"password": "correct-horse-1"}).status_code == 403


def test_own_password_is_never_kept(people):
    admin = signed_in("dad")
    issued = _create(admin)
    from conftest import Browser
    b = Browser()
    assert b.login("newbie", issued).status_code == 303
    b.post("/profile/password", {"current": issued, "new": "my-own-secret-9", "again": "my-own-secret-9"})
    page = admin.post("/admin/passwords", {"password": "correct-horse-1"}).text
    assert issued not in page and "my-own-secret-9" not in page and "chose their own" in page
    with db.SessionLocal() as s:
        assert s.query(db.Person).filter_by(username="newbie").one().password_enc is None


def test_wrong_guesses_lock_the_admin(people):
    admin = signed_in("dad")
    for _ in range(5):
        admin.post("/admin/passwords", {"password": "nope"})
    r = admin.post("/admin/passwords", {"password": "correct-horse-1"})
    assert r.status_code == 401 and "Too many" in r.text
