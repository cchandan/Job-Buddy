"""A user only ever reaches their own pages; only admins reach Admin pages or another user's data."""
from app import db
from conftest import make_job, signed_in


def _entry_for(browser, title="Private job"):
    browser.post("/tracker/new", {"title": title, "company": "Co", "description": "x" * 200})
    with db.SessionLocal() as s:
        return s.query(db.TrackerEntry).order_by(db.TrackerEntry.id.desc()).first().id


def test_user_cannot_open_admin_pages(people):
    wife = signed_in("wife")
    for url in ("/admin", "/admin/review", "/admin/sync", f"/admin/users/{people['brother']}/tracker",
                f"/admin/users/{people['brother']}/profile", f"/admin/users/{people['brother']}/calendar",
                f"/admin/users/{people['brother']}/cv/download"):
        assert wife.get(url).status_code == 403, url
    for url in ("/admin/sync/publish", "/admin/sync/fetch", f"/admin/users/{people['brother']}/assign", "/admin/accounts"):
        assert wife.post(url).status_code == 403, url


def test_user_cannot_reach_another_users_entry(people):
    brother = signed_in("brother")
    entry_id = _entry_for(brother)
    wife = signed_in("wife")
    for url in (f"/tracker/{entry_id}", f"/tracker/{entry_id}/cv", f"/tracker/{entry_id}/edit", f"/tracker/{entry_id}/cv/print"):
        assert wife.get(url).status_code == 403, url
    for url, data in ((f"/tracker/{entry_id}/status", {"status": "applied"}), (f"/tracker/{entry_id}/remove", {}),
                      (f"/tracker/{entry_id}/deadline", {"deadline": "2026-11-01"}), (f"/tracker/{entry_id}/notes", {"notes": "hi"}),
                      (f"/tracker/{entry_id}/cv/create", {}), (f"/tracker/{entry_id}/apply", {})):
        assert wife.post(url, data).status_code == 403, url
    with db.SessionLocal() as s:
        entry = s.get(db.TrackerEntry, entry_id)
        assert entry is not None and entry.status == "shortlisted" and not entry.notes and entry.deadline_override is None
    assert "Private job" not in wife.get("/tracker").text


def test_admin_can_view_a_user_but_not_change_their_status(people):
    brother = signed_in("brother")
    entry_id = _entry_for(brother)
    dad = signed_in("dad")
    page = dad.get(f"/admin/users/{people['brother']}/tracker")
    assert page.status_code == 200 and "Private job" in page.text and "Viewing Brother" in page.text
    assert dad.get(f"/admin/users/{people['brother']}/entry/{entry_id}").status_code == 200
    assert dad.post(f"/tracker/{entry_id}/status", {"status": "applied"}).status_code == 403
    assert dad.get("/tracker").status_code == 403  # admins have no Tracker of their own


def test_admin_entry_view_checks_the_user_matches(people):
    brother = signed_in("brother")
    entry_id = _entry_for(brother)
    dad = signed_in("dad")
    assert dad.get(f"/admin/users/{people['wife']}/entry/{entry_id}").status_code == 404


def test_admin_assigns_a_job_to_one_user_only(people):
    make_job("j1", title="Assigned role")
    dad = signed_in("dad")
    for _ in range(2):  # assigning twice still makes one entry
        dad.post(f"/admin/users/{people['wife']}/assign", {"how": "board", "job_id": "j1"})
    with db.SessionLocal() as s:
        entries = s.query(db.TrackerEntry).all()
        assert len(entries) == 1 and entries[0].person_id == people["wife"] and entries[0].source == "assigned"
    wife, brother = signed_in("wife"), signed_in("brother")
    assert "New job from Dad" in wife.get("/").text
    page = wife.get("/tracker").text
    assert "Assigned role" in page and "Assigned by Dad" in page
    assert "Assigned role" not in brother.get("/tracker").text
